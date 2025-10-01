import sqlite3
import google.generativeai as genai
import json
import os
import time
from dotenv import load_dotenv

# Load variables from the .env file into the environment
load_dotenv()

# --- Configuration ---
DB_FILE = 'ocr_data.db'

# --- IMPORTANT: Configure Gemini API Key ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("Error: GEMINI_API_KEY not found in .env file or environment variables.")

genai.configure(api_key=GEMINI_API_KEY)


def fetch_records_to_process(conn):
    """Fetches records from the DB that are ready for processing."""
    cursor = conn.cursor()
    cursor.execute("""
                   SELECT pdf_id, pdf_name, ocr_text
                   FROM documents
                   WHERE status = 'ocr_complete'
                     AND (gemini_status IS NULL OR gemini_status = 'gemini_error')
                   """)
    return cursor.fetchall()


def is_document_relevant(ocr_text, pdf_name):
    """
    Uses a cheap, fast prompt to check if the document is relevant for full analysis.
    Returns True if relevant, False otherwise.
    """
    print(f"  > Routing '{pdf_name}'...")
    try:
        model = genai.GenerativeModel('models/gemini-2.5-flash-lite')

        prompt = f"""
        Does the following text from a public document appear to contain a discussion, application, or vote related to a specific land use project, construction, solar project, or zoning change? 
        Answer only with the single word YES or NO.

        ---
        {ocr_text[:3000]} 
        ---
        """  # We only send the first 3000 characters to save costs

        response = model.generate_content(prompt)

        if "YES" in response.text.upper():
            print("    - Result: Relevant. Proceeding to full analysis.")
            return True
        else:
            print("    - Result: Irrelevant. Skipping.")
            return False

    except Exception as e:
        print(f"    !! Router prompt failed for '{pdf_name}': {e}. Skipping as a precaution.")
        return False


def extract_structured_data(ocr_text, pdf_name):
    """Sends OCR'd text to Gemini and asks for specific structured data."""
    print(f"  > Analyzing '{pdf_name}' with Gemini...")
    model = genai.GenerativeModel('models/gemini-flash-latest')
    # ... (The detailed prompt remains the same as before) ...
    prompt = f"""
    Analyze the following text from a Southampton County, VA, public document...
    (Your full detailed prompt goes here)
    """

    try:
        response = model.generate_content(prompt)
        json_string = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(json_string)
    except Exception as e:
        print(f"    !! Gemini API or JSON parsing error: {e}")
        return {"error": str(e), "raw_response": response.text if 'response' in locals() else "No response"}


def update_database_with_result(conn, pdf_id, json_result, status):
    """Updates the database with the result and new status."""
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE documents SET gemini_json = ?, gemini_status = ? WHERE pdf_id = ?",
        (json.dumps(json_result, indent=2), status, pdf_id)
    )
    conn.commit()


# --- Main Script Execution ---
if __name__ == "__main__":
    conn = sqlite3.connect(DB_FILE)

    records_to_process = fetch_records_to_process(conn)

    if not records_to_process:
        print("No new records found for processing.")
    else:
        print(f"Found {len(records_to_process)} records to process.")

        for record in records_to_process:
            pdf_id, pdf_name, ocr_text = record
            print(f"\nProcessing: {pdf_name}")

            # --- ROUTER LOGIC ADDED ---
            # First, check if the document is relevant.
            if is_document_relevant(ocr_text, pdf_name):
                # If it's relevant, perform the full extraction.
                structured_data = extract_structured_data(ocr_text, pdf_name)
                update_database_with_result(conn, pdf_id, structured_data,
                                            'gemini_error' if 'error' in structured_data else 'gemini_complete')
                print(f"  > ✅ Successfully updated database for '{pdf_name}'.")
            else:
                # If not relevant, mark it as such and skip.
                update_database_with_result(conn, pdf_id, {"status": "irrelevant"}, 'irrelevant')
                print(f"  > ✅ Marked '{pdf_name}' as irrelevant in the database.")

            time.sleep(31)

    conn.close()
    print("\n\n🎉 All processing complete.")
