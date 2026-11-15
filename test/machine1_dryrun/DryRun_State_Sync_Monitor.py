"""
DryRun_State_Sync_Monitor - utils/StateSync.StateSyncMonitor on its own (CHANGES 108), with a simulated clock: the
frontend-side comparison of its state with the IOServer's, which only logs. No Redis, Qt, PLC or camera.
  M1 a cycle where the frontend runs ahead of the IOServer as it really does (next tile on the camera's OK, before the
     IOServer's handshake ends; one tile ahead after value / press events): no MISMATCH; summary "in sync";
  M2 a difference shorter than the grace period: nothing; longer: one MISMATCH (with both states, the frontend states
     expected, cycle, QR, seq, pid, since when), not repeated; RESOLVED with its length when in step again;
  M3 the end of a cycle: the frontend shows the free-rotation torque for the display time while the IOServer is back
     at the QR code - not a mismatch within grace + display time + 1 s, a mismatch after it;
  M4 the latest snapshot read at start and then the same one on the stream: no GAP; missing numbers: GAP; a new
     IOServer pid: IOSERVER-RESTART;
  M5 a mismatch still open when the cycle ends: the summary counts it with its length so far;
  M6 every IOServer state has a frontend-state set (the table is complete), and every line starts with "STATE-SYNC".

Run from the project root:   python test/machine1_dryrun/DryRun_State_Sync_Monitor.py
"""
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
os.chdir(HERE.parents[1])
os.environ["COSTHETA_PLC_VENDOR"] = "siemens" if HERE.name.startswith("machine2") else "allenbradley"

from statemachine.StateMachine import MachineState as MS   # noqa: E402
from utils.StateSync import StateSyncMonitor, FRONTEND_STATES_FOR   # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   [{detail}]"), flush=True)


class Sim:
    def __init__(self, grace=5.0, display=2.0):
        self.t = 1000.0
        self.lines = []
        self.seq = 0
        self.pid = 11
        self.m = StateSyncMonitor(log=lambda lvl, txt: self.lines.append(txt), graceSecs=lambda: grace,
                                  clock=lambda: self.t, wallClock=lambda: 1.7e9 + self.t,
                                  endOfCycleDisplaySecs=lambda: display)
        self.fe = MS.READ_QR_CODE
        self.io = None

    def ioState(self, new, cycle=5, reason=None, seq=None, pid=None, fromKey=False):
        if pid is not None:
            self.pid = pid
        self.seq = seq if seq is not None else self.seq + 1
        snap = {"pid": self.pid, "seq": self.seq, "cycleId": cycle, "qr": "QR-M", "old": None if self.io is None else int(self.io),
                "new": int(new), "reason": reason, "time": 1.7e9 + self.t}
        self.io = new
        self.m.onIOServerStates([snap], fromKey=fromKey)
        return snap

    def run(self, secs, step=0.25):
        end = self.t + secs
        while self.t < end:
            self.m.check(self.fe)
            self.t += step

    def has(self, word):
        return [x for x in self.lines if word in x]


def realisticCycle(s: Sim, cycle=5):
    """IOServer and frontend as they move in a real cycle: the frontend goes on when the camera's answer reaches it,
    the IOServer 0.8 s later after the handshake; value / press events: the frontend one tile ahead for 0.5 s."""
    s.ioState(MS.WRITE_QR_CODE, cycle); s.run(0.3)
    s.fe = MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE; s.run(0.8)              # QR accepted on the frontend first
    order = [(MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING),
             (MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING, MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING),
             (MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER),
             (MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER, MS.READ_TIGHTENING_TORQUE_1)]
    for read, feNext in order:
        s.ioState(read, cycle); s.run(4.0)                                    # the operator loads, presses the buttons
        s.ioState(MS(int(read) + 1), cycle); s.run(1.5)                       # waiting for the camera
        s.fe = feNext; s.run(0.8)                                              # frontend has the OK; IOServer handshakes
    s.ioState(MS.READ_TIGHTENING_TORQUE_1_DONE, cycle); s.run(20)             # tightening
    s.ioState(MS.READ_TIGHTENING_TORQUE_1, cycle); s.run(0.2)
    s.fe = MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS; s.run(0.5)
    rest = [(MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS, MS.READ_COMPONENT_PRESS_DONE),
            (MS.READ_COMPONENT_PRESS_DONE, MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK),
            (MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK, MS.READ_TIGHTENING_TORQUE_2)]
    for st, feNext in rest:
        s.ioState(st, cycle); s.run(3.0)
        if st.name.startswith("READ_TAKE_PICTURE"):
            s.ioState(MS(int(st) + 1), cycle); s.run(1.0)
        s.fe = feNext; s.run(0.6)
    s.ioState(MS.READ_TIGHTENING_TORQUE_2_DONE, cycle); s.run(15)
    s.ioState(MS.READ_TIGHTENING_TORQUE_2, cycle); s.run(0.2)
    s.fe = MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER; s.run(0.5)
    for read, feNext in [(MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER, MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP),
                         (MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP, MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS),
                         (MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS, MS.READ_CAP_PRESS_DONE)]:
        s.ioState(read, cycle); s.run(3.0)
        s.ioState(MS(int(read) + 1), cycle); s.run(1.2)
        s.fe = feNext; s.run(0.8)
    s.ioState(MS.READ_CAP_PRESS_DONE, cycle); s.run(4)
    s.fe = MS.READ_FREE_ROTATION_TORQUE_1; s.ioState(MS.READ_FREE_ROTATION_TORQUE_1_DONE, cycle); s.run(10)
    s.ioState(MS.READ_FREE_ROTATION_TORQUE_1, cycle); s.run(0.2)
    s.ioState(MS.READ_QR_CODE, cycle, reason="cycle completed"); s.run(2.75)  # free-rotation torque display + record
    s.fe = MS.READ_QR_CODE; s.run(3)


def main():
    s = Sim()
    s.ioState(MS.READ_QR_CODE, 0, reason="IOServer start"); s.run(2)
    realisticCycle(s, 5)
    check("M1 a realistic cycle (frontend ahead after each OK and event, end-of-cycle display): no MISMATCH",
          not s.has("MISMATCH"), s.lines)
    check("M1 ... summary 'STATE-SYNC CYCLE 5 completed: in sync'", s.has("CYCLE 5 completed: in sync"), s.lines)
    # bypass: Torque 1 with moveToState = split pin: IOServer 12 -> 20, frontend 12 -> 20
    s.ioState(MS.WRITE_QR_CODE, 6); s.fe = MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE; s.run(1)
    s.ioState(MS.READ_TIGHTENING_TORQUE_1_DONE, 6); s.fe = MS.READ_TIGHTENING_TORQUE_1; s.run(3)   # (skipping ahead)
    n0 = len(s.lines)
    s.ioState(MS.READ_TIGHTENING_TORQUE_1, 6); s.run(0.2)
    s.fe = MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER; s.run(0.5)
    s.ioState(MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER, 6); s.run(6)
    check("M1 bypassed cycle (IOServer 12 -> 20, the frontend too): no mismatch", not [x for x in s.lines[n0:] if "MISMATCH" in x],
          s.lines[n0:])

    # M2 a difference: short, then long
    s.ioState(MS.WRITE_RESULT_OF_CHECKING_SPLITPIN_AND_WASHER, 6); s.run(1)
    s.ioState(MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP, 6)                       # the frontend missed the split pin OK
    s.run(4.5)
    check("M2 a difference shorter than the grace period (5 s): nothing logged", not s.has("MISMATCH"))
    s.run(1.0)
    mm = s.has("MISMATCH #1")
    check("M2 ... longer: one 'STATE-SYNC MISMATCH #1' with both states, the expected frontend states, cycle, QR, seq, pid",
          len(mm) == 1 and "frontend READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER(20) vs IOServer "
                           "READ_TAKE_PICTURE_FOR_CHECKING_CAP(22)" in mm[0]
          and "[READ_TAKE_PICTURE_FOR_CHECKING_CAP(22)]" in mm[0] and "cycle 6" in mm[0] and "QR QR-M" in mm[0]
          and f"seq {s.seq} pid 11" in mm[0] and "since" in mm[0], mm)
    s.run(20)
    check("M2 ... not repeated while it lasts", len(s.has("MISMATCH #")) == 1, s.has("MISMATCH #"))
    s.fe = MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP; s.run(0.5)
    res = s.has("RESOLVED #1")
    check("M2 in step again: 'STATE-SYNC RESOLVED #1 after 25.x s'", res and "after 25." in res[0], res)

    # M3 end of cycle: frontend shows the free-rotation torque while the IOServer is at the QR code
    s.ioState(MS.READ_FREE_ROTATION_TORQUE_1, 6); s.fe = MS.READ_FREE_ROTATION_TORQUE_1; s.run(1)
    n0 = len(s.lines)
    s.ioState(MS.READ_QR_CODE, 6, reason="cycle completed")
    s.run(7.75)                                                               # grace 5 + display 2 + 1 = 8 s
    check("M3 end-of-cycle display (frontend at the free-rotation torque, IOServer at the QR code): not a mismatch "
          "within grace + display time + 1 s", not [x for x in s.lines[n0:] if "MISMATCH #" in x], s.lines[n0:])
    s.run(0.5)
    check("M3 ... a mismatch after it (frontend stuck at the last step)",
          any("MISMATCH #" in x and "READ_FREE_ROTATION_TORQUE_1(28) vs IOServer READ_QR_CODE(1)" in x for x in s.lines[n0:]),
          s.lines[n0:])
    s.fe = MS.READ_QR_CODE; s.run(0.5)

    # M4 key then the same on the stream; gaps; restart
    s2 = Sim()
    snap = s2.ioState(MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING, 9, seq=40, fromKey=True)
    s2.m.onIOServerStates([snap])                                              # the same snapshot on the stream
    s2.ioState(MS.WRITE_RESULT_OF_CHECKING_HUB_AND_BOTTOM_BEARING, 9)          # seq 41
    check("M4 started from the latest snapshot ('from the latest snapshot'), the same one again on the stream: no GAP",
          s2.has("from the latest snapshot") and not s2.has("GAP"), s2.lines)
    s2.ioState(MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, 9, seq=45)
    check("M4 numbers 42-44 missing: 'STATE-SYNC GAP ... seq 41 -> 45: 3 missing'", s2.has("seq 41 -> 45: 3 missing"), s2.lines)
    s2.ioState(MS.READ_QR_CODE, 0, seq=1, pid=99, reason="IOServer start")
    check("M4 a new IOServer pid: 'STATE-SYNC IOSERVER-RESTART  pid 11 -> 99'", s2.has("IOSERVER-RESTART  pid 11 -> 99"), s2.lines)

    # M5 a mismatch still open when the cycle ends
    s3 = Sim()
    s3.ioState(MS.READ_QR_CODE, 0); s3.run(1)
    s3.ioState(MS.WRITE_QR_CODE, 3); s3.fe = MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE; s3.run(1)
    s3.ioState(MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, 3); s3.run(1)
    s3.ioState(MS.WRITE_RESULT_OF_CHECKING_KNUCKLE, 3); s3.run(1)
    s3.ioState(MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING, 3); s3.run(9)   # frontend never got the OK
    s3.ioState(MS.READ_QR_CODE, 3, reason="abort: emergency")
    summary = s3.has("CYCLE 3 aborted")
    check("M5 a mismatch open at the end of the cycle: summary '1 MISMATCH(ES), longest 9.0 s'",
          summary and "1 MISMATCH(ES), longest 9.0 s" in summary[0], s3.lines)

    # M6 table complete; every line STATE-SYNC
    missing = [st.name for st in MS if st != MS.INVALID_STATE and st not in FRONTEND_STATES_FOR]
    check("M6 every IOServer state has its set of frontend states", not missing, missing)
    allLines = s.lines + s2.lines + s3.lines
    check("M6 every line starts with 'STATE-SYNC '", allLines and all(x.startswith("STATE-SYNC ") for x in allLines),
          [x for x in allLines if not x.startswith("STATE-SYNC ")])
    print("\n".join("   " + x for x in s.lines[:4]))
    print(f"\nRESULT {sum(RESULTS)}/{len(RESULTS)} checks passed", flush=True)
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
