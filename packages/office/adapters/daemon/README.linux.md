# devin-office — systemd user service (Linux)

> New units — the 7-day no-silent-failure observation window started
> 2026-10-12 and has not completed; see [README.md](README.md).

**`devin-office.service`** (`Type=simple`, `Restart=on-failure`) runs
`python3 .../src/devin_office/daemon.py --port 8788` — the module
self-bootstraps its imports, so a bare checkout still works, no install
needed — and keeps the dashboard up for as long as you are logged in.
After `pip install -e packages/office` (or `uv tool install devin-office`),
`ExecStart` can simply be `devin-office --port 8788`. There is no timer —
a resident server just needs `WantedBy=default.target`.

The service is hardened for a read-mostly loopback server:
`ProtectSystem=strict` + `ProtectHome=read-only` with two punched holes —
`session_locks/` needs an `O_RDWR` handle for the flock probe that tells
live sessions apart (a read-only open would make every session report a
false "held" lock), and **the checkout dir itself** stays writable
because `POST /api/kanban` persists closed-state to `kanban.json` inside
`src/devin_office/` (atomic `.tmp`+rename — a read-only checkout makes the
kanban API return HTTP 400). `RestrictAddressFamilies` + `IPAddressAllow=localhost`
/`IPAddressDeny=any` confine all I/O to loopback: the daemon binds
127.0.0.1 and its eco probes only hit 127.0.0.1 health endpoints.

## Install

```bash
# 1. Point ExecStart AND the second ReadWritePaths entry at YOUR
#    devin-control checkout — the shipped paths are
#    %h/devin/devin-ecosystem/devin-control/packages/office (ReadWritePaths)
#    plus .../packages/office/src/devin_office/daemon.py (ExecStart).
#    (ReadWritePaths covers the checkout so kanban.json can be written;
#    moving ExecStart without it silently breaks the kanban API.)
$EDITOR devin-office.service

# 2. Install and start
mkdir -p ~/.config/systemd/user
cp devin-office.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now devin-office.service
```

Optional overrides (`OFFICE_DATA_DIR`, `OFFICE_CONF_DIR`,
`OFFICE_JEV_DB`, `OFFICE_HEARTBEAT_FILE`, `OFFICE_CORS_ORIGIN`) go in an
`Environment=` line or a `devin-office.service.d/override.conf` drop-in.
If `OFFICE_ECO_URL` ever points at a non-loopback collector, remove the
`IPAddressDeny=any` line or the probe will be denied.

To reach the dashboard while logged out, enable lingering
(`loginctl enable-linger $USER`) — the service stays alive without a
session.

## Smoke check — did it run without silent failure?

```bash
systemctl --user status devin-office.service
journalctl --user -u devin-office.service -n 20 --no-pager
# Expect: "devin-office smoke daemon -> http://localhost:8788"

curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8788/api/state   # 200
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"id":"nonexistent","action":"close"}' http://localhost:8788/api/kanban
# 4xx for the bogus id is fine — HTTP 400 mentioning read-only/EROFS
# means ReadWritePaths lost the checkout entry.
```

A crash loop shows as `activating (auto-restart)`; check `NRestarts`
(`systemctl --user show devin-office.service -p NRestarts`) if the port
stays closed. A common first-run failure is an `EROFS`/`EACCES` on
`session_locks` — meaning the `ReadWritePaths` line was edited out.

## Headless check

`./verify.sh` in this directory lints the unit (`systemd-analyze
verify` when available), parses the task XML, and cross-checks the ps1
scripts — the same checks `tests/test_daemon_adapter.py` runs in CI.

## Uninstall

```bash
systemctl --user disable --now devin-office.service
rm ~/.config/systemd/user/devin-office.service
systemctl --user daemon-reload
```

The checkout and Devin's stores are left untouched (the daemon never
writes to them anyway).
