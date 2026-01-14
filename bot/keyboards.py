from telegram import InlineKeyboardButton, InlineKeyboardMarkup


class Keyboards:
    """Inline keyboard builders for the bot."""

    @staticmethod
    def reminder_actions(row: int) -> InlineKeyboardMarkup:
        """
        Create keyboard for reminder notification.

        Args:
            row: Row number in Google Sheets for callback data
        """
        keyboard = [
            [
                InlineKeyboardButton("Done", callback_data=f"done_{row}"),
                InlineKeyboardButton("Cancel", callback_data=f"cancel_{row}")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def timeless_reminder_actions(row: int) -> InlineKeyboardMarkup:
        """
        Create keyboard for weekly check of timeless reminders.

        Args:
            row: Row number in Google Sheets
        """
        keyboard = [
            [
                InlineKeyboardButton("Still relevant", callback_data=f"relevant_{row}"),
                InlineKeyboardButton("No longer needed", callback_data=f"cancel_{row}")
            ],
            [
                InlineKeyboardButton("Set deadline", callback_data=f"settime_{row}")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def confirm_creation() -> InlineKeyboardMarkup:
        """Keyboard to confirm reminder was created."""
        keyboard = [
            [
                InlineKeyboardButton("OK", callback_data="confirm_ok")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
