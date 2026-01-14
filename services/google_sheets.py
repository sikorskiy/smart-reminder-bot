import logging
from typing import List, Dict, Optional
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]


class GoogleSheetsService:
    """
    Service for Google Sheets operations.

    Table structure:
    | datetime | text | timezone | sent | status | comment | forward_author | user_id |
    |----------|------|----------|------|--------|---------|----------------|---------|
    """

    COLUMNS = {
        'datetime': 1,
        'text': 2,
        'timezone': 3,
        'sent': 4,
        'status': 5,
        'comment': 6,
        'forward_author': 7,
        'user_id': 8
    }

    def __init__(self, creds_path: str, spreadsheet_name: str, worksheet_name: str = 'reminders'):
        """
        Initialize Google Sheets connection.

        Args:
            creds_path: Path to service account credentials JSON
            spreadsheet_name: Name of the spreadsheet
            worksheet_name: Name of the worksheet
        """
        try:
            creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
            self.gc = gspread.authorize(creds)
            self.spreadsheet = self.gc.open(spreadsheet_name)
            self.worksheet = self.spreadsheet.worksheet(worksheet_name)
            logger.info(f"Connected to Google Sheets: {spreadsheet_name}/{worksheet_name}")
        except Exception as e:
            logger.error(f"Failed to connect to Google Sheets: {e}")
            raise

    def add_reminder(
        self,
        text: str,
        datetime_str: Optional[str] = None,
        timezone: str = 'Europe/Moscow',
        comment: str = '',
        forward_author: str = '',
        user_id: Optional[int] = None
    ) -> Optional[int]:
        """
        Add a new reminder to the sheet.

        Args:
            text: Reminder text
            datetime_str: Date/time string (YYYY-MM-DD HH:MM:SS) or None for timeless reminders
            timezone: Timezone string
            comment: Original forwarded message text (if any)
            forward_author: Author of forwarded message (if any)
            user_id: Telegram user ID who created the reminder

        Returns:
            Row number if successful, None otherwise
        """
        try:
            row = [
                datetime_str or '',  # datetime
                text,                 # text
                timezone,             # timezone
                'FALSE',              # sent
                '',                   # status
                comment,              # comment (original forwarded text)
                forward_author,       # forward_author
                str(user_id) if user_id else ''  # user_id
            ]

            logger.info(f"Adding reminder: {row}")
            self.worksheet.append_row(row)

            # Get the row number
            all_values = self.worksheet.get_all_values()
            row_number = len(all_values)

            return row_number

        except Exception as e:
            logger.error(f"Error adding reminder: {e}")
            return None

    def get_pending_reminders(self) -> List[Dict]:
        """
        Get all reminders that haven't been sent yet and have a datetime set.

        Returns:
            List of reminder dictionaries
        """
        try:
            records = self.worksheet.get_all_records()
            reminders = []

            for i, row in enumerate(records, start=2):  # Row 1 is header
                sent = str(row.get('sent', '')).strip().lower()
                datetime_val = row.get('datetime', '')

                # Only get reminders with datetime that haven't been sent
                if datetime_val and sent != 'true':
                    reminders.append({
                        'row': i,
                        'datetime': datetime_val,
                        'text': row.get('text', ''),
                        'timezone': row.get('timezone', 'Europe/Moscow'),
                        'comment': row.get('comment', ''),
                        'forward_author': row.get('forward_author', ''),
                        'status': row.get('status', ''),
                        'user_id': row.get('user_id', '')
                    })

            return reminders

        except Exception as e:
            logger.error(f"Error getting pending reminders: {e}")
            return []

    def get_timeless_reminders(self) -> List[Dict]:
        """
        Get all reminders without datetime (for weekly review).

        Returns:
            List of reminder dictionaries without datetime
        """
        try:
            records = self.worksheet.get_all_records()
            reminders = []

            for i, row in enumerate(records, start=2):
                datetime_val = row.get('datetime', '')
                status = row.get('status', '').strip().lower()

                # Get reminders without datetime that aren't done/canceled
                if not datetime_val and status not in ['done', 'canceled']:
                    reminders.append({
                        'row': i,
                        'text': row.get('text', ''),
                        'timezone': row.get('timezone', 'Europe/Moscow'),
                        'comment': row.get('comment', ''),
                        'forward_author': row.get('forward_author', ''),
                        'user_id': row.get('user_id', '')
                    })

            return reminders

        except Exception as e:
            logger.error(f"Error getting timeless reminders: {e}")
            return []

    def mark_as_sent(self, row: int) -> bool:
        """Mark a reminder as sent."""
        try:
            self.worksheet.update_cell(row, self.COLUMNS['sent'], 'TRUE')
            logger.info(f"Marked row {row} as sent")
            return True
        except Exception as e:
            logger.error(f"Error marking as sent: {e}")
            return False

    def update_status(self, row: int, status: str) -> bool:
        """
        Update reminder status.

        Args:
            row: Row number
            status: New status ('done' or 'canceled')
        """
        try:
            self.worksheet.update_cell(row, self.COLUMNS['status'], status)
            logger.info(f"Updated row {row} status to: {status}")
            return True
        except Exception as e:
            logger.error(f"Error updating status: {e}")
            return False

    def update_datetime(self, row: int, datetime_str: str) -> bool:
        """
        Update reminder datetime (for converting timeless to timed reminders).

        Args:
            row: Row number
            datetime_str: New datetime string
        """
        try:
            self.worksheet.update_cell(row, self.COLUMNS['datetime'], datetime_str)
            logger.info(f"Updated row {row} datetime to: {datetime_str}")
            return True
        except Exception as e:
            logger.error(f"Error updating datetime: {e}")
            return False

    def get_reminder_by_row(self, row: int) -> Optional[Dict]:
        """Get a reminder by row number."""
        try:
            row_values = self.worksheet.row_values(row)
            if len(row_values) >= 2:
                return {
                    'row': row,
                    'datetime': row_values[0] if len(row_values) > 0 else '',
                    'text': row_values[1] if len(row_values) > 1 else '',
                    'timezone': row_values[2] if len(row_values) > 2 else 'Europe/Moscow',
                    'sent': row_values[3] if len(row_values) > 3 else '',
                    'status': row_values[4] if len(row_values) > 4 else '',
                    'comment': row_values[5] if len(row_values) > 5 else '',
                    'forward_author': row_values[6] if len(row_values) > 6 else '',
                    'user_id': row_values[7] if len(row_values) > 7 else ''
                }
            return None
        except Exception as e:
            logger.error(f"Error getting reminder by row: {e}")
            return None
