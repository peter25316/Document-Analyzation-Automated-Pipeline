import os
import io
import sqlite3
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from google.cloud import vision

# --- Configuration ---
# 1. This is the Folder ID from your Google Drive URL.
DRIVE_FOLDER_ID = '1o7u6u0_29MVBnuV0gPRf0y7pWkJgheIg'

# 2. This is the name of your Vision API service account key file.
SERVICE_ACCOUNT_FILE = 'solarplanninganalytics-serviceaccount.json'

# 3. This is the name for your new SQLite database file.
DB_FILE = 'ocr_data.db'

# 4. Google API Scopes - we only need to read files.
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

# --- Set up authentication for the Vision API ---
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = SERVICE_ACCOUNT_FILE


def setup_database():
    """Creates the SQLite database and table if they don't exist."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # The status column will track our progress for each file.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY,
            pdf_id TEXT UNIQUE NOT NULL,
            pdf_name TEXT NOT NULL,
            ocr_text TEXT,
            status TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()


def get_drive_service():
    """Handles Google Drive authentication and returns a service object."""
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('drive', 'v3', credentials=creds)


def ocr_pdf_content(pdf_content):
    """Sends PDF content to the Vision API and returns the extracted text."""
    client = vision.ImageAnnotatorClient()
    input_config = vision.InputConfig(content=pdf_content, mime_type='application/pdf')
    features = [vision.Feature(type_=vision.Feature.Type.DOCUMENT_TEXT_DETECTION)]
    request = vision.AnnotateFileRequest(input_config=input_config, features=features)
    response = client.batch_annotate_files(requests=[request])

    # Check for errors in the response
    if response.responses[0].error.message:
        raise Exception(response.responses[0].error.message)

    full_text = ""
    for image_response in response.responses[0].responses:
        full_text += image_response.full_text_annotation.text
    return full_text


# --- Main Script Execution ---
if __name__ == "__main__":
    # 1. Prepare the database.
    setup_database()
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # 2. Get authorized access to Google Drive.
    print("Authenticating with Google Drive...")
    drive_service = get_drive_service()
    print("Authentication successful.")

    # 3. Get the list of PDF files from the specified folder.
    print(f"Fetching PDF files from folder ID: {DRIVE_FOLDER_ID}...")
    try:
        query = f"'{DRIVE_FOLDER_ID}' in parents and mimeType='application/pdf'"
        results = drive_service.files().list(q=query, fields="files(id, name)").execute()
        items = results.get('files', [])
    except HttpError as error:
        print(f"An error occurred fetching files: {error}")
        items = []

    if not items:
        print('No PDF files found in the specified folder.')
    else:
        print(f"Found {len(items)} PDF files. Starting processing...")

        for item in items:
            pdf_id = item['id']
            pdf_name = item['name']

            # 4. Check if the file has already been processed successfully.
            cursor.execute("SELECT status FROM documents WHERE pdf_id = ?", (pdf_id,))
            result = cursor.fetchone()
            if result and result[0] == 'ocr_complete':
                print(f"'{pdf_name}' has already been processed. Skipping.")
                continue

            print(f"\n--- Processing '{pdf_name}' ---")
            try:
                # 5. Download the file content into memory.
                request = drive_service.files().get_media(fileId=pdf_id)
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                    print(f"Download {int(status.progress() * 100)}%.")

                # 6. Send the content to the Vision API for OCR.
                print("Sending to Vision API for OCR...")
                extracted_text = ocr_pdf_content(fh.getvalue())

                # 7. Save the successful result to the database.
                cursor.execute(
                    "INSERT OR REPLACE INTO documents (pdf_id, pdf_name, ocr_text, status) VALUES (?, ?, ?, ?)",
                    (pdf_id, pdf_name, extracted_text, 'ocr_complete')
                )
                conn.commit()
                print(f"Successfully processed and saved '{pdf_name}' to the database.")

            except Exception as e:
                # 8. If any step fails, record the error in the database.
                print(f"An ERROR occurred while processing '{pdf_name}': {e}")
                cursor.execute(
                    "INSERT OR REPLACE INTO documents (pdf_id, pdf_name, ocr_text, status) VALUES (?, ?, ?, ?)",
                    (pdf_id, pdf_name, str(e), 'ocr_error')
                )
                conn.commit()

    conn.close()
    print("\n✅ All processing complete.")