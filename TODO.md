# Wardrive — Pending Items

## Messenger Integration (Slack / Telegram / Discord)
- [ ] Add `SLACK_WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL` to `config/wardrive.conf`
- [ ] Create `supervisor/notifier.py` that posts to configured channels on:
  - Collector state changes (crash, unavailable, reconnect)
  - USB over-current events
  - Power budget shedding events
  - Session start / stop
  - All-collectors-down (critical alert)
- [ ] Subscribe `notifier.py` to `event_bus` at supervisor startup
- [ ] Add `/api/debug/notify/test` endpoint to send a test message to all configured channels
- [ ] Add bot command listener (Telegram / Discord) that accepts:
  - `/status` — collector states + GPS fix + power usage
  - `/restart <collector>` — remote restart
  - `/sdr wideband` / `/sdr rtl433` — toggle SDR mode
  - `/log <collector> [lines]` — tail a collector log

## Live WebUI Improvements
- [ ] Live device-feed panel on dashboard (htmx WebSocket push of `new_device` events)
- [ ] Session export: zip raw capture dir + sliced DB → download
- [ ] Mobile bottom tab bar (`<768px`)
- [ ] Push notification when new session starts (browser Notification API)

## HackRF One (when hardware arrives)
- [ ] Replace stub in `processing/hackrf_scanner.py` with SoapySDR FFT sweep
- [ ] Build GNU Radio flowgraph for spectrum monitoring
- [ ] Wire `hackrf_obs` table inserts into scanner
- [ ] Add HackRF data to map and analytics views

## Hardware / System
- [ ] Test `uhubctl` port-level control on Pi 3B LAN9514 hub
- [ ] Verify `max_usb_current=1` in `/boot/config.txt` increases budget correctly
- [ ] Confirm AP fallback (`rpiwifi2_4ghz`) doesn't appear in Kismet scan results
- [ ] Validate that pi-gen stage-wardrive builds a bootable image end-to-end

## Security / Hardening (when exposing beyond LAN)
- [ ] Add HTTPS with a self-signed cert (or Let's Encrypt if hostname is reachable)
- [ ] Lock down DEBUG_TOKEN to something strong in wardrive.conf on first boot
