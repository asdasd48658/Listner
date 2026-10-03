from __future__ import annotations
import asyncio, json, logging, urllib.parse, urllib.request, time
from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession
from datetime import datetime
from zoneinfo import ZoneInfo
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
        self._entity_cache: dict[int, types.User] = {}
        self._entity_miss_until: dict[int, float] = {}
        self._contacts_loaded = False
        self._contacts_users: list[types.User] = []
        self._call_states: dict[tuple[int, int, int], tuple[bool, bool]] = {}

    async def resolve_watched_user(self, user_id: int):
        """Resolve a watched numeric ID without repeatedly flooding Telegram."""
        cached = self._entity_cache.get(user_id)
        if cached is not None:
            return cached
        if self._entity_miss_until.get(user_id, 0) > time.monotonic():
            return None

        if not self._contacts_loaded:
            try:
                result = await self.client(functions.contacts.GetContactsRequest(hash=0))
                self._contacts_users = [u for u in result.users if isinstance(u, types.User)]
                log.info("loaded %s Telegram contacts for watched-user resolution", len(self._contacts_users))
            except Exception as exc:
                log.warning("contact entity lookup failed: %s", exc)
            finally:
                self._contacts_loaded = True

        for entity in self._contacts_users:
            if getattr(entity, "id", None) == user_id:
                self._entity_cache[user_id] = entity
                return entity

        try:
            entity = await self.client.get_entity(user_id)
            if isinstance(entity, types.User):
                self._entity_cache[user_id] = entity
                return entity
        except Exception:
            pass

        try:
            dialogs = await self.client.get_dialogs(limit=None)
            for dialog in dialogs:
                entity = getattr(dialog, "entity", None)
                if isinstance(entity, types.User) and getattr(entity, "id", None) == user_id:
                    self._entity_cache[user_id] = entity
                    return entity
        except Exception as exc:
            log.debug("dialog entity lookup for %s failed: %s", user_id, exc)

        try:
            for group in self.db.groups():
                group_entity = await self.client.get_entity(group["group_id"])
                if not isinstance(group_entity, (types.Channel, types.Chat)):
                    continue
                participants = await self.client.get_participants(group_entity, limit=10000)
                for entity in participants:
                    if isinstance(entity, types.User) and getattr(entity, "id", None) == user_id:
                        self._entity_cache[user_id] = entity
                        log.info("resolved watched user %s from group %s", user_id, group["group_id"])
                        return entity
        except Exception as exc:
            log.debug("group participant lookup for watched user %s failed: %s", user_id, exc)

        self._entity_miss_until[user_id] = time.monotonic() + 60
        return None

    def contact_label(self, user_id: int, user=None) -> str:
        """Prefer first name + last name for every live alert; never show numeric ID."""
        entity = user or self._entity_cache.get(user_id)
        if entity is not None:
            name = " ".join(filter(None, [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]))
            if name:
                return name
            username = getattr(entity, "username", None)
            if username:
                return f"@{username}"
        return "Unknown contact"

    async def handle_user_update(self, event: events.UserUpdate.Event) -> None:
        watched = self.db.watched()
        log.info(
            "Telegram UserUpdate received: user=%s online=%s status=%r watched=%s",
            event.user_id,
            event.online,
            event.status,
            event.user_id in watched,
        )
        if event.user_id not in watched or event.status is None:
            return
        if self.db.record_online_status(event.user_id, event.online):
            await self.deliver_alerts()

    async def poll_user_statuses(self) -> None:
        watched_ids = self.db.watched()
        log.info("polling watched user statuses: count=%s ids=%s", len(watched_ids), watched_ids)
        for user_id in watched_ids:
            try:
                user = await self.resolve_watched_user(user_id)
                if user is None:
                    log.warning("watched user %s could not be resolved; add the account to the monitoring account's contacts or use its username once", user_id)
                    continue
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
        for user_id in self.db.watched():
            try:
                user = await self.resolve_watched_user(user_id)
                if user is None:
                    log.warning("common chats: watched user %s could not be resolved", user_id)
                    continue
                result = await self.client(functions.messages.GetCommonChatsRequest(user_id=user, max_id=0, limit=100))
                for chat in result.chats:
                    self.db.save_group(chat.id, getattr(chat, "title", str(chat.id)))
            except Exception as exc:
                log.warning("common chats lookup %s failed: %s", user_id, exc)

    async def get_active_call(self, entity):
        if isinstance(entity, types.Channel):
            full = await self.client(functions.channels.GetFullChannelRequest(entity))
        elif isinstance(entity, types.Chat):
            full = await self.client(functions.messages.GetFullChatRequest(entity.id))
        else:
            return None
        return full.full_chat.call

    @staticmethod
    def call_participant_state(participant) -> tuple[bool, bool]:
        """Return (mic_unmuted, camera_on) for a group-call participant.

        V1 reports Telegram participant flags only:
        muted=False means the microphone is unmuted, not that the person is
        necessarily speaking. video_joined=True means the participant has
        joined the video stream. If Telegram reports a paused video object,
        it is treated as camera off.
        """
        mic_unmuted = getattr(participant, "muted", True) is False

        video_joined = getattr(participant, "video_joined", False) is True
        video = getattr(participant, "video", None)
        video_paused = getattr(video, "paused", False) if video is not None else False
        camera_on = video_joined and not video_paused

        return mic_unmuted, camera_on

    async def scan_calls(self) -> None:
        for group in self.db.groups():
            try:
                entity = await self.client.get_entity(group["group_id"])
                call = await self.get_active_call(entity)
                if not call:
                    continue
                owner = self.db.acquire_monitor(group["group_id"], call.id, call.access_hash, lease_seconds=self.s.lease_seconds)
                if owner:
                    log.info("active call found: group=%s title=%s call=%s", group["group_id"], group["title"], call.id)
                    asyncio.create_task(self.monitor_call(group["group_id"], group["title"], call, owner))
            except Exception as exc:
                log.warning("call scan %s failed: %s", group["group_id"], exc)

    async def send_call_state_update(self, title: str, user_label: str, mic_unmuted: bool, camera_on: bool) -> None:
        if not (self.s.bot_token and self.s.bot_chat_id):
            return
        text = (
            f"📞 CALL STATE — {user_label}\\n"
            f"Group: {title}\\n"
            f"🎤 Mic: {'ON' if mic_unmuted else 'OFF'}\\n"
            f"📹 Camera: {'ON' if camera_on else 'OFF'}"
        )
        try:
            payload = urllib.parse.urlencode({'chat_id': self.s.bot_chat_id, 'text': text}).encode()
            urllib.request.urlopen(f"https://api.telegram.org/bot{self.s.bot_token}/sendMessage", payload, timeout=10).read()
        except Exception as exc:
            log.warning("call state delivery failed: %s", exc)

    async def monitor_call(self, group_id: int, title: str, call: types.InputGroupCall, owner: str) -> None:
        log.info("monitoring call %s/%s", group_id, call.id)
        while self.db.renew_monitor(group_id, call.id, owner, self.s.lease_seconds):
            try:
                page = await self.client(functions.phone.GetGroupCallRequest(call=call, limit=100))
                participants = [
                    p for p in page.participants
                    if isinstance(p.peer, types.PeerUser)
                ]
                present = {p.peer.user_id for p in participants}
                watched_ids = self.db.watched()
                watched_present = sorted(set(watched_ids) & present)

                log.info(
                    "call participants: group=%s call=%s count=%s watched_present=%s",
                    group_id, call.id, len(present), watched_present,
                )

                participant_by_user = {p.peer.user_id: p for p in participants}

                for watched in watched_ids:
                    user = await self.resolve_watched_user(watched)
                    is_present = watched in present

                    if is_present:
                        participant = participant_by_user[watched]
                        mic_unmuted, camera_on = self.call_participant_state(participant)
                        contact_label = self.contact_label(watched, user)

                        log.info(
                            "V1 CALL STATE: group=%s title=%r call=%s user=%s "
                            "present=true mic=%s camera=%s",
                            group_id,
                            title,
                            call.id,
                            contact_label,
                            "ON" if mic_unmuted else "OFF",
                            "ON" if camera_on else "OFF",
                        )

                        state_key = (group_id, call.id, watched)
                        current_state = (mic_unmuted, camera_on)
                        previous_state = self._call_states.get(state_key)
                        if previous_state is not None and previous_state != current_state:
                            await self.send_call_state_update(title, contact_label, mic_unmuted, camera_on)
                        self._call_states[state_key] = current_state

                        join_time = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%H:%M:%S")
                        body = f"{contact_label} joined {title} at {join_time}"
                    else:
                        self._call_states.pop((group_id, call.id, watched), None)
                        log.info(
                            "V1 CALL STATE: group=%s title=%r call=%s user=%s "
                            "present=false mic=OFF camera=OFF",
                            group_id,
                            title,
                            call.id,
                            self.contact_label(watched, user),
                        )
                        body = f"{self.contact_label(watched, user)} in {title}"

                    change = self.db.record_presence(group_id, call.id, watched, is_present, body)
                    if change:
                        await self.deliver_alerts()

                await asyncio.sleep(self.s.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.info("call %s ended or unavailable: %s", call.id, exc)
                break

    async def deliver_alerts(self) -> None:
        if not (self.s.bot_token and self.s.bot_chat_id):
            return
        for alert in self.db.pending_alerts():
            if alert['kind'] == 'joined':
                text = f"🔴 LIVE — {alert['body']}"
            elif alert['kind'] == 'left':
                text = f"⚪ LIVE — {alert['body']}"
            else:
                text = f"🟢 Watched user {alert['telegram_user_id']} is online" if alert['kind'] == 'online' else f"⚫ Watched user {alert['telegram_user_id']} is offline"
            try:
                payload = urllib.parse.urlencode({'chat_id':self.s.bot_chat_id, 'text':text}).encode()
                urllib.request.urlopen(f"https://api.telegram.org/bot{self.s.bot_token}/sendMessage", payload, timeout=10).read()
                self.db.mark_delivered(alert['id'])
            except Exception as exc:
                log.warning("alert delivery failed: %s", exc)

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
            await self.discover_groups()
            await self.scan_calls()
            await self.deliver_alerts()
            await asyncio.sleep(max(5, self.s.poll_seconds))

def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(ListnerWorker(Settings()).run())

if __name__ == '__main__':
    main()
