"""devin-orchestrator — background-worker fan-out policy for Devin Desktop.

The planner is a *policy engine*, not a classifier: the model supplies
structured signals about a task, and the planner applies deterministic rules
(worker caps, nesting ban, profile selection, collection contract). This
keeps the risky part — limits and safety — in code, and the judgment part —
task decomposition — with the model.
"""

from devin_orchestrator.planner import MAX_WORKERS, WorkerPlan, plan_task

__all__ = ["MAX_WORKERS", "WorkerPlan", "plan_task"]
__version__ = "0.2.0"
