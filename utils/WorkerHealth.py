# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
WorkerHealth: whether a server's WORK threads are still working, for its heartbeat.

A server's heartbeat used to be sent by a thread of its own: it said ALIVE as long as that thread ran, even when the
thread doing the work (the camera's assessment, the DB server's inserts, the IOServer's read / write loops, the
frontend's GUI) had died or was stuck. Each server now registers its work threads here; its heartbeat sends DEAD
while any of them is not alive, or has not been round its loop for heartbeat.work.loop.max.silence.sec (60 s).

  watch = WorkerWatch("Camera server")
  watch.add("assessment", converterThread)                       # a GenericQueueProcessor: its lastLoopAt is used
  watch.add("read loop", lambda: io.continuousReadLoop, lastTick=lambda: io.lastReadLoopAt)
  problems = watch.problems()       # [] = all working; else e.g. ["assessment: no loop for 75 s"]

A problem (and its end) is logged once by problemsLogged().
"""

import threading
import time
from typing import Any, Callable, List, Optional


def maxLoopSilenceSecs() -> float:
    try:
        from Configuration import CosThetaConfigurator
        return CosThetaConfigurator.getInstance().getWorkLoopMaxSilence()
    except Exception:
        return 60.0


class WorkerWatch:

    def __init__(self, serverName: str):
        self.serverName = serverName
        self._entries: list = []
        self._lock = threading.Lock()
        self._lastReported: List[str] = []

    def add(self, label: str, thread: Any, lastTick: Optional[Callable[[], Optional[float]]] = None) -> None:
        """thread: a Thread (or a callable returning the current one, for a thread that is replaced). lastTick: a
        callable returning time.monotonic() of its last pass round the loop; default the thread's lastLoopAt
        (GenericQueueProcessor) if it has one; None = only is_alive() is checked."""
        with self._lock:
            self._entries.append((label, thread, lastTick))

    def problems(self, maxSilence: Optional[float] = None) -> List[str]:
        limit = maxLoopSilenceSecs() if maxSilence is None else maxSilence
        now = time.monotonic()
        found = []
        with self._lock:
            entries = list(self._entries)
        for label, thread, lastTick in entries:
            try:
                t = thread() if callable(thread) and not isinstance(thread, threading.Thread) else thread
                if t is None or not t.is_alive():
                    found.append(f"{label}: thread not running")
                    continue
                tick = lastTick() if lastTick is not None else getattr(t, "lastLoopAt", None)
                if tick is not None and now - tick > limit:
                    found.append(f"{label}: no loop for {now - tick:.0f} s")
            except Exception as e:
                found.append(f"{label}: not checked ({e})")
        return found

    def problemsLogged(self, log: Callable[[str], None] = print, maxSilence: Optional[float] = None) -> List[str]:
        """problems(), and a log line when they change (a new problem, or all working again)."""
        found = self.problems(maxSilence)
        if found != self._lastReported:
            if found:
                log(f"{self.serverName}: work thread(s) not working - heartbeat DEAD: {'; '.join(found)}")
            else:
                log(f"{self.serverName}: work threads working again")
            self._lastReported = list(found)
        return found
