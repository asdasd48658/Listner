# Listner

Listner monitors watched numeric Telegram user IDs through a **Telethon user account**. It sends alerts when Telegram reports that a watched user comes online or goes offline, and when they join or leave an active shared group voice/video call. A separate Telegram Bot API process and a small Vercel-compatible control API manage the watch list.

## Architecture and guarantees

- The worker discovers shared groups with Telegram `messages.GetCommonChats` for each watched account, then checks channel full-info for active group calls.
- PostgreSQL is the shared production database between the Vercel control API and the persistent worker. Each active `(group_id, call_id)` is protected by its database primary key plus an atomic conditional UPSERT lease. Only its successful holder polls participants, renews its lease before each approximately two-second poll, and reports presence transitions.
- Join state and alerts are durable in PostgreSQL, so only transitions produce alerts; leave alerts include the measured duration.
- User-status alerts are likewise durable: a first observed online status and every later online/offline transition generate one alert each. An initial offline status establishes a baseline so a worker restart does not send a false offline alert. Telegram only supplies user-status updates that the signed-in account is permitted to see, so privacy settings and account relationship can limit these alerts.
- The worker needs a persistent disk/volume only for its Telethon session. **Vercel's filesystem is ephemeral**, so deploy the control API there with the same external `DATABASE_URL` as the worker. The Telethon worker must run on an always-on, persistent runtime. SQLite is available only when an explicit `DATABASE_PATH` is set for local development.
- **V1 call diagnostics:** while an active call is being monitored, Listner samples each watched participant on every `POLL_SECONDS` interval and logs whether Telegram reports the microphone as unmuted and video as currently broadcasting. This is diagnostic only; it does not record the call.

## V1 mic/camera test

The Telegram participant object exposes a `muted` flag and a `video_joined` flag. Telegram defines `muted=false` as unmuted and `video_joined=true` as currently broadcasting video. A video stream's `paused` flag is also checked. citeturn0search0turn0search8

With the default `POLL_SECONDS=2`, worker logs should contain entries similar to:

```
V1 CALL STATE: group=-100123 title='My Group' call=123 user=John present=true mic=ON camera=OFF
V1 CALL STATE: group=-100123 title='My Group' call=123 user=John present=true mic=ON camera=ON
V1 CALL STATE: group=-100123 title='My Group' call=123 user=John present=true mic=OFF camera=ON
```

**Important:** `mic=ON` means Telegram reports the participant as **unmuted**. It does not prove that the person is actively speaking; the participant schema provides the mute state, not a direct speech-activity flag. citeturn0search0

## Setup

1. Create a managed PostgreSQL database (for example, Neon or Supabase), create Telegram API credentials at [my.telegram.org](https://my.telegram.org), create a bot with BotFather, and copy `.env.example` to `.env`. Set `DATABASE_URL` to a URL such as `postgresql://listner:password@db.example.com:5432/listner?sslmode=require`.
2. Set `BOT_CHAT_ID` to the administrator chat ID. Start the worker locally once with `python -m listner.worker` and complete Telethon's interactive user-account sign-in; retain the created session file.
3. Start the durable services:
   ```sh
   docker compose up -d --build
   ```
4. In the bot administrator chat, use `/watch 123456789`, `/unwatch 123456789`, or `/list`. IDs must be numeric Telegram user IDs, not usernames.

## HTTP control API / Vercel

`api/index.py` accepts `GET /` for status, `POST /watch` with `{"telegram_user_id": 123}`, and `POST /unwatch`. Set `Authorization: Bearer $CONTROL_SECRET` when configured.

Deploy the API with `vercel --prod` after setting `DATABASE_URL` and `CONTROL_SECRET` in Vercel environment variables. Give the worker the exact same `DATABASE_URL`, while keeping its Telethon session on persistent storage; Vercel does not run the forever Telethon worker. Never commit `.env`, Telegram API hashes, bot tokens, Telethon session strings, or database credentials.

## Development

```sh
python -m unittest discover -s tests -v
python -m compileall -q listner api
```
