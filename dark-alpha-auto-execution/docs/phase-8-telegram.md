# Phase 8 — Telegram Bot

Remote monitoring + kill switch. Independent of the money path.

## Setup

1. Talk to `@BotFather` on Telegram → create bot → copy the token.
2. Message your new bot once, then visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your numeric `chat.id`.
3. In `.env`:

   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=<your-chat-id>
   # Or, for multiple admins:
   # TELEGRAM_ADMIN_CHAT_IDS=111,222,333
   ```

4. Launch: `poetry run dark-alpha telegram` (or use `./scripts/start-all.sh --tg`).

## Commands

| Command          | Effect                                            |
| ---------------- | ------------------------------------------------- |
| `/help`          | List commands                                     |
| `/status`        | Kill switch + breakers + open positions + today PnL |
| `/positions`     | Each open position (entry/stop/TP/qty)            |
| `/pnl_today`     | Today's trades and gross/fees/net                 |
| `/breakers`      | Circuit breaker states                            |
| `/halt <reason>` | Activate kill switch (creates sentinel file)      |
| `/resume`        | Clear kill switch                                 |

## Security

- **Whitelist enforced.** Messages from non-admin chat IDs are silently ignored
  (logged but no reply, so a scanning bot can't probe the command surface).
- **No write path to real orders.** The bot only reads DB state and toggles the
  kill switch sentinel file. It cannot place or close orders directly.
- **Token is secret.** Do not commit `.env`. If leaked, revoke via `@BotFather`.

## Reliability

- Long-poll with 30 s timeout. Network drops are logged and retried.
- Handler exceptions reply `handler error: ...` instead of crashing the loop.
- On SIGINT/SIGTERM the bot finishes the current poll and exits cleanly.
