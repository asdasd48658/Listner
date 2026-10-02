"""Vercel control API and Telegram webhook for Listner."""
from http.server import BaseHTTPRequestHandler
import json
import logging
from listner.bot import handle_message, send_reply
from listner.config import Settings
from listner.db import Store

log = logging.getLogger("listner.api")


class handler(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        encoded = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _store(self):
        s = Settings()
        if s.control_secret and self.headers.get("Authorization") != f"Bearer {s.control_secret}":
            self._send(401, {"error": "unauthorized"})
            return None
        return Store(s.database)

    def do_GET(self):
        db = self._store()
        if db:
            self._send(200, {"watched_user_ids": db.watched(), "groups": [dict(x) for x in db.groups()]})

    def do_POST(self):
        s = Settings()

        # Telegram webhook endpoint. Vercel invokes this function for each update;
        # unlike long polling, it does not require a persistent process.
        if self.path.startswith("/telegram/webhook"):
            expected = s.bot_webhook_secret
            received = self.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if expected and received != expected:
                self._send(401, {"error": "unauthorized"})
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
                update = json.loads(self.rfile.read(length))
                message = update.get("message", {})
                chat_id = str(message.get("chat", {}).get("id", ""))
                text = message.get("text", "")
                if not chat_id:
                    self._send(200, {"ok": True, "ignored": "no_chat"})
                    return
                if s.bot_chat_id and chat_id != s.bot_chat_id:
                    log.info("Ignoring webhook chat=%s because BOT_CHAT_ID is configured", chat_id)
                    self._send(200, {"ok": True, "ignored": "chat_filter"})
                    return

                db = Store(s.database)
                reply = handle_message(db, text)
                log.info("Webhook command=%r reply=%r", text, reply)
                send_reply(s.bot_token, chat_id, reply)
                self._send(200, {"ok": True})
            except Exception:
                log.exception("Telegram webhook processing failed")
                self._send(500, {"ok": False})
            return

        db = self._store()
        if not db:
            return
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            user_id = int(payload["telegram_user_id"])
        except (ValueError, KeyError, json.JSONDecodeError):
            return self._send(400, {"error": "telegram_user_id must be a numeric integer"})

        if self.path.endswith("/unwatch"):
            db.remove_watched(user_id)
            action = "removed"
        else:
            db.add_watched(user_id, str(payload.get("display_name", "")))
            action = "watching"
        self._send(200, {"status": action, "telegram_user_id": user_id})
