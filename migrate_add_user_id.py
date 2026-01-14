#!/usr/bin/env python3
"""
Migration script to add user_id column to existing reminders.
Run once after deploying the multi-user update.
"""

import gspread
from google.oauth2.service_account import Credentials

import config

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# Your Telegram user ID
DEFAULT_USER_ID = '36542572'


def migrate():
    print("Connecting to Google Sheets...")

    creds = Credentials.from_service_account_file(config.GOOGLE_SHEETS_CREDS, scopes=SCOPES)
    gc = gspread.authorize(creds)
    spreadsheet = gc.open(config.GOOGLE_SHEETS_SPREADSHEET)
    worksheet = spreadsheet.worksheet(config.GOOGLE_SHEETS_WORKSHEET)

    # Get all values
    all_values = worksheet.get_all_values()

    if not all_values:
        print("Sheet is empty, nothing to migrate")
        return

    # Check header row
    header = all_values[0]
    print(f"Current headers: {header}")

    # Add user_id header if missing
    if len(header) < 8 or header[7] != 'user_id':
        print("Adding 'user_id' header...")
        worksheet.update_cell(1, 8, 'user_id')
    else:
        print("'user_id' header already exists")

    # Update all data rows with default user_id
    data_rows = all_values[1:]  # Skip header
    updated = 0

    for i, row in enumerate(data_rows, start=2):  # Row numbers start from 2
        # Check if user_id is missing or empty
        current_user_id = row[7] if len(row) > 7 else ''

        if not current_user_id:
            worksheet.update_cell(i, 8, DEFAULT_USER_ID)
            updated += 1
            print(f"Updated row {i} with user_id={DEFAULT_USER_ID}")

    print(f"\nMigration complete! Updated {updated} rows.")


if __name__ == '__main__':
    migrate()
