#!/usr/bin/env bash
# verify.sh — headless sanity for the devin-bridge-intake daemon adapter.
# Lints the systemd units, parses the task XML, and cross-checks that the
# ps1 scripts reference the shipped task name. Run from this directory:
#   bash verify.sh
set -u
cd "$(dirname "$0")"
fails=0

note() { printf '%s\n' "$*"; }
bad()  { printf 'FAIL %s\n' "$*"; fails=$((fails+1)); }
ok()   { printf 'ok   %s\n' "$*"; }

# --- systemd units -------------------------------------------------------
for u in devin-bridge-intake.service devin-bridge-intake.path devin-bridge-intake.timer; do
    [ -f "$u" ] || bad "$u missing"
done
if command -v systemd-analyze >/dev/null 2>&1; then
    if systemd-analyze verify --user devin-bridge-intake.service devin-bridge-intake.path devin-bridge-intake.timer 2>verify.err; then
        ok "systemd-analyze verify"
    else
        bad "systemd-analyze verify: $(cat verify.err)"
    fi
    rm -f verify.err
else
    for u in devin-bridge-intake.service devin-bridge-intake.path devin-bridge-intake.timer; do
        grep -q '^\[Unit\]' "$u" || bad "$u lacks [Unit]"
    done
    # Unit= names the unit a trigger activates — valid in [Path]/[Timer],
    # never in the service's own [Unit] section. Require it only where
    # systemd expects the binding, pointed at this service.
    for u in devin-bridge-intake.path devin-bridge-intake.timer; do
        grep -q '^Unit=devin-bridge-intake\.service' "$u" || bad "$u lacks Unit=devin-bridge-intake.service"
    done
    grep -q '^ExecStart=/usr/bin/env devin-bridge intake' devin-bridge-intake.service || bad "service ExecStart"
    grep -q '^PathExistsGlob=.*inbox/\*\.json' devin-bridge-intake.path || bad "path glob"
    ok "section lint (systemd-analyze absent)"
fi

# --- task.xml ------------------------------------------------------------
python3 - <<'PY' || bad "task.xml parse"
import xml.etree.ElementTree as ET, sys
ns = '{http://schemas.microsoft.com/windows/2004/02/mit/task}'
r = ET.parse('devin-bridge-intake.task.xml').getroot()
assert r.find(f'.//{ns}URI').text == r'\devin-bridge-intake'
assert r.find(f'{ns}Actions/{ns}Exec/{ns}Arguments').text == '/c devin-bridge intake'
assert r.find(f'{ns}Triggers/{ns}LogonTrigger') is not None
PY
ok "task.xml parse + URI/action/trigger"

# --- ps1 <-> task name consistency --------------------------------------
for s in install-task.ps1 remove-task.ps1; do
    [ -f "$s" ] || bad "$s missing"
    grep -q "TaskName = 'devin-bridge-intake'" "$s" || bad "$s TaskName mismatch"
done
grep -q 'devin-bridge-intake.task.xml' install-task.ps1 || bad "install-task.ps1 XML reference"
ok "ps1 task-name consistency"

[ "$fails" -eq 0 ] && note "PASS" || { note "$fails check(s) failed"; exit 1; }
