import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# OpenAI
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Google Sheets
GOOGLE_SHEETS_CREDS = os.getenv('GOOGLE_SHEETS_CREDS', 'credentials.json')
GOOGLE_SHEETS_SPREADSHEET = os.getenv('GOOGLE_SHEETS_SPREADSHEET', 'reminders')
GOOGLE_SHEETS_WORKSHEET = os.getenv('GOOGLE_SHEETS_WORKSHEET', 'reminders')

# Timezone
DEFAULT_TIMEZONE = os.getenv('TIMEZONE', 'Europe/Moscow')

# Scheduler settings
REMINDER_CHECK_INTERVAL_MINUTES = 1
WEEKLY_CHECK_DAY = 'sun'  # Day for weekly check of timeless reminders
WEEKLY_CHECK_HOUR = 10    # Hour for weekly check
