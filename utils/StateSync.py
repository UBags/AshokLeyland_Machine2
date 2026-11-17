"""
StateSync - phase A of "one source of truth for the cycle state" (CHANGES 108).

The IOServer is the master: it publishes every state change (utils/RedisUtils.publishIOServerState). The frontend
still walks its own states as before (no behaviour change in phase A) and, in a worker of its own, compares its state
with the IOServer's. A difference that lasts longer than `frontend.state.sync.grace.sec` (5 s) is logged, once, as a
MISMATCH, and again when it ends (RESOLVED). Every log line starts with "STATE-SYNC", so

    findstr /C:"STATE-SYNC" logs\\*.log        (Windows)      grep STATE-SYNC logs/*.log      (Linux)

lists every case. Lines:

    STATE-SYNC MISMATCH #n  frontend <state> vs IOServer <state> for <s> s (frontend should be one of [...]); ...
    STATE-SYNC RESOLVED #n  after <s> s: ...
    STATE-SYNC GAP          IOServer state messages <a> -> <b>: <k> missing (published but not received, or not
                            published - the IOServer's audit line "STATE ... (seq N, NOT published)" tells which)
    STATE-SYNC IOSERVER-RESTART   pid <a> -> <b>
    STATE-SYNC CYCLE <id> completed / aborted in <state>: in sync | <n> MISMATCH(ES), longest <s> s
    STATE-SYNC START        first IOServer state seen

Why a grace period and a set of states per IOServer state: the two never change at the same moment. The frontend
moves to the next tile when the camera's OK reaches it; the IOServer moves only after the PLC handshake (about
0.5-1 s later). After a value or press event the frontend may be one tile ahead too. Until CHANGES 123 the
frontend stayed at the free-rotation torque for frontend.free.rotation.torque.display.sec after the cycle while the
IOServer was already back at the QR code (for that case the display time, plus 1 s, is added to the grace period);
since then it returns to the QR code at once (only the tiles stay on screen), so that allowance no longer comes
into play. So for each
IOServer state the frontend states it may show are listed (FRONTEND_STATES_FOR), and a difference must also last
longer than the grace period before it counts.
"""
import time
from typing import Callable, Dict, FrozenSet, Optional

from statemachine.StateMachine import MachineState as MS

# For each IOServer state: the frontend states that agree with it (the frontend shows only the READ states of the
# checks, and the value / event states it waits in; it is one step ahead while the IOServer finishes a handshake).
FRONTEND_STATES_FOR: Dict[MS, FrozenSet[MS]] = {
    MS.READ_QR_CODE: frozenset({MS.READ_QR_CODE}),
    MS.WRITE_QR_CODE: frozenset({MS.READ_QR_CODE, MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE}),
    MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE}),
    MS.WRITE_RESULT_OF_CHECKING_KNUCKLE: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE,
                                                    MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING}),
    MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING}),
    MS.WRITE_RESULT_OF_CHECKING_HUB_AND_BOTTOM_BEARING: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING,
                                                                   MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING}),
    MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING}),
    MS.WRITE_RESULT_OF_CHECKING_TOP_BEARING: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING,
                                                        MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER}),
    MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER}),
    # after the nut / washer OK the frontend waits for torque 1 in READ_TIGHTENING_TORQUE_1 (it has no "_DONE" state)
    MS.WRITE_RESULT_OF_CHECKING_NUT_AND_PLATEWASHER: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER,
                                                                MS.READ_TIGHTENING_TORQUE_1}),
    MS.READ_TIGHTENING_TORQUE_1_DONE: frozenset({MS.READ_TIGHTENING_TORQUE_1}),
    # torque 1 value sent: the frontend goes on to the bung check (or the split pin check, when bypassed)
    MS.READ_TIGHTENING_TORQUE_1: frozenset({MS.READ_TIGHTENING_TORQUE_1,
                                            MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_COMPONENT_PRESS,
                                            MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER}),
    MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_COMPONENT_PRESS: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_COMPONENT_PRESS}),
    MS.WRITE_RESULT_OF_CHECKING_BUNG_FOR_COMPONENT_PRESS: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_COMPONENT_PRESS,
                                                                     MS.READ_COMPONENT_PRESS_DONE}),
    MS.READ_COMPONENT_PRESS_DONE: frozenset({MS.READ_COMPONENT_PRESS_DONE, MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNG}),
    MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNG: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNG}),
    MS.WRITE_RESULT_OF_CHECKING_NO_BUNG: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNG, MS.READ_TIGHTENING_TORQUE_2}),
    MS.READ_TIGHTENING_TORQUE_2_DONE: frozenset({MS.READ_TIGHTENING_TORQUE_2}),
    MS.READ_TIGHTENING_TORQUE_2: frozenset({MS.READ_TIGHTENING_TORQUE_2, MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER}),
    MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER}),
    MS.WRITE_RESULT_OF_CHECKING_SPLITPIN_AND_WASHER: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER,
                                                                MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP}),
    MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP}),
    MS.WRITE_RESULT_OF_CHECKING_CAP: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP,
                                                MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_CAP_PRESS}),
    MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_CAP_PRESS: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_CAP_PRESS}),
    MS.WRITE_RESULT_OF_CHECKING_BUNG_FOR_CAP_PRESS: frozenset({MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_CAP_PRESS,
                                                               MS.READ_CAP_PRESS_DONE}),
    MS.READ_CAP_PRESS_DONE: frozenset({MS.READ_CAP_PRESS_DONE, MS.READ_FREE_ROTATION_TORQUE_1}),
    MS.READ_FREE_ROTATION_TORQUE_1_DONE: frozenset({MS.READ_FREE_ROTATION_TORQUE_1}),
    MS.READ_FREE_ROTATION_TORQUE_1: frozenset({MS.READ_FREE_ROTATION_TORQUE_1, MS.READ_QR_CODE}),
}


def _name(state) -> str:
    try:
        s = MS(int(state))
        return f"{s.name}({int(s)})"
    except Exception:
        return str(state)


def _clock(wallTime: Optional[float]) -> str:
    if not wallTime:
        return "?"
    return time.strftime("%H:%M:%S", time.localtime(wallTime)) + f".{int((wallTime % 1) * 1000):03d}"


class StateSyncMonitor:
    """Compares the frontend's state with the IOServer's latest published state. Feed it the IOServer snapshots as
    they arrive (onIOServerStates) and call check() with the frontend's state a few times a second. It only logs;
    it changes nothing. log(level, text): level is 'logInfo' or 'logWarning'."""

    def __init__(self, log: Callable[[str, str], None], graceSecs: Callable[[], float] = lambda: 5.0,
                 clock: Callable[[], float] = time.monotonic, wallClock: Callable[[], float] = time.time,
                 endOfCycleDisplaySecs: Callable[[], float] = lambda: 2.0):
        self.log, self.graceSecs, self.clock, self.wallClock = log, graceSecs, clock, wallClock
        # the frontend shows the free-rotation torque this long (frontend.free.rotation.torque.display.sec) after the
        # IOServer is back at the QR code: added to the grace period for that one case
        self.endOfCycleDisplaySecs = endOfCycleDisplaySecs
        self.io: Optional[dict] = None             # latest IOServer snapshot
        self.ioPid = None
        self.ioSeq = None
        self.fe: Optional[MS] = None
        self.feSince: Optional[float] = None       # monotonic, when the frontend's state last changed (as seen here)
        self.feSinceWall: Optional[float] = None
        self.mismatchSince: Optional[float] = None
        self.mismatchReported = False
        self.mismatchCount = 0                     # since the frontend started (numbers the MISMATCH lines)
        self.cycleMismatches = 0
        self.cycleLongest = 0.0
        self.gaps = 0

    # -------------------------------------------------------------------------------------------- IOServer side
    def onIOServerStates(self, snapshots, fromKey: bool = False) -> None:
        for snap in snapshots or []:
            try:
                self._onSnapshot(snap, fromKey)
            except Exception as e:                  # a malformed snapshot must never stop the monitor
                self.log("logWarning", f"STATE-SYNC BAD-MESSAGE {snap!r}: {type(e).__name__}: {e}")

    def _onSnapshot(self, snap: dict, fromKey: bool) -> None:
        pid, seq = snap.get("pid"), int(snap.get("seq", 0))
        if self.ioPid is None:
            self.log("logInfo", f"STATE-SYNC START  IOServer pid {pid}, seq {seq}, state {_name(snap.get('new'))}"
                                f"{' (from the latest snapshot)' if fromKey else ''}; grace {self.graceSecs():g} s")
        elif pid != self.ioPid:
            self.log("logWarning", f"STATE-SYNC IOSERVER-RESTART  pid {self.ioPid} -> {pid} (seq {self.ioSeq} -> {seq}); "
                                   f"state {_name(snap.get('new'))}")
        elif self.ioSeq is not None and seq <= self.ioSeq:
            return                                  # already seen (the key read at start, then the same on the stream)
        elif self.ioSeq is not None and seq > self.ioSeq + 1:
            self.gaps += 1
            self.log("logWarning", f"STATE-SYNC GAP  IOServer state messages seq {self.ioSeq} -> {seq}: "
                                   f"{seq - self.ioSeq - 1} missing (see the IOServer audit lines 'STATE ... (seq N')")
        previous = self.io
        self.ioPid, self.ioSeq, self.io = pid, seq, snap
        new = snap.get("new")
        if (previous is not None and int(new) == int(MS.READ_QR_CODE)
                and previous.get("new") is not None and int(previous["new"]) != int(MS.READ_QR_CODE)):
            self._endOfCycle(previous, snap)

    def _endOfCycle(self, previous: dict, snap: dict) -> None:
        how = "completed" if int(previous["new"]) == int(MS.READ_FREE_ROTATION_TORQUE_1) else \
            f"aborted in {_name(previous['new'])}"
        cycle = previous.get("cycleId", "?")
        if self.mismatchSince is not None and self.mismatchReported:     # still open: counted with its length so far
            self.cycleLongest = max(self.cycleLongest, self.clock() - self.mismatchSince)
        if self.cycleMismatches:
            self.log("logWarning", f"STATE-SYNC CYCLE {cycle} {how}: {self.cycleMismatches} MISMATCH(ES), longest "
                                   f"{self.cycleLongest:.1f} s; QR {previous.get('qr') or '-'}")
        else:
            self.log("logInfo", f"STATE-SYNC CYCLE {cycle} {how}: in sync; QR {previous.get('qr') or '-'}")
        self.cycleMismatches, self.cycleLongest = 0, 0.0

    # -------------------------------------------------------------------------------------------- comparison
    def expectedFrontendStates(self) -> Optional[FrozenSet[MS]]:
        if self.io is None:
            return None
        try:
            return FRONTEND_STATES_FOR.get(MS(int(self.io.get("new"))))
        except Exception:
            return None

    def check(self, feState) -> Optional[str]:
        """Returns 'mismatch' / 'resolved' when it logged one, else None."""
        now = self.clock()
        try:
            feState = MS(int(feState))
        except Exception:
            return None
        if feState != self.fe:
            self.fe, self.feSince, self.feSinceWall = feState, now, self.wallClock()
        expected = self.expectedFrontendStates()
        if expected is None:                        # no IOServer state yet: nothing to compare
            return None
        if feState in expected:
            if self.mismatchSince is not None:
                lasted = now - self.mismatchSince
                reported = self.mismatchReported
                self.mismatchSince, self.mismatchReported = None, False
                if reported:
                    self.cycleLongest = max(self.cycleLongest, lasted)
                    self.log("logWarning", f"STATE-SYNC RESOLVED #{self.mismatchCount}  after {lasted:.1f} s: frontend "
                                           f"{_name(feState)}, IOServer {_name(self.io.get('new'))} (seq {self.ioSeq})")
                    return "resolved"
            return None
        if self.mismatchSince is None:
            self.mismatchSince = now
        lasted = now - self.mismatchSince
        grace = self.graceSecs()
        if feState == MS.READ_FREE_ROTATION_TORQUE_1 and int(self.io.get("new")) == int(MS.READ_QR_CODE):
            grace += max(0.0, float(self.endOfCycleDisplaySecs())) + 1.0     # the end-of-cycle display, and its record
        if not self.mismatchReported and lasted >= grace:
            self.mismatchReported = True
            self.mismatchCount += 1
            self.cycleMismatches += 1
            io = self.io
            self.log("logWarning",
                     f"STATE-SYNC MISMATCH #{self.mismatchCount}  frontend {_name(feState)} vs IOServer "
                     f"{_name(io.get('new'))} for {lasted:.1f} s (frontend should be one of "
                     f"[{', '.join(_name(s) for s in sorted(expected))}]); cycle {io.get('cycleId', '?')}, QR "
                     f"{io.get('qr') or '-'}; IOServer seq {self.ioSeq} pid {self.ioPid}, in that state since "
                     f"{_clock(io.get('time'))} (came from {_name(io.get('old'))}, {io.get('reason') or 'state change'}); "
                     f"frontend in its state since {_clock(self.feSinceWall)}")
            return "mismatch"
        return None
