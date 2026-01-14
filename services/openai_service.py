import os
import json
import logging
import tempfile
from datetime import datetime
from typing import Optional, Dict, Tuple

import openai
import pytz
import requests

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False

logger = logging.getLogger(__name__)


class OpenAIService:
    """Service for OpenAI API interactions: date parsing and voice transcription."""

    def __init__(self, api_key: str, timezone: str = 'Europe/Moscow'):
        self.client = openai.OpenAI(api_key=api_key)
        self.timezone = timezone

    def extract_reminder_info(self, message: str) -> Optional[Dict]:
        """
        Extract reminder information from text message using GPT-4.

        Handles complex date/time expressions like:
        - "through 10 hours"
        - "on the 10th" (current month)
        - "on Sunday" (nearest)
        - "30 hours before 18:00 on October 29th"

        Returns:
            Dict with 'text', 'datetime' (or None), 'timezone'
        """
        try:
            tz = pytz.timezone(self.timezone)
            current_time = datetime.now(tz)
            current_time_str = current_time.strftime('%Y-%m-%d %H:%M:%S')
            weekday_name = current_time.strftime('%A')

            system_prompt = f"""You are a precise date/time extraction assistant for a reminder bot.

CURRENT DATE AND TIME: {current_time_str} ({self.timezone})
CURRENT DAY OF WEEK: {weekday_name}

Your task: Extract reminder information from user messages in Russian.

RULES FOR DATE/TIME CALCULATION:

1. RELATIVE TIME:
   - "через X часов/минут/дней" = current time + X
   - "через полчаса" = current time + 30 minutes
   - "через час" = current time + 1 hour

2. SPECIFIC DATES WITHOUT YEAR:
   - "10-го числа" or "10-го" = 10th of CURRENT month (if not passed) or NEXT month
   - "в январе", "в феврале" = NEAREST future occurrence of that month
   - If the date has already passed this month/year, use NEXT occurrence

3. DAYS OF WEEK:
   - "в воскресенье", "в понедельник" = NEAREST FUTURE occurrence
   - "в эту субботу" = this week's Saturday (if not passed)
   - "в следующий понедельник" = next week's Monday

4. COMPLEX EXPRESSIONS:
   - "за X часов до события" = event_time - X hours
   - "за 30 часов до 18:00 29-го октября" = calculate 29 Oct 18:00 - 30 hours
   - "за день до встречи в пятницу" = Thursday (day before Friday)

5. NO TIME SPECIFIED:
   - If message has NO time/date info, return datetime: null
   - Examples: "купить молоко", "позвонить маме" without time = datetime: null

6. DEFAULT TIME:
   - If date specified but no time: use 09:00 as default
   - "завтра" without time = tomorrow at 09:00
   - "в пятницу" without time = Friday at 09:00

CRITICAL: Never return a past date/time. Always calculate relative to {current_time_str}.

EXTRACT:
1. text: The reminder content (what to remind about), in Russian, starting with capital letter
2. datetime: In format "YYYY-MM-DD HH:MM:SS" or null if no time specified
3. timezone: "{self.timezone}"

Return ONLY a JSON object:
{{"text": "reminder text", "datetime": "YYYY-MM-DD HH:MM:SS" or null, "timezone": "{self.timezone}"}}
"""

            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message}
                ],
                temperature=0.1,
                max_tokens=300
            )

            result = response.choices[0].message.content.strip()

            # Clean up response - remove markdown code blocks if present
            if result.startswith("```"):
                result = result.split("```")[1]
                if result.startswith("json"):
                    result = result[4:]
                result = result.strip()

            reminder_info = json.loads(result)

            if reminder_info is None:
                return None

            if 'text' not in reminder_info:
                logger.warning(f"Missing 'text' in reminder info: {reminder_info}")
                return None

            # Ensure timezone is set
            if 'timezone' not in reminder_info:
                reminder_info['timezone'] = self.timezone

            logger.info(f"Extracted reminder info: {reminder_info}")
            return reminder_info

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}, response: {result}")
            return None
        except Exception as e:
            logger.error(f"Error extracting reminder info: {e}")
            return None

    def validate_reminder_info(self, reminder_info: Dict) -> Tuple[bool, str]:
        """
        Validate extracted reminder information.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not reminder_info:
            return False, "No reminder information"

        if 'text' not in reminder_info or not reminder_info['text']:
            return False, "Missing reminder text"

        # Allow reminders without datetime
        if reminder_info.get('datetime') is None:
            return True, ""

        datetime_str = reminder_info['datetime']

        try:
            dt = datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')

            # Check if time is in the past (with 1 minute buffer)
            tz = pytz.timezone(reminder_info.get('timezone', self.timezone))
            current_time = datetime.now(tz).replace(tzinfo=None)

            if dt < current_time:
                return False, "Reminder time is in the past"

        except ValueError:
            return False, "Invalid datetime format"

        return True, ""

    def extract_and_validate(self, message: str) -> Tuple[Optional[Dict], str]:
        """
        Extract and validate reminder info in one call.
        If validation fails due to past time, retry with adjustment.

        Returns:
            Tuple of (reminder_info or None, error_message)
        """
        reminder_info = self.extract_reminder_info(message)

        if reminder_info is None:
            return None, "Could not parse reminder"

        is_valid, error = self.validate_reminder_info(reminder_info)

        if is_valid:
            return reminder_info, ""

        # If time is in the past, retry with explicit instruction
        if error == "Reminder time is in the past":
            adjusted_message = f"""{message}

IMPORTANT: Previous calculation resulted in a past time.
Recalculate to get the NEAREST FUTURE date/time while preserving the original intent."""

            reminder_info = self.extract_reminder_info(adjusted_message)
            if reminder_info:
                is_valid, error = self.validate_reminder_info(reminder_info)
                if is_valid:
                    return reminder_info, ""

        return None, error

    def extract_forwarded_message_info(self, forwarded_text: str) -> Optional[Dict]:
        """
        Extract reminder info from a forwarded message (without user explanation).
        Creates a task description from the forwarded content.
        """
        prompt = f"""Convert this forwarded message into a short, actionable reminder task.
Do NOT include words like "remind" - just the action itself.
If there's a date/time mentioned, extract it. If not, datetime should be null.

Forwarded message: {forwarded_text}"""

        return self.extract_reminder_info(prompt)

    async def transcribe_voice(self, audio_data: bytes) -> Optional[str]:
        """
        Transcribe voice message using Whisper API.

        Args:
            audio_data: Raw audio bytes (OGG format from Telegram)

        Returns:
            Transcribed text or None
        """
        if not PYDUB_AVAILABLE:
            logger.error("pydub not available for audio conversion")
            return None

        temp_input = None
        temp_output = None

        try:
            # Save OGG to temp file
            with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as f:
                f.write(audio_data)
                temp_input = f.name

            # Convert to MP3
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
                temp_output = f.name

            audio = AudioSegment.from_ogg(temp_input)
            audio.export(temp_output, format="mp3")

            # Transcribe with Whisper
            with open(temp_output, 'rb') as audio_file:
                transcript = self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="ru"
                )

            text = transcript.text.strip()
            logger.info(f"Transcribed voice: {text}")
            return text

        except Exception as e:
            logger.error(f"Error transcribing voice: {e}")
            return None
        finally:
            # Cleanup temp files
            for path in [temp_input, temp_output]:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except:
                        pass

    async def download_and_transcribe(self, file_url: str) -> Optional[str]:
        """
        Download audio from URL and transcribe it.

        Args:
            file_url: URL to audio file

        Returns:
            Transcribed text or None
        """
        try:
            response = requests.get(file_url, timeout=30)
            response.raise_for_status()
            return await self.transcribe_voice(response.content)
        except Exception as e:
            logger.error(f"Error downloading audio: {e}")
            return None
