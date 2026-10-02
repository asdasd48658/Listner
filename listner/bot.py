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

log = logging.getLogger("listner.bot")


def request(token: str, method: str, data: dict):
    url = f"https://api.telegram.org/bot{token}/{method}"
    payload = urllib.parse.urlencode(data).encode()
    with urllib.request.urlopen(url, payload, timeout=35) as response:
        result = json.loads(response.read())
    if not result.get("ok", False):
        raise RuntimeError(f"Telegram {method} failed: {result}")
    return result


def send_reply(token: str, chat_id: str, text: str) -> None:
    request(token, "sendMessage", {"chat_id": chat_id, "text": text})


def command_and_args(text: str) -> tuple[str, list[str]]:
    words = (text or "").split()
    if not words:
        return "", []
    command = words[0].split("@", 1)[0].lower()
    return command, words[1:]


def handle_message(db: Store, text: str) -> str:
    command, args = command_and_args(text)

    if command in {"/start", "/help"}:
        return (
            "Listner commands:\n"
            "/watch <numeric_user_id> [name] — add a listener\n"
            "/unwatch <numeric_user_id> — remove a listener\n"
            "/list — show all listeners"
        )

    if command in {"/watch", "/add"}:
        if not args or not args[0].lstrip("-").isdigit():
            return "Usage: /watch <numeric_user_id> [name]"
        user_id = int(args[0])
        name = " ".join(args[1:]).strip()
        db.add_watched(user_id, name)
        return f"Watching {user_id}" + (f" ({name})" if name else "")

    if command in {"/unwatch", "/remove"}:
        if not args or not args[0].lstrip("-").isdigit():
            return "Usage: /unwatch <numeric_user_id>"
        user_id = int(args[0])
        removed = db.remove_watched(user_id)
        return f"Removed {user_id}" if removed else f"{user_id} was not in the listener list"

    if command == "/list":
        users = db.watched()
        if not users:
            return "Listeners: none"
        return "Listeners:\n" + "\n".join(
            f"{index}. {user_id}" for index, user_id in enumerate(users, 1)
        )

    return "Unknown command. Send /start for the available commands."


def main():
    s = Settings()
    if not s.bot_token:
        raise RuntimeError("BOT_TOKEN is required")

    db = Store(s.database)
    db.initialize()
    offset = 0

    # Polling and webhooks cannot be used together. Clear an old webhook
    # so /list and the other commands are actually received by this process.
    try:
        request(s.bot_token, "deleteWebhook", {"drop_pending_updates": False})
    except Exception:
        log.exception("Unable to clear Telegram webhook")

    log.info("Listner bot started")

    while True:
        try:
            updates = request(
                s.bot_token,
                "getUpdates",
                {"offset": offset, "timeout": 25, "allowed_updates": json.dumps(["message"])},
            ).get("result", [])

            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message", {})
                chat = str(message.get("chat", {}).get("id", ""))

                if not chat:
                    continue
                if s.bot_chat_id and chat != s.bot_chat_id:
                    continue

                try:
                    reply = handle_message(db, message.get("text", ""))
                    send_reply(s.bot_token, chat, reply)
                except Exception:
                    log.exception("Failed to process bot command")
                    send_reply(s.bot_token, chat, "Listner error: command could not be processed.")

        except (urllib.error.URLError, TimeoutError, OSError):
            log.exception("Telegram polling/network error")
            time.sleep(2)
        except Exception:
            log.exception("Bot loop error")
            time.sleep(2)


if __name__ == "__main__":
    main()
