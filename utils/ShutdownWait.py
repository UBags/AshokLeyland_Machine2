# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
MainProgram's wait between the stop command and ending the server processes (CHANGES 157).

The DB server drains fe2db.q after the stop command (after the stop a failed insert is written to FailedRecords/ at
once, without a retry) and then sends its stopped response. MainProgram waits for that response - at least
mainprogram.stop.min.wait.sec (the other servers' time to act on the stop), at most mainprogram.db.drain.wait.max.sec -
and stops waiting early if the DB server process is no longer running. A record still on the queue when the servers
are ended is lost: the next start flushes Redis.
"""

import time
from typing import Callable, Optional, Tuple


def waitForDatabaseServer(isDrained: Callable[[], bool], process: Callable[[], Optional[object]],
                          minimumWait: float, maximumWait: float, poll: float = 0.1,
                          clock: Callable[[], float] = time.monotonic,
                          sleep: Callable[[float], None] = time.sleep) -> Tuple[str, float]:
    """Wait for the DB server after the stop command. isDrained(): its stopped response is there; process(): its
    multiprocessing.Process (None = not known). Returns (outcome, seconds waited): outcome "drained", "not running"
    (the process ended without the response) or "timed out"."""
    maximumWait = max(maximumWait, minimumWait)
    start = clock()
    outcome = None
    while True:
        elapsed = clock() - start
        if outcome is None:
            try:
                if isDrained():
                    outcome = "drained"
            except Exception:
                pass
        if outcome is None:
            try:
                p = process()
                if p is not None and not p.is_alive():
                    outcome = "not running"
            except Exception:
                pass
        if outcome is not None and elapsed >= minimumWait:
            return outcome, elapsed
        if elapsed >= maximumWait:
            return outcome or "timed out", elapsed
        sleep(poll)
