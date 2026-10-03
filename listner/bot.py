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
    widths = [max([len(headers[i])] + [len(row[i]) for row in values]) for i in range(3)]
    border = "+-" + "-+-".join("-" * width for width in widths) + "-+"
    lines = [border, "| " + " | ".join(headers[i].ljust(widths[i]) for i in range(3)) + " |", border]
    for row in values:
        lines.append("| " + " | ".join(row[i].ljust(widths[i]) for i in range(3)) + " |")
    lines.append(border)
    return "\n".join(lines)


def contact_list(settings: Settings) -> tuple[str, list[list[dict[str, str]]]]:
    users = asyncio.run(_telegram_contacts(settings))
    if not users:
        return "Telegram Contacts: none", []
    rows = []
    for user in users:
        name = " ".join(x for x in (user.first_name, user.last_name) if x) or "(no name)"
        username = f"@{user.username}" if user.username else "(none)"
        rows.append((username, name, user.id))
    text = "Telegram Contacts\n\n" + format_contact_table(rows) + "\n\nTap Watch to add a contact."
    return text, contact_keyboard(rows)


def watched_list(settings: Settings, user_ids: list[int]) -> tuple[str, list[list[dict[str, str]]]]:
    if not user_ids:
        return "Listeners: none", []
    try:
        users = asyncio.run(_telegram_contacts(settings))
        by_id = {user.id: user for user in users}
    except Exception:
        log.exception("Failed to fetch contacts while formatting /list")
        by_id = {}
    rows = []
    for user_id in user_ids:
        user = by_id.get(user_id)
        if user:
            name = " ".join(x for x in (user.first_name, user.last_name) if x) or "(no name)"
            username = f"@{user.username}" if user.username else "(none)"
        else:
            username, name = "(unknown)", "(unknown)"
        rows.append((username, name, user_id))
    return "Listeners\n\n" + format_contact_table(rows), watched_keyboard(user_ids)


def handle_message(db: Store, text: str, settings: Settings) -> str:
    command, args = command_and_args(text)
    log.info("Handling command: %s args=%s", command, args)

    if command in {"/start", "/help"}:
        return (
            "Listner Bot\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Available Commands\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "👁 /watch <user_id> [name]\n"
            "   Add a contact to the watch list.\n\n"
            "🚫 /unwatch <user_id>\n"
            "   Remove a contact from the watch list.\n\n"
            "📋 /list\n"
            "   Show all currently watched contacts.\n\n"
            "👥 /contacts\n"
            "   Show Telegram contacts with username and numeric user ID.\n\n"
            "ℹ️ Tip: Use /contacts first, then copy the User ID into /watch."
        )

    if command in {"/watch", "/add"}:
        if not args or not args[0].lstrip("-").isdigit():
            log.warning("Invalid watch command args=%s", args)
            return "Usage: /watch <numeric_user_id> [name]"
        user_id = int(args[0])
        name = " ".join(args[1:]).strip()
        log.info("Adding listener: user_id=%s name=%r", user_id, name)
        db.add_watched(user_id, name)
        return f"Watching {user_id}" + (f" ({name})" if name else "")

    if command in {"/unwatch", "/remove"}:
        if not args or not args[0].lstrip("-").isdigit():
            log.warning("Invalid unwatch command args=%s", args)
            return "Usage: /unwatch <numeric_user_id>"
        user_id = int(args[0])
        log.info("Removing listener: user_id=%s", user_id)
        removed = db.remove_watched(user_id)
        return f"Removed {user_id}" if removed else f"{user_id} was not in the listener list"

    if command in {"/contact", "/contacts"}:
        try:
            return contact_list(settings)[0]
        except Exception as exc:
            log.exception("Failed to fetch Telegram contacts")
            return f"Could not fetch Telegram contacts: {exc}"

    if command == "/list":
        log.info("Fetching listener list from database")
        started = time.monotonic()
        users = db.watched()
        log.info("Listener list loaded: count=%d elapsed=%.3fs", len(users), time.monotonic() - started)
        if not users:
            return "Listeners: none"
        return watched_list(settings, users)[0]

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
                {"offset": offset, "timeout": 25, "allowed_updates": json.dumps(["message", "callback_query"])},
            ).get("result", [])
            log.info("Telegram poll returned %d update(s)", len(updates))
            for update in updates:
                offset = update["update_id"] + 1
                callback = update.get("callback_query")
                if callback:
                    message = callback.get("message", {})
                    chat = str(message.get("chat", {}).get("id", ""))
                    data = callback.get("data", "")
                    log.info("Received callback update=%s chat=%s data=%r", update.get("update_id"), chat, data)
                    if not chat:
                        log.warning("Ignoring callback without chat id")
                        continue
                    if s.bot_chat_id and chat != s.bot_chat_id:
                        log.warning("Ignoring callback chat=%s because BOT_CHAT_ID is configured", chat)
                        continue
                    try:
                        if data.startswith("watch:") and data[6:].lstrip("-").isdigit():
                            user_id = int(data[6:])
                            reply = handle_message(db, f"/watch {user_id}", s)
                        elif data.startswith("unwatch:") and data[8:].lstrip("-").isdigit():
                            user_id = int(data[8:])
                            reply = handle_message(db, f"/unwatch {user_id}", s)
                        else:
                            reply = "Unknown action"
                        answer_callback(s.bot_token, callback.get("id", ""), reply)
                        send_reply(s.bot_token, chat, reply)
                    except Exception:
                        log.exception("Failed to process callback: %r", data)
                        try:
                            answer_callback(s.bot_token, callback.get("id", ""), "Listner error")
                        except Exception:
                            log.exception("Failed to answer callback")
                    continue

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
                    command, _ = command_and_args(text)
                    reply = handle_message(db, text, s)
                    keyboard = None
                    if command in {"/contact", "/contacts"} and reply != "Telegram Contacts: none":
                        try:
                            _, keyboard = contact_list(s)
                        except Exception:
                            keyboard = None
                    elif command == "/list" and reply != "Listeners: none":
                        keyboard = watched_keyboard(db.watched())
                    log.info("Command %r produced reply=%r", text, reply)
                    send_reply(s.bot_token, chat, reply, keyboard)
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
