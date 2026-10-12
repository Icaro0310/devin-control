# devin-office — systemd user service (Linux)

> New units — the 7-day no-silent-failure observation window started
> 2026-10-12 and has not completed; see [README.md](README.md).

**`devin-office.service`** (`Type=simple`, `Restart=on-failure`) runs
`python3 .../daemon.py --port 8788` and keeps the dashboard up for as
long as you are logged in. There is no timer — a resident server just
needs `WantedBy=default.target`.

The service is hardened for a read-only loopback server:
`ProtectSystem=strict` + `ProtectHome=read-only` with one punched hole —
`session_locks/` needs an `O_RDWR` handle for the flock probe that tells
live sessions apart (a read-only open would make every session report a
false "held" lock). `RestrictAddressFamilies` + `IPAddressAllow=localhost`
/`IPAddressDeny=any` confine all I/O to loopback: the daemon binds
127.0.0.1 and its eco probes only hit 127.0.0.1 health endpoints.

## Install

```bash
# 1. Point ExecStart at YOUR devin-control checkout — the shipped path is
#    %h/devin/devin-ecosystem/devin-control/packages/office.
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
```

A crash loop shows as `activating (auto-restart)`; check `NRestarts`
(`systemctl --user show devin-office.service -p NRestarts`) if the port
stays closed. A common first-run failure is an `EROFS`/`EACCES` on
`session_locks` — meaning the `ReadWritePaths` line was edited out.

## Uninstall

```bash
systemctl --user disable --now devin-office.service
rm ~/.config/systemd/user/devin-office.service
systemctl --user daemon-reload
```

The checkout and Devin's stores are left untouched (the daemon never
writes to them anyway).
