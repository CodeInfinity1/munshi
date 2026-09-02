"""Execution adapter interface.

Every action that touches the outside world goes through one of these. The
orchestrator, the policy engine and the reasoning layer are all adapter-agnostic,
so swapping the simulator for live Razorpay changes exactly one construction site.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class UnsupportedInTestMode(RuntimeError):
    """Raised when an action cannot be genuinely performed against Razorpay test mode.

    Deliberately loud. The alternative -- quietly simulating the call and reporting
    it as if Razorpay had run it -- is the exact failure mode this project exists to
    avoid, so the executor records the action as `not_executed` with this reason
    rather than inventing an outcome.
    """


@dataclass(slots=True)
class ActionResult:
    outcome: str                      # success | failed | pending | not_executed
    detail: dict = field(default_factory=dict)
    recovered_paise: int = 0
    provider_ref: str | None = None   # pay_xxx / plink_xxx returned by the provider
    #: True when the money movement was produced by the simulator rather than by a
    #: real payment rail. Carried into the ledger and rendered in the UI.
    simulated: bool = True


class Adapter(Protocol):
    name: str

    def execute(self, action_type: str, case: dict, params: dict, now: int) -> ActionResult: ...
