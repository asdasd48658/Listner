from __future__ import annotations
import asyncio, json, logging, urllib.parse, urllib.request
from telethon import TelegramClient, functions, types
from .config import Settings
from .db import Store
log = logging.getLogger(__name__)

class ListnerWorker:
    def __init__(self, settings: Settings):
        if not settings.telegram_api_id or not settings.telegram_api_hash: raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")
        self.s, self.db = settings, Store(settings.database_path)
        self.client = TelegramClient(settings.telegram_session, settings.telegram_api_id, settings.telegram_api_hash)

    async def discover_groups(self) -> None:
        """Use Telegram's GetCommonChats for every watched numeric account ID."""
        for user_id in self.db.watched():
            try:
                user = await self.client.get_input_entity(user_id)
                result = await self.client(functions.messages.GetCommonChatsRequest(user_id=user, max_id=0, limit=100))
                for chat in result.chats: self.db.save_group(chat.id, getattr(chat, "title", str(chat.id)))
            except Exception as exc: log.warning("common chats lookup %s failed: %s", user_id, exc)

    async def scan_calls(self) -> None:
        for group in self.db.groups():
            try:
                entity = await self.client.get_entity(group["group_id"])
                full = await self.client(functions.channels.GetFullChannelRequest(entity))
                call = full.full_chat.call
                if call:
                    owner = self.db.acquire_monitor(group["group_id"], call.id, call.access_hash, lease_seconds=self.s.lease_seconds)
                    if owner: asyncio.create_task(self.monitor_call(group["group_id"], group["title"], call, owner))
            except Exception as exc: log.debug("call scan %s: %s", group["group_id"], exc)

    async def monitor_call(self, group_id: int, title: str, call: types.InputGroupCall, owner: str) -> None:
        """Only the successful database lease holder polls this exact group/call."""
        log.info("monitoring call %s/%s", group_id, call.id)
        while self.db.renew_monitor(group_id, call.id, owner, self.s.lease_seconds):
            try:
                page = await self.client(functions.phone.GetGroupParticipantsRequest(call=call, ids=[], sources=[], offset="", limit=100))
                present = {p.peer.user_id for p in page.participants if isinstance(p.peer, types.PeerUser)}
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
            text = (f"🔔 Watched user {alert['telegram_user_id']} joined: {alert['body']}" if alert['kind']=='joined'
                    else f"👋 Watched user {alert['telegram_user_id']} left: {alert['body']}")
            try:
                payload = urllib.parse.urlencode({'chat_id':self.s.bot_chat_id, 'text':text}).encode()
                urllib.request.urlopen(f"https://api.telegram.org/bot{self.s.bot_token}/sendMessage", payload, timeout=10).read()
                self.db.mark_delivered(alert['id'])
            except Exception as exc: log.warning("alert delivery failed: %s", exc)
    async def run(self) -> None:
        self.db.initialize(); await self.client.start()
        while True:
            await self.discover_groups(); await self.scan_calls(); await self.deliver_alerts()
            await asyncio.sleep(max(5, self.s.poll_seconds))

def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(ListnerWorker(Settings()).run())
if __name__ == '__main__': main()
