# XVIP Hybrid Telegram Bot

Userbot (Pyrogram) monitors source channels → forwards to converter bots → routes replies to destinations.
Admin interface runs on Bot API token.

---

## Environment Variables (Railway Config)

| Variable | Example | Description |
|---|---|---|
| `API_ID` | `12345678` | From my.telegram.org |
| `API_HASH` | `abcdef1234...` | From my.telegram.org |
| `BOT_TOKEN` | `110201543:AAHdqT...` | From @BotFather |
| `STRING_SESSION` | `BQA...long string...` | Pyrogram string session |
| `ADMIN_IDS` | `1234567,9876543` | Comma-separated user IDs |
| `TERA_SOURCE_CHANNELS` | `-100123456789,-100987654321` | Numeric channel IDs to monitor for tera links |
| `DISK_SOURCE_CHANNELS` | `-100555444333,-100666777888` | Numeric channel IDs to monitor for disk links |
| `TERA_CONVERTER_BOT` | `@TeraBox_Converter_Bot` | 3rd-party tera link converter bot username |
| `DISK_CONVERTER_BOT` | `@Disk_Converter_Bot` | 3rd-party disk link converter bot username |
| `TERA_DESTINATION` | `-100112233445` or `@mychannel` | Where to send converted tera posts |
| `DISK_DESTINATION` | `-100998877665` or `@mychannel` | Where to send converted disk posts |

---

## Generating STRING_SESSION

Run once locally (not on Railway):

```python
from pyrogram import Client

async def main():
    async with Client("my_account", api_id=API_ID, api_hash=API_HASH) as app:
        print(await app.export_session_string())

import asyncio
asyncio.run(main())
```

Copy the printed string → set as `STRING_SESSION` env var.

---

## Deploy on Railway

1. Push this repo to GitHub.
2. New Railway project → Deploy from GitHub repo.
3. Add all env vars in Railway dashboard → Variables tab.
4. Railway auto-detects `Dockerfile` or `Procfile`.
5. Deploy. Check logs to confirm userbot connection.

---

## Admin Commands

| Command | Description |
|---|---|
| `/start` or `/menu` | Welcome + uptime |
| `/status` | Live config: source IDs, converter bots, destinations |
| `/test` | Ping userbot session |

---

## Post Flow

```
Source Channel (tera/disk)
    │  new post with media + tera/disk link
    ▼
Userbot detects → keyword + source alignment check
    │
    ├─ TERA → forwards to @TERA_CONVERTER_BOT DM
    └─ DISK → forwards to @DISK_CONVERTER_BOT DM
                    │
                    │  converter bot replies (reply msg)
                    ▼
             Userbot intercepts reply
                    │
                    ├─ from TERA bot → sends to TERA_DESTINATION
                    └─ from DISK bot → sends to DISK_DESTINATION
```
