#!/usr/bin/env bash
# verify.sh — headless sanity for the devin-office daemon adapter.
#   bash verify.sh
set -u
cd "$(dirname "$0")"
fails=0

note() { printf '%s\n' "$*"; }
bad()  { printf 'FAIL %s\n' "$*"; fails=$((fails+1)); }
ok()   { printf 'ok   %s\n' "$*"; }

[ -f devin-office.service ] || bad "devin-office.service missing"
if command -v systemd-analyze >/dev/null 2>&1; then
    if systemd-analyze verify --user devin-office.service 2>verify.err; then
        ok "systemd-analyze verify"
    else
        bad "systemd-analyze verify: $(cat verify.err)"
    fi
    rm -f verify.err
else
    grep -q '^ExecStart=/usr/bin/python3 .*daemon\.py --port' devin-office.service || bad "service ExecStart"
    grep -q '^Restart=on-failure' devin-office.service || bad "service Restart"
    grep -q '^WantedBy=default.target' devin-office.service || bad "service Install"
    ok "section lint (systemd-analyze absent)"
fi
# ReadWritePaths must cover BOTH session_locks (flock probe) and the
# checkout dir (kanban.json atomic write) — a missing entry is the
# silent-failure mode this adapter's README warns about.
grep -q 'ReadWritePaths=.*session_locks' devin-office.service || bad "ReadWritePaths lacks session_locks"
grep -q 'ReadWritePaths=.*packages/office' devin-office.service || bad "ReadWritePaths lacks checkout dir"

python3 - <<'PY' || bad "task.xml parse"
import xml.etree.ElementTree as ET
ns = '{http://schemas.microsoft.com/windows/2004/02/mit/task}'
r = ET.parse('devin-office.task.xml').getroot()
assert r.find(f'.//{ns}URI').text == r'\devin-office'
assert r.find(f'{ns}Triggers/{ns}LogonTrigger') is not None
args = r.find(f'{ns}Actions/{ns}Exec/{ns}Arguments').text
assert '__OFFICE_CHECKOUT__' in args and 'daemon.py' in args
limit = r.find(f'{ns}Settings/{ns}ExecutionTimeLimit').text
assert limit == 'PT0S', limit
PY
ok "task.xml parse + URI/trigger/resident-settings"

for s in install-task.ps1 remove-task.ps1; do
    [ -f "$s" ] || bad "$s missing"
    grep -q "TaskName = 'devin-office'" "$s" || bad "$s TaskName mismatch"
done
grep -q '__OFFICE_CHECKOUT__' install-task.ps1 || bad "install-task.ps1 token patch missing"
ok "ps1 task-name consistency"

[ "$fails" -eq 0 ] && note "PASS" || { note "$fails check(s) failed"; exit 1; }
