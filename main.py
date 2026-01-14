import os
import logging
import asyncio
from datetime import datetime

import pytz
from telegram import Bot
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from bot.handlers import BotHandlers
from bot.keyboards import Keyboards
from services.openai_service import OpenAIService
from services.google_sheets import GoogleSheetsService

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global references
bot_instance = None
sheets_service = None
handlers = None


async def check_and_send_reminders():
    """Check for due reminders and send them."""
    global bot_instance, sheets_service

    if not bot_instance or not sheets_service:
        return

    try:
        reminders = sheets_service.get_pending_reminders()
        current_time = datetime.now(pytz.UTC)

        for reminder in reminders:
            try:
                if not reminder.get('datetime'):
                    continue

                # Parse reminder time
                tz_str = reminder.get('timezone', config.DEFAULT_TIMEZONE)
                try:
                    tz = pytz.timezone(tz_str)
                except:
                    tz = pytz.timezone(config.DEFAULT_TIMEZONE)

                dt = datetime.strptime(reminder['datetime'], '%Y-%m-%d %H:%M:%S')
                reminder_time = tz.localize(dt)
                reminder_time_utc = reminder_time.astimezone(pytz.UTC)

                # Check if it's time
                if current_time >= reminder_time_utc:
                    await send_reminder_notification(reminder)
                    sheets_service.mark_as_sent(reminder['row'])

            except Exception as e:
                logger.error(f"Error processing reminder {reminder.get('row')}: {e}")

    except Exception as e:
        logger.error(f"Error in check_and_send_reminders: {e}")


async def send_reminder_notification(reminder: dict):
    """Send reminder notification to user."""
    global bot_instance

    try:
        text_parts = [f"<b>Reminder:</b>\n\n{reminder['text']}"]

        # Add original message if present
        if reminder.get('comment'):
            text_parts.append(f"\n\n<b>Original message:</b>\n{reminder['comment']}")
            if reminder.get('forward_author'):
                text_parts.append(f"\n<b>From:</b> {reminder['forward_author']}")

        message_text = "".join(text_parts)
        keyboard = Keyboards.reminder_actions(reminder['row'])

        await bot_instance.send_message(
            chat_id=config.TELEGRAM_CHAT_ID,
            text=message_text,
            parse_mode='HTML',
            reply_markup=keyboard
        )

        logger.info(f"Sent reminder: {reminder['text'][:50]}...")

    except Exception as e:
        logger.error(f"Error sending reminder: {e}")


async def check_timeless_reminders():
    """Weekly check for reminders without datetime."""
    global bot_instance, sheets_service

    if not bot_instance or not sheets_service:
        return

    try:
        reminders = sheets_service.get_timeless_reminders()

        for reminder in reminders:
            try:
                text_parts = [
                    f"<b>Weekly review:</b>\n\n"
                    f"<b>Task:</b> {reminder['text']}\n\n"
                    f"Is this still relevant?"
                ]

                if reminder.get('comment'):
                    text_parts.append(f"\n\n<b>Original:</b> {reminder['comment'][:100]}...")

                message_text = "".join(text_parts)
                keyboard = Keyboards.timeless_reminder_actions(reminder['row'])

                await bot_instance.send_message(
                    chat_id=config.TELEGRAM_CHAT_ID,
                    text=message_text,
                    parse_mode='HTML',
                    reply_markup=keyboard
                )

                # Small delay between messages
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error sending timeless reminder review: {e}")

    except Exception as e:
        logger.error(f"Error in check_timeless_reminders: {e}")


async def main():
    """Main entry point."""
    global bot_instance, sheets_service, handlers

    # Validate config
    missing = []
    if not config.TELEGRAM_TOKEN:
        missing.append('TELEGRAM_TOKEN')
    if not config.OPENAI_API_KEY:
        missing.append('OPENAI_API_KEY')

    if missing:
        logger.error(f"Missing environment variables: {', '.join(missing)}")
        return

    # Initialize services
    try:
        openai_service = OpenAIService(config.OPENAI_API_KEY, config.DEFAULT_TIMEZONE)
        sheets_service = GoogleSheetsService(
            config.GOOGLE_SHEETS_CREDS,
            config.GOOGLE_SHEETS_SPREADSHEET,
            config.GOOGLE_SHEETS_WORKSHEET
        )
    except Exception as e:
        logger.error(f"Failed to initialize services: {e}")
        return

    # Initialize handlers
    handlers = BotHandlers(openai_service, sheets_service, config.DEFAULT_TIMEZONE)

    # Build application
    application = Application.builder().token(config.TELEGRAM_TOKEN).build()
    bot_instance = application.bot

    # Register handlers
    application.add_handler(CommandHandler("start", handlers.handle_start))
    application.add_handler(CommandHandler("help", handlers.handle_help))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_message))
    application.add_handler(MessageHandler(filters.VOICE, handlers.handle_voice))
    application.add_handler(CallbackQueryHandler(handlers.handle_callback))

    # Setup scheduler
    scheduler = AsyncIOScheduler()

    # Check reminders every minute
    scheduler.add_job(
        check_and_send_reminders,
        CronTrigger(minute='*'),
        id='check_reminders',
        replace_existing=True
    )

    # Weekly check for timeless reminders (Sunday at 10:00)
    scheduler.add_job(
        check_timeless_reminders,
        CronTrigger(
            day_of_week=config.WEEKLY_CHECK_DAY,
            hour=config.WEEKLY_CHECK_HOUR
        ),
        id='weekly_check',
        replace_existing=True
    )

    scheduler.start()
    logger.info("Scheduler started")

    # Run bot
    logger.info("Starting Telegram bot...")

    try:
        await application.initialize()
        await application.start()
        await application.updater.start_polling()

        # Keep running
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Error in main: {e}")
    finally:
        scheduler.shutdown()
        await application.stop()
        logger.info("Shutdown complete")


if __name__ == '__main__':
    asyncio.run(main())
