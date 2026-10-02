# Listner

Listner monitors watched numeric Telegram user IDs through a **Telethon user account**. It sends alerts when Telegram reports that a watched user comes online or goes offline, and when they join or leave an active shared group voice/video call. A separate Telegram Bot API process and a small Vercel-compatible control API manage the watch list.

## Architecture and guarantees

- The worker discovers shared groups with Telegram `messages.GetCommonChats` for each watched account, then checks channel full-info for active group calls.
- Each active `(group_id, call_id)` is protected by SQLite's `PRIMARY KEY (group_id, call_id)` plus an atomic conditional UPSERT lease. Only its successful holder polls participants, renews its lease before each approximately two-second poll, and reports presence transitions.
- Join state and alerts are durable in SQLite, so only transitions produce alerts; leave alerts include the measured duration.
- User-status alerts are likewise durable: a first observed online status and every later online/offline transition generate one alert each. An initial offline status establishes a baseline so a worker restart does not send a false offline alert. Telegram only supplies user-status updates that the signed-in account is permitted to see, so privacy settings and account relationship can limit these alerts.
- The worker needs a persistent disk/volume. **Vercel's filesystem is ephemeral**: deploy the control API there only if `DATABASE_PATH` points to shared persistent storage (or use it only as a stateless control deployment paired with a shared database adapter). The included SQLite worker is intended for Docker with its named volume.

## Setup

1. Create Telegram API credentials at [my.telegram.org](https://my.telegram.org), create a bot with BotFather, and copy `.env.example` to `.env`.
2. Set `BOT_CHAT_ID` to the administrator chat ID. Start the worker locally once with `python -m listner.worker` and complete Telethon's interactive user-account sign-in; retain the created session file.
3. Start the durable services:
   ```sh
   docker compose up -d --build
   ```
4. In the bot administrator chat, use `/watch 123456789`, `/unwatch 123456789`, or `/list`. IDs must be numeric Telegram user IDs, not usernames.

## HTTP control API / Vercel

`api/index.py` accepts `GET /` for status, `POST /watch` with `{"telegram_user_id": 123}`, and `POST /unwatch`. Set `Authorization: Bearer $CONTROL_SECRET` when configured.

Deploy the API with `vercel --prod` after setting `CONTROL_SECRET` and a suitable database configuration in Vercel environment variables. Continue deploying the Docker worker somewhere with persistent storage; Vercel does not run the forever Telethon worker.

## Development

```sh
python -m unittest discover -s tests -v
python -m compileall -q listner api
```
