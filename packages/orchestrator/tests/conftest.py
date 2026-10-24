import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.fixture(autouse=True)
def _neutral_signals(monkeypatch):
    """Plans under test see an unmeasurable host, not the CI machine's RAM.

    Tests that exercise real signal semantics inject ``signals=``
    explicitly, which bypasses collection entirely.
    """
    from devin_orchestrator import planner

    monkeypatch.setattr(
        planner, "collect_signals", lambda env=None: {"ram_pressure": None}
    )
