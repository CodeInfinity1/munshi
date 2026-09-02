"""Virtual clock.

Recovery plays out over days: an insufficient-funds retry that fires 36 hours
later is a different action from one that fires immediately. To evaluate that
honestly in a batch run we need to advance time, not pretend it passed.

`VirtualClock` is the batch clock: the orchestrator advances it in steps across
the recovery window, so scheduled actions actually come due. `SystemClock` is
what the live webhook path uses. Both expose the same two methods, so nothing
downstream knows which one it is holding.
"""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo


class SystemClock:
    virtual = False

    def now(self) -> int:
        return int(time.time())

    def advance(self, seconds: int) -> int:  # no-op; wall time advances on its own
        return self.now()


class VirtualClock:
    virtual = True

    def __init__(self, start: int):
        self._t = int(start)

    def now(self) -> int:
        return self._t

    def advance(self, seconds: int) -> int:
        self._t += int(seconds)
        return self._t


def local(ts: int, tz: str) -> datetime:
    return datetime.fromtimestamp(ts, ZoneInfo(tz))


def local_hour(ts: int, tz: str) -> int:
    return local(ts, tz).hour
