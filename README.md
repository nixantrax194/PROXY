# PX Proxy Checker V2 — Railway Ready

Features:
- `/start` menu with Check Proxies + Format Converter buttons.
- Single and multiline `/chk`.
- Reply-to-TXT `/chk`.
- Direct `.txt` upload auto-checking.
- Live proxies automatically returned as a TXT file.
- Live file name: `@BOTUSERNAME____.txt`.
- `/convert 1` through `/convert 4`.
- Format 4: `host:port:user:pass` -> `http://user:pass@host:port`.
- Concurrent proxy validation, latency and exit IP.

Railway variables:
- Required: `BOT_TOKEN`
- Optional: `MAX_PROXIES=100`, `PROXY_TIMEOUT=8`, `CHECK_WORKERS=15`, `OWNER_URL=`, `CHANNEL_URL=`

Procfile: `worker: python bot.py`

Do not commit `.env` or a real Telegram token. Only one polling instance should use a Telegram bot token at a time.

Developer: @HEXAZONxHERE
