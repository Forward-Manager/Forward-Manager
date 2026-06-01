"""
XVIP Hybrid Bot — Self-Setup Mode
===================================
Pehli baar: Bot khud phone/OTP/2FA maangega aur STRING_SESSION save karega.
Uske baad: Normal userbot+admin mode mein kaam karega.

ENV VARS (Railway):
  API_ID, API_HASH, BOT_TOKEN, ADMIN_IDS
  TERA_SOURCE_CHANNELS, DISK_SOURCE_CHANNELS
  TERA_CONVERTER_BOT, DISK_CONVERTER_BOT
  TERA_DESTINATION, DISK_DESTINATION
  STRING_SESSION  ← optional, agar pehle se hai to setup skip hoga
"""

import asyncio
import logging
import os
import time
from typing import Optional

from pyrogram import Client, filters as pyro_filters
from pyrogram.errors import (
    FloodWait, PeerIdInvalid, RPCError,
    PhoneNumberInvalid, PhoneCodeInvalid,
    PhoneCodeExpired, SessionPasswordNeeded,
)
from pyrogram.types import Message as PyroMessage

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters,
)

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger("xvip")

# ─────────────────────────────────────────────
# ENV HELPERS
# ─────────────────────────────────────────────
def _require(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise EnvironmentError(f"Missing env var: {key}")
    return val

def _optional(key: str) -> str:
    return os.environ.get(key, "").strip()

def _parse_int_list(raw: str) -> list[int]:
    result = []
    for p in raw.split(","):
        p = p.strip()
        if p:
            try:
                result.append(int(p))
            except ValueError:
                logger.warning(f"Invalid channel ID skipped: {p!r}")
    return result

# ─────────────────────────────────────────────
# LOAD ENV
# ─────────────────────────────────────────────
API_ID   = int(_require("API_ID"))
API_HASH = _require("API_HASH")
BOT_TOKEN = _require("BOT_TOKEN")
ADMIN_IDS: set[int] = set(_parse_int_list(_require("ADMIN_IDS")))

TERA_SOURCE_IDS: list[int] = _parse_int_list(_optional("TERA_SOURCE_CHANNELS"))
DISK_SOURCE_IDS: list[int] = _parse_int_list(_optional("DISK_SOURCE_CHANNELS"))
TERA_SOURCE_SET  = set(TERA_SOURCE_IDS)
DISK_SOURCE_SET  = set(DISK_SOURCE_IDS)

TERA_CONVERTER_BOT = _optional("TERA_CONVERTER_BOT").lstrip("@")
DISK_CONVERTER_BOT = _optional("DISK_CONVERTER_BOT").lstrip("@")
TERA_DESTINATION   = _optional("TERA_DESTINATION")
DISK_DESTINATION   = _optional("DISK_DESTINATION")

# STRING_SESSION — may or may not exist at startup
STORED_SESSION = _optional("STRING_SESSION")

# ─────────────────────────────────────────────
# GLOBAL STATE
# ─────────────────────────────────────────────
userbot: Optional[Client] = None
userbot_connected: bool = False
START_TIME = time.time()

# Setup conversation temp storage
setup_state: dict = {}   # keyed by admin user_id

# Conversation states
SETUP_PHONE, SETUP_OTP, SETUP_2FA = range(3)

# ─────────────────────────────────────────────
# USERBOT HELPERS
# ─────────────────────────────────────────────
def _has_media(msg: PyroMessage) -> bool:
    return bool(msg.photo or msg.video)

def _contains_tera(text: Optional[str]) -> bool:
    if not text: return False
    t = text.lower()
    return "tera" in t or "terabox" in t

def _contains_disk(text: Optional[str]) -> bool:
    if not text: return False
    t = text.lower()
    return "disk" in t or "1drv" in t or "onedrive" in t

async def _send_retry(client: Client, chat, *, fwd_msg=None, text=None, retries=3):
    for attempt in range(retries):
        try:
            if fwd_msg:
                return await client.forward_messages(
                    chat_id=chat,
                    from_chat_id=fwd_msg.chat.id,
                    message_ids=fwd_msg.id,
                )
            elif text:
                return await client.send_message(chat_id=chat, text=text)
        except FloodWait as e:
            await asyncio.sleep(e.value + 2)
        except PeerIdInvalid:
            logger.error(f"PeerIdInvalid: {chat!r}")
            return None
        except RPCError as e:
            logger.error(f"RPC error: {e}")
            await asyncio.sleep(2 ** attempt)
    return None

def _resolve_dest(dest: str):
    dest = dest.strip()
    try:
        return int(dest)
    except ValueError:
        return dest.lstrip("@")

# ─────────────────────────────────────────────
# REGISTER USERBOT HANDLERS
# ─────────────────────────────────────────────
def register_userbot_handlers(ub: Client) -> None:
    all_sources = TERA_SOURCE_IDS + DISK_SOURCE_IDS
    if not all_sources:
        logger.warning("No source channels configured!")
        return

    @ub.on_message(pyro_filters.chat(all_sources) & pyro_filters.incoming)
    async def on_source_post(client: Client, msg: PyroMessage):
        chat_id = msg.chat.id
        caption = msg.caption or msg.text or ""

        if not _has_media(msg):
            return

        if chat_id in TERA_SOURCE_SET:
            if not _contains_tera(caption):
                return
            if TERA_CONVERTER_BOT:
                logger.info(f"[TERA] Forwarding to @{TERA_CONVERTER_BOT}")
                await _send_retry(client, TERA_CONVERTER_BOT, fwd_msg=msg)

        elif chat_id in DISK_SOURCE_SET:
            if not _contains_disk(caption):
                return
            if DISK_CONVERTER_BOT:
                logger.info(f"[DISK] Forwarding to @{DISK_CONVERTER_BOT}")
                await _send_retry(client, DISK_CONVERTER_BOT, fwd_msg=msg)

    @ub.on_message(pyro_filters.private & pyro_filters.incoming & pyro_filters.reply)
    async def on_converter_reply(client: Client, msg: PyroMessage):
        sender = (msg.chat.username or "").lstrip("@").lower()
        is_tera = sender == TERA_CONVERTER_BOT.lower() if TERA_CONVERTER_BOT else False
        is_disk = sender == DISK_CONVERTER_BOT.lower() if DISK_CONVERTER_BOT else False

        if not (is_tera or is_disk):
            return

        dest_raw = TERA_DESTINATION if is_tera else DISK_DESTINATION
        if not dest_raw:
            return

        dest = _resolve_dest(dest_raw)
        label = "TERA" if is_tera else "DISK"
        logger.info(f"[{label}] Converted reply → {dest!r}")
        await _send_retry(client, dest, fwd_msg=msg)


# ─────────────────────────────────────────────
# START USERBOT
# ─────────────────────────────────────────────
async def start_userbot(session_string: str) -> bool:
    global userbot, userbot_connected
    try:
        ub = Client(
            name="xvip_userbot",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_string,
            in_memory=True,
        )
        register_userbot_handlers(ub)
        await ub.start()
        me = await ub.get_me()
        userbot = ub
        userbot_connected = True
        logger.info(f"✅ Userbot started: {me.first_name} ({me.id})")
        return True
    except Exception as e:
        logger.error(f"Userbot start failed: {e}")
        userbot_connected = False
        return False


# ─────────────────────────────────────────────
# SETUP CONVERSATION — Phone/OTP/2FA
# ─────────────────────────────────────────────
async def setup_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return ConversationHandler.END

    if userbot_connected:
        await update.message.reply_text("✅ Userbot already connected hai! /status dekho.")
        return ConversationHandler.END

    await update.message.reply_text(
        "📱 *Userbot Setup*\n\n"
        "Apna Telegram phone number bhejo:\n"
        "Format: `+919876543210`",
        parse_mode="Markdown",
    )
    return SETUP_PHONE


async def setup_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    phone = update.message.text.strip()

    await update.message.reply_text(f"⏳ `{phone}` pe OTP bhej raha hoon...", parse_mode="Markdown")

    try:
        client = Client(f"setup_{uid}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        await client.connect()
        sent = await client.send_code(phone)

        setup_state[uid] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": sent.phone_code_hash,
        }

        await update.message.reply_text(
            "✅ OTP bheja gaya!\n\nTelegram se aaya *5-digit code* bhejo:",
            parse_mode="Markdown",
        )
        return SETUP_OTP

    except PhoneNumberInvalid:
        await update.message.reply_text("❌ Invalid number. /setup se dobara karo.")
        return ConversationHandler.END
    except FloodWait as e:
        await update.message.reply_text(f"⏳ FloodWait: {e.value}s baad try karo.")
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ Error: `{e}`\n\n/setup se dobara karo.", parse_mode="Markdown")
        return ConversationHandler.END


async def setup_otp(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    otp = update.message.text.strip().replace(" ", "")
    state = setup_state.get(uid)

    if not state:
        await update.message.reply_text("❌ Session expired. /setup se dobara karo.")
        return ConversationHandler.END

    client   = state["client"]
    phone    = state["phone"]
    ph_hash  = state["phone_code_hash"]

    try:
        await client.sign_in(phone, ph_hash, otp)
        session_string = await client.export_session_string()
        await client.disconnect()
        setup_state.pop(uid, None)

        ok = await start_userbot(session_string)

        if ok:
            await update.message.reply_text(
                "🎉 *Setup Complete!*\n\n"
                "Userbot connected ho gaya!\n\n"
                "⚠️ *Important:* Ye session string Railway mein `STRING_SESSION` variable mein save karo "
                "taaki restart pe dobara setup na karna pade:\n\n"
                f"`{session_string}`",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text("❌ Userbot start nahi hua. /setup se dobara try karo.")
        return ConversationHandler.END

    except PhoneCodeInvalid:
        await update.message.reply_text("❌ Wrong OTP. Sahi code bhejo:")
        return SETUP_OTP

    except PhoneCodeExpired:
        await update.message.reply_text("❌ OTP expire ho gaya. /setup se dobara karo.")
        setup_state.pop(uid, None)
        return ConversationHandler.END

    except SessionPasswordNeeded:
        await update.message.reply_text(
            "🔐 *Two-Step Verification*\n\n2FA password bhejo:",
            parse_mode="Markdown",
        )
        setup_state[uid]["session_pending"] = True
        return SETUP_2FA

    except Exception as e:
        await update.message.reply_text(f"❌ Error: `{e}`", parse_mode="Markdown")
        setup_state.pop(uid, None)
        return ConversationHandler.END


async def setup_2fa(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    password = update.message.text.strip()
    state = setup_state.get(uid)

    if not state:
        await update.message.reply_text("❌ Session expired. /setup se dobara karo.")
        return ConversationHandler.END

    client = state["client"]

    try:
        await client.check_password(password)
        session_string = await client.export_session_string()
        await client.disconnect()
        setup_state.pop(uid, None)

        ok = await start_userbot(session_string)

        if ok:
            await update.message.reply_text(
                "🎉 *Setup Complete!*\n\n"
                "Userbot connected ho gaya!\n\n"
                "⚠️ *Important:* Ye string Railway `STRING_SESSION` mein save karo:\n\n"
                f"`{session_string}`",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text("❌ Userbot start nahi hua. /setup se dobara try karo.")
        return ConversationHandler.END

    except Exception as e:
        await update.message.reply_text(f"❌ Wrong password: `{e}`\n\nDobara bhejo:", parse_mode="Markdown")
        return SETUP_2FA


async def setup_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    state = setup_state.pop(uid, None)
    if state:
        try:
            await state["client"].disconnect()
        except Exception:
            pass
    await update.message.reply_text("❌ Setup cancelled.")
    return ConversationHandler.END


# ─────────────────────────────────────────────
# ADMIN COMMANDS
# ─────────────────────────────────────────────
def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if (update.effective_user.id if update.effective_user else None) not in ADMIN_IDS:
            return
        return await func(update, ctx)
    wrapper.__name__ = func.__name__
    return wrapper


@admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uptime = int(time.time() - START_TIME)
    h, r = divmod(uptime, 3600); m, s = divmod(r, 60)
    ub = "✅ Connected" if userbot_connected else "❌ Not connected — /setup karo"
    await update.message.reply_html(
        f"🤖 <b>XVIP Hybrid Bot</b>\n\n"
        f"⏱ Uptime: <code>{h:02d}:{m:02d}:{s:02d}</code>\n"
        f"📡 Userbot: {ub}\n\n"
        f"Commands:\n"
        f"  /setup  — Userbot connect karo\n"
        f"  /status — Config dekho\n"
        f"  /test   — Ping userbot"
    )


@admin_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ub = "✅ Connected" if userbot_connected else "❌ Disconnected"
    t_ids = "\n".join(f"  • <code>{i}</code>" for i in TERA_SOURCE_IDS) or "  (none)"
    d_ids = "\n".join(f"  • <code>{i}</code>" for i in DISK_SOURCE_IDS) or "  (none)"
    await update.message.reply_html(
        f"📊 <b>Status</b>\n\n"
        f"<b>Userbot:</b> {ub}\n\n"
        f"━━━ TERA ━━━\n"
        f"<b>Sources:</b>\n{t_ids}\n"
        f"<b>Converter:</b> @{TERA_CONVERTER_BOT or '—'}\n"
        f"<b>Destination:</b> <code>{TERA_DESTINATION or '—'}</code>\n\n"
        f"━━━ DISK ━━━\n"
        f"<b>Sources:</b>\n{d_ids}\n"
        f"<b>Converter:</b> @{DISK_CONVERTER_BOT or '—'}\n"
        f"<b>Destination:</b> <code>{DISK_DESTINATION or '—'}</code>"
    )


@admin_only
async def cmd_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not userbot_connected or not userbot:
        await update.message.reply_text("❌ Userbot connected nahi. /setup karo.")
        return
    try:
        me = await userbot.get_me()
        await update.message.reply_html(
            f"✅ <b>Userbot OK</b>\n"
            f"Name: <code>{me.first_name}</code>\n"
            f"ID: <code>{me.id}</code>"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
async def main():
    # Build PTB app
    ptb = Application.builder().token(BOT_TOKEN).build()

    # Setup conversation
    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("setup", setup_start)],
        states={
            SETUP_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_phone)],
            SETUP_OTP:   [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_otp)],
            SETUP_2FA:   [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_2fa)],
        },
        fallbacks=[CommandHandler("cancel", setup_cancel)],
        per_user=True,
        per_chat=True,
    )

    ptb.add_handler(CommandHandler("start",  cmd_start))
    ptb.add_handler(CommandHandler("menu",   cmd_start))
    ptb.add_handler(CommandHandler("status", cmd_status))
    ptb.add_handler(CommandHandler("test",   cmd_test))
    ptb.add_handler(setup_conv)

    # Auto-start userbot if session already exists
    if STORED_SESSION:
        logger.info("STRING_SESSION found — auto-starting userbot...")
        await start_userbot(STORED_SESSION)
    else:
        logger.info("No STRING_SESSION — admin ko /setup karna hoga bot pe.")

    # Start PTB
    await ptb.initialize()
    await ptb.start()
    await ptb.updater.start_polling(drop_pending_updates=True)
    logger.info("✅ Bot running!")

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await ptb.updater.stop()
        await ptb.stop()
        await ptb.shutdown()
        if userbot:
            await userbot.stop()
        logger.info("Stopped.")


if __name__ == "__main__":
    asyncio.run(main())
