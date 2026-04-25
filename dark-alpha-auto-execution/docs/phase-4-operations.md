# Phase 4 — Shadow Mode Operations

Phase 4 is the **≥60 calendar-day shadow-mode run** required by Gate 1.
Code stabilizes in Phase 3; Phase 4 is primarily an operations phase.

## What runs where

| Process                  | How it starts                      | Purpose                                             |
| ------------------------ | ---------------------------------- | --------------------------------------------------- |
| Signal receiver (uvicorn) | `poetry run uvicorn signal_adapter.receiver:app --host 127.0.0.1 --port 8765` | Accept Dark Alpha postbacks, run strategy pipeline |
| Supervisor loop           | `poetry run dark-alpha run`        | Mark-to-market open positions, write daily snapshot |
| Telegram bot (optional)   | `poetry run dark-alpha telegram`   | Remote status + kill switch (Phase 8)               |
| Dark Alpha                | (external, unchanged)              | `POSTBACK_URL=http://127.0.0.1:8765/signal`         |

## Quickest start (tmux)

```bash
poetry run dark-alpha doctor          # pre-flight checks
./scripts/start-all.sh --tg           # receiver + supervisor + telegram bot
# detach: Ctrl-B D; reattach: tmux attach -t dark-alpha
```

Both processes must run 7×24. Recommended hosts: a small always-on VPS,
systemd/launchd-managed, logs shipped off-host.

## launchd plist (macOS, recommended for single-dev setups)

`~/Library/LaunchAgents/com.darkalpha.supervisor.plist`:

```xml
<plist version="1.0">
<dict>
  <key>Label</key><string>com.darkalpha.supervisor</string>
  <key>WorkingDirectory</key><string>/Users/YOU/dark-alpha-auto-execution</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/poetry</string>
    <string>run</string>
    <string>dark-alpha</string>
    <string>run</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/dark-alpha-supervisor.out</string>
  <key>StandardErrorPath</key><string>/tmp/dark-alpha-supervisor.err</string>
</dict>
</plist>
```

Load with `launchctl load ~/Library/LaunchAgents/com.darkalpha.supervisor.plist`.

## systemd unit (Linux)

`/etc/systemd/system/dark-alpha-supervisor.service`:

```ini
[Unit]
Description=Dark Alpha shadow-mode supervisor
After=network-online.target

[Service]
Type=simple
User=dark-alpha
WorkingDirectory=/opt/dark-alpha-auto-execution
ExecStart=/usr/local/bin/poetry run dark-alpha run
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Daily operations checklist

- [ ] Verify both processes up (`launchctl list | grep darkalpha` or `systemctl status`)
- [ ] `dark-alpha status` — confirm kill switch clear, no breakers tripped
- [ ] `dark-alpha report daily` — writes yesterday's snapshot (run after 00:00 UTC)
- [ ] Spot-check `audit_log` for unexpected reject reasons
- [ ] Confirm Dark Alpha postback hits are being accepted (receiver logs)

## Weekly operations

- [ ] `dark-alpha report weekly` — generates `reports/weekly-YYYY-WW.md`
- [ ] Review win rate, expectancy, drawdown vs prior week
- [ ] If any config change: **restart** both processes; never hot-reload

## Emergency procedures

| Situation                       | Action                                                           |
| ------------------------------- | ---------------------------------------------------------------- |
| Unexpected strategy behaviour   | `dark-alpha halt --reason "investigating ..."`                   |
| System misbehaving globally     | `touch /tmp/dark-alpha-kill` (sentinel, any process can do this) |
| Back to normal                  | `dark-alpha resume` (confirms)                                   |

## Ending Phase 4

Phase 4 ends when you produce `docs/shadow-mode-report.md` using the
template in this directory. The Gate 1 decision is **yours**:

- Expectancy positive, risk well-behaved → proceed to Phase 5 (live broker)
- Expectancy at/below zero → back to Phase 2 or shut the system down

Do not skip the decision. Section 10 of the spec exists for this moment.
