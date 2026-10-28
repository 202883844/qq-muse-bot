# Restore

1. Unpack the backup into `~/workspace/qqbot-ws/` (backups never contain `.env`).
2. If the package includes `SHA256SUMS`, verify: `sha256sum -c SHA256SUMS`
3. Rebuild the venv: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
4. Restore `.env` out-of-band, then `chmod 600 .env` (confirm `BOT_MODE=muse`).
5. Self-check: `.venv/bin/python bot.py --dry-run`
6. Render and install the unit: `.venv/bin/python ensure_service.py --install`
7. Start: `systemctl enable --now qqbot-ws`
8. Verify in QQ: send `/ping`, expect `pong`; send a normal message and confirm a reply is delivered and the queue file is archived to `queue/done/`.

Queue directories are recreated empty by the code (`ensure_queue_dirs`).
