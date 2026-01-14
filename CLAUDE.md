# Smart Reminder Bot

## Deployment

- **Server**: 80.90.183.91
- **User**: root
- **Path**: /opt/telegram_bots/smart-reminder-bot
- **Service**: smart-reminder-bot.service

### SSH Connection
```bash
ssh root@80.90.183.91
```

### Useful Commands
```bash
# Check status
systemctl status smart-reminder-bot.service

# View logs
journalctl -u smart-reminder-bot.service -f

# Restart
systemctl restart smart-reminder-bot.service

# Deploy updates
cd /opt/telegram_bots/smart-reminder-bot && git pull origin main && systemctl restart smart-reminder-bot.service
```

## Tech Stack

- Python 3.10
- python-telegram-bot 20.7
- OpenAI API (Whisper for voice, GPT-4o-mini for text parsing)
- Google Sheets (storage)
- pydub + ffmpeg (audio conversion)
