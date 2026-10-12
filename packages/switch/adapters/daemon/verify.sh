#!/usr/bin/env bash
# verify.sh — headless sanity for the devin-switch-drift daemon adapter.
#   bash verify.sh
set -u
cd "$(dirname "$0")"
fails=0

note() { printf '%s\n' "$*"; }
bad()  { printf 'FAIL %s\n' "$*"; fails=$((fails+1)); }
ok()   { printf 'ok   %s\n' "$*"; }

for u in devin-switch-drift.service devin-switch-drift.timer; do
    [ -f "$u" ] || bad "$u missing"
done
if command -v systemd-analyze >/dev/null 2>&1; then
    if systemd-analyze verify --user devin-switch-drift.service devin-switch-drift.timer 2>verify.err; then
        ok "systemd-analyze verify"
    else
        bad "systemd-analyze verify: $(cat verify.err)"
    fi
    rm -f verify.err
else
    for u in devin-switch-drift.service devin-switch-drift.timer; do
        grep -q '^\[Unit\]' "$u" || bad "$u lacks [Unit]"
    done
    grep -q '^ExecStart=/usr/bin/env devin-switch doctor' devin-switch-drift.service || bad "service ExecStart"
    grep -q '^OnCalendar=' devin-switch-drift.timer || bad "timer OnCalendar"
    ok "section lint (systemd-analyze absent)"
fi

python3 - <<'PY' || bad "task.xml parse"
import xml.etree.ElementTree as ET
ns = '{http://schemas.microsoft.com/windows/2004/02/mit/task}'
r = ET.parse('devin-switch-drift.task.xml').getroot()
assert r.find(f'.//{ns}URI').text == r'\devin-switch-drift'
args = r.find(f'{ns}Actions/{ns}Exec/{ns}Arguments').text
assert 'devin-switch doctor' in args and 'drift.log' in args
assert r.find(f'{ns}Triggers/{ns}LogonTrigger') is not None
assert r.find(f'{ns}Triggers/{ns}CalendarTrigger') is not None
PY
ok "task.xml parse + URI/action/triggers"

for s in install-task.ps1 remove-task.ps1; do
    [ -f "$s" ] || bad "$s missing"
    grep -q "TaskName = 'devin-switch-drift'" "$s" || bad "$s TaskName mismatch"
done
ok "ps1 task-name consistency"

[ "$fails" -eq 0 ] && note "PASS" || { note "$fails check(s) failed"; exit 1; }
