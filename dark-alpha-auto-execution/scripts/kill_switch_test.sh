#!/usr/bin/env bash
# Kill Switch 3-trigger test (spec Phase 1 acceptance requirement)
# Run from project root: bash scripts/kill_switch_test.sh

set -e
PYTHONPATH=src

echo "=========================================="
echo " Dark Alpha Kill Switch — 3-Trigger Test"
echo "=========================================="
echo ""

# ---- Test 1: File sentinel ----------------------------------------
echo "Test 1: File sentinel (external touch)"
SENTINEL_1="/tmp/dark-alpha-kill-test-1"
rm -f "$SENTINEL_1"

touch "$SENTINEL_1"
PYTHONPATH=src poetry run python - <<PYEOF
from pathlib import Path
from safety.kill_switch import KillSwitch
ks = KillSwitch(sentinel_path=Path("$SENTINEL_1"))
assert ks.is_active(), "FAIL: is_active() should be True when sentinel file exists"
print("  is_active() = True ✓")
PYEOF

rm -f "$SENTINEL_1"
PYTHONPATH=src poetry run python - <<PYEOF
from pathlib import Path
from safety.kill_switch import KillSwitch
ks = KillSwitch(sentinel_path=Path("$SENTINEL_1"))
assert not ks.is_active(), "FAIL: is_active() should be False after sentinel removed"
print("  is_active() = False after removal ✓")
PYEOF
echo "✓ Test 1 PASSED"
echo ""

# ---- Test 2: Programmatic activate() / deactivate() ---------------
echo "Test 2: Programmatic activate() + deactivate()"
SENTINEL_2="/tmp/dark-alpha-kill-test-2"
rm -f "$SENTINEL_2"

PYTHONPATH=src poetry run python - <<PYEOF
from pathlib import Path
from safety.kill_switch import KillSwitch
ks = KillSwitch(sentinel_path=Path("$SENTINEL_2"))

ks.activate(reason="integration_test")
assert ks.is_active(), "FAIL: not active after activate()"
assert Path("$SENTINEL_2").exists(), "FAIL: sentinel file not created"
print("  activate() → is_active()=True, sentinel created ✓")

ks.deactivate()
assert not ks.is_active(), "FAIL: still active after deactivate()"
assert not Path("$SENTINEL_2").exists(), "FAIL: sentinel file not removed"
print("  deactivate() → is_active()=False, sentinel removed ✓")
PYEOF
echo "✓ Test 2 PASSED"
echo ""

# ---- Test 3: CLI halt command -------------------------------------
echo "Test 3: CLI halt command"
SENTINEL_3="/tmp/dark-alpha-kill-test-3"
rm -f "$SENTINEL_3"

PYTHONPATH=src poetry run python -m cli.main halt \
    --reason "kill_switch_test_3" \
    --sentinel "$SENTINEL_3"

if [ -f "$SENTINEL_3" ]; then
    echo "  sentinel file created by CLI ✓"
else
    echo "✗ FAIL: sentinel file not created by CLI halt command"
    exit 1
fi

PYTHONPATH=src poetry run python - <<PYEOF
from pathlib import Path
from safety.kill_switch import KillSwitch
ks = KillSwitch(sentinel_path=Path("$SENTINEL_3"))
assert ks.is_active(), "FAIL: is_active() should detect CLI-created sentinel"
print("  is_active() detects CLI sentinel ✓")
PYEOF

rm -f "$SENTINEL_3"
echo "✓ Test 3 PASSED"
echo ""

# ---- Summary -------------------------------------------------------
echo "=========================================="
echo " All 3 kill switch tests PASSED ✓"
echo "=========================================="
