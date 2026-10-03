"""Telegram Bot API control loop for Listner."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import Settings
from .db import Store
from telethon import TelegramClient, functions
from telethon.sessions import StringSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("listner.bot")


def request(token: str, method: str, data: dict):
    url = f"https://api.telegram.org/bot{token}/{method}"
    safe_data = dict(data)
    if "chat_id" in safe_data:
        safe_data["chat_id"] = str(safe_data["chat_id"])
    log.info("Telegram request: %s data=%s", method, safe_data)
    started = time.monotonic()
    payload = urllib.parse.urlencode(data).encode()
    try:
        with urllib.request.urlopen(url, payload, timeout=35) as response:
            result = json.loads(response.read())
    except Exception:
        log.exception("Telegram request FAILED: %s (%.3fs)", method, time.monotonic() - started)
        raise
    elapsed = time.monotonic() - started
    log.info("Telegram response: %s ok=%s elapsed=%.3fs", method, result.get("ok"), elapsed)
    if not result.get("ok", False):
        log.error("Telegram API error: method=%s result=%s", method, result)
        raise RuntimeError(f"Telegram {method} failed: {result}")
    return result


def send_reply(token: str, chat_id: str, text: str) -> None:
    log.info("Sending reply to chat=%s: %s", chat_id, text.replace("\\n", " | "))
    request(token, "sendMessage", {"chat_id": chat_id, "text": text})


def command_and_args(text: str) -> tuple[str, list[str]]:
    words = (text or "").split()
    if not words:
        return "", []
    command = words[0].split("@", 1)[0].lower()
    return command, words[1:]


async def _telegram_contacts(settings: Settings):
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")
    if not settings.telegram_session_string:
        raise RuntimeError("TELEGRAM_SESSION_STRING is required")
    client = TelegramClient(
        StringSession(settings.telegram_session_string),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Telethon user session is not authorized")
        if await client.is_bot():
            raise RuntimeError("TELEGRAM_SESSION_STRING must belong to the user account")
        # Telethon 1.x exposes contacts through the raw API rather than
        # TelegramClient.get_contacts(). GetContactsRequest returns the
        # contact users in the response.
        result = await client(functions.contacts.GetContactsRequest(hash=0))
        return result.users
    finally:
        await client.disconnect()


def format_contact_table(rows: list[tuple[str, str, int]]) -> str:
    headers = ("UserName", "First + Last Name", "User ID")
    values = [(username, name, str(user_id)) for username, name, user_id in rows]
    widths = [
        max([len(headers[0])] + [len(row[0]) for row in values]),
        max([len(headers[1])] + [len(row[1]) for row in values]),
        max([len(headers[2])] + [len(row[2]) for row in values]),
    ]
    border = "+-" + "-+-".join("-" * width for width in widths) + "-+"
    header = "| " + " | ".join(headers[i].ljust(widths[i]) for i in range(3)) + " |"
    lines = [border, header, border]
    for row in values:
        lines.append("| " + " | ".join(row[i].ljust(widths[i]) for i in range(3)) + " |")
    lines.append(border)
    return "\n".join(lines)


def contact_list(settings: Settings) -> str:
    users = asyncio.run(_telegram_contacts(settings))
    if not users:
        return "Telegram Contacts: none"
    rows = []
    for user in users:
        name = " ".join(x for x in (user.first_name, user.last_name) if x) or "(no name)"
        username = f"@{user.username}" if user.username else "(none)"
        rows.append((username, name, user.id))
    return "Telegram Contacts\n\n" + format_contact_table(rows) + "\n\nUse: /watch <user_id> [name]"


def watched_list(settings: Settings, user_ids: list[int]) -> str:
    if not user_ids:
        return watched_list(settings, users)

    log.warning("Unknown bot command: %s args=%s", command, args)
    return "Unknown command. Send /start for the available commands."


def main():
    s = Settings()
    if not s.bot_token:
        raise RuntimeError("BOT_TOKEN is required")
    db = Store(s.database)
    db.initialize()
    offset = 0
    log.info("Listner bot started; database=%s", "postgres" if db.is_postgres else "sqlite")

    while True:
        try:
            log.info("Polling Telegram: offset=%s", offset)
            updates = request(
                s.bot_token,
                "getUpdates",
                {"offset": offset, "timeout": 25, "allowed_updates": json.dumps(["message"])},
            ).get("result", [])
            log.info("Telegram poll returned %d update(s)", len(updates))
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message", {})
                chat = str(message.get("chat", {}).get("id", ""))
                text = message.get("text", "")
                log.info("Received update=%s chat=%s text=%r", update.get("update_id"), chat, text)
                if not chat:
                    log.warning("Ignoring update without chat id")
                    continue
                if s.bot_chat_id and chat != s.bot_chat_id:
                    log.warning("Ignoring chat=%s because BOT_CHAT_ID is configured", chat)
                    continue
                try:
                    reply = handle_message(db, text, s)
                    log.info("Command %r produced reply=%r", text, reply)
                    send_reply(s.bot_token, chat, reply)
                except Exception:
                    log.exception("Failed to process bot command: %r", text)
                    try:
                        send_reply(s.bot_token, chat, "Listner error: command could not be processed.")
                    except Exception:
                        log.exception("Failed to send error reply")
        except (urllib.error.URLError, TimeoutError, OSError):
            log.exception("Telegram polling/network error")
            time.sleep(2)
        except Exception:
            log.exception("Bot loop error")
            time.sleep(2)


if __name__ == "__main__":
    main()
