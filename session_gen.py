"""
XVIP Session Generator Bot
===========================
Deploy karo Railway pe → /generate command se
Telegram ke andar hi phone + OTP maango → STRING_SESSION print karo.

ENV VARS needed:
  BOT_TOKEN  - @BotFather wala token
  API_ID     - my.telegram.org
  API_HASH   - my.telegram.org
  ADMIN_IDS  - tumhara Telegram user ID
"""

import asyncio
import logging
import os
from typing import Optional

from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    FloodWait,
)

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("session-gen")

# ── Env ──
BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID    = int(os.environ["API_ID"])
API_HASH  = os.environ["API_HASH"]
ADMIN_IDS = {int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}

# ── Conversation states ──
PHONE, OTP, PASSWORD = range(3)

# ── Temp storage per user ──
user_clients: dict[int, Client] = {}
user_phone: dict[int, str] = {}
user_hash: dict[int, str] = {}


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


# ─────────────────────────────────────────────
# /generate - start flow
# ─────────────────────────────────────────────
async def cmd_generate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    if not is_admin(uid):
        return ConversationHandler.END

    await update.message.reply_text(
        "📱 *Session Generator*\n\n"
        "Apna phone number bhejo *international format* mein:\n"
        "Example: `+919876543210`\n\n"
        "/cancel — band karo",
        parse_mode="Markdown",
    )
    return PHONE


# ─────────────────────────────────────────────
# PHONE received
# ─────────────────────────────────────────────
async def got_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    phone = update.message.text.strip()

    await update.message.reply_text(f"⏳ `{phone}` pe OTP bhej raha hoon...", parse_mode="Markdown")

    try:
        client = Client(
            name=f"gen_{uid}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True,
        )
        await client.connect()

        sent = await client.send_code(phone)

        user_clients[uid] = client
        user_phone[uid]   = phone
        user_hash[uid]    = sent.phone_code_hash

        await update.message.reply_text(
            "✅ OTP bheja gaya!\n\n"
            "Telegram se jo 5-digit code aaya hai woh bhejo:\n"
            "Example: `12345`\n\n"
            "_(Space ke saath bhi chalega: `1 2 3 4 5`)_",
            parse_mode="Markdown",
        )
        return OTP

    except PhoneNumberInvalid:
        await update.message.reply_text("❌ Invalid phone number. Dobara `/generate` se try karo.")
        return ConversationHandler.END
    except FloodWait as e:
        await update.message.reply_text(f"⏳ FloodWait: {e.value} seconds baad try karo.")
        return ConversationHandler.END
    except Exception as e:
        logger.exception(e)
        await update.message.reply_text(f"❌ Error: `{e}`\n\nDobara `/generate` try karo.", parse_mode="Markdown")
        return ConversationHandler.END


# ─────────────────────────────────────────────
# OTP received
# ─────────────────────────────────────────────
async def got_otp(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    otp = update.message.text.strip().replace(" ", "")

    client  = user_clients.get(uid)
    phone   = user_phone.get(uid)
    ph_hash = user_hash.get(uid)

    if not client or not phone or not ph_hash:
        await update.message.reply_text("❌ Session expired. `/generate` se dobara shuru karo.", parse_mode="Markdown")
        return ConversationHandler.END

    try:
        await client.sign_in(phone, ph_hash, otp)
        session_string = await client.export_session_string()
        await client.disconnect()

        # Cleanup
        user_clients.pop(uid, None)
        user_phone.pop(uid, None)
        user_hash.pop(uid, None)

        await update.message.reply_text(
            "✅ *Session Generate Ho Gaya!*\n\n"
            "Ye string Railway mein `STRING_SESSION` variable mein paste karo:\n\n"
            f"`{session_string}`\n\n"
            "⚠️ *Kisi ko mat dena — ye tumhara Telegram account access deta hai.*",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    except PhoneCodeInvalid:
        await update.message.reply_text("❌ Wrong OTP. Sahi code bhejo:")
        return OTP

    except PhoneCodeExpired:
        await update.message.reply_text("❌ OTP expire ho gaya. `/generate` se dobara try karo.", parse_mode="Markdown")
        _cleanup(uid)
        return ConversationHandler.END

    except SessionPasswordNeeded:
        await update.message.reply_text(
            "🔐 Two-Step Verification enabled hai.\n\n"
            "Apna *2FA password* bhejo:",
            parse_mode="Markdown",
        )
        return PASSWORD

    except Exception as e:
        logger.exception(e)
        await update.message.reply_text(f"❌ Error: `{e}`", parse_mode="Markdown")
        _cleanup(uid)
        return ConversationHandler.END


# ─────────────────────────────────────────────
# 2FA PASSWORD received
# ─────────────────────────────────────────────
async def got_password(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    uid      = update.effective_user.id
    password = update.message.text.strip()

    client = user_clients.get(uid)
    if not client:
        await update.message.reply_text("❌ Session expired. `/generate` se dobara shuru karo.", parse_mode="Markdown")
        return ConversationHandler.END

    try:
        await client.check_password(password)
        session_string = await client.export_session_string()
        await client.disconnect()

        _cleanup(uid)

        await update.message.reply_text(
            "✅ *Session Generate Ho Gaya!*\n\n"
            "Ye string Railway mein `STRING_SESSION` variable mein paste karo:\n\n"
            f"`{session_string}`\n\n"
            "⚠️ *Kisi ko mat dena.*",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    except Exception as e:
        logger.exception(e)
        await update.message.reply_text(f"❌ Wrong password ya error: `{e}`", parse_mode="Markdown")
        _cleanup(uid)
        return ConversationHandler.END


# ─────────────────────────────────────────────
# /cancel
# ─────────────────────────────────────────────
async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    _cleanup(uid)
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


def _cleanup(uid: int) -> None:
    client = user_clients.pop(uid, None)
    if client:
        try:
            asyncio.create_task(client.disconnect())
        except Exception:
            pass
    user_phone.pop(uid, None)
    user_hash.pop(uid, None)


# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    await update.message.reply_text(
        "🔑 *XVIP Session Generator Bot*\n\n"
        "Commands:\n"
        "  /generate — Pyrogram String Session banao\n"
        "  /cancel   — Cancel karo\n\n"
        "Sirf ek baar karna hai. Session generate hone ke baad\n"
        "is bot ko Railway se hata ke main bot deploy karo.",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("generate", cmd_generate)],
        states={
            PHONE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, got_phone)],
            OTP:      [MessageHandler(filters.TEXT & ~filters.COMMAND, got_otp)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_password)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_user=True,
        per_chat=True,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(conv)

    logger.info("Session generator bot running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
