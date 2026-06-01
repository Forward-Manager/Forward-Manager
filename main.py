"""
XVIP Hybrid Telegram Bot
========================
Userbot (Pyrogram) + Bot API (python-telegram-bot) running concurrently.

Flow:
  Source Channel → Userbot detects post → forwards to Converter Bot DM
  → Converter Bot replies → Userbot catches reply → sends to Destination
"""

import asyncio
import logging
import os
import time
from typing import Optional

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, PeerIdInvalid, RPCError
from pyrogram.types import Message

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger("xvip-bot")

# ─────────────────────────────────────────────
# ENVIRONMENT VARIABLES
# ─────────────────────────────────────────────
def _require(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise EnvironmentError(f"Missing required environment variable: {key}")
    return val

def _parse_int_list(raw: str) -> list[int]:
    """Parse comma-separated numeric channel IDs into list of ints."""
    result = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                result.append(int(part))
            except ValueError:
                logger.warning(f"Skipping invalid channel ID: {part!r}")
    return result

def _parse_id_set(raw: str) -> set[int]:
    return set(_parse_int_list(raw))

# ── Required ──
API_ID          = int(_require("API_ID"))
API_HASH        = _require("API_HASH")
BOT_TOKEN       = _require("BOT_TOKEN")
STRING_SESSION  = _require("STRING_SESSION")

ADMIN_IDS: set[int] = _parse_id_set(_require("ADMIN_IDS"))

TERA_SOURCE_IDS: list[int] = _parse_int_list(_require("TERA_SOURCE_CHANNELS"))
DISK_SOURCE_IDS: list[int] = _parse_int_list(_require("DISK_SOURCE_CHANNELS"))

TERA_CONVERTER_BOT: str = _require("TERA_CONVERTER_BOT").lstrip("@")
DISK_CONVERTER_BOT: str = _require("DISK_CONVERTER_BOT").lstrip("@")

TERA_DESTINATION: str = _require("TERA_DESTINATION")
DISK_DESTINATION: str = _require("DISK_DESTINATION")

# ── Derived sets for fast lookup ──
TERA_SOURCE_SET: set[int] = set(TERA_SOURCE_IDS)
DISK_SOURCE_SET: set[int] = set(DISK_SOURCE_IDS)

# ── Global state ──
userbot_connected: bool = False
START_TIME: float = time.time()

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def _has_media(msg: Message) -> bool:
    """Return True if message contains photo or video."""
    return bool(msg.photo or msg.video)

def _contains_tera(text: Optional[str]) -> bool:
    if not text:
        return False
    t = text.lower()
    return "tera" in t or "terabox" in t

def _contains_disk(text: Optional[str]) -> bool:
    if not text:
        return False
    t = text.lower()
    return "disk" in t or "1drv" in t or "onedrive" in t

async def _send_with_retry(
    userbot: Client,
    chat: str | int,
    *,
    forward_msg: Optional[Message] = None,
    text: Optional[str] = None,
    retries: int = 3,
) -> Optional[Message]:
    """Send or forward a message with FloodWait retry logic."""
    for attempt in range(retries):
        try:
            if forward_msg:
                return await userbot.forward_messages(
                    chat_id=chat,
                    from_chat_id=forward_msg.chat.id,
                    message_ids=forward_msg.id,
                )
            elif text:
                return await userbot.send_message(chat_id=chat, text=text)
        except FloodWait as e:
            wait = e.value + 2
            logger.warning(f"FloodWait: sleeping {wait}s (attempt {attempt+1}/{retries})")
            await asyncio.sleep(wait)
        except PeerIdInvalid:
            logger.error(f"PeerIdInvalid for chat={chat!r}. Check IDs/usernames.")
            return None
        except RPCError as e:
            logger.error(f"RPC error on attempt {attempt+1}: {e}")
            await asyncio.sleep(2 ** attempt)
    logger.error(f"Failed to send to {chat!r} after {retries} attempts.")
    return None

# ─────────────────────────────────────────────
# DESTINATION RESOLVER
# ─────────────────────────────────────────────
async def _resolve_destination(userbot: Client, dest: str) -> int | str:
    """
    Accept numeric string, -100xxx integer string, or @username / bot username.
    Returns int if numeric, else str username.
    """
    dest = dest.strip()
    try:
        return int(dest)
    except ValueError:
        # username with or without @
        return dest.lstrip("@")

# ─────────────────────────────────────────────
# PYROGRAM USERBOT
# ─────────────────────────────────────────────
def build_userbot() -> Client:
    return Client(
        name="xvip_userbot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=STRING_SESSION,
        in_memory=True,
    )

def register_userbot_handlers(userbot: Client) -> None:

    all_source_ids = TERA_SOURCE_IDS + DISK_SOURCE_IDS

    # ── STEP A + B: Monitor source channels ──
    @userbot.on_message(filters.chat(all_source_ids) & filters.incoming)
    async def on_source_post(client: Client, msg: Message) -> None:
        chat_id = msg.chat.id
        caption = msg.caption or msg.text or ""

        logger.info(f"[SOURCE] chat={chat_id} | has_media={_has_media(msg)} | caption_len={len(caption)}")

        # STEP B1: media check
        if not _has_media(msg):
            logger.debug(f"[DROP] No media in post from {chat_id}")
            return

        # STEP B2: keyword + source alignment
        if chat_id in TERA_SOURCE_SET:
            if not _contains_tera(caption):
                logger.debug(f"[DROP] Tera source but no tera keyword: {chat_id}")
                return
            logger.info(f"[FORWARD→TERA_BOT] Forwarding msg {msg.id} to @{TERA_CONVERTER_BOT}")
            await _send_with_retry(client, TERA_CONVERTER_BOT, forward_msg=msg)

        elif chat_id in DISK_SOURCE_SET:
            if not _contains_disk(caption):
                logger.debug(f"[DROP] Disk source but no disk keyword: {chat_id}")
                return
            logger.info(f"[FORWARD→DISK_BOT] Forwarding msg {msg.id} to @{DISK_CONVERTER_BOT}")
            await _send_with_retry(client, DISK_CONVERTER_BOT, forward_msg=msg)

        else:
            logger.warning(f"[UNEXPECTED] chat_id {chat_id} not in any source set")

    # ── STEP C + D: Intercept converter bot replies ──
    @userbot.on_message(
        filters.private
        & filters.incoming
        & filters.reply
    )
    async def on_converter_reply(client: Client, msg: Message) -> None:
        sender = msg.chat.username or ""
        sender_clean = sender.lstrip("@").lower()

        is_tera = sender_clean == TERA_CONVERTER_BOT.lower()
        is_disk = sender_clean == DISK_CONVERTER_BOT.lower()

        if not (is_tera or is_disk):
            return  # not from our converter bots

        logger.info(f"[CONVERTER_REPLY] From={sender} is_tera={is_tera} is_disk={is_disk}")

        dest_raw = TERA_DESTINATION if is_tera else DISK_DESTINATION
        dest = await _resolve_destination(client, dest_raw)

        label = "TERA" if is_tera else "DISK"
        logger.info(f"[DISPATCH→{label}] Sending converted post to {dest!r}")

        await _send_with_retry(client, dest, forward_msg=msg)


# ─────────────────────────────────────────────
# PTB BOT (ADMIN INTERFACE)
# ─────────────────────────────────────────────
def admin_only(func):
    """Decorator: silently ignore non-admins."""
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id if update.effective_user else None
        if uid not in ADMIN_IDS:
            logger.debug(f"[ADMIN_GUARD] Blocked user {uid}")
            return
        return await func(update, ctx)
    wrapper.__name__ = func.__name__
    return wrapper


@admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uptime_s = int(time.time() - START_TIME)
    h, rem = divmod(uptime_s, 3600)
    m, s = divmod(rem, 60)
    text = (
        "🤖 <b>XVIP Hybrid Bot</b> — Active\n\n"
        f"⏱ Uptime: <code>{h:02d}:{m:02d}:{s:02d}</code>\n\n"
        "Commands:\n"
        "  /status — Live config breakdown\n"
        "  /test   — Ping userbot session\n"
        "  /menu   — This menu"
    )
    await update.message.reply_html(text)


@admin_only
async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


@admin_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ub_status = "✅ Connected" if userbot_connected else "❌ Disconnected"

    tera_ids_fmt = "\n".join(f"  • <code>{i}</code>" for i in TERA_SOURCE_IDS) or "  (none)"
    disk_ids_fmt = "\n".join(f"  • <code>{i}</code>" for i in DISK_SOURCE_IDS) or "  (none)"

    text = (
        f"📊 <b>Live Status</b>\n\n"
        f"<b>Userbot:</b> {ub_status}\n\n"
        f"━━━━━━ TERA ━━━━━━\n"
        f"<b>Source Channels:</b>\n{tera_ids_fmt}\n"
        f"<b>Converter Bot:</b> @{TERA_CONVERTER_BOT}\n"
        f"<b>Destination:</b> <code>{TERA_DESTINATION}</code>\n\n"
        f"━━━━━━ DISK ━━━━━━\n"
        f"<b>Source Channels:</b>\n{disk_ids_fmt}\n"
        f"<b>Converter Bot:</b> @{DISK_CONVERTER_BOT}\n"
        f"<b>Destination:</b> <code>{DISK_DESTINATION}</code>"
    )
    await update.message.reply_html(text)


@admin_only
async def cmd_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🏓 Pinging userbot…")
    try:
        app: Client = ctx.bot_data["userbot"]
        me = await app.get_me()
        await update.message.reply_html(
            f"✅ <b>Userbot OK</b>\n"
            f"Logged in as: <code>{me.first_name}</code> (<code>{me.id}</code>)"
        )
    except Exception as e:
        await update.message.reply_html(f"❌ <b>Userbot error:</b> <code>{e}</code>")


def build_ptb_app(userbot: Client) -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.bot_data["userbot"] = userbot

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu",  cmd_menu))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("test",  cmd_test))

    return app


# ─────────────────────────────────────────────
# ENTRYPOINT
# ─────────────────────────────────────────────
async def main() -> None:
    global userbot_connected

    userbot = build_userbot()
    register_userbot_handlers(userbot)

    ptb_app = build_ptb_app(userbot)

    logger.info("Starting userbot…")
    await userbot.start()
    userbot_connected = True
    me = await userbot.get_me()
    logger.info(f"Userbot logged in as: {me.first_name} ({me.id})")

    logger.info("Starting PTB bot…")
    await ptb_app.initialize()
    await ptb_app.start()
    await ptb_app.updater.start_polling(drop_pending_updates=True)

    logger.info(
        f"✅ Bot running | "
        f"Tera sources: {TERA_SOURCE_IDS} | "
        f"Disk sources: {DISK_SOURCE_IDS}"
    )

    # Run until Ctrl-C / SIGTERM
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        logger.info("Shutting down…")
        await ptb_app.updater.stop()
        await ptb_app.stop()
        await ptb_app.shutdown()
        await userbot.stop()
        userbot_connected = False
        logger.info("Stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
