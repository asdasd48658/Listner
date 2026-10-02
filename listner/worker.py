from __future__ import annotations
import asyncio, json, logging, urllib.parse, urllib.request
from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession
from .config import Settings
from .db import Store
log = logging.getLogger(__name__)

class ListnerWorker:
    def __init__(self, settings: Settings):
        if not settings.telegram_api_id or not settings.telegram_api_hash: raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")
        self.s, self.db = settings, Store(settings.database)
        if not settings.telegram_session_string:
            raise RuntimeError("TELEGRAM_SESSION_STRING is required on Render Free; generate an authenticated Telethon StringSession and set it in Render")
        session = StringSession(settings.telegram_session_string)
        self.client = TelegramClient(session, settings.telegram_api_id, settings.telegram_api_hash)

    async def handle_user_update(self, event: events.UserUpdate.Event) -> None:
        if event.user_id not in self.db.watched() or event.status is None:
            return
        log.info("Telegram status update: user=%s online=%s", event.user_id, event.online)
        if self.db.record_online_status(event.user_id, event.online):
            await self.deliver_alerts()

    async def poll_user_statuses(self) -> None:
        for user_id in self.db.watched():
            try:
                user = await self.client.get_entity(user_id)
                status = getattr(user, "status", None)
                if status is None:
                    continue
                online = isinstance(status, types.UserStatusOnline)
                if self.db.record_online_status(user_id, online):
                    log.info("polled status change: user=%s online=%s", user_id, online)
                    await self.deliver_alerts()
            except Exception as exc:
                log.warning("status lookup %s failed: %s", user_id, exc)

    async def discover_groups(self) -> None:
        """Use Telegram's GetCommonChats for every watched numeric account ID."""
        for user_id in self.db.watched():
            try:
                user = await self.client.get_input_entity(user_id)
                result = await self.client(functions.messages.GetCommonChatsRequest(user_id=user, max_id=0, limit=100))
                for chat in result.chats: self.db.save_group(chat.id, getattr(chat, "title", str(chat.id)))
            except Exception as exc: log.warning("common chats lookup %s failed: %s", user_id, exc)

    async def get_active_call(self, entity):
        if isinstance(entity, types.Channel):
            full = await self.client(functions.channels.GetFullChannelRequest(entity))
        elif isinstance(entity, types.Chat):
            full = await self.client(functions.messages.GetFullChatRequest(entity.id))
        else:
            return None
        return full.full_chat.call

    async def scan_calls(self) -> None:
        for group in self.db.groups():
            try:
                entity = await self.client.get_entity(group["group_id"])
                call = await self.get_active_call(entity)
                if not call:
                    continue
                owner = self.db.acquire_monitor(
                    group["group_id"], call.id, call.access_hash,
                    lease_seconds=self.s.lease_seconds,
                )
                if owner:
                    log.info("active call found: group=%s title=%s call=%s", group["group_id"], group["title"], call.id)
                    asyncio.create_task(self.monitor_call(group["group_id"], group["title"], call, owner))
            except Exception as exc:
                log.warning("call scan %s failed: %s", group["group_id"], exc)

    async def monitor_call(self, group_id: int, title: str, call: types.InputGroupCall, owner: str) -> None:
        """Only the successful database lease holder polls this exact group/call."""
        log.info("monitoring call %s/%s", group_id, call.id)
        while self.db.renew_monitor(group_id, call.id, owner, self.s.lease_seconds):
            try:
                page = await self.client(functions.phone.GetGroupCallRequest(call=call, limit=100))
                present = {p.peer.user_id for p in page.participants if isinstance(p.peer, types.PeerUser)}
                log.info("call participants: group=%s call=%s count=%s watched_present=%s", group_id, call.id, len(present), sorted(set(self.db.watched()) & present))
                for watched in self.db.watched():
                    change = self.db.record_presence(group_id, call.id, watched, watched in present, f"{watched} in {title}")
                    if change: await self.deliver_alerts()
                await asyncio.sleep(self.s.poll_seconds)
            except (asyncio.CancelledError,): raise
            except Exception as exc:
                log.info("call %s ended or unavailable: %s", call.id, exc); break

    async def deliver_alerts(self) -> None:
        if not (self.s.bot_token and self.s.bot_chat_id): return
        for alert in self.db.pending_alerts():
            if alert['kind'] == 'joined':
                text = f"🔔 Watched user {alert['telegram_user_id']} joined: {alert['body']}"
            elif alert['kind'] == 'left':
                text = f"👋 Watched user {alert['telegram_user_id']} left: {alert['body']}"
            else:
                text = f"🟢 Watched user {alert['telegram_user_id']} is online" if alert['kind'] == 'online' else f"⚫ Watched user {alert['telegram_user_id']} is offline"
            try:
                payload = urllib.parse.urlencode({'chat_id':self.s.bot_chat_id, 'text':text}).encode()
                urllib.request.urlopen(f"https://api.telegram.org/bot{self.s.bot_token}/sendMessage", payload, timeout=10).read()
                self.db.mark_delivered(alert['id'])
            except Exception as exc: log.warning("alert delivery failed: %s", exc)
    async def run(self) -> None:
        self.db.initialize()
        self.client.add_event_handler(self.handle_user_update, events.UserUpdate)
        await self.client.start()
        if not await self.client.is_user_authorized():
            raise RuntimeError("Telegram user session is not authorized")
        log.info("Listner Telegram user session connected")
        await self.client.catch_up()
        while True:
            await self.poll_user_statuses()
            await self.discover_groups(); await self.scan_calls(); await self.deliver_alerts()
            await asyncio.sleep(max(5, self.s.poll_seconds))

def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(ListnerWorker(Settings()).run())
if __name__ == '__main__': main()
