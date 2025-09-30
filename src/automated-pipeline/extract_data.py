import sqlite3
import os

# --- Configuration ---
DB_FILE = 'ocr_data.db'


def fetch_records_to_process():
    """
    Connects to the SQLite database and fetches all records that are ready for Gemini processing.
    A record is ready if its 'status' is 'ocr_complete' and its 'gemini_status' is not 'gemini_complete'.
    """

    # Check if the database file exists
    if not os.path.exists(DB_FILE):
        print(f"Error: Database file '{DB_FILE}' not found. Please run the OCR script first.")
        return []

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # This query selects only the rows we need to work on.
    cursor.execute("""
                   SELECT pdf_id, pdf_name, ocr_text
                   FROM documents
                   WHERE status = 'ocr_complete'
                     AND (gemini_status IS NULL OR gemini_status != 'gemini_complete')
                   """)

    records = cursor.fetchall()
    conn.close()

    return records


# --- Main Script Execution ---
if __name__ == "__main__":
    print("Attempting to fetch records from the database...")

    records_to_process = fetch_records_to_process()

    if records_to_process:
        print(f"\n✅ Success! Found {len(records_to_process)} records ready for analysis.")

        # As an example, let's look at the first record found.
        first_record = records_to_process[0]
        pdf_id, pdf_name, ocr_text = first_record

        print(f"\n--- Example Record ---")
        print(f"PDF Name: {pdf_name}")
        print(f"PDF ID: {pdf_id}")
        print(f"OCR Text Snippet: '{ocr_text[:100]}...'")  # Print the first 100 characters

    else:
        print("\nNo new records to process, or the database file was not found.")