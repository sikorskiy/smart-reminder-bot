import logging
import asyncio
import time
from datetime import datetime
from typing import Optional, Dict

from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards import Keyboards
from services.openai_service import OpenAIService
from services.google_sheets import GoogleSheetsService

logger = logging.getLogger(__name__)


class BotHandlers:
    """
    Telegram bot message handlers.

    Handles:
    - Text messages (reminders)
    - Voice messages (Whisper transcription)
    - Forwarded messages (with or without explanation)
    - Callback queries (done/not done buttons)
    """

    def __init__(
        self,
        openai_service: OpenAIService,
        sheets_service: GoogleSheetsService,
        timezone: str = 'Europe/Moscow'
    ):
        self.openai = openai_service
        self.sheets = sheets_service
        self.timezone = timezone

        # Buffer for linking messages (explanation + forwarded)
        # {user_id: {'message': str, 'is_forwarded': bool, 'timestamp': float, 'update': Update}}
        self.message_buffer: Dict[int, Dict] = {}
        self.MESSAGE_LINK_TIMEOUT = 30  # seconds - time to wait for pair
        self.MESSAGE_WAIT_BEFORE_PROCESS = 5  # seconds - wait before processing single

        # Store last reminder info for callback handling
        # {user_id: {'row': int, ...}}
        self.last_reminders: Dict[int, Dict] = {}

        # Store pending time inputs
        # {user_id: {'row': int, 'awaiting_time': bool}}
        self.pending_time_input: Dict[int, Dict] = {}

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        welcome = """
Hello! I'm a smart reminder bot.

You can:
- Send me text messages with reminders
- Send voice messages
- Forward messages to create reminders from them

Examples:
- "Remind me tomorrow at 15:00 about the meeting"
- "Call mom in 2 hours"
- "On the 10th" (means 10th of current month)
- "On Sunday" (nearest Sunday)
- "30 hours before 18:00 on October 29th"

Use /help for more info.
        """
        await update.message.reply_text(welcome)

    async def handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        help_text = """
<b>How to use:</b>

<b>Text messages:</b>
Just write what you need to be reminded about with time/date.

<b>Voice messages:</b>
Send a voice message - I'll transcribe it and create a reminder.

<b>Forwarded messages:</b>
- Forward a message alone - I'll create a reminder from its content
- Send explanation first, then forward - reminder uses your explanation

<b>Time formats:</b>
- "in 2 hours", "in 30 minutes"
- "tomorrow at 15:00"
- "on the 10th" (current month)
- "on Sunday" (nearest)
- "30 hours before 18:00 on October 29th"

<b>After reminder fires:</b>
You'll get buttons to mark it as done or not done.

<b>Reminders without time:</b>
Will be stored and reviewed weekly.
        """
        await update.message.reply_text(help_text, parse_mode='HTML')

    def _is_forwarded(self, message) -> bool:
        """Check if message is forwarded (compatible with different library versions)."""
        # Try forward_origin first (newer versions)
        if hasattr(message, 'forward_origin') and message.forward_origin:
            return True
        # Fallback to forward_date (older versions)
        if hasattr(message, 'forward_date') and message.forward_date:
            return True
        return False

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Unified handler for all text messages (regular and forwarded).

        Logic:
        1. Save message to buffer
        2. Wait for potential pair (explanation + forwarded)
        3. Process as pair or single message
        """
        user_id = update.effective_user.id
        message = update.message
        is_forwarded = self._is_forwarded(message)
        message_text = message.text or message.caption or ""

        # Check if user is inputting time for a timeless reminder
        if user_id in self.pending_time_input and self.pending_time_input[user_id].get('awaiting_time'):
            await self._handle_time_input(update, context, message_text)
            return

        logger.info(f"Message from {user_id}: forwarded={is_forwarded}, text={message_text[:50]}...")

        # Clean old messages from buffer
        self._cleanup_buffer()

        current_time = time.time()

        # Check if there's a recent message in buffer (potential pair)
        if user_id in self.message_buffer:
            existing = self.message_buffer[user_id]
            time_diff = current_time - existing['timestamp']

            if time_diff < self.MESSAGE_LINK_TIMEOUT:
                # Check if this is a pair (one forwarded, one not)
                if existing['is_forwarded'] != is_forwarded and not existing.get('processed'):
                    # This is a pair!
                    # Mark existing as processed so it won't be processed again
                    self.message_buffer[user_id]['processed'] = True

                    if is_forwarded:
                        # Current is forwarded, existing is explanation
                        await self._process_pair(
                            explanation=existing['message'],
                            forwarded_text=message_text,
                            forwarded_author=self._get_forward_author(message),
                            update=existing['update'],
                            context=context
                        )
                    else:
                        # Current is explanation, existing is forwarded
                        await self._process_pair(
                            explanation=message_text,
                            forwarded_text=existing['message'],
                            forwarded_author=existing.get('forward_author', ''),
                            update=update,
                            context=context
                        )
                    # Clear buffer
                    del self.message_buffer[user_id]
                    return

        # Save to buffer and wait for potential pair
        self.message_buffer[user_id] = {
            'message': message_text,
            'is_forwarded': is_forwarded,
            'timestamp': current_time,
            'update': update,
            'forward_author': self._get_forward_author(message) if is_forwarded else '',
            'processed': False
        }

        # Wait a short time for potential second message
        await asyncio.sleep(self.MESSAGE_WAIT_BEFORE_PROCESS)

        # Check if our message was consumed by pair processing
        if user_id not in self.message_buffer:
            return

        # Check if marked as processed (by pair handler)
        if self.message_buffer[user_id].get('processed'):
            del self.message_buffer[user_id]
            return

        # Check if a newer message arrived (means we're the old one in a pair)
        if self.message_buffer[user_id]['timestamp'] > current_time:
            return

        # Process as single message
        data = self.message_buffer.pop(user_id)

        if data['is_forwarded']:
            await self._process_single_forwarded(
                forwarded_text=data['message'],
                forwarded_author=data['forward_author'],
                update=data['update'],
                context=context
            )
        else:
            await self._process_single_message(
                text=data['message'],
                update=data['update'],
                context=context
            )

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle voice messages."""
        user_id = update.effective_user.id
        voice = update.message.voice

        logger.info(f"Voice message from {user_id}, duration: {voice.duration}s")

        processing_msg = await update.message.reply_text("Transcribing voice message...")

        try:
            # Get file URL
            file_info = await context.bot.get_file(voice.file_id)
            file_url = file_info.file_path

            # Transcribe
            text = await self.openai.download_and_transcribe(file_url)

            if not text:
                await processing_msg.edit_text(
                    "Could not transcribe voice message. Please try again or send text."
                )
                return

            # Update status
            await processing_msg.edit_text(
                f"<b>Transcribed:</b>\n<i>{text}</i>\n\nProcessing reminder...",
                parse_mode='HTML'
            )

            # Process as regular message
            reminder_info, error = self.openai.extract_and_validate(text)

            if not reminder_info:
                await processing_msg.edit_text(
                    f"<b>Transcribed:</b>\n<i>{text}</i>\n\n"
                    f"Could not create reminder: {error}",
                    parse_mode='HTML'
                )
                return

            # Save to sheets
            row = self.sheets.add_reminder(
                text=reminder_info['text'],
                datetime_str=reminder_info.get('datetime'),
                timezone=reminder_info.get('timezone', self.timezone)
            )

            if row:
                self.last_reminders[user_id] = {'row': row, **reminder_info}
                await processing_msg.edit_text(
                    self._format_success_message(reminder_info, text),
                    parse_mode='HTML'
                )
            else:
                await processing_msg.edit_text("Error saving reminder. Please try again.")

        except Exception as e:
            logger.error(f"Error processing voice: {e}")
            await processing_msg.edit_text("Error processing voice message.")

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button callbacks."""
        query = update.callback_query
        user_id = update.effective_user.id
        data = query.data

        await query.answer()

        logger.info(f"Callback from {user_id}: {data}")

        try:
            if data.startswith("done_"):
                row = int(data.split("_")[1])
                self.sheets.update_status(row, "done")
                await query.edit_message_text(
                    query.message.text + "\n\n<b>Status: Done</b>",
                    parse_mode='HTML'
                )

            elif data.startswith("notdone_"):
                row = int(data.split("_")[1])
                self.sheets.update_status(row, "not_done")
                await query.edit_message_text(
                    query.message.text + "\n\n<b>Status: Not done</b>",
                    parse_mode='HTML'
                )

            elif data.startswith("relevant_"):
                row = int(data.split("_")[1])
                await query.edit_message_text(
                    query.message.text + "\n\n<i>Marked as still relevant</i>",
                    parse_mode='HTML'
                )

            elif data.startswith("cancel_"):
                row = int(data.split("_")[1])
                self.sheets.update_status(row, "canceled")
                await query.edit_message_text(
                    query.message.text + "\n\n<b>Canceled</b>",
                    parse_mode='HTML'
                )

            elif data.startswith("settime_"):
                row = int(data.split("_")[1])
                self.pending_time_input[user_id] = {'row': row, 'awaiting_time': True}
                await query.edit_message_text(
                    query.message.text + "\n\n<i>Please send the deadline (e.g., 'tomorrow at 15:00')</i>",
                    parse_mode='HTML'
                )

            elif data == "confirm_ok":
                await query.edit_message_reply_markup(None)

        except Exception as e:
            logger.error(f"Error handling callback: {e}")
            await query.edit_message_text("Error processing action.")

    async def _handle_time_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
        """Handle time input for timeless reminder conversion."""
        user_id = update.effective_user.id
        pending = self.pending_time_input.pop(user_id, None)

        if not pending:
            return

        row = pending['row']

        # Extract datetime from user input
        reminder_info, error = self.openai.extract_and_validate(f"Remind me {text}")

        if not reminder_info or not reminder_info.get('datetime'):
            await update.message.reply_text(
                f"Could not parse time from: {text}\nPlease try again with a clearer format."
            )
            self.pending_time_input[user_id] = pending  # Restore pending state
            return

        # Update the reminder in sheets
        success = self.sheets.update_datetime(row, reminder_info['datetime'])

        if success:
            dt = datetime.strptime(reminder_info['datetime'], '%Y-%m-%d %H:%M:%S')
            formatted = dt.strftime('%d.%m.%Y at %H:%M')
            await update.message.reply_text(
                f"Deadline set: <b>{formatted}</b>",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text("Error updating reminder. Please try again.")

    async def _process_single_message(self, text: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Process a single (non-forwarded) message."""
        user_id = update.effective_user.id

        processing_msg = await update.message.reply_text("Processing...")

        reminder_info, error = self.openai.extract_and_validate(text)

        if not reminder_info:
            await processing_msg.edit_text(
                f"Could not create reminder: {error}\n\n"
                "Examples:\n"
                "- 'Remind me tomorrow at 15:00 about meeting'\n"
                "- 'Call mom in 2 hours'"
            )
            return

        row = self.sheets.add_reminder(
            text=reminder_info['text'],
            datetime_str=reminder_info.get('datetime'),
            timezone=reminder_info.get('timezone', self.timezone)
        )

        if row:
            self.last_reminders[user_id] = {'row': row, **reminder_info}
            await processing_msg.edit_text(
                self._format_success_message(reminder_info),
                parse_mode='HTML'
            )
        else:
            await processing_msg.edit_text("Error saving reminder.")

    async def _process_single_forwarded(
        self,
        forwarded_text: str,
        forwarded_author: str,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ):
        """Process a single forwarded message (no explanation)."""
        user_id = update.effective_user.id

        processing_msg = await update.message.reply_text("Processing forwarded message...")

        # Extract info from forwarded content
        reminder_info = self.openai.extract_forwarded_message_info(forwarded_text)

        if not reminder_info:
            await processing_msg.edit_text(
                "Could not create reminder from forwarded message.\n"
                "Try adding an explanation message before forwarding."
            )
            return

        # Validate
        is_valid, error = self.openai.validate_reminder_info(reminder_info)
        if not is_valid and error != "":
            # Allow timeless reminders
            if reminder_info.get('datetime') is not None:
                await processing_msg.edit_text(f"Error: {error}")
                return

        row = self.sheets.add_reminder(
            text=reminder_info['text'],
            datetime_str=reminder_info.get('datetime'),
            timezone=reminder_info.get('timezone', self.timezone),
            comment=forwarded_text,
            forward_author=forwarded_author
        )

        if row:
            self.last_reminders[user_id] = {'row': row, **reminder_info}
            await processing_msg.edit_text(
                self._format_success_message(
                    reminder_info,
                    forwarded_text=forwarded_text,
                    forwarded_author=forwarded_author
                ),
                parse_mode='HTML'
            )
        else:
            await processing_msg.edit_text("Error saving reminder.")

    async def _process_pair(
        self,
        explanation: str,
        forwarded_text: str,
        forwarded_author: str,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ):
        """Process a pair of messages: explanation + forwarded."""
        user_id = update.effective_user.id

        processing_msg = await update.message.reply_text("Processing message pair...")

        # Use explanation for reminder info
        reminder_info, error = self.openai.extract_and_validate(explanation)

        if not reminder_info:
            await processing_msg.edit_text(f"Could not create reminder: {error}")
            return

        # Save with forwarded message as comment
        row = self.sheets.add_reminder(
            text=reminder_info['text'],
            datetime_str=reminder_info.get('datetime'),
            timezone=reminder_info.get('timezone', self.timezone),
            comment=forwarded_text,
            forward_author=forwarded_author
        )

        if row:
            self.last_reminders[user_id] = {'row': row, **reminder_info}
            await processing_msg.edit_text(
                self._format_success_message(
                    reminder_info,
                    forwarded_text=forwarded_text,
                    forwarded_author=forwarded_author
                ),
                parse_mode='HTML'
            )
        else:
            await processing_msg.edit_text("Error saving reminder.")

    def _get_forward_author(self, message) -> str:
        """Extract author info from forwarded message."""
        try:
            # Try new API (forward_origin) first
            origin = getattr(message, 'forward_origin', None)
            if origin:
                # User
                sender_user = getattr(origin, 'sender_user', None)
                if sender_user:
                    name = " ".join(filter(None, [sender_user.first_name, sender_user.last_name]))
                    username = f"@{sender_user.username}" if getattr(sender_user, 'username', None) else ""
                    return name or username or str(sender_user.id)

                # Chat
                sender_chat = getattr(origin, 'sender_chat', None)
                if sender_chat and getattr(sender_chat, 'title', None):
                    return f"Chat: {sender_chat.title}"

                # Channel
                channel_chat = getattr(origin, 'chat', None)
                if channel_chat and getattr(channel_chat, 'title', None):
                    return f"Channel: {channel_chat.title}"

                # Hidden user
                hidden_name = getattr(origin, 'sender_user_name', None)
                if hidden_name:
                    return hidden_name

            # Fallback to old API (forward_from, forward_from_chat)
            forward_from = getattr(message, 'forward_from', None)
            if forward_from:
                name = " ".join(filter(None, [forward_from.first_name, forward_from.last_name]))
                username = f"@{forward_from.username}" if getattr(forward_from, 'username', None) else ""
                return name or username or str(forward_from.id)

            forward_from_chat = getattr(message, 'forward_from_chat', None)
            if forward_from_chat:
                return forward_from_chat.title or f"Chat {forward_from_chat.id}"

            forward_sender_name = getattr(message, 'forward_sender_name', None)
            if forward_sender_name:
                return forward_sender_name

        except Exception as e:
            logger.error(f"Error getting forward author: {e}")

        return "Unknown"

    def _format_success_message(
        self,
        reminder_info: Dict,
        transcribed_text: str = None,
        forwarded_text: str = None,
        forwarded_author: str = None
    ) -> str:
        """Format success message for created reminder."""
        parts = ["<b>Reminder created!</b>\n"]

        if transcribed_text:
            parts.append(f"<b>Voice:</b> <i>{transcribed_text}</i>\n")

        parts.append(f"<b>Task:</b> {reminder_info['text']}")

        if reminder_info.get('datetime'):
            dt = datetime.strptime(reminder_info['datetime'], '%Y-%m-%d %H:%M:%S')
            formatted = dt.strftime('%d.%m.%Y at %H:%M')
            parts.append(f"\n<b>Time:</b> {formatted}")
            parts.append(f"\n<b>Timezone:</b> {reminder_info.get('timezone', self.timezone)}")
        else:
            parts.append("\n<i>No time set - will be reviewed weekly</i>")

        if forwarded_text:
            preview = forwarded_text[:100] + "..." if len(forwarded_text) > 100 else forwarded_text
            parts.append(f"\n\n<b>Original message:</b> {preview}")
            if forwarded_author:
                parts.append(f"\n<b>From:</b> {forwarded_author}")

        return "".join(parts)

    def _cleanup_buffer(self):
        """Remove expired messages from buffer."""
        current = time.time()
        expired = [
            uid for uid, data in self.message_buffer.items()
            if current - data['timestamp'] > self.MESSAGE_LINK_TIMEOUT * 2
        ]
        for uid in expired:
            del self.message_buffer[uid]
