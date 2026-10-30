"""Synthetic dry-run of costhetaio/allenbradley/IOServer.py (Allen-Bradley path): the three non-happy paths.

Same harness as DryRun_IOServer_Two_Full_Cycles.py: the REAL IOServer, real Redis, pycomm3.LogixDriver replaced by an
in-memory tag table, and the PLC, QR server and camera server simulated. Every scenario ends with automatic checks;
the script prints PASS/FAIL per check and exits 1 if any check fails.

  A. Camera NOT OK, then retry
     The camera answers NOT OK once for the knuckle and once for the cap. IOServer must write <Check>OK=False and
     <Check>Done=True, go back to the READ (picture) state, ask the camera again for the same state, and on the OK
     answer write OK=True and carry on. The cycle must still complete and wrap to state 1.
     (The PLC here drops the trigger and Done as soon as it sees Done, then raises the trigger again for the retry.
     If IOServer's read loop sees the trigger before the PLC drops it, it re-requests the picture on its own; the
     checks below hold in both cases.)

  B. QR code rejected
     The QR server answers NOT OK, then (operator re-scans) OK. Current behaviour, checked as such: IOServer stays in
     WRITE_QR_CODE and writes NOTHING to the PLC for the rejected code (no QRCodeCheckOK/Done, no cycle settings) -
     see CHANGES_MACHINE2.md section 9 (rejection is not reported to the PLC; to be decided). The accepted code then
     completes state 2 normally, with the side/model of THAT code.

  C. Emergency abort part-way through a cycle (after tightening torque 1, waiting for the bunk picture)
     Stale messages are placed on every queue first. After the PLC raises PLC_PC_EmergencyAbort, IOServer must:
     send ABORT to the QR server (io2qrcodeabort.q) and the emergency message to the frontend (emergency.q); clear the
     7 key communication queues (no stale message left); write False / 0.0 to all 37 per-cycle handshake tags (an
     explicit list in this script, independent of resetAll(); each is set to True / 12.34 first), including
     PLC_PC_EmergencyAbort; go back to READ_QR_CODE and re-arm
     PLC_PC_CheckQRCode=True (so it moves straight on to WRITE_QR_CODE and asks the QR server for a code).
     A full normal cycle must then complete.

Run from the project root with Redis running:  python test/machine1_dryrun/DryRun_IOServer_Fault_Scenarios.py
"""
import collections, sys, threading, time
import pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TAGS = collections.defaultdict(lambda: False)
WRITES = []                      # (tag, value) for every PC write
LOCK = threading.Lock()


class _V:
    def __init__(self, v): self.value = v


class FakeLogix:
    def __init__(self, ip): self.connected = False
    def open(self): self.connected = True
    def close(self): self.connected = False
    def read(self, *tags):
        with LOCK:
            return _V(TAGS[tags[0]])
    def write(self, tag, value):
        with LOCK:
            TAGS[tag] = value; WRITES.append((tag, value))


import costhetaio.allenbradley.IOServer as IOS
IOS.LogixDriver = FakeLogix
from costhetaio.allenbradley.IOServer import IOServer
from statemachine.StateMachine import MachineState as MS
from utils.RedisUtils import *

r = Redis('localhost', 6379); clearQueues(r)
QR_DOST_LHS = "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"
QR_DOSTPLUS_RHS = "8201206$400102VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNURH$SNPL-MAT$"
TAGS[IOServer.PLC_PC_WatchTag] = 1234

RESULTS = []
def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""), flush=True)

# ---------- simulated QR + camera servers (scriptable) ----------
PAUSE = threading.Event()                 # set -> simulators do not touch any queue
QR_PLAN = collections.deque()             # (status, qrCode) answers, one per operator scan
SCAN = threading.Event()                  # operator scans once per SCAN.set()
CAMERA_PLAN = collections.defaultdict(collections.deque)   # picture state -> deque of results (default ok)
PICTURE_REQUESTS = []                     # picture states the camera was asked for, in order

def qr_and_camera():
    rq = Redis('localhost', 6379)
    while True:
        if PAUSE.is_set():
            time.sleep(0.05); continue
        getData(rq, io2qrcodeq, block=50)             # consume IOServer's read request (scan is operator-driven)
        if SCAN.is_set() and QR_PLAN:
            SCAN.clear()
            status, code = QR_PLAN.popleft()
            sendDataFromQRCodeServerToIOServer(rq, qrCode=code, qrCodeStatus=status)
        t, state, take = readDataInCameraServerFromIOServer(rq, block=50)
        if take:
            PICTURE_REQUESTS.append(state)
            result = CAMERA_PLAN[state].popleft() if CAMERA_PLAN[state] else ok
            time.sleep(0.3)
            sendDataFromCameraServerToIOServer(rq, result=result, currentMachineState=state)
threading.Thread(target=qr_and_camera, daemon=True).start()

def fe():                                  # consume io2frontend.q like the frontend does
    rf = Redis('localhost', 6379)
    while True:
        if PAUSE.is_set():
            time.sleep(0.05); continue
        readDataInFEServerFromIOServer(rf, block=200)
threading.Thread(target=fe, daemon=True).start()

io = IOServer(mode="Test")

TRACE = []
def tracer():
    last = None
    while True:
        s = io.machineState.getCurrentState()
        if s != last: TRACE.append(s.value); last = s
        time.sleep(0.005)
threading.Thread(target=tracer, daemon=True).start()

def wait_for(pred, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred(): return True
        time.sleep(0.005)
    return False

# ---------- PLC behaviour ----------
def plc_check(trigger, done):
    """PLC raises the trigger, waits for Done, then drops trigger and Done."""
    with LOCK: TAGS[trigger] = True
    assert wait_for(lambda: TAGS[done] is True), f"timeout waiting for {done}"
    with LOCK: TAGS[trigger] = False; TAGS[done] = False
    time.sleep(0.3)

def plc_value(setTag, valTag, value):
    with LOCK: TAGS[valTag] = value; TAGS[setTag] = True
    time.sleep(2.5)       # IOServer resets these after 1.5/2.0 s via UpdateTagsToDefaultProcessor

def plc_pulse(tag):
    with LOCK: TAGS[tag] = True
    time.sleep(0.8)
    with LOCK: TAGS[tag] = False

def plc_start_cycle(code, status_sequence=(ok,)):
    """PLC raises CheckQRCode; the operator scans once per status in status_sequence (last one must be ok)."""
    for st in status_sequence:
        QR_PLAN.append((st, code if st == ok else "BAD$CODE$"))
    with LOCK: TAGS[IOServer.PLC_PC_CheckQRCode] = True

VISION = [  # (trigger, Done, OK) in Machine 2 order, with the PLC-driven steps marked
    ("KNUCKLE", IOServer.PLC_PC_CheckKnuckle, IOServer.PC_PLC_KnuckleCheckDone, IOServer.PC_PLC_KnuckleCheckOK),
    ("HUB", IOServer.PLC_PC_CheckHub, IOServer.PC_PLC_HubCheckDone, IOServer.PC_PLC_HubCheckOK),
    ("TOP", IOServer.PLC_PC_CheckHubAndSecondBearing, IOServer.PC_PLC_HubAndSecondBearingCheckDone, IOServer.PC_PLC_HubAndSecondBearingCheckOK),
    ("NUT", IOServer.PLC_PC_CheckNutAndPlateWasher, IOServer.PC_PLC_NutAndPlateWasherCheckDone, IOServer.PC_PLC_NutAndPlateWasherCheckOK),
    ("TORQUE1", None, None, None),
    ("BUNK_CP", IOServer.PLC_PC_CheckNoCapBunk, IOServer.PC_PLC_NoCapBunkCheckDone, IOServer.PC_PLC_NoCapBunkCheckOK),
    ("COMP_PRESS", None, None, None),
    ("NOBUNK", IOServer.PLC_PC_CheckNoBunk, IOServer.PC_PLC_NoBunkCheckDone, IOServer.PC_PLC_NoBunkCheckOK),
    ("TORQUE2", None, None, None),
    ("SPLITPIN", IOServer.PLC_PC_CheckSplitPinAndWasher, IOServer.PC_PLC_SplitPinAndWasherCheckDone, IOServer.PC_PLC_SplitPinAndWasherCheckOK),
    ("CAP", IOServer.PLC_PC_CheckCap, IOServer.PC_PLC_CapCheckDone, IOServer.PC_PLP_CapCheckOK),
    ("BUNK_CAPP", IOServer.PLC_PC_CheckBunk, IOServer.PC_PLC_BunkCheckDone, IOServer.PC_PLC_BunkCheckOK),
    ("CAP_PRESS", None, None, None),
    ("FREE_ROT", None, None, None),
]

def run_steps(first="KNUCKLE", last="FREE_ROT", retry_on_notok=()):
    """Drive the PLC through the steps first..last. For steps in retry_on_notok, the PLC expects a NOT OK first:
    it drops trigger and Done, then raises the trigger again for the retry."""
    names = [v[0] for v in VISION]
    for name, trig, done, okTag in VISION[names.index(first): names.index(last) + 1]:
        if name == "TORQUE1": plc_value(IOServer.PLC_PC_Station2TorqueValueSet, IOServer.PLC_PC_Station2TorqueValue, 19.08)
        elif name == "TORQUE2": plc_value(IOServer.PLC_PC_Station2TorqueValueSet, IOServer.PLC_PC_Station2TorqueValue, 19.03)
        elif name == "COMP_PRESS": plc_pulse(IOServer.PLC_PC_ComponentPressDone)
        elif name == "CAP_PRESS": plc_pulse(IOServer.PLC_PC_CapPressDone)
        elif name == "FREE_ROT": plc_value(IOServer.PLC_PC_Station3TorqueValueSet, IOServer.PLC_PC_Station3TorqueValue, 2.41)
        elif name in retry_on_notok:
            with LOCK: TAGS[trig] = True
            assert wait_for(lambda: TAGS[done] is True), f"timeout waiting for NOT OK Done of {name}"
            okAtNotOk = TAGS[okTag]
            with LOCK: TAGS[trig] = False; TAGS[done] = False
            NOTOK_SEEN[name] = okAtNotOk
            time.sleep(0.5)
            plc_check(trig, done)                    # retry
            RETRY_OK[name] = TAGS[okTag]
        else:
            plc_check(trig, done)

NOTOK_SEEN, RETRY_OK = {}, {}

def wrapped():
    return wait_for(lambda: 28 in TRACE and TRACE[-1] in (1, 2), timeout=10)

def finish_qr_handshake():
    assert wait_for(lambda: TAGS[IOServer.PC_PLC_QRCodeCheckDone] is True), "QR done timeout"
    time.sleep(0.3)
    with LOCK: TAGS[IOServer.PLC_PC_CheckQRCode] = False; TAGS[IOServer.PC_PLC_QRCodeCheckDone] = False

def writes_of(tags, since):
    return [(t, v) for t, v in WRITES[since:] if t in tags]

time.sleep(6)          # IOServer __init__ sleeps 5 s before starting its threads

# =====================================================================================================
def run_scenarios():
    global mark
    print("\n=== A. Camera NOT OK, then retry (knuckle and cap) ===", flush=True)
    TRACE.clear(); PICTURE_REQUESTS.clear()
    CAMERA_PLAN[MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE].append(notok)
    CAMERA_PLAN[MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP].append(notok)
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    run_steps(retry_on_notok=("KNUCKLE", "CAP"))
    cycleA = wrapped()
    for name, read_state in (("KNUCKLE", 3), ("CAP", 22)):
        write_state = read_state + 1
        i = next((k for k in range(1, len(TRACE)) if TRACE[k - 1] == write_state and TRACE[k] == read_state), None)
        check(f"A {name}: NOT OK wrote OK=False with Done=True", NOTOK_SEEN.get(name) is False, f"OK tag at NOT OK = {NOTOK_SEEN.get(name)}")
        check(f"A {name}: went back from state {write_state} to picture state {read_state}", i is not None)
        check(f"A {name}: camera asked twice for state {read_state}", PICTURE_REQUESTS.count(MS(read_state)) == 2,
              f"{PICTURE_REQUESTS.count(MS(read_state))} requests")
        check(f"A {name}: retry answered OK -> OK=True", RETRY_OK.get(name) is True)
    check("A: cycle completed and wrapped to state 1", cycleA, f"trace tail {TRACE[-6:]}")
    check("A: each other picture state asked exactly once",
          all(PICTURE_REQUESTS.count(MS(s)) == 1 for s in (5, 7, 9, 13, 16, 20, 24)))

    # =====================================================================================================
    print("\n=== B. QR code rejected, then accepted ===", flush=True)
    TRACE.clear(); mark = len(WRITES)
    QR_TAGS = (IOServer.PC_PLC_QRCodeCheckOK, IOServer.PC_PLC_QRCodeCheckDone)
    SETTING_TAGS = (IOServer.PC_PLC_LH_RH_Selection, IOServer.PC_PLC_ModelSelection,
                    IOServer.PC_PLC_TighteningTorque1Setpoint, IOServer.PC_PLC_TighteningTorque2Setpoint,
                    IOServer.PC_PLC_RotationRPM1, IOServer.PC_PLC_RotationRPM2)
    # the cycle end already re-armed CheckQRCode (state 2); scan a bad code first
    plc_start_cycle(QR_DOSTPLUS_RHS, status_sequence=(notok, ok))
    check("B: waiting in WRITE_QR_CODE before the scan", wait_for(lambda: io.machineState.getCurrentState() == MS.WRITE_QR_CODE))
    mark = len(WRITES)
    SCAN.set()                                   # operator scans -> rejected code
    time.sleep(2.0)
    check("B: rejected code -> still in WRITE_QR_CODE", io.machineState.getCurrentState() == MS.WRITE_QR_CODE,
          io.machineState.getCurrentState().name)
    check("B: rejected code -> nothing written to QRCodeCheckOK/Done (not reported to the PLC; see section 9)",
          writes_of(QR_TAGS, mark) == [], str(writes_of(QR_TAGS, mark)))
    check("B: rejected code -> no cycle settings written", writes_of(SETTING_TAGS, mark) == [])
    mark = len(WRITES)
    SCAN.set()                                   # operator re-scans -> accepted code
    finish_qr_handshake()
    qr_writes = writes_of(QR_TAGS, mark)
    check("B: accepted code -> QRCodeCheckOK=True then Done=True",
          (IOServer.PC_PLC_QRCodeCheckOK, True) in qr_writes and (IOServer.PC_PLC_QRCodeCheckDone, True) in qr_writes, str(qr_writes))
    settings = dict(writes_of(SETTING_TAGS, mark))
    check("B: settings are those of the accepted DOSTPLUS RHS code",
          settings.get(IOServer.PC_PLC_LH_RH_Selection) == 2 and settings.get(IOServer.PC_PLC_ModelSelection) == 2, str(settings))
    check("B: moved on to the knuckle picture state", wait_for(lambda: io.machineState.getCurrentState() == MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE))
    run_steps()
    check("B: cycle completed and wrapped to state 1", wrapped())

    # =====================================================================================================
    print("\n=== C. Emergency abort part-way through a cycle ===", flush=True)
    TRACE.clear()
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    run_steps(last="TORQUE1")
    check("C: before abort, waiting for the bunk (component press) picture",
          wait_for(lambda: io.machineState.getCurrentState() == MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS),
          io.machineState.getCurrentState().name)
    PAUSE.set(); time.sleep(0.3)
    KEY_QUEUES = [io2cameraq, io2qrcodeq, qrcode2ioq, qrcode2feq, qrcode2cameraq, camera2ioq, camera2feq]
    for q in KEY_QUEUES + [io2feq]:
        sendData(r, {"stale": "1"}, queueName=q, aProducer="DRYRUN_STALE")
    abortLen0, emergLen0 = r.xlen(io2qrcodeabortq), r.xlen(emergencyq)
    # Every per-cycle handshake tag is put in a NON-default state first (True, or a non-zero torque), so that a tag the
    # abort fails to reset is caught. The list is the specification, written out here, NOT read from resetAll(), so a
    # tag dropped from resetAll() is detected. Tags IOServer does not reset on abort: the PLC watch tag, the PC
    # heartbeat tag and the six cycle-setting tags written in state 2.
    RESET_TAGS = {a: ("float" if a.endswith("TorqueValue") else "bool") for a in (
        "PLC_PC_CheckQRCode", "PC_PLC_QRCodeCheckOK", "PC_PLC_QRCodeCheckDone",
        "PLC_PC_CheckKnuckle", "PC_PLC_KnuckleCheckOK", "PC_PLC_KnuckleCheckDone",
        "PLC_PC_CheckHub", "PC_PLC_HubCheckOK", "PC_PLC_HubCheckDone",
        "PLC_PC_CheckHubAndSecondBearing", "PC_PLC_HubAndSecondBearingCheckOK", "PC_PLC_HubAndSecondBearingCheckDone",
        "PLC_PC_CheckNutAndPlateWasher", "PC_PLC_NutAndPlateWasherCheckOK", "PC_PLC_NutAndPlateWasherCheckDone",
        "PLC_PC_Station2TorqueValueSet", "PLC_PC_Station2TorqueValue",
        "PLC_PC_CheckNoCapBunk", "PC_PLC_NoCapBunkCheckOK", "PC_PLC_NoCapBunkCheckDone",
        "PLC_PC_ComponentPressDone",
        "PLC_PC_CheckNoBunk", "PC_PLC_NoBunkCheckOK", "PC_PLC_NoBunkCheckDone",
        "PLC_PC_CheckSplitPinAndWasher", "PC_PLC_SplitPinAndWasherCheckOK", "PC_PLC_SplitPinAndWasherCheckDone",
        "PLC_PC_CheckCap", "PC_PLP_CapCheckOK", "PC_PLC_CapCheckDone",
        "PLC_PC_CheckBunk", "PC_PLC_BunkCheckOK", "PC_PLC_BunkCheckDone",
        "PLC_PC_CapPressDone",
        "PLC_PC_Station3TorqueValueSet", "PLC_PC_Station3TorqueValue",
        "PLC_PC_EmergencyAbort")}
    NOT_RESET_ON_ABORT = {"PLC_PC_WatchTag", "PC_PLC_ConnectionStatusTag", "PC_PLC_LH_RH_Selection", "PC_PLC_ModelSelection",
                          "PC_PLC_TighteningTorque1Setpoint", "PC_PLC_TighteningTorque2Setpoint",
                          "PC_PLC_RotationRPM1", "PC_PLC_RotationRPM2"}
    all_tag_attrs = {a for a in vars(IOServer) if a.split("_")[0] in ("PLC", "PC") and a[:6] in ("PLC_PC", "PC_PLC", "PC_PLP")}
    unclassified = all_tag_attrs - set(RESET_TAGS) - NOT_RESET_ON_ABORT
    check("C: every IOServer PLC tag is classified (reset on abort, or deliberately not)", unclassified == set(), str(sorted(unclassified)))
    with LOCK:
        for a, kind in RESET_TAGS.items():
            TAGS[getattr(IOServer, a)] = 12.34 if kind == "float" else True
    mark = len(WRITES)
    reset_ok = wait_for(lambda: TAGS[IOServer.PLC_PC_EmergencyAbort] is False and TAGS[IOServer.PLC_PC_CheckQRCode] is True, timeout=5)
    time.sleep(0.6)
    check("C: abort handled within 5 s (EmergencyAbort reset, CheckQRCode re-armed)", reset_ok)
    check("C: ABORT sent to the QR server (io2qrcodeabort.q)", r.xlen(io2qrcodeabortq) > abortLen0)
    check("C: emergency message sent to the frontend (emergency.q)", r.xlen(emergencyq) > emergLen0)
    for q in KEY_QUEUES:
        stale = [m for m in r.xrange(q) if m[1].get(b"producer") == b"DRYRUN_STALE"]
        check(f"C: queue {q} cleared of stale messages", stale == [], f"{len(stale)} stale left")
    # every one of those tags must now be False / 0.0, except CheckQRCode, which the PC re-arms
    not_reset = {}
    for a, kind in RESET_TAGS.items():
        tag = getattr(IOServer, a)
        expected = True if a == "PLC_PC_CheckQRCode" else (0.0 if kind == "float" else False)
        if TAGS[tag] != expected or type(TAGS[tag]) != type(expected):
            not_reset[a] = TAGS[tag]
    check(f"C: all {len(RESET_TAGS)} handshake tags reset by resetAll() (CheckQRCode re-armed True)",
          len(RESET_TAGS) > 30 and not_reset == {}, str(not_reset))
    check("C: state back to the QR code (READ_QR_CODE -> WRITE_QR_CODE)",
          io.machineState.getCurrentState() in (MS.READ_QR_CODE, MS.WRITE_QR_CODE), io.machineState.getCurrentState().name)
    fresh = [m for m in r.xrange(io2qrcodeq) if m[1].get(b"producer") != b"DRYRUN_STALE"]
    check("C: fresh QR read request sent after the abort", len(fresh) >= 1 or io.machineState.getCurrentState() == MS.READ_QR_CODE)
    staleFE = [m for m in r.xrange(io2feq) if m[1].get(b"producer") == b"DRYRUN_STALE"]
    print(f"INFO  io2frontend.q is not in IOServer's abort clear list (architecture finding R14): stale message "
          f"{'still there' if staleFE else 'gone'}", flush=True)
    PAUSE.clear()
    TRACE.clear()
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    run_steps()
    check("C: a full normal cycle completes after the abort", wrapped(), f"trace {TRACE}")
    check("C: that cycle walked every state 1..28", set(range(1, 29)) <= set(TRACE))


try:
    run_scenarios()
except AssertionError as e:
    check(f"scenario aborted: {e}", False)

# =====================================================================================================
failed = [n for n, ok_ in RESULTS if not ok_]
print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
import os; os._exit(1 if failed else 0)
