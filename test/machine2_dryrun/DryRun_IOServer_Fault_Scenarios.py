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
     7 key communication queues (no stale message left); write False / 0.0 to all 37 per-cycle handshake tags, 38 on Siemens (an
     explicit list in this script, independent of resetAll(); each is set to True / 12.34 first; on Siemens also
     PLC_PC_Station2RotationDuringTighteningDone), including
     PLC_PC_EmergencyAbort; go back to READ_QR_CODE and re-arm
     PLC_PC_CheckQRCode=True (so it moves straight on to WRITE_QR_CODE and asks the QR server for a code).
     A full normal cycle must then complete.

  D. State-2 cycle settings
     Allen-Bradley (cycle.settings.source=properties): side and model from the QR code, torque setpoints from the
     properties. Siemens (cycle.settings.source=database): side from the QR code and the 5 settings in force in
     machine_settings (default 30 rpm, 15 / 19 Nm, 1 / 1 rotations), no model. In both cases the settings written
     are published to Redis (cycleSettings:<QR>) for the DB record. Siemens: settings changed while a cycle runs
     (as from the frontend's Machine Settings dialog) are written in the NEXT cycle.

  E. Values before Done
     Every Done=True the PC writes comes after its OK tag; in state 2 all settings are written before
     QRCodeCheckOK and QRCodeCheckDone. If a setting cannot be written (PLC refuses it) or the machine settings are
     not available from the database (Siemens; then nothing at all is written), the PC writes QRCodeCheckOK=False
     and Done=True and goes back to the QR code; after the fault is gone a re-scan writes the settings, OK=True,
     Done, and the cycle runs.

  F. Abort from the frontend (frontend -> IOServer queue; the frontend's manual abort, and 'Reset to Step 0' in the
     real-PLC test window). Part-way through a cycle, and while the IOServer waits for a camera result: it must go
     back to the QR code at once, stop a QR scan in progress, reset the handshake tags, NOT send an emergency message
     back to the frontend, ignore a late camera answer for the aborted step, and then run a full cycle.

  G. Vision result, OK or NOT OK -> the IOServer sets the check's OK and Done back to False after
     plc.vision.result.reset.delay.sec (not before); a retry's result written before that cancels the earlier reset.

  H. End of cycle -> camera server: when the cycle completes, on an abort from the frontend and on an emergency abort
     from the PLC, the IOServer sends the end-of-cycle message on the camera queue (after an abort has cleared the
     queues), exactly once per cycle, and none while the cycle runs. (The camera server then drops the top bearing
     profile it keeps in memory for the nut / washer check.)

  I. PLC connection lost / PLC restarted / verified reset
     I0: at start the PC resets every per-cycle tag and arms PLC_PC_CheckQRCode (written and read back).
     I1: link lost between steps, PLC keeps its memory (the PLC keeps its triggers True here): writes and reads on
         hold, after the reconnect the reference tag (last PLC_PC tag the PLC raised, CheckKnuckle) is still True ->
         no abort, no reset, same machine state.
     I2: link lost while the PC waits for a camera result that arrives during the outage: nothing is written until
         the PLC has been checked; then OK and Done are written and the cycle carries on. The PLC dropping its own
         trigger while connected moves the reference back to the previous tag.
     I3: PLC restarts while the link is down, part-way through a cycle: the reference reads False -> cycle aborted
         (QR server, frontend, camera told), every per-cycle tag at its default, CheckQRCode armed; the next cycle
         writes the settings again and completes.
     I4: PLC restarts while idle (waiting for the QR scan): detected, reset, CheckQRCode armed again.
     I5: reset done while the link is down -> incomplete; completed as soon as the link is back.
     I6: a tag the PLC does not take (write ignored) -> the reset reports it (written and read back, 5 attempts).

Run from the project root with Redis running:  python test/machine2_dryrun/DryRun_IOServer_Fault_Scenarios.py
"""
import collections, sys, threading, time
import pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _plc_sim import setup, setting_tags, use_machine_settings        # COSTHETA_PLC_VENDOR=siemens -> the Siemens IOServer on a simulated S7 data block
IOServer, TAGS, WRITES, LOCK, VENDOR = setup()
print(f"=== PLC vendor: {VENDOR} ({IOServer.__module__}) ===", flush=True)
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
END_OF_CYCLE_SEEN = []                    # times the camera received the end-of-cycle message (scenario H)

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
        if (not take) and state == MS.READ_QR_CODE:
            END_OF_CYCLE_SEEN.append(time.time())
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
            notOkMark = len(WRITES)
            NOTOK_SEEN[name] = okAtNotOk
            # after a NOT OK the PC resets OK, Done and the trigger after plc.vision.result.reset.delay.sec; the
            # operator presses the two-hand buttons again after that
            assert wait_for(lambda: (trig, False) in writes_of((trig,), notOkMark), RESET_DELAY + 3), \
                f"timeout waiting for the PC to reset {trig} after the NOT OK of {name}"
            plc_check(trig, done)                    # retry
            RETRY_OK[name] = TAGS[okTag]
        else:
            plc_check(trig, done)

NOTOK_SEEN, RETRY_OK = {}, {}
from Configuration import CosThetaConfigurator as _C
RESET_DELAY = _C.getInstance().getPlcVisionResultResetDelay()

def wrapped():
    return wait_for(lambda: 28 in TRACE and TRACE[-1] in (1, 2), timeout=10)

def finish_qr_handshake():
    assert wait_for(lambda: TAGS[IOServer.PC_PLC_QRCodeCheckDone] is True), "QR done timeout"
    time.sleep(0.3)
    with LOCK: TAGS[IOServer.PLC_PC_CheckQRCode] = False; TAGS[IOServer.PC_PLC_QRCodeCheckDone] = False

SETTING_TAGS = setting_tags(IOServer)      # the state-2 settings tags this PLC has

def writes_of(tags, since):
    return [(t, v) for t, v in WRITES[since:] if t in tags]

time.sleep(6)          # IOServer __init__ sleeps 5 s before starting its threads
WRITES_AT_START = list(WRITES)

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
          settings.get(IOServer.PC_PLC_LH_RH_Selection) == 2 and
          (not IOServer.PC_PLC_ModelSelection or settings.get(IOServer.PC_PLC_ModelSelection) == 2), str(settings))
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
    # Station 2 rotation-during-tightening done: reset with the Station 2 torque tags when the PLC provides it
    if IOServer.PLC_PC_Station2RotationDuringTighteningDone:
        RESET_TAGS["PLC_PC_Station2RotationDuringTighteningDone"] = "bool"
    NOT_RESET_ON_ABORT = {"PLC_PC_WatchTag", "PC_PLC_ConnectionStatusTag", "PC_PLC_LH_RH_Selection", "PC_PLC_ModelSelection",
                          "PC_PLC_TighteningTorque1Setpoint", "PC_PLC_TighteningTorque2Setpoint",
                          "PC_PLC_RotationRPM1", "PC_PLC_RotationRPM2",
                          "PC_PLC_RotationRPM", "PC_PLC_NoOfRotation1CCW", "PC_PLC_NoOfRotation1CW"}
    if not IOServer.PLC_PC_Station2RotationDuringTighteningDone:
        NOT_RESET_ON_ABORT.add("PLC_PC_Station2RotationDuringTighteningDone")      # not on this PLC (empty name)
    all_tag_attrs = {a for a in dir(IOServer) if a[:6] in ("PLC_PC", "PC_PLC", "PC_PLP")}
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

    # =====================================================================================================
    print("\n=== D. State-2 cycle settings: values, published for the DB record, a change applies to the next cycle ===", flush=True)
    from Configuration import CosThetaConfigurator
    cfg = CosThetaConfigurator.getInstance()
    fromDatabase = cfg.getCycleSettingsSource() == "database"
    KEY_FOR_TAG = ((IOServer.PC_PLC_TighteningTorque1Setpoint, "tightening_torque1_setpoint"),   # tag "" = not on this PLC
                   (IOServer.PC_PLC_TighteningTorque2Setpoint, "tightening_torque2_setpoint"),
                   (IOServer.PC_PLC_RotationRPM, "rotation_rpm"),
                   (IOServer.PC_PLC_NoOfRotation1CCW, "no_of_rotation1_ccw"),
                   (IOServer.PC_PLC_NoOfRotation1CW, "no_of_rotation1_cw"))

    def published_matches(code, written):
        published = readCycleSettingsForQRCode(r, code)
        expected = {key: None for tag, key in KEY_FOR_TAG}
        for tag, key in KEY_FOR_TAG:
            if tag and tag in written:
                expected[key] = written[tag]
        return published == expected, f"published {published}, expected {expected}"

    TRACE.clear(); mark = len(WRITES)
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    first = dict(writes_of(SETTING_TAGS, mark))
    if fromDatabase:
        expected = {IOServer.PC_PLC_LH_RH_Selection: 1, IOServer.PC_PLC_TighteningTorque1Setpoint: 15.0,
                    IOServer.PC_PLC_TighteningTorque2Setpoint: 19.0, IOServer.PC_PLC_RotationRPM: 30.0,
                    IOServer.PC_PLC_NoOfRotation1CCW: 1.0, IOServer.PC_PLC_NoOfRotation1CW: 1.0}
        check("D: side + the 5 settings in force in the database written (30 rpm, 15 / 19 Nm, 1 / 1 rotations)",
              first == expected, str(first))
        check("D: no model written (no ModelSelection tag on this PLC)", not IOServer.PC_PLC_ModelSelection)
        if hasattr(TAGS, "raw"):          # Siemens simulator: check the bytes the PLC CPU actually holds
            import struct
            from costhetaio.siemens.plc_backend import load_tag_map
            _, tagmap = load_tag_map()
            bad = {}
            for tag, value in expected.items():
                addr = tagmap[tag][0]
                fmt = ">d" if ",LR" in addr else ">f" if ",R" in addr else ">h"
                want = struct.pack(fmt, value)
                if TAGS.raw(tag) != want:
                    bad[f"{tag} ({addr})"] = (TAGS.raw(tag).hex(" "), want.hex(" "))
            check("D: PLC memory holds each setting in its declared width (LREAL 8 bytes, REAL 4, INT 2) = the value written",
                  bad == {}, str(bad))
    else:
        check("D: side + model written from the QR code (1 = LHS, 1 = DOST)",
              first.get(IOServer.PC_PLC_LH_RH_Selection) == 1 and first.get(IOServer.PC_PLC_ModelSelection) == 1, str(first))
        t1 = cfg.getTighteningTorque1Setpoint()
        check("D: torque setpoint 1 from the properties (-1 = not written)",
              first.get(IOServer.PC_PLC_TighteningTorque1Setpoint) == (None if t1 == -1 else float(t1)), str(first))
    ok_, detail = published_matches(QR_DOST_LHS, first)
    check("D: the settings written are published for the DB record (cycleSettings:<QR>)", ok_, detail)
    if fromDatabase:          # Supervisor changes the settings while this cycle runs: the next cycle uses them
        use_machine_settings("Test", rotation_rpm=35, tightening_torque1_setpoint=17.5, tightening_torque2_setpoint=18.25,
                             no_of_rotation1_ccw=2, no_of_rotation1_cw=1)
    run_steps()
    check("D: cycle completed", wrapped())
    if fromDatabase:
        TRACE.clear(); mark = len(WRITES)
        plc_start_cycle(QR_DOSTPLUS_RHS); SCAN.set(); finish_qr_handshake()
        second = dict(writes_of(SETTING_TAGS, mark))
        expected = {IOServer.PC_PLC_LH_RH_Selection: 2, IOServer.PC_PLC_TighteningTorque1Setpoint: 17.5,
                    IOServer.PC_PLC_TighteningTorque2Setpoint: 18.25, IOServer.PC_PLC_RotationRPM: 35.0,
                    IOServer.PC_PLC_NoOfRotation1CCW: 2.0, IOServer.PC_PLC_NoOfRotation1CW: 1.0}
        check("D: settings changed mid-cycle are written in the NEXT cycle (35 rpm, 17.5 / 18.25 Nm, 2 / 1)",
              second == expected, str(second))
        ok_, detail = published_matches(QR_DOSTPLUS_RHS, second)
        check("D: the new settings are published for that component's record", ok_, detail)
        run_steps()
        check("D: that cycle completed", wrapped())
        use_machine_settings("Test")          # back to the defaults

    # =====================================================================================================
    print("\n=== E. Values before Done: every OK / setting is written before its Done; settings not writable -> QR NOT OK ===", flush=True)
    QR_OK, QR_DONE = IOServer.PC_PLC_QRCodeCheckOK, IOServer.PC_PLC_QRCodeCheckDone
    PAIRS = [(QR_DONE, QR_OK)] + [(done, okTag) for _, trig, done, okTag in VISION if done]
    DONE_TO_OK = dict(PAIRS)

    def order_problems(since):
        """For every Done=True the PC wrote after `since`: its OK tag must have been written after the previous
        handshake and before this Done; in state 2, every settings tag too."""
        problems, last = [], since
        w = WRITES[since:]
        for i, (tag, value) in enumerate(w):
            if tag in DONE_TO_OK and value is True:
                window = [t for t, v in w[last - since:i]]
                if DONE_TO_OK[tag] not in window:
                    problems.append(f"{tag}=True without {DONE_TO_OK[tag]} written first")
                if tag == QR_DONE:
                    okIdx = max(k for k, t in enumerate(window) if t == QR_OK) if QR_OK in window else -1
                    late = [t for k, t in enumerate(window) if t in SETTING_TAGS and k > okIdx]
                    if late:
                        problems.append(f"settings written after QRCodeCheckOK: {late}")
                last = since + i + 1
        return problems

    # E1: a normal cycle - every Done preceded by its OK, settings before QR OK / Done
    TRACE.clear(); mark = len(WRITES)
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    run_steps()
    check("E1 cycle completed", wrapped())
    seq = [t for t, v in WRITES[mark:] if t in SETTING_TAGS or t in (QR_OK, QR_DONE)]
    qrDoneAt = next((k for k, t in enumerate(seq) if t == QR_DONE), -1)
    check("E1 state 2: all settings, then QRCodeCheckOK, then QRCodeCheckDone",
          qrDoneAt > 0 and seq[qrDoneAt - 1] == QR_OK and IOServer.PC_PLC_LH_RH_Selection in seq[:qrDoneAt - 1]
          and set(seq[:qrDoneAt - 1]) <= set(SETTING_TAGS)
          and (not fromDatabase or set(seq[:qrDoneAt - 1]) == set(SETTING_TAGS)), str(seq[:qrDoneAt + 1]))
    p_ = order_problems(mark)
    check("E1 every Done=True of the cycle written only after its OK (and settings) were written", p_ == [], str(p_))

    # E2: a settings write fails (PLC refuses one setting) -> nothing says OK: QR OK=False, Done=True, back to state 1
    import types
    failTag = IOServer.PC_PLC_LH_RH_Selection
    origWriteInt = io.write_int
    io.write_int = types.MethodType(lambda self, tag, value, debug=False: False if tag == failTag else origWriteInt(tag, value, debug), io)
    TRACE.clear(); mark = len(WRITES)
    r.delete("cycleSettings:" + QR_DOSTPLUS_RHS)
    plc_start_cycle(QR_DOSTPLUS_RHS); SCAN.set(); finish_qr_handshake()
    qrw = writes_of((QR_OK, QR_DONE), mark)
    check("E2 setting not writable -> QRCodeCheckOK=False, then QRCodeCheckDone=True (no OK=True)",
          (QR_OK, False) in qrw and (QR_OK, True) not in qrw and qrw.index((QR_OK, False)) < qrw.index((QR_DONE, True))
          if (QR_DONE, True) in qrw else False, str(qrw))
    check("E2 not moved on to the knuckle; back to the QR code", MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE.value not in TRACE
          and wait_for(lambda: io.machineState.getCurrentState() in (MS.READ_QR_CODE, MS.WRITE_QR_CODE)), f"trace {TRACE}")
    check("E2 settings for that QR not published for the DB record", readCycleSettingsForQRCode(r, QR_DOSTPLUS_RHS) == {})
    io.write_int = origWriteInt
    # operator re-scans once the fault is gone -> settings, OK, Done, and the cycle runs
    mark = len(WRITES)
    QR_PLAN.append((ok, QR_DOSTPLUS_RHS))
    wait_for(lambda: io.machineState.getCurrentState() == MS.WRITE_QR_CODE)
    SCAN.set(); finish_qr_handshake()
    qrw = writes_of((QR_OK, QR_DONE), mark)
    check("E2 after the fault: re-scan -> settings written, QRCodeCheckOK=True, Done=True",
          (QR_OK, True) in qrw and dict(writes_of(SETTING_TAGS, mark)).get(failTag) == 2 and order_problems(mark) == [],
          f"{qrw} {order_problems(mark)}")
    run_steps()
    check("E2 cycle then completes", wrapped())

    if fromDatabase:
        # E3: machine settings not available from the database -> NOTHING written, QR OK=False, Done=True
        import costhetaio.allenbradley.IOServer as ABIO
        origGet = ABIO.getMachineSettings
        ABIO.getMachineSettings = lambda db: {}
        TRACE.clear(); mark = len(WRITES)
        r.delete("cycleSettings:" + QR_DOST_LHS)
        plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
        qrw = writes_of((QR_OK, QR_DONE), mark)
        check("E3 no machine settings in the database -> no settings tag written at all", writes_of(SETTING_TAGS, mark) == [],
              str(writes_of(SETTING_TAGS, mark)))
        check("E3 -> QRCodeCheckOK=False then QRCodeCheckDone=True", (QR_OK, False) in qrw and (QR_OK, True) not in qrw
              and (QR_DONE, True) in qrw and qrw.index((QR_OK, False)) < qrw.index((QR_DONE, True)), str(qrw))
        check("E3 nothing published for the DB record", readCycleSettingsForQRCode(r, QR_DOST_LHS) == {})
        ABIO.getMachineSettings = origGet
        mark = len(WRITES)
        QR_PLAN.append((ok, QR_DOST_LHS))
        wait_for(lambda: io.machineState.getCurrentState() == MS.WRITE_QR_CODE)
        SCAN.set(); finish_qr_handshake()
        check("E3 settings back -> re-scan writes them, then OK=True / Done", (QR_OK, True) in writes_of((QR_OK,), mark)
              and len(writes_of(SETTING_TAGS, mark)) == len(SETTING_TAGS) and order_problems(mark) == [], str(order_problems(mark)))
        run_steps()
        check("E3 cycle then completes", wrapped())


def run_frontend_abort():
    """F. Abort sent by the frontend (manual abort; also 'Reset to Step 0' in the real-PLC test window)."""
    print("\n=== F. Abort from the frontend part-way through a cycle -> back to the QR code ===", flush=True)
    # F1: between steps (waiting for the top-bearing picture trigger)
    TRACE.clear()
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    run_steps(last="HUB")
    check("F1 before the abort: waiting for the top-bearing picture",
          wait_for(lambda: io.machineState.getCurrentState() == MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING),
          io.machineState.getCurrentState().name)
    abortLen0, emergLen0, aborts0 = r.xlen(io2qrcodeabortq), r.xlen(emergencyq), io.abortCount
    with LOCK:
        TAGS[IOServer.PC_PLC_HubCheckOK] = True; TAGS[IOServer.PC_PLC_HubCheckDone] = True
    sendAbortFromFEServerToIOServer(r, reason="dry run F1")
    check("F1 IOServer aborted within 3 s and is back at the QR code (READ_QR_CODE -> WRITE_QR_CODE)",
          wait_for(lambda: io.abortCount > aborts0 and io.machineState.getCurrentState() in (MS.READ_QR_CODE, MS.WRITE_QR_CODE), 3),
          io.machineState.getCurrentState().name)
    time.sleep(0.5)
    check("F1 the reason is recorded", "dry run F1" in io.lastAbortReason, io.lastAbortReason)
    check("F1 ABORT sent to the QR server (a scan in progress is stopped)", r.xlen(io2qrcodeabortq) > abortLen0)
    check("F1 NO emergency message back to the frontend (the abort came from it)", r.xlen(emergencyq) == emergLen0)
    check("F1 handshake tags of the aborted cycle reset (Hub OK / Done False), CheckQRCode re-armed",
          TAGS[IOServer.PC_PLC_HubCheckOK] is False and TAGS[IOServer.PC_PLC_HubCheckDone] is False
          and TAGS[IOServer.PLC_PC_CheckQRCode] is True)
    check("F1 the abort message was consumed (frontend -> IOServer queue empty)", r.xlen(fe2ioq) == 0)
    TRACE.clear()
    plc_start_cycle(QR_DOSTPLUS_RHS); SCAN.set(); finish_qr_handshake()
    run_steps()
    check("F1 a full normal cycle completes after the abort", wrapped(), f"trace {TRACE}")

    # F2: while the IOServer waits for a camera result (WRITE state); the late camera answer must not move it on
    TRACE.clear()
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    PAUSE.set(); time.sleep(0.3)                          # the camera does not answer
    with LOCK: TAGS[IOServer.PLC_PC_CheckKnuckle] = True
    check("F2 waiting for the knuckle camera result (WRITE state)",
          wait_for(lambda: io.machineState.getCurrentState() == MS.WRITE_RESULT_OF_CHECKING_KNUCKLE, 5),
          io.machineState.getCurrentState().name)
    aborts0 = io.abortCount
    sendAbortFromFEServerToIOServer(r, reason="dry run F2")
    check("F2 aborted while waiting for the camera: back to the QR code",
          wait_for(lambda: io.abortCount > aborts0 and io.machineState.getCurrentState() in (MS.READ_QR_CODE, MS.WRITE_QR_CODE), 3),
          io.machineState.getCurrentState().name)
    with LOCK: TAGS[IOServer.PLC_PC_CheckKnuckle] = False
    mark = len(WRITES)
    sendDataFromCameraServerToIOServer(r, result=ok, currentMachineState=MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)
    time.sleep(1.5)
    check("F2 a late camera OK for the aborted knuckle check moves nothing (no Knuckle OK/Done written, still at the QR code)",
          writes_of((IOServer.PC_PLC_KnuckleCheckOK, IOServer.PC_PLC_KnuckleCheckDone), mark) == [] and
          io.machineState.getCurrentState() in (MS.READ_QR_CODE, MS.WRITE_QR_CODE),
          f"{writes_of((IOServer.PC_PLC_KnuckleCheckOK, IOServer.PC_PLC_KnuckleCheckDone), mark)} {io.machineState.getCurrentState().name}")
    r.delete(camera2ioq)
    PAUSE.clear()
    TRACE.clear()
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    run_steps()
    check("F2 a full normal cycle completes after the abort", wrapped(), f"trace {TRACE}")

    # F3: a state change only from the state a step started in (an abort in between wins)
    from statemachine.StateMachine import MachineStateMachine
    sm = MachineStateMachine(); sm.setCurrentState(MS.WRITE_RESULT_OF_CHECKING_KNUCKLE)
    sm.setCurrentState(MS.READ_QR_CODE)                          # abort
    moved = sm.incrementStateFrom(MS.WRITE_RESULT_OF_CHECKING_KNUCKLE)
    check("F3 a step that finishes after an abort does not move the reset state on (incrementStateFrom)",
          moved is False and sm.getCurrentState() == MS.READ_QR_CODE)
    sm.setCurrentState(MS.WRITE_RESULT_OF_CHECKING_KNUCKLE)
    check("F3 ... and moves it on normally otherwise",
          sm.incrementStateFrom(MS.WRITE_RESULT_OF_CHECKING_KNUCKLE) and
          sm.getCurrentState() == MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING)


def run_vision_result_reset():
    """G. After a vision check's result (OK or NOT OK) was written, the IOServer sets its OK and Done tags back to
    False after plc.vision.result.reset.delay.sec (default 2 s); a retry's result written before that cancels it."""
    from Configuration import CosThetaConfigurator
    delay = CosThetaConfigurator.getInstance().getPlcVisionResultResetDelay()
    print(f"\n=== G. Vision result (OK or NOT OK) -> OK and Done set back to False after {delay} s ===", flush=True)
    KN_OK, KN_DONE = IOServer.PC_PLC_KnuckleCheckOK, IOServer.PC_PLC_KnuckleCheckDone
    HUB_OK, HUB_DONE = IOServer.PC_PLC_HubCheckOK, IOServer.PC_PLC_HubCheckDone
    assert wait_for(lambda: io.machineState.getCurrentState() in (MS.READ_QR_CODE, MS.WRITE_QR_CODE), 10)
    TRACE.clear()
    CAMERA_PLAN[MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING].append(notok)
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    mark = len(WRITES)
    with LOCK: TAGS[IOServer.PLC_PC_CheckKnuckle] = True
    assert wait_for(lambda: TAGS[KN_DONE] is True), "knuckle Done timeout"
    tDone = time.time()
    with LOCK: TAGS[IOServer.PLC_PC_CheckKnuckle] = False        # the PLC drops its trigger, NOT the PC's tags
    time.sleep(max(0.0, delay - 0.6 - (time.time() - tDone)))
    early = writes_of((KN_OK, KN_DONE), mark)
    check(f"G knuckle OK: OK / Done still True {delay - 0.6:.1f} s after Done (no reset yet)",
          (KN_OK, False) not in early and (KN_DONE, False) not in early and TAGS[KN_OK] is True and TAGS[KN_DONE] is True,
          str(early))
    reset = wait_for(lambda: TAGS[KN_OK] is False and TAGS[KN_DONE] is False, 2.0)
    elapsed = time.time() - tDone
    check(f"G knuckle OK: OK and Done set back to False by the PC about {delay} s after Done ({elapsed:.1f} s)",
          reset and delay - 0.3 <= elapsed <= delay + 1.0, str(writes_of((KN_OK, KN_DONE), mark)))
    # hub: NOT OK -> after the delay OK, Done AND the trigger set to False; the standing trigger is NOT acted on
    HUB_TRIG = IOServer.PLC_PC_CheckHub
    mark = len(WRITES); req0 = PICTURE_REQUESTS.count(MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING)
    with LOCK: TAGS[HUB_TRIG] = True                              # the PLC keeps the trigger True
    assert wait_for(lambda: TAGS[HUB_DONE] is True), "hub NOT OK Done timeout"
    tDone = time.time()
    # back in the picture state after the NOT OK, still within the delay (with a short delay, e.g. 0.6 s, a fixed
    # sleep of delay - 0.6 s checked before the IOServer had gone back)
    wait_for(lambda: io.machineState.getCurrentState() == MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING, delay)
    time.sleep(max(0.0, min(0.2, delay - 0.15 - (time.time() - tDone))))
    check("G hub NOT OK: during the delay the standing trigger does NOT retake the picture",
          PICTURE_REQUESTS.count(MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING) == req0 + 1 and
          io.machineState.getCurrentState() == MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING,
          f"{PICTURE_REQUESTS.count(MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING) - req0} requests, "
          f"{io.machineState.getCurrentState().name}")
    reset = wait_for(lambda: TAGS[HUB_TRIG] is False and TAGS[HUB_DONE] is False and TAGS[HUB_OK] is False, 2.0)
    elapsed = time.time() - tDone
    w = writes_of((HUB_OK, HUB_DONE, HUB_TRIG), mark)
    check(f"G hub NOT OK: after about {delay} s ({elapsed:.1f} s) OK, Done and the trigger {HUB_TRIG} set to False by the PC",
          reset and w[:2] == [(HUB_OK, False), (HUB_DONE, True)] and
          w[-3:] == [(HUB_OK, False), (HUB_DONE, False), (HUB_TRIG, False)] and delay - 0.3 <= elapsed <= delay + 1.0, str(w))
    time.sleep(0.5)
    check("G hub NOT OK: still no new picture until the next 2HB press",
          PICTURE_REQUESTS.count(MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING) == req0 + 1)
    mark = len(WRITES)
    plc_check(HUB_TRIG, HUB_DONE)                                 # next 2HB press: retaken, answered OK
    w = writes_of((HUB_OK, HUB_DONE, HUB_TRIG), mark)
    check("G hub retry OK (next 2HB press): OK and Done set back to False after the delay; the trigger is left to the PLC",
          wait_for(lambda: (HUB_OK, False) in writes_of((HUB_OK,), mark) and (HUB_DONE, False) in writes_of((HUB_DONE,), mark),
                   delay + 2.0) and (HUB_TRIG, False) not in writes_of((HUB_TRIG,), mark),
          str(writes_of((HUB_OK, HUB_DONE, HUB_TRIG), mark)))
    # top bearing: a 2HB press DURING the delay after a NOT OK is not acted on (the PC then resets the trigger)
    TOP_TRIG, TOP_OK, TOP_DONE = (IOServer.PLC_PC_CheckHubAndSecondBearing, IOServer.PC_PLC_HubAndSecondBearingCheckOK,
                                  IOServer.PC_PLC_HubAndSecondBearingCheckDone)
    CAMERA_PLAN[MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING].append(notok)
    mark = len(WRITES); req0 = PICTURE_REQUESTS.count(MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING)
    with LOCK: TAGS[TOP_TRIG] = True
    assert wait_for(lambda: TAGS[TOP_DONE] is True), "top bearing NOT OK Done timeout"
    with LOCK: TAGS[TOP_TRIG] = False
    time.sleep(0.3)
    with LOCK: TAGS[TOP_TRIG] = True                              # 2HB pressed again within the delay
    assert wait_for(lambda: (TOP_TRIG, False) in writes_of((TOP_TRIG,), mark), delay + 2.0), "trigger not reset"
    check("G 2HB pressed during the delay after a NOT OK: not acted on; the PC resets the trigger after the delay",
          PICTURE_REQUESTS.count(MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING) == req0 + 1 and TAGS[TOP_TRIG] is False,
          str(writes_of((TOP_OK, TOP_DONE, TOP_TRIG), mark)))
    plc_check(TOP_TRIG, TOP_DONE)                                 # pressed again after the delay: retaken, OK
    check("G ... pressed again after the delay: picture retaken and answered OK",
          PICTURE_REQUESTS.count(MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING) == req0 + 2 and
          wait_for(lambda: io.machineState.getCurrentState().value >= MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER.value, 3),
          io.machineState.getCurrentState().name)
    check("G one scheduler thread for all the resets (no thread per reset)",
          sum(1 for th in threading.enumerate() if th.name == "Vision Result Reset Scheduler") == 1 and
          not any(th.name.startswith("Reset ") for th in threading.enumerate()))
    run_steps(first="NUT")
    check("G cycle completes", wrapped(), f"trace {TRACE}")


def run_end_of_cycle():
    """H. End-of-cycle message to the camera server at cycle end and on aborts."""
    print("\n=== H. End of cycle -> camera server (it drops the top bearing profile): completed, frontend abort, PLC emergency abort ===", flush=True)
    def seen_after(mark):
        return [t for t in END_OF_CYCLE_SEEN if t > mark]
    # H1: a normal cycle - nothing while it runs, one message when it completes
    time.sleep(0.5); TRACE.clear(); mark = time.time()
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    run_steps(last="CAP")
    check("H1 no end-of-cycle message while the cycle runs (knuckle ... cap done)", seen_after(mark) == [], str(len(seen_after(mark))))
    run_steps(first="BUNK_CAPP")
    check("H1 cycle completes", wrapped(), f"trace {TRACE}")
    check("H1 end-of-cycle message received by the camera server when the cycle completed",
          wait_for(lambda: len(seen_after(mark)) == 1, 3), str(len(seen_after(mark))))
    # H2: abort from the frontend after the top bearing check
    time.sleep(0.5); TRACE.clear()
    plc_start_cycle(QR_DOSTPLUS_RHS); SCAN.set(); finish_qr_handshake()
    run_steps(last="TOP")
    mark = time.time(); aborts0 = io.abortCount
    sendAbortFromFEServerToIOServer(r, reason="dry run H2")
    check("H2 abort from the frontend handled", wait_for(lambda: io.abortCount > aborts0, 3))
    check("H2 end-of-cycle message received by the camera server (after the queues were cleared)",
          wait_for(lambda: len(seen_after(mark)) == 1, 3), str(len(seen_after(mark))))
    # H3: emergency abort from the PLC after the nut / washer check
    time.sleep(0.5); TRACE.clear()
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    run_steps(last="NUT")
    mark = time.time(); aborts0 = io.abortCount
    with LOCK: TAGS[IOServer.PLC_PC_EmergencyAbort] = True
    check("H3 emergency abort handled", wait_for(lambda: io.abortCount > aborts0 and TAGS[IOServer.PLC_PC_EmergencyAbort] is False, 5))
    check("H3 end-of-cycle message received by the camera server (after the queues were cleared)",
          wait_for(lambda: len(seen_after(mark)) == 1, 3), str(len(seen_after(mark))))
    time.sleep(0.6); TRACE.clear()
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    run_steps()
    check("H a full normal cycle completes afterwards", wrapped(), f"trace {TRACE}")


def run_plc_connection():
    """I. PLC connection lost / PLC restarted / verified reset."""
    print("\n=== I. PLC connection lost / PLC restarted / verified reset ===", flush=True)
    from _plc_sim import LINK, STUCK, plc_restart
    WATCH, QRT = IOServer.PLC_PC_WatchTag, IOServer.PLC_PC_CheckQRCode
    KN, KN_DONE = IOServer.PLC_PC_CheckKnuckle, IOServer.PC_PLC_KnuckleCheckDone
    HUB, HUB_DONE, HUB_OK = IOServer.PLC_PC_CheckHub, IOServer.PC_PLC_HubCheckDone, IOServer.PC_PLC_HubCheckOK
    state = lambda: io.machineState.getCurrentState()

    def keep_check(trigger, done):
        """The PLC raises the trigger and keeps it True until the end of the cycle."""
        with LOCK: TAGS[trigger] = True
        assert wait_for(lambda: TAGS[done] is True), f"timeout waiting for {done}"
        time.sleep(0.3)

    def not_default():
        out = {}
        for tag, kind in io.perCycleTags():
            want = True if tag == QRT else (False if kind is bool else 0.0)
            have = TAGS[tag]
            if (have != want) if kind is bool else abs(float(have) - want) > 1e-3:
                out[tag] = have
        return out

    def checked(timeout=10):
        return wait_for(lambda: not io._plcCheckPending, timeout)

    # I0
    start_writes = [w for w in WRITES_AT_START if w[0] in (QRT, KN)]
    check("I0 at start: per-cycle tags reset, then CheckQRCode armed by the PC",
          (KN, False) in start_writes and (QRT, True) in start_writes and
          start_writes.index((KN, False)) < start_writes.index((QRT, True)), str(start_writes[:6]))

    # I1
    time.sleep(0.5); TRACE.clear()
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    keep_check(KN, KN_DONE)
    check("I1 waiting for the hub picture; reference = CheckKnuckle",
          wait_for(lambda: state() == MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING and io.referenceTag() == KN, 3),
          f"{state().name}, reference {io.referenceTag()}")
    aborts0, restarts0, mark = io.abortCount, io.plcRestartsDetected, len(WRITES)
    LINK["down"] = True
    check("I1 connection loss detected; writes and reads on hold", wait_for(lambda: io._plcCheckPending, 5))
    time.sleep(2.0)
    LINK["down"] = False
    check("I1 PLC checked after the reconnect", checked())
    check("I1 CheckKnuckle still True -> no restart: no abort, no reset, same state",
          io.abortCount == aborts0 and io.plcRestartsDetected == restarts0 and
          state() == MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING and (KN, False) not in writes_of((KN,), mark),
          f"aborts {io.abortCount - aborts0}, restarts {io.plcRestartsDetected - restarts0}, {state().name}")

    # I2
    with LOCK: TAGS[HUB] = True
    assert wait_for(lambda: state() == MS.WRITE_RESULT_OF_CHECKING_HUB_AND_BOTTOM_BEARING, 3), "hub picture not requested"
    LINK["down"] = True
    time.sleep(2.0)                           # the camera answers (after 0.3 s) during the outage
    check("I2 nothing written while the link is down", TAGS[HUB_DONE] is False and TAGS[HUB_OK] is False)
    LINK["down"] = False
    check("I2 PLC checked after the reconnect: CheckHub still True -> no restart", checked() and io.abortCount == aborts0
          and io.plcRestartsDetected == restarts0)
    check("I2 the camera result is written after the check (OK, then Done)",
          wait_for(lambda: TAGS[HUB_DONE] is True and TAGS[HUB_OK] is True, 5))
    check("I2 reference = CheckHub", io.referenceTag() == HUB, str(io.referenceTag()))
    with LOCK: TAGS[HUB] = False               # the PLC drops its own trigger while connected
    check("I2 PLC dropped CheckHub itself -> reference back to CheckKnuckle", wait_for(lambda: io.referenceTag() == KN, 4),
          str(io.referenceTag()))
    run_steps(first="TOP")
    check("I1/I2 cycle completes", wrapped(), f"trace {TRACE}")

    # I3
    time.sleep(0.5); TRACE.clear()
    plc_start_cycle(QR_DOSTPLUS_RHS); SCAN.set(); finish_qr_handshake()
    keep_check(KN, KN_DONE); keep_check(HUB, HUB_DONE)
    check("I3 waiting for the top bearing picture; reference = CheckHub",
          wait_for(lambda: state() == MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING and io.referenceTag() == HUB, 3),
          f"{state().name}, {io.referenceTag()}")
    aborts0, restarts0 = io.abortCount, io.plcRestartsDetected
    emerg0, qrab0, eoc = r.xlen(emergencyq), r.xlen(io2qrcodeabortq), time.time()
    LINK["down"] = True; time.sleep(1.5)
    plc_restart(WATCH)
    time.sleep(1.0); LINK["down"] = False
    check("I3 PLC restart detected (CheckHub reads False)", wait_for(lambda: io.plcRestartsDetected == restarts0 + 1, 10),
          io.lastAbortReason)
    check("I3 cycle aborted; QR code server and frontend told",
          wait_for(lambda: io.abortCount == aborts0 + 1, 5) and r.xlen(io2qrcodeabortq) > qrab0 and
          r.xlen(emergencyq) > emerg0 and "PLC restarted" in io.lastAbortReason, io.lastAbortReason)
    check("I3 end-of-cycle message to the camera server", wait_for(lambda: any(t > eoc for t in END_OF_CYCLE_SEEN), 3))
    check("I3 every per-cycle tag at its default, CheckQRCode armed", wait_for(lambda: not not_default(), 5), str(not_default()))
    check("I3 reset complete and PLC check finished", not io._resetIncomplete and not io._plcCheckPending)
    time.sleep(0.6); TRACE.clear(); mark = len(WRITES)
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    written = {t for t, _ in writes_of(SETTING_TAGS, mark)}
    check("I3 next cycle writes the cycle settings again", written == set(SETTING_TAGS), str(sorted(written)))
    run_steps()
    check("I3 a full cycle completes after the PLC restart", wrapped(), f"trace {TRACE}")

    # I4
    check("I4 idle, waiting for the QR scan; reference = CheckQRCode",
          wait_for(lambda: state() == MS.WRITE_QR_CODE and io.referenceTag() == QRT, 5), f"{state().name}, {io.referenceTag()}")
    restarts0 = io.plcRestartsDetected
    LINK["down"] = True; time.sleep(1.5); plc_restart(WATCH); time.sleep(0.5); LINK["down"] = False
    check("I4 PLC restart while idle detected and reset; CheckQRCode armed again",
          wait_for(lambda: io.plcRestartsDetected == restarts0 + 1 and not not_default() and not io._plcCheckPending, 10),
          f"{io.lastAbortReason}; {not_default()}")

    # I5
    time.sleep(1.0)
    with LOCK:
        for tag, kind in io.perCycleTags():
            if tag != IOServer.PLC_PC_EmergencyAbort:
                TAGS[tag] = 12.34 if kind is float else True
    LINK["down"] = True
    assert wait_for(lambda: io._plcCheckPending, 5), "link loss not detected"
    complete = io.resetForNextCycle("dry run I5 (link down)")
    check("I5 reset while the link is down -> reported incomplete", complete is False and io._resetIncomplete)
    time.sleep(1.0); LINK["down"] = False
    check("I5 completed as soon as the link is back: every tag at its default, CheckQRCode armed",
          wait_for(lambda: not io._resetIncomplete and not io._plcCheckPending and not not_default(), 10), str(not_default()))

    # I6
    STUCK_TAG = IOServer.PC_PLC_CapCheckDone
    with LOCK: TAGS[STUCK_TAG] = True
    STUCK.add(STUCK_TAG)
    t0 = time.time(); complete = io.resetForNextCycle("dry run I6 (a tag the PLC does not take)")
    took = time.time() - t0
    STUCK.clear()
    check(f"I6 a tag the PLC does not take is reported (read back, {IOServer.RESET_WRITE_ATTEMPTS} attempts, {took:.1f} s)",
          complete is False and io._resetIncomplete and TAGS[STUCK_TAG] is True)
    check("I6 next reset with the tag accepted -> complete", io.resetForNextCycle("dry run I6 again") and not not_default(),
          str(not_default()))
    time.sleep(0.6); TRACE.clear()
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    run_steps()
    check("I a full normal cycle completes afterwards", wrapped(), f"trace {TRACE}")


try:
    run_scenarios()
    run_frontend_abort()
    run_vision_result_reset()
    run_end_of_cycle()
    run_plc_connection()
except AssertionError as e:
    check(f"scenario aborted: {e}", False)

# =====================================================================================================
failed = [n for n, ok_ in RESULTS if not ok_]
print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
import os; os._exit(1 if failed else 0)
