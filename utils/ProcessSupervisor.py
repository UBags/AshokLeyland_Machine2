# Copyright (c) 2025 Uddipan Bagchi. All rights reserved.
# See LICENSE in the project root for license information.

"""
ProcessSupervisor: MainProgram starts the server processes (logging, camera, QR code, IO, frontend, heartbeat, DB);
this restarts one that has died, so that a crash does not leave the station without, say, its camera server until
someone restarts the whole application.

  * every supervisor.check.interval.sec (2 s) each process is checked;
  * one found dead is restarted (same function, same arguments) after a short grace period - unless a stop command
    is on the stop queue (Exit from the frontend, or MainProgram's own stop), then nothing is restarted any more;
  * at most supervisor.max.restarts.per.hour (10) restarts of one server within an hour; after that it is left down
    and that is logged (a server that dies at once every time would otherwise be restarted for ever);
  * the list of process ids MainProgram uses at shutdown is kept up to date;
  * supervisor.restart.dead.processes = False turns it off.

While a server is down the others carry on as they would without it (IOServer rules: camera down -> pictures NOT OK;
DB down -> cycle finished, next one held; frontend / heartbeat server down -> cycle aborted, next one held).
"""

import threading
import time
from collections import deque
from typing import Callable, Dict, List, Optional


class ProcessSupervisor:

    GRACE_SECS = 2.0          # a process found dead: wait this long (a stop command may be on its way), then decide

    def __init__(self, processFactory: Callable, isStopping: Callable[[], bool], pidList: Optional[List[int]] = None,
                 checkInterval: float = 2.0, maxRestartsPerHour: int = 10, log: Callable[[str], None] = print):
        """processFactory(target, args) -> a started process (multiprocessing.Process with .is_alive(), .pid,
        .exitcode); isStopping() -> True once a stop command was given."""
        self._factory = processFactory
        self._isStopping = isStopping
        self._pids = pidList if pidList is not None else []
        self._interval = checkInterval
        self._maxPerHour = maxRestartsPerHour
        self._log = log
        self._entries: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.restarts: Dict[str, int] = {}

    def add(self, name: str, target: Callable, args: tuple, process, restartArgs: Optional[tuple] = None) -> None:
        """restartArgs: the arguments for a restart, when not the same as at the start (the camera server is restarted
        with its cycle state file, started without it)."""
        with self._lock:
            self._entries[name] = {"target": target, "args": args, "process": process,
                                   "restartArgs": restartArgs if restartArgs is not None else args,
                                   "restarts": deque(), "givenUp": False}
            self.restarts.setdefault(name, 0)

    def process(self, name: str):
        with self._lock:
            entry = self._entries.get(name)
            return entry["process"] if entry else None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="Process Supervisor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()

    def _run(self) -> None:
        while not self._stopped.wait(self._interval):
            try:
                self.checkOnce()
            except Exception as e:
                self._log(f"Process supervisor: {e}")

    def checkOnce(self) -> None:
        """One check of every process (the loop calls this every checkInterval)."""
        if self._isStopping():
            self._stopped.set()
            return
        with self._lock:
            dead = [(name, entry) for name, entry in self._entries.items()
                    if not entry["givenUp"] and not entry["process"].is_alive()]
        for name, entry in dead:
            if self._stopped.wait(self.GRACE_SECS) or self._isStopping():
                self._stopped.set()
                return
            old = entry["process"]
            now = time.monotonic()
            while entry["restarts"] and now - entry["restarts"][0] > 3600:
                entry["restarts"].popleft()
            if len(entry["restarts"]) >= self._maxPerHour:
                entry["givenUp"] = True
                self._log(f"{name} died again (exit code {old.exitcode}); restarted {len(entry['restarts'])} times in "
                          f"the last hour - NOT restarted any more. Restart the application.")
                continue
            self._log(f"{name} has died (exit code {old.exitcode}); restarting it")
            try:
                new = self._factory(entry["target"], entry["restartArgs"])
            except Exception as e:
                self._log(f"{name} could not be restarted: {e}")
                entry["restarts"].append(now)
                continue
            entry["restarts"].append(now)
            self.restarts[name] = self.restarts.get(name, 0) + 1
            with self._lock:
                entry["process"] = new
                if old.pid in self._pids:
                    self._pids.remove(old.pid)
                self._pids.append(new.pid)
            self._log(f"{name} restarted (pid {new.pid}; restart {self.restarts[name]})")
