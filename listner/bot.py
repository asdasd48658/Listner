"""Telegram Bot API control loop for Listner."""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import Settings
from .db import Store

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
    log.info("Sending reply to chat=%s: %s", chat_id, text.replace("\n", " | "))
    request(token, "sendMessage", {"chat_id": chat_id, "text": text})


def command_and_args(text: str) -> tuple[str, list[str]]:
    words = (text or "").split()
    if not words:
        return "", []
    command = words[0].split("@", 1)[0].lower()
    return command, words[1:]


def handle_message(db: Store, text: str) -> str:
    command, args = command_and_args(text)
    log.info("Handling command: %s args=%s", command, args)

    if command in {"/start", "/help"}:
        return (
            "Listner commands:\n"
            "/watch <numeric_user_id> [name] — add a listener\n"
            "/unwatch <numeric_user_id> — remove a listener\n"
            "/list — show all listeners"
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

    if command == "/list":
        log.info("Fetching listener list from database")
        started = time.monotonic()
        users = db.watched()
        log.info("Listener list loaded: count=%d elapsed=%.3fs", len(users), time.monotonic() - started)
        if not users:
            return "Listeners: none"
        return "Listeners:\n" + "\n".join(
            f"{index}. {user_id}" for index, user_id in enumerate(users, 1)
        )

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
                    reply = handle_message(db, text)
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
