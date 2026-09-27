import asyncio
import io
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from html import escape
from urllib.parse import quote, urlparse

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler,
    MessageHandler, filters,
)

TOKEN = os.getenv("BOT_TOKEN", "").strip()
MAX_PROXIES = int(os.getenv("MAX_PROXIES", "100"))
TIMEOUT = float(os.getenv("PROXY_TIMEOUT", "8"))
WORKERS = max(1, int(os.getenv("CHECK_WORKERS", "15")))
OWNER_URL = os.getenv("OWNER_URL", "").strip()
CHANNEL_URL = os.getenv("CHANNEL_URL", "").strip()

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger("proxy-checker")
executor = ThreadPoolExecutor(max_workers=WORKERS)
PAT = re.compile(r"^(?P<host>[^:\s]+):(?P<port>\d{1,5}):(?P<user>[^:\s]+):(?P<password>[^:\s]+)$")
BOT_USERNAME = "bot"


def valid_button_url(value):
    """Return a Telegram-safe button URL or None."""
    if not value:
        return None
    value = value.strip()
    try:
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return value
    except Exception:
        pass
    log.warning("Ignoring invalid button URL: %r", value)
    return None


def menu():
    owner_url = valid_button_url(OWNER_URL)
    channel_url = valid_button_url(CHANNEL_URL)

    owner = (
        InlineKeyboardButton("👑 Owner", url=owner_url)
        if owner_url
        else InlineKeyboardButton("👑 Owner", callback_data="owner")
    )
    channel = (
        InlineKeyboardButton("📢 Channel", url=channel_url)
        if channel_url
        else InlineKeyboardButton("📢 Channel", callback_data="channel")
    )
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚡ Check Proxies", callback_data="check"),
            InlineKeyboardButton("🔄 Format Converter", callback_data="convert"),
        ],
        [owner, channel],
    ])


def parse(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    m = PAT.fullmatch(line)
    if not m or not 1 <= int(m["port"]) <= 65535:
        return {"raw": line, "valid": False}
    return {"raw": line, "valid": True, **m.groupdict()}


def parse_all(text):
    out, seen = [], set()
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        p = parse(line)
        if p and p["raw"] not in seen:
            out.append(p)
            seen.add(p["raw"])
    return out


def endpoint(p):
    return p.get("raw", "") if not p.get("valid") else f'{p["host"]}:{p["port"]}'


def check(p):
    if not p.get("valid"):
        return {"status": "INVALID", "proxy": p, "reason": "Expected host:port:user:pass"}
    started = time.perf_counter()
    user = quote(p["user"], safe="")
    password = quote(p["password"], safe="")
    proxy_url = f"http://{user}:{password}@{p['host']}:{p['port']}"
    try:
        r = requests.get(
            "https://api.ipify.org?format=json",
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=TIMEOUT,
            headers={"User-Agent": "PX-Proxy-Checker/2.0"},
        )
        r.raise_for_status()
        ip = r.json().get("ip", "Unknown")
        return {"status": "LIVE", "proxy": p, "latency": round((time.perf_counter() - started) * 1000), "ip": ip}
    except requests.exceptions.ProxyError:
        reason = "Proxy connection/authentication failed"
    except requests.exceptions.ConnectTimeout:
        reason = "Connection timeout"
    except requests.exceptions.ReadTimeout:
        reason = "Response timeout"
    except requests.exceptions.SSLError:
        reason = "TLS/SSL error"
    except requests.exceptions.RequestException:
        reason = "Network request failed"
    except Exception:
        reason = "Unexpected checker error"
    return {"status": "DEAD", "proxy": p, "reason": reason}


async def checks(items):
    loop = asyncio.get_running_loop()
    return await asyncio.gather(*[loop.run_in_executor(executor, check, p) for p in items])


def fmt(r):
    if r["status"] == "INVALID":
        raw = escape(str(r["proxy"].get("raw", "")))
        reason = escape(str(r.get("reason", "")))
        return f"⚪ <b>INVALID</b>\n📝 <code>{raw}</code>\nℹ️ {reason}"
    if r["status"] == "LIVE":
        proxy = escape(endpoint(r["proxy"]))
        latency = escape(str(r.get("latency", "")))
        ip = escape(str(r.get("ip", "Unknown")))
        return (
            f"🟢 <b>LIVE</b>\n🌐 <code>{proxy}</code>\n"
            f"⚡ Latency: <b>{latency} ms</b>\n🔎 Exit IP: <code>{ip}</code>\n"
            "📡 Protocol: <b>HTTP/HTTPS</b>"
        )
    proxy = escape(endpoint(r["proxy"]))
    reason = escape(str(r.get("reason", "")))
    return f"🔴 <b>DEAD</b>\n🌐 <code>{proxy}</code>\nℹ️ {reason}"


def chunks(text, limit=3800):
    out, cur = [], ""
    for block in text.split("\n\n"):
        if not block:
            continue
        if cur and len(cur) + len(block) + 2 > limit:
            out.append(cur)
            cur = block
        else:
            cur = (cur + "\n\n" + block).strip()
    if cur:
        out.append(cur)
    return out


async def send_results(update, results, elapsed):
    live = sum(x["status"] == "LIVE" for x in results)
    dead = sum(x["status"] == "DEAD" for x in results)
    invalid = sum(x["status"] == "INVALID" for x in results)
    body = (
        "╔══════════════════════╗\n   🔐 <b>PROXY CHECKER</b>\n╚══════════════════════╝\n\n"
        f"📊 Total: <b>{len(results)}</b>\n🟢 Live: <b>{live}</b>\n🔴 Dead: <b>{dead}</b>\n⚪ Invalid: <b>{invalid}</b>\n⏱ Check time: <b>{elapsed:.2f}s</b>\n\n━━━━━━━━━━━━━━━━━━━━"
    )
    for r in results:
        body += "\n\n" + fmt(r)
    body += "\n\n━━━━━━━━━━━━━━━━━━━━\n👨‍💻 <b>Developer:</b> @HEXAZONxHERE"
    for c in chunks(body):
        await update.message.reply_text(c, parse_mode=ParseMode.HTML)

    live_lines = [r["proxy"]["raw"] for r in results if r["status"] == "LIVE"]
    if live_lines:
        filename = f"@{BOT_USERNAME}____.txt"
        data = ("\n".join(live_lines) + "\n").encode("utf-8")
        await update.message.reply_document(
            document=io.BytesIO(data),
            filename=filename,
            caption=f"🟢 <b>{len(live_lines)} LIVE proxies</b>\n📄 Live proxies only",
            parse_mode=ParseMode.HTML,
        )


async def do_check(update, text):
    items = parse_all(text)
    if not items:
        await update.message.reply_text("❌ <b>No proxy found.</b>\n\n<code>/chk host:port:user:pass</code>", parse_mode=ParseMode.HTML)
        return
    if len(items) > MAX_PROXIES:
        await update.message.reply_text(f"❌ Maximum <b>{MAX_PROXIES}</b> proxies per request.\n📊 Received: <b>{len(items)}</b>", parse_mode=ParseMode.HTML)
        return
    msg = await update.message.reply_text(f"⏳ <b>Checking {len(items)} proxy(ies)...</b>", parse_mode=ParseMode.HTML)
    started = time.perf_counter()
    results = await checks(items)
    elapsed = time.perf_counter() - started
    live = sum(x["status"] == "LIVE" for x in results)
    dead = sum(x["status"] == "DEAD" for x in results)
    invalid = sum(x["status"] == "INVALID" for x in results)
    await msg.edit_text(
        f"✅ <b>Check completed</b>\n🟢 Live: <b>{live}</b> | 🔴 Dead: <b>{dead}</b> | ⚪ Invalid: <b>{invalid}</b>\n📄 Sending live file...",
        parse_mode=ParseMode.HTML,
    )
    await send_results(update, results, elapsed)


async def start(update, context):
    await update.message.reply_text(
        "⚡ <b>PX PROXY CHECKER</b>\n\n"
        "Fast proxy validation, format conversion and TXT support.\n\n"
        "• ⚡ High-speed concurrent checking\n"
        "• 🔄 Multi-format proxy converter\n"
        "• 📄 Direct TXT auto-checking\n"
        "• 🟢 Live proxies are returned as a TXT file\n\n"
        "<i>Select an option below or send your commands directly.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=menu(),
    )


async def chk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    body = (update.message.text or "").partition(" ")[2].strip()
    if body:
        await do_check(update, body)
        return
    reply = update.message.reply_to_message
    if reply and reply.document:
        if not (reply.document.file_name or "").lower().endswith(".txt"):
            await update.message.reply_text("❌ Please reply to a <b>.txt</b> file.", parse_mode=ParseMode.HTML)
            return
        if reply.document.file_size and reply.document.file_size > 2 * 1024 * 1024:
            await update.message.reply_text("❌ TXT file is too large. Maximum 2 MB.")
            return
        f = await context.bot.get_file(reply.document.file_id)
        data = await f.download_as_bytearray()
        await do_check(update, bytes(data).decode("utf-8-sig", errors="ignore"))
        return
    await update.message.reply_text("❌ Use <code>/chk host:port:user:pass</code> or reply to a TXT file with <code>/chk</code>.", parse_mode=ParseMode.HTML)


async def auto_txt(update, context):
    doc = update.message.document
    if not (doc.file_name or "").lower().endswith(".txt"):
        return
    if doc.file_size and doc.file_size > 2 * 1024 * 1024:
        await update.message.reply_text("❌ TXT file is too large. Maximum 2 MB.")
        return
    f = await context.bot.get_file(doc.file_id)
    data = await f.download_as_bytearray()
    await do_check(update, bytes(data).decode("utf-8-sig", errors="ignore"))


FORMAT_HELP = (
    "🔄 <b>Proxy Converter</b>\n\n"
    "Usage: <code>/convert &lt;1-4&gt;</code>\n\n"
    "Formats:\n"
    "1 ➜ <code>ip:port:user:pass</code>\n"
    "2 ➜ <code>ip:port</code>\n"
    "3 ➜ <code>user:pass@ip:port</code>\n"
    "4 ➜ <code>scheme://user:pass@ip:port</code>\n\n"
    "Example:\n"
    "<code>px023004.pointtoserver.com:10780:purevpn0s11383538:43z2vhwa</code>\n"
    "➜ <code>http://purevpn0s11383538:43z2vhwa@px023004.pointtoserver.com:10780</code>\n\n"
    "Reply to a TXT file with <code>/convert 4</code> or paste proxy lines after the command."
)


def convert_line(line, target):
    p = parse(line)
    if not p or not p.get("valid"):
        return None
    if target == 1:
        return p["raw"]
    if target == 2:
        return f'{p["host"]}:{p["port"]}'
    if target == 3:
        return f'{p["user"]}:{p["password"]}@{p["host"]}:{p["port"]}'
    if target == 4:
        return f'http://{p["user"]}:{p["password"]}@{p["host"]}:{p["port"]}'
    return None


async def convert_data(update, target, text):
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        x = convert_line(raw.strip(), target)
        if x and x not in lines:
            lines.append(x)
    if not lines:
        await update.message.reply_text("❌ No valid <code>host:port:user:pass</code> proxies found.", parse_mode=ParseMode.HTML)
        return
    data = ("\n".join(lines) + "\n").encode("utf-8")
    await update.message.reply_document(
        document=io.BytesIO(data),
        filename=f"converted_proxies_format_{target}.txt",
        caption=f"✅ Converted <b>{len(lines)}</b> proxies",
        parse_mode=ParseMode.HTML,
    )


async def convert(update, context):
    if not context.args or context.args[0] not in {"1", "2", "3", "4"}:
        await update.message.reply_text(FORMAT_HELP, parse_mode=ParseMode.HTML)
        return
    target = int(context.args[0])
    text = update.message.text or ""
    body = text.partition(" ")[2].strip()
    body = body[len(context.args[0]):].strip() if body.startswith(context.args[0]) else ""
    if body:
        await convert_data(update, target, body)
        return
    reply = update.message.reply_to_message
    if reply and reply.document:
        if not (reply.document.file_name or "").lower().endswith(".txt"):
            await update.message.reply_text("❌ Please reply to a .txt file.")
            return
        f = await context.bot.get_file(reply.document.file_id)
        data = await f.download_as_bytearray()
        await convert_data(update, target, bytes(data).decode("utf-8-sig", errors="ignore"))
        return
    await update.message.reply_text("📄 Reply to a TXT file with <code>/convert 4</code> or paste proxies after the command.", parse_mode=ParseMode.HTML)


async def buttons(update, context):
    q = update.callback_query
    await q.answer()
    if q.data == "check":
        await q.message.reply_text(
            "⚡ <b>Check Proxies</b>\n\n"
            "Single:\n<code>/chk host:port:user:pass</code>\n\n"
            "Multiple:\n<code>/chk proxy1\nproxy2</code>\n\n"
            "TXT:\nSend a .txt file directly or reply to one with <code>/chk</code>.",
            parse_mode=ParseMode.HTML,
        )
    elif q.data == "convert":
        await q.message.reply_text(FORMAT_HELP, parse_mode=ParseMode.HTML)
    else:
        await q.answer("This link is not configured yet.", show_alert=True)


async def errors(update, context):
    # Keep the complete Telegram API error in Railway logs.
    # The exact BadRequest reason identifies the invalid field.
    log.exception("Unhandled Telegram error: %s", context.error)


def main():
    global BOT_USERNAME
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Add it in Railway Variables.")
    log.info("🚀 PX Proxy Checker V2 starting...")
    app = Application.builder().token(TOKEN).build()

    async def post_init(application):
        global BOT_USERNAME
        me = await application.bot.get_me()
        BOT_USERNAME = me.username or "bot"
        log.info("🤖 Logged in as @%s", BOT_USERNAME)

    app.post_init = post_init
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("chk", chk))
    app.add_handler(CommandHandler("convert", convert))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.Document.FileExtension("txt"), auto_txt))
    app.add_error_handler(errors)
    log.info("🤖 Bot ready; starting polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
