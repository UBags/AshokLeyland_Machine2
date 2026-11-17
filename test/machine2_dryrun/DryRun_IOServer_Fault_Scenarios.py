"""Synthetic dry-run of costhetaio/commonplc/PLCIOServer.py (Allen-Bradley PLCDataIO by default): the three non-happy paths.

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

  G. Result handshake (vision checks) and the torque / press resets:
     G1 OK: OK=True, (0.05 s) Check=False, Done=True, (plc.sleeptime.between.done.and.reset_done 0.4 s) Done=False,
        OK=False, in that order; only then the next state.
     G2 NOT OK: OK=False, (0.05 s) Check=False, Done=True, (plc.sleeptime.between.done.and.re_read 0.1 s) Done=False,
        Check=False again; only then back to the picture state (Check and Done both False there). The PLC raises the trigger once per 2HB
        press: no picture without a press; the next press takes exactly one more picture.
     G3 NOT OK, 2HB pressed again 0.35 s after Done: acted on, the cycle moves on (field issue 1).
     G4 Set-Value (torque 1): Set=False, Value=0.0 (and RotationDuringTighteningDone=False) plc.handshake.torquevalues.reset.delay.sec
        after the read.
     G5 Done (component press): Done=False plc.handshake.componentpress.reset.delay.sec after the read.
     G6 a camera answer for another picture state is ignored; the answer for this check then moves the cycle on.
     G7 one scheduler thread for the delayed resets.

  H. End of cycle -> camera server: when the cycle completes, on an abort from the frontend and on an emergency abort
     from the PLC, the IOServer sends the end-of-cycle message on the camera queue (after an abort has cleared the
     queues), exactly once per cycle, and none while the cycle runs. (The camera server then drops the top bearing
     profile it keeps in memory for the nut / washer check.)

  I. PLC connection lost / PLC restarted / verified reset
     I0: at start the PC resets every per-cycle tag and arms PLC_PC_CheckQRCode (written and read back).
     I1: link lost between steps, PLC keeps its memory: writes and reads on hold. No PLC-raised tag is True (the PC
         cleared CheckKnuckle with the result handshake), so after the reconnect the fallback reference decides:
         PC_PLC_LH_RH_Selection still holds the side written in state 2 -> no abort, no reset, same machine state.
     I2: link lost while the PC waits for a camera result that arrives during the outage (CheckHub, raised by the PLC,
         is the reference): nothing is written until the PLC has been checked; then the result handshake is written and
         the cycle carries on; the PC's own Check=False removes CheckHub as a reference.
     I3: PLC restarts while the link is down, part-way through a cycle: the side setting reads 0 -> cycle aborted
         (QR server, frontend, camera told), every per-cycle tag at its default, CheckQRCode armed; the next cycle
         writes the settings again and completes.
     I4: PLC restarts while idle (waiting for the QR scan): detected, reset, CheckQRCode armed again.
     I5: reset done while the link is down -> incomplete; completed as soon as the link is back.
     I6: a tag the PLC does not take (write ignored) -> the reset reports it (written and read back, 5 attempts).

  J. (see run_servers_down) camera answer timeout, request ids, servers down.

  K. Abort races (2026-11-07)
     K1: an abort while Done=True is being written: the write lands, then the abort's reset sets Done False.
     K2: PC_PLC_ConnectionStatus keeps toggling while an abort runs on the heartbeat status thread.

  L. IOServer heartbeat (io2hb.q) DEAD while the read or the write loop is stuck, ALIVE once it goes round (2026-11-07).

  M. (see run_frontend_abort_request) the frontend's own abort.

  N. Bypass of the component press and second tightening (Machine Setting per model, 2026-11-13; see run_bypass)
     N1: the setting is on but the PLC has no PC_PLC_Skip_Second_Tightening (no address / not on the PLC): full run.
     N2: tag on the PLC: True written (read back) in state 2 with the other settings (Torque 2 setpoint too); after
         Torque 1 the frontend is told moveToState = split pin picture and states 13-19 are skipped; the cycle ends OK.
     N3: a model without the bypass: False written, full run.
     N4: the tag cannot be written (PLC ignores it): QR code NOT OK, nothing moves on; re-scan once fixed -> bypass.
  O. State sync phase A (CHANGES 108; see run_state_sync): the IOServer publishes every state change on io2festate.q
     and io:state. O1 published at start; O2 a cycle with a cap retake: consecutive sequence numbers, one pid,
     chained old -> new, the IOServer's path, reasons, cycle id and QR code; O3 the key holds the latest; O4 an
     abort's reason; O5 one publish refused by Redis: its number skipped (the frontend logs a GAP), then published.

COSTHETA_DRYRUN_SCENARIOS=G (or F,G ...) runs only those groups (A-E together).
Run from the project root with Redis running:  python test/machine2_dryrun/DryRun_IOServer_Fault_Scenarios.py
"""
import collections, sys, threading, time
import pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _plc_sim import setup, setting_tags, use_machine_settings, HEARTBEAT, STUCK        # COSTHETA_PLC_VENDOR=siemens -> SiemensPLCDataIO on a simulated S7 data block
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
REQUEST_IDS = []                          # request ids of the picture requests, in order (scenario J)

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
        t, state, take, requestId = readDataInCameraServerFromIOServer(rq, block=50, withRequestId=True)
        if (not take) and state == MS.READ_QR_CODE:
            END_OF_CYCLE_SEEN.append(time.time())
        if take:
            PICTURE_REQUESTS.append(state)
            REQUEST_IDS.append(requestId)
            result = CAMERA_PLAN[state].popleft() if CAMERA_PLAN[state] else ok
            if result == "SILENT":                       # the camera server never answers (scenario J)
                continue
            if isinstance(result, tuple) and result[0] == "LATE":   # ("LATE", secs, result): answers after secs
                threading.Timer(result[1], sendDataFromCameraServerToIOServer,
                                kwargs=dict(redisConnection=Redis('localhost', 6379), result=result[2],
                                            currentMachineState=state, requestId=requestId)).start()
                continue
            time.sleep(0.3)
            sendDataFromCameraServerToIOServer(rq, result=result, currentMachineState=state, requestId=requestId)
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
    time.sleep(3.0)       # IOServer resets these plc.handshake.torquevalues.reset.delay.sec (2 s) after reading them

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
    ("CAP", IOServer.PLC_PC_CheckCap, IOServer.PC_PLC_CapCheckDone, IOServer.PC_PLC_CapCheckOK),
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
            notOkMark = len(WRITES)
            with LOCK: TAGS[trig] = True
            assert wait_for(lambda: TAGS[done] is True), f"timeout waiting for NOT OK Done of {name}"
            okAtNotOk = TAGS[okTag]
            NOTOK_SEEN[name] = okAtNotOk
            # rule (a): with the NOT OK the PC writes OK=False, Done=True, then the trigger False, and sets Done False
            # after plc.handshake.reset.delay.sec; the operator presses the two-hand buttons again after that
            assert wait_for(lambda: (trig, False) in writes_of((trig,), notOkMark), 3), \
                f"the PC did not reset {trig} with the NOT OK of {name}"
            assert wait_for(lambda: TAGS[done] is False, RESET_DELAY + 3), f"Done of {name} not reset after the NOT OK"
            time.sleep(0.25)                         # the read loop sees the trigger False (one read interval)
            plc_check(trig, done)                    # retry
            RETRY_OK[name] = TAGS[okTag]
        else:
            plc_check(trig, done)

NOTOK_SEEN, RETRY_OK = {}, {}
from Configuration import CosThetaConfigurator as _C
RESET_DELAY = _C.getInstance().getPlcHandshakeResetDelay()

def wrapped():
    return wait_for(lambda: 28 in TRACE and TRACE[-1] in (1, 2), timeout=10)

def finish_qr_handshake():
    # The PC writes the whole result handshake itself (OK, CheckQRCode=False, Done=True, Done=False, and after a NOT OK
    # CheckQRCode=True again to re-arm the read), so the simulated PLC only waits for Done. (It used to set CheckQRCode
    # False 0.3 s after Done, which could overwrite the PC's re-arm after a NOT OK: E2's re-scan then never came.)
    assert wait_for(lambda: TAGS[IOServer.PC_PLC_QRCodeCheckDone] is True), "QR done timeout"
    time.sleep(0.3)

SETTING_TAGS = setting_tags(IOServer)      # the state-2 settings tags this PLC has

def writes_of(tags, since):
    return [(t, v) for t, v in WRITES[since:] if t in tags]

time.sleep(6)          # IOServer __init__ sleeps 5 s before starting its threads
WRITES_AT_START = list(WRITES)
# state sync (O1): the IOServer's first published states, taken now (the stream is capped at about 500 entries)
START_STATE_SNAPSHOTS = r.xrange(IO_STATE_STREAM, "-", "+", count=5)

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
        "PLC_PC_CheckCap", "PC_PLC_CapCheckOK", "PC_PLC_CapCheckDone",
        "PLC_PC_CheckBunk", "PC_PLC_BunkCheckOK", "PC_PLC_BunkCheckDone",
        "PLC_PC_CapPressDone",
        "PLC_PC_Station3TorqueValueSet", "PLC_PC_Station3TorqueValue",
        "PLC_PC_EmergencyAbort")}
    # Station 2 rotation-during-tightening done: reset with the Station 2 torque tags when the PLC provides it
    if IOServer.PLC_PC_Station2RotationDuringTighteningDone:
        RESET_TAGS["PLC_PC_Station2RotationDuringTighteningDone"] = "bool"
    NOT_RESET_ON_ABORT = {"PC_PLC_CycleAborted",        # set True after a PC-side abort, False at the next QR code
                          "PC_PLC_Skip_Second_Tightening",   # written True / False in every state 2
                          "PLC_PC_WatchTag", "PC_PLC_ConnectionStatusTag", "PC_PLC_LH_RH_Selection", "PC_PLC_ModelSelection",
                          "PC_PLC_TighteningTorque1Setpoint", "PC_PLC_TighteningTorque2Setpoint",
                          "PC_PLC_RotationRPM1", "PC_PLC_RotationRPM2",
                          "PC_PLC_RotationRPM", "PC_PLC_NoOfRotation1CCW", "PC_PLC_NoOfRotation1CW"}
    if not IOServer.PLC_PC_Station2RotationDuringTighteningDone:
        NOT_RESET_ON_ABORT.add("PLC_PC_Station2RotationDuringTighteningDone")      # not on this PLC (empty name)
    all_tag_attrs = {a for a in dir(IOServer) if a[:6] in ("PLC_PC", "PC_PLC")}
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
        # D6 (2026-11-08): settings per model: each model's cycle gets its own settings
        use_machine_settings("Test", model_name="DOST", rotation_rpm=22, tightening_torque1_setpoint=11.5,
                             tightening_torque2_setpoint=12.75, no_of_rotation1_ccw=3, no_of_rotation1_cw=4)
        use_machine_settings("Test", model_name="DOSTPLUS", rotation_rpm=38, tightening_torque1_setpoint=16.25,
                             tightening_torque2_setpoint=17.5, no_of_rotation1_ccw=2, no_of_rotation1_cw=1)
        for code, side, want in ((QR_DOST_LHS, 1, (11.5, 12.75, 22.0, 3.0, 4.0)),
                                 (QR_DOSTPLUS_RHS, 2, (16.25, 17.5, 38.0, 2.0, 1.0))):
            TRACE.clear(); mark = len(WRITES)
            plc_start_cycle(code); SCAN.set(); finish_qr_handshake()
            got = dict(writes_of(SETTING_TAGS, mark))
            expected = {IOServer.PC_PLC_LH_RH_Selection: side, IOServer.PC_PLC_TighteningTorque1Setpoint: want[0],
                        IOServer.PC_PLC_TighteningTorque2Setpoint: want[1], IOServer.PC_PLC_RotationRPM: want[2],
                        IOServer.PC_PLC_NoOfRotation1CCW: want[3], IOServer.PC_PLC_NoOfRotation1CW: want[4]}
            check(f"D6 per model: {'DOST' if side == 1 else 'DOSTPLUS'} cycle writes that model's settings {want}",
                  got == expected, str(got))
            run_steps()
            check("D6 cycle completed", wrapped())
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
        # E3 (2026-11-06): machine settings not available from the database -> the side is written, the other settings
        # are left as the PLC holds them, QR OK=True, the cycle goes ahead; the record gets the settings last written
        import costhetaio.commonplc.PLCIOServer as PIO
        lastWritten = dict(io._lastWrittenSettings)
        origGet = PIO.getMachineSettings
        PIO.getMachineSettings = lambda db: {}
        TRACE.clear(); mark = len(WRITES)
        r.delete("cycleSettings:" + QR_DOST_LHS)
        r.delete("feNotice:settings")
        plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
        qrw = writes_of((QR_OK, QR_DONE), mark)
        check("E3 no machine settings in the database -> only the side written",
              writes_of(SETTING_TAGS, mark) == [(IOServer.PC_PLC_LH_RH_Selection, 1)], str(writes_of(SETTING_TAGS, mark)))
        check("E3 -> QRCodeCheckOK=True then QRCodeCheckDone=True (the cycle goes ahead)",
              (QR_OK, True) in qrw and (QR_OK, False) not in qrw[:qrw.index((QR_OK, True))]
              and qrw.index((QR_OK, True)) < qrw.index((QR_DONE, True)), str(qrw))
        published = readCycleSettingsForQRCode(r, QR_DOST_LHS)
        check("E3 the record gets the settings the PLC holds (last written)", lastWritten != {} and published == lastWritten,
              f"published {published}, last written {lastWritten}")
        check("E3 'settings not updated' shown on the frontend", bool(r.get("feNotice:settings")))
        PIO.getMachineSettings = origGet
        run_steps()
        check("E3 cycle completes", wrapped())

        # E4: one setting refused by the PLC (3 attempts) -> the cycle goes ahead; that setting's last written value
        # is recorded
        failTag = IOServer.PC_PLC_TighteningTorque2Setpoint
        origWriteFloat = io.write_float
        io.write_float = types.MethodType(lambda self, tag, value, debug=False: False if tag == failTag
                                          else origWriteFloat(tag, value, debug), io)
        use_machine_settings("Test", tightening_torque2_setpoint=18.5)
        TRACE.clear(); mark = len(WRITES)
        r.delete("cycleSettings:" + QR_DOST_LHS)
        plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
        qrw = writes_of((QR_OK, QR_DONE), mark)
        io.write_float = origWriteFloat
        check("E4 a setting the PLC refuses -> QR OK=True, the cycle goes ahead", (QR_OK, True) in qrw, str(qrw))
        published = readCycleSettingsForQRCode(r, QR_DOST_LHS)
        check("E4 the refused setting is recorded with its last written value, the others with the new ones",
              published.get("tightening_torque2_setpoint") == lastWritten.get("tightening_torque2_setpoint")
              and published.get("tightening_torque1_setpoint") is not None, str(published))
        run_steps()
        check("E4 cycle completes", wrapped())

        # E5 (section 82): a setting written but not held by the PLC (read back differs) counts as not written
        rpmTag = IOServer.PC_PLC_RotationRPM
        lastRpm = io._lastWrittenSettings.get("rotation_rpm")
        use_machine_settings("Test", rotation_rpm=35)
        STUCK.add(rpmTag)
        TRACE.clear(); mark = len(WRITES)
        r.delete("cycleSettings:" + QR_DOST_LHS)
        plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
        STUCK.discard(rpmTag)
        published = readCycleSettingsForQRCode(r, QR_DOST_LHS)
        rpmWrites = [v for t, v in writes_of((rpmTag,), mark)]
        check("E5 RPM written but the PLC holds the old value (read back) -> 3 attempts, recorded as the PLC's (last written)",
              len(rpmWrites) == IOServer.SETTINGS_WRITE_ATTEMPTS and published.get("rotation_rpm") == lastRpm
              and TAGS[rpmTag] == lastRpm, f"writes {rpmWrites}, published {published.get('rotation_rpm')}, PLC {TAGS[rpmTag]}")
        run_steps()
        check("E5 cycle completes", wrapped())
        use_machine_settings("Test")


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
    """G. Result handshake sequences and the torque / press resets; see the module docstring."""
    from Configuration import CosThetaConfigurator
    C = CosThetaConfigurator.getInstance()
    delay = C.getPlcHandshakeResetDelay()
    tDelay, pDelay = C.getPlcTorqueValuesResetDelay(), C.getPlcComponentPressResetDelay()
    holdOk, holdNotOk = C.getPlcSleepTimeBetweenDoneAndResetDone(), C.getPlcSleepTimeBetweenDoneAndReRead()
    lo, hi = delay - 0.15, delay + 0.8                      # tolerance on "after the delay" (torque / press resets)
    print(f"\n=== G. Result handshake (Done held {holdOk} s OK / {holdNotOk} s NOT OK); resets after {delay} s ===", flush=True)
    MSP = MS
    def ordered(writes, wanted):                            # wanted appear in this order (other writes may interleave)
        i = 0
        for w in writes:
            if i < len(wanted) and w == wanted[i]:
                i += 1
        return i == len(wanted)
    def timed_writes(tags, since, timeout):
        t0 = time.time()
        ok_ = wait_for(lambda: all(tv in writes_of([t for t, _ in tags], since) for tv in tags), timeout)
        return (time.time() - t0) if ok_ else None
    def spy_state_change(method, fromState):                # WRITES index when the state changes away from fromState
        spy = {}
        original = getattr(io.machineState, method)
        def wrapper(state):
            if state == fromState:
                spy.setdefault("at", len(WRITES))
            return original(state)
        setattr(io.machineState, method, wrapper)
        return spy, (lambda: setattr(io.machineState, method, original))
    def done_hold(doneTag):                                 # seconds Done stayed True in the PLC
        assert wait_for(lambda: TAGS[doneTag] is True, 8), f"{doneTag} not set True"
        t0 = time.time()
        assert wait_for(lambda: TAGS[doneTag] is False, 3), f"{doneTag} not set back to False"
        return time.time() - t0
    assert wait_for(lambda: io.machineState.getCurrentState() in (MS.READ_QR_CODE, MS.WRITE_QR_CODE), 10)
    TRACE.clear()
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()

    # G1: knuckle OK
    KT, KO, KD = IOServer.PLC_PC_CheckKnuckle, IOServer.PC_PLC_KnuckleCheckOK, IOServer.PC_PLC_KnuckleCheckDone
    assert wait_for(lambda: io.machineState.getCurrentState() == MSP.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, 5)
    mark = len(WRITES)
    spy, restore = spy_state_change("incrementStateFrom", MSP.WRITE_RESULT_OF_CHECKING_KNUCKLE)
    with LOCK: TAGS[KT] = True                              # the PLC raises the trigger and keeps it
    held = done_hold(KD)
    wait_for(lambda: "at" in spy, 3); restore()
    w = writes_of((KT, KO, KD), mark)
    check("G1 OK: OK=True, Check=False, Done=True, Done=False, OK=False, in that order",
          w[:5] == [(KO, True), (KT, False), (KD, True), (KD, False), (KO, False)], str(w))
    check(f"G1 OK: Done held True about {holdOk} s ({held:.2f} s)", holdOk - 0.05 <= held <= holdOk + 0.3)
    check("G1 OK: next state only after Done=False and OK=False were written",
          "at" in spy and (KD, False) in [(t, v) for t, v in WRITES[mark:spy["at"]] if t == KD]
          and (KO, False) in [(t, v) for t, v in WRITES[mark:spy["at"]] if t == KO], f"spy {spy}")

    # G2: hub NOT OK; the PLC raises the trigger once per 2HB press
    HT, HO, HD = IOServer.PLC_PC_CheckHub, IOServer.PC_PLC_HubCheckOK, IOServer.PC_PLC_HubCheckDone
    HS = MSP.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING
    CAMERA_PLAN[HS].append(notok)
    assert wait_for(lambda: io.machineState.getCurrentState() == HS, 5)
    mark = len(WRITES); req0 = PICTURE_REQUESTS.count(HS)
    spy, restore = spy_state_change("decrementStateFrom", MSP.WRITE_RESULT_OF_CHECKING_HUB_AND_BOTTOM_BEARING)
    with LOCK: TAGS[HT] = True
    held = done_hold(HD)
    wait_for(lambda: "at" in spy, 3); restore()
    w = writes_of((HT, HO, HD), mark)
    check("G2 NOT OK: OK=False, Check=False, Done=True, Done=False, Check=False again, in that order",
          w[:5] == [(HO, False), (HT, False), (HD, True), (HD, False), (HT, False)], str(w))
    check(f"G2 NOT OK: Done held True about {holdNotOk} s ({held:.2f} s)", holdNotOk - 0.05 <= held <= holdNotOk + 0.3)
    check("G2 NOT OK: back to the picture state only after Done=False and the second Check=False (both False there)",
          "at" in spy and [(t, v) for t, v in WRITES[mark:spy["at"]] if t in (HT, HD)][-2:] == [(HD, False), (HT, False)]
          and wait_for(lambda: io.machineState.getCurrentState() == HS, 2) and TAGS[HT] is False and TAGS[HD] is False,
          f"spy {spy}, {io.machineState.getCurrentState().name}")
    time.sleep(1.0)
    check("G2 NOT OK: no picture without a 2HB press (1 s)", PICTURE_REQUESTS.count(HS) == req0 + 1,
          f"{PICTURE_REQUESTS.count(HS) - req0} pictures")
    mark = len(WRITES)
    plc_check(HT, HD)                                        # next 2HB press: one picture, answered OK
    check("G2 ... next 2HB press: exactly one more picture, answered OK, on to the top bearing",
          PICTURE_REQUESTS.count(HS) == req0 + 2 and (HO, True) in writes_of((HO,), mark) and
          wait_for(lambda: io.machineState.getCurrentState() == MSP.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, 3),
          f"{PICTURE_REQUESTS.count(HS) - req0} pictures, {io.machineState.getCurrentState().name}")

    # G3 field issue 1: top bearing NOT OK; 2HB pressed again 0.35 s after Done
    TT, TO, TD = (IOServer.PLC_PC_CheckHubAndSecondBearing, IOServer.PC_PLC_HubAndSecondBearingCheckOK,
                  IOServer.PC_PLC_HubAndSecondBearingCheckDone)
    TS = MSP.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING
    CAMERA_PLAN[TS].append(notok)
    mark = len(WRITES); req0 = PICTURE_REQUESTS.count(TS)
    with LOCK: TAGS[TT] = True
    assert wait_for(lambda: TAGS[TD] is True), "top bearing NOT OK Done timeout"
    time.sleep(0.35)
    with LOCK: TAGS[TT] = True                               # pressed again
    moved = wait_for(lambda: io.machineState.getCurrentState() == MSP.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER, 5)
    w = writes_of((TT, TO, TD), mark)
    check("G3 field issue 1: 2HB pressed 0.35 s after the NOT OK -> picture retaken, OK, cycle moves on (no stall)",
          moved and PICTURE_REQUESTS.count(TS) == req0 + 2 and (TO, True) in w, f"{w}; {io.machineState.getCurrentState().name}")
    check("G3 ... the NOT OK's Done went False before the retry's Done=True (the PLC sees a new Done)",
          ordered(w, [(TD, True), (TD, False), (TO, True), (TD, True)]), str(w))

    # nut (plain), then G4 (c): torque 1
    plc_check(IOServer.PLC_PC_CheckNutAndPlateWasher, IOServer.PC_PLC_NutAndPlateWasherCheckDone)
    ST, SV, RD = IOServer.PLC_PC_Station2TorqueValueSet, IOServer.PLC_PC_Station2TorqueValue, \
        IOServer.PLC_PC_Station2RotationDuringTighteningDone
    assert wait_for(lambda: io.machineState.getCurrentState() == MSP.READ_TIGHTENING_TORQUE_1_DONE, 5)
    mark = len(WRITES)
    with LOCK: TAGS[SV] = 19.08; TAGS[ST] = True
    if RD: TAGS[RD] = True
    assert wait_for(lambda: io.machineState.getCurrentState().value > MSP.READ_TIGHTENING_TORQUE_1.value, 5), "torque 1 not read"
    tRead = time.time()
    resets = [(ST, False), (SV, 0.0)] + ([(RD, False)] if RD else [])
    took = timed_writes(resets, mark, tDelay + 3)
    elapsed = time.time() - tRead
    check(f"G4 (c) torque 1 read -> Set=False, Value=0.0{', RotationDone=False' if RD else ''} about {tDelay} s later "
          f"({elapsed:.2f} s)", took is not None and tDelay - 0.15 <= elapsed <= tDelay + 0.8, str(writes_of([t for t, _ in resets], mark)))

    # bunk for component press (plain), then G5 (d): component press Done
    plc_check(IOServer.PLC_PC_CheckNoCapBunk, IOServer.PC_PLC_NoCapBunkCheckDone)
    CPD = IOServer.PLC_PC_ComponentPressDone
    assert wait_for(lambda: io.machineState.getCurrentState() == MSP.READ_COMPONENT_PRESS_DONE, 5)
    mark = len(WRITES)
    with LOCK: TAGS[CPD] = True                              # the PLC keeps it True; the PC resets it
    assert wait_for(lambda: io.machineState.getCurrentState() != MSP.READ_COMPONENT_PRESS_DONE, 5), "press Done not read"
    tRead = time.time()
    took = timed_writes([(CPD, False)], mark, pDelay + 3)
    elapsed = time.time() - tRead
    check(f"G5 (d) component press Done read -> Done=False about {pDelay} s later ({elapsed:.2f} s)",
          took is not None and pDelay - 0.15 <= elapsed <= pDelay + 0.8 and TAGS[CPD] is False, str(writes_of((CPD,), mark)))

    # G6: a camera answer for another picture state is not this check's result
    NB = MSP.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK
    NT, NO, ND = IOServer.PLC_PC_CheckNoBunk, IOServer.PC_PLC_NoBunkCheckOK, IOServer.PC_PLC_NoBunkCheckDone
    assert wait_for(lambda: io.machineState.getCurrentState() == NB, 5)
    PAUSE.set(); time.sleep(0.3)
    mark = len(WRITES)
    with LOCK: TAGS[NT] = True
    assert wait_for(lambda: io.machineState.getCurrentState() == MSP.WRITE_RESULT_OF_CHECKING_NO_BUNK, 5)
    rr = Redis('localhost', 6379)
    sendDataFromCameraServerToIOServer(rr, result=ok, currentMachineState=MSP.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)
    time.sleep(1.0)
    check("G6 camera answer for the knuckle while waiting for the no-bunk result: ignored (nothing written, no move)",
          io.machineState.getCurrentState() == MSP.WRITE_RESULT_OF_CHECKING_NO_BUNK and writes_of((NO, ND), mark) == [],
          f"{io.machineState.getCurrentState().name} {writes_of((NO, ND), mark)}")
    sendDataFromCameraServerToIOServer(rr, result=ok, currentMachineState=NB)
    moved = wait_for(lambda: TAGS[ND] is True, 3)
    rr.delete(io2cameraq)                                    # the take-picture request the paused camera did not see
    with LOCK: TAGS[NT] = False
    PAUSE.clear()
    check("G6 ... the answer for the no-bunk picture: OK, Done written, on to tightening torque 2",
          moved and wait_for(lambda: io.machineState.getCurrentState() == MSP.READ_TIGHTENING_TORQUE_2_DONE, 3),
          io.machineState.getCurrentState().name)
    time.sleep(delay + 0.3)
    check("G7 one scheduler thread for the delayed resets (no thread per reset)",
          sum(1 for th in threading.enumerate() if th.name == "PLC Handshake Reset Scheduler") == 1 and
          not any(th.name.startswith("Reset ") for th in threading.enumerate()))
    run_steps(first="TORQUE2")
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
    check("I1 waiting for the hub picture; no PLC-raised tag True (CheckKnuckle cleared by the PC), side recorded",
          wait_for(lambda: state() == MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING and io.referenceTag() is None
                   and io._sideWrittenThisCycle == 1, 3),
          f"{state().name}, reference {io.referenceTag()}, side {io._sideWrittenThisCycle}")
    aborts0, restarts0, mark = io.abortCount, io.plcRestartsDetected, len(WRITES)
    LINK["down"] = True
    check("I1 connection loss detected; writes and reads on hold", wait_for(lambda: io._plcCheckPending, 5))
    time.sleep(2.0)
    LINK["down"] = False
    check("I1 PLC checked after the reconnect", checked())
    check("I1 side still 1 (fallback reference) -> no restart: no abort, no reset, same state",
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
    check("I2 after the result the PC has cleared CheckHub: no PLC-raised tag True (the side is the fallback)",
          wait_for(lambda: io.referenceTag() is None and TAGS[HUB] is False, 3), str(io.referenceTag()))
    run_steps(first="TOP")
    check("I1/I2 cycle completes", wrapped(), f"trace {TRACE}")

    # I3
    time.sleep(0.5); TRACE.clear()
    plc_start_cycle(QR_DOSTPLUS_RHS); SCAN.set(); finish_qr_handshake()
    keep_check(KN, KN_DONE); keep_check(HUB, HUB_DONE)
    check("I3 waiting for the top bearing picture; no PLC-raised tag True, side 2 recorded",
          wait_for(lambda: state() == MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING and io.referenceTag() is None
                   and io._sideWrittenThisCycle == 2, 3), f"{state().name}, {io.referenceTag()}, {io._sideWrittenThisCycle}")
    aborts0, restarts0 = io.abortCount, io.plcRestartsDetected
    emerg0, qrab0, eoc = r.xlen(emergencyq), r.xlen(io2qrcodeabortq), time.time()
    LINK["down"] = True; time.sleep(1.5)
    plc_restart(WATCH)
    time.sleep(1.0); LINK["down"] = False
    check("I3 PLC restart detected (the side setting reads 0)", wait_for(lambda: io.plcRestartsDetected == restarts0 + 1, 10),
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


def run_servers_down():
    """J (2026-11-06). Camera answer timeout and request ids; what the IOServer does when another server is down:
    camera -> picture NOT OK at once; database -> cycle finished, next one held; frontend / heartbeat server -> cycle
    aborted, next one held. The simulated heartbeat server (_plc_sim.HEARTBEAT) plays the statuses."""
    print("\n=== J. Camera answer timeout; request ids; camera / database / frontend / heartbeat server down ===", flush=True)
    from Configuration import CosThetaConfigurator as C
    origTimeout = C.getPlcCameraAnswerTimeout
    C.getPlcCameraAnswerTimeout = classmethod(lambda cls: 3.0)
    def notok_handshake(trig, done, okTag, mark):
        w = writes_of((okTag, done, trig), mark)
        return (okTag, False) in w and (done, True) in w and w.index((okTag, False)) < w.index((done, True))
    def press_and_wait_done(trig, done, timeout):
        with LOCK: TAGS[trig] = True
        t0 = time.time()
        got = wait_for(lambda: TAGS[done] is True, timeout)
        return got, time.time() - t0
    def release(trig, done):
        with LOCK: TAGS[trig] = False
        wait_for(lambda: TAGS[done] is False, 3); time.sleep(0.3)

    # J1: the camera server never answers -> NOT OK after the timeout; the frontend gets the NOT OK (new request id)
    TRACE.clear()
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    CAMERA_PLAN[MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE].append("SILENT")
    r.delete("feNotice:camera")
    fe0 = r.xlen(camera2feq); mark = len(WRITES); n0 = len(REQUEST_IDS)
    got, took = press_and_wait_done(IOServer.PLC_PC_CheckKnuckle, IOServer.PC_PLC_KnuckleCheckDone, 8)
    check("J1 no camera answer -> NOT OK written after the 3 s timeout", got and 2.5 <= took <= 5.5
          and notok_handshake(IOServer.PLC_PC_CheckKnuckle, IOServer.PC_PLC_KnuckleCheckDone, IOServer.PC_PLC_KnuckleCheckOK, mark),
          f"{took:.1f} s {writes_of((IOServer.PC_PLC_KnuckleCheckOK, IOServer.PC_PLC_KnuckleCheckDone), mark)}")
    release(IOServer.PLC_PC_CheckKnuckle, IOServer.PC_PLC_KnuckleCheckDone)
    check("J1 back to the knuckle picture state", wait_for(lambda: io.machineState.getCurrentState() == MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, 3))
    active = (r.get(ACTIVE_CAMERA_REQUEST_KEY) or b"").decode()
    last = r.xrevrange(camera2feq, count=1)
    feMsg = last[0][1] if last else {}
    check("J1 frontend sent the NOT OK of the knuckle with the replacement request id (active)",
          r.xlen(camera2feq) == fe0 + 1 and feMsg.get(b"result") == notok.encode()
          and active == f"{REQUEST_IDS[n0]}:timeout" and feMsg.get(requestIdKeyString.encode()) == active.encode(),
          f"active {active}, request {REQUEST_IDS[n0:]}")
    check("J1 'Picture NOT OK: no answer ...' notice for the frontend", b"no answer" in (r.get("feNotice:camera") or b""))
    # a late camera result of the old request is not shown by the frontend
    sendDataFromCameraServerToFEServer(r, None, None, ok, MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, requestId=REQUEST_IDS[n0])
    r.xdel(camera2feq, *[m[0] for m in r.xrange(camera2feq)][:-1])     # keep only the late one
    t_, _, _, res_, _ = readDataInFEServerFromCameraServer(r, block=100)
    check("J1 the frontend reader drops a late result of the timed-out request", res_ is None and r.xlen(camera2feq) == 0)
    plc_check(IOServer.PLC_PC_CheckKnuckle, IOServer.PC_PLC_KnuckleCheckDone)       # retry: answered OK
    check("J1 retry -> OK, on to the hub", wait_for(lambda: io.machineState.getCurrentState() == MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING, 3))

    # J2: the camera answers NOT OK too late (after the timeout); the retry's OK is taken, not the late NOT OK
    CAMERA_PLAN[MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING].append(("LATE", 4.0, notok))
    mark = len(WRITES); n0 = len(REQUEST_IDS)
    got, took = press_and_wait_done(IOServer.PLC_PC_CheckHub, IOServer.PC_PLC_HubCheckDone, 8)
    release(IOServer.PLC_PC_CheckHub, IOServer.PC_PLC_HubCheckDone)
    time.sleep(1.5)                                    # the late NOT OK arrives (now in the queue)
    mark2 = len(WRITES)
    plc_check(IOServer.PLC_PC_CheckHub, IOServer.PC_PLC_HubCheckDone)              # retry, answered OK
    check("J2 timeout -> NOT OK; the late NOT OK of the first request ignored; the retry's OK taken",
          got and (IOServer.PC_PLC_HubCheckOK, True) in writes_of((IOServer.PC_PLC_HubCheckOK,), mark2)
          and wait_for(lambda: io.machineState.getCurrentState() == MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, 3)
          and len(set(REQUEST_IDS[n0:])) == 2, f"ids {REQUEST_IDS[n0:]} {writes_of((IOServer.PC_PLC_HubCheckOK,), mark2)}")

    # J3: camera server down -> the picture is NOT OK at once (no waiting for the timeout)
    HEARTBEAT["camera"] = False
    check("J3 camera server reported down", wait_for(lambda: io._cameraDown, 5))
    CAMERA_PLAN[MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING].append("SILENT")
    mark = len(WRITES)
    got, took = press_and_wait_done(IOServer.PLC_PC_CheckHubAndSecondBearing, IOServer.PC_PLC_HubAndSecondBearingCheckDone, 5)
    check("J3 camera down -> NOT OK at once (< 1.5 s)", got and took < 1.5
          and notok_handshake(IOServer.PLC_PC_CheckHubAndSecondBearing, IOServer.PC_PLC_HubAndSecondBearingCheckDone,
                              IOServer.PC_PLC_HubAndSecondBearingCheckOK, mark), f"{took:.2f} s")
    check("J3 notice 'camera server down'", b"camera server down" in (r.get("feNotice:camera") or b""))
    release(IOServer.PLC_PC_CheckHubAndSecondBearing, IOServer.PC_PLC_HubAndSecondBearingCheckDone)
    HEARTBEAT["camera"] = True
    check("J3 camera server back", wait_for(lambda: not io._cameraDown, 5))
    run_steps(first="TOP", last="NOBUNK")

    # J4: database down part-way -> the cycle is finished ("Continue till end of cycle"), the next one is held
    HEARTBEAT["db"] = False
    check("J4 database reported down", wait_for(lambda: io._databaseDown, 5))
    check("J4 notice 'Continue till end of cycle' during the cycle",
          wait_for(lambda: b"continue till end of cycle" in (r.get("feNotice:db") or b"").lower(), 4))
    run_steps(first="TORQUE2")
    check("J4 the cycle in progress completes", wrapped(), f"trace {TRACE}")
    check("J4 next cycle held: stays at READ_QR_CODE (CheckQRCode armed, QR code server not asked)",
          not wait_for(lambda: io.machineState.getCurrentState() != MS.READ_QR_CODE, 3)
          and TAGS[IOServer.PLC_PC_CheckQRCode] is True, io.machineState.getCurrentState().name)
    check("J4 notice 'next cycle on hold' after the cycle", b"next cycle on hold" in (r.get("feNotice:db") or b"").lower())
    HEARTBEAT["db"] = True
    check("J4 database back -> the QR code read is asked for (WRITE_QR_CODE)",
          wait_for(lambda: io.machineState.getCurrentState() == MS.WRITE_QR_CODE, 6), io.machineState.getCurrentState().name)
    check("J4 notice removed", wait_for(lambda: r.get("feNotice:db") is None, 4))

    # J5: database down while waiting for the scan -> QR code read stopped, back to READ_QR_CODE; back when it returns
    a0 = r.xlen(io2qrcodeabortq)
    HEARTBEAT["db"] = False
    check("J5 database down while waiting for the scan -> ABORT to the QR code server, back to READ_QR_CODE",
          wait_for(lambda: io.machineState.getCurrentState() == MS.READ_QR_CODE and r.xlen(io2qrcodeabortq) > a0, 6))
    HEARTBEAT["db"] = True
    check("J5 database back -> WRITE_QR_CODE again", wait_for(lambda: io.machineState.getCurrentState() == MS.WRITE_QR_CODE, 6))

    _cycleAbortedTag = IOServer.PC_PLC_CycleAborted
    if VENDOR == "allenbradley":          # the simulated Allen-Bradley takes any tag name; the Siemens one needs an address
        IOServer.PC_PLC_CycleAborted = "PC_PLC_CycleAborted"
    # J6: frontend down part-way -> abort (the frontend not told: it is down), next cycle held until it is back
    TRACE.clear()
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    run_steps(last="KNUCKLE")
    e0, aborts0 = r.xlen(emergencyq), io.abortCount
    HEARTBEAT["fe"] = False
    check("J6 frontend down -> cycle aborted, back at the QR code",
          wait_for(lambda: io.abortCount > aborts0 and io.machineState.getCurrentState() == MS.READ_QR_CODE, 6)
          and "frontend down" in io.lastAbortReason, io.lastAbortReason)
    check("J6 no emergency message to the (down) frontend", r.xlen(emergencyq) == e0)
    if VENDOR == "allenbradley":
        check("J6 PC_PLC_CycleAborted = True after the PC's abort", TAGS["PC_PLC_CycleAborted"] is True)
    check("J6 next cycle held while the frontend is down",
          not wait_for(lambda: io.machineState.getCurrentState() != MS.READ_QR_CODE, 3))
    HEARTBEAT["fe"] = True
    check("J6 frontend back -> QR code read asked for", wait_for(lambda: io.machineState.getCurrentState() == MS.WRITE_QR_CODE, 6))

    # J7: heartbeat server silent part-way -> abort, frontend and camera server told, next cycle held
    TRACE.clear()
    QR_PLAN.append((ok, QR_DOSTPLUS_RHS)); SCAN.set(); finish_qr_handshake()
    if VENDOR == "allenbradley":
        check("J6 PC_PLC_CycleAborted = False once the next QR code is accepted", TAGS["PC_PLC_CycleAborted"] is False)
    run_steps(last="HUB")
    e0, aborts0, eoc0 = r.xlen(emergencyq), io.abortCount, len(END_OF_CYCLE_SEEN)
    HEARTBEAT["silent"] = True
    silence = C.getInstance().getHeartbeatServerSilenceSecs()
    check(f"J7 heartbeat server silent > {silence:g} s -> cycle aborted, frontend and camera server told",
          wait_for(lambda: io.abortCount > aborts0 and io.machineState.getCurrentState() == MS.READ_QR_CODE, silence + 5)
          and "heartbeat server down" in io.lastAbortReason and r.xlen(emergencyq) > e0
          and wait_for(lambda: len(END_OF_CYCLE_SEEN) > eoc0, 3), io.lastAbortReason)
    check("J7 next cycle held while the heartbeat server is down",
          not wait_for(lambda: io.machineState.getCurrentState() != MS.READ_QR_CODE, 3))
    HEARTBEAT["silent"] = False
    check("J7 heartbeat server back -> QR code read asked for",
          wait_for(lambda: io.machineState.getCurrentState() == MS.WRITE_QR_CODE, 6))
    TRACE.clear()
    QR_PLAN.append((ok, QR_DOST_LHS)); SCAN.set(); finish_qr_handshake()
    run_steps()
    check("J a full normal cycle completes afterwards", wrapped(), f"trace {TRACE}")
    # J8 (section 83): the heartbeat server's Redis check fails -> no new cycle (QR read stopped / not asked for)
    check("J8 at the QR code after the cycle", wait_for(lambda: io.machineState.getCurrentState() == MS.WRITE_QR_CODE, 6))
    HEARTBEAT["redis"] = False
    check("J8 Redis reported not storing -> QR read stopped, held at READ_QR_CODE",
          wait_for(lambda: io.newCycleHoldReason() == "Redis not storing data"
                   and io.machineState.getCurrentState() == MS.READ_QR_CODE, 6)
          and not wait_for(lambda: io.machineState.getCurrentState() != MS.READ_QR_CODE, 2), io.newCycleHoldReason())
    HEARTBEAT["redis"] = True
    check("J8 Redis storing again -> QR read asked for", wait_for(lambda: io.machineState.getCurrentState() == MS.WRITE_QR_CODE, 6))
    IOServer.PC_PLC_CycleAborted = _cycleAbortedTag
    C.getPlcCameraAnswerTimeout = origTimeout


def run_abort_races():
    """K (2026-11-07). K1: an abort while a handshake write is on its way to the PLC: the write lands first and the
    abort's reset then sets it back (before, the abort could reset the tags in between and the write landed after
    it: Done=True left set for the next cycle). K2: the PC's connection-status toggle keeps toggling while an abort
    runs (it used to be toggled by the thread that runs the heartbeat aborts)."""
    print("\n=== K. Abort during a handshake write; connection-status toggle during an abort ===", flush=True)
    KT, KD = IOServer.PLC_PC_CheckKnuckle, IOServer.PC_PLC_KnuckleCheckDone
    # K1
    TRACE.clear()
    check("K1 at the QR code", wait_for(lambda: io.machineState.getCurrentState() == MS.WRITE_QR_CODE, 8),
          io.machineState.getCurrentState().name)
    QR_PLAN.append((ok, QR_DOST_LHS)); SCAN.set(); finish_qr_handshake()
    check("K1 waiting for the knuckle picture",
          wait_for(lambda: io.machineState.getCurrentState() == MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, 5))
    inFlight = threading.Event()
    original = io.write_bool
    def slowDone(tag, value, debug=True):
        if tag == KD and value is True and not inFlight.is_set():
            inFlight.set()
            time.sleep(1.5)                  # the write of Done=True is on its way (a slow PLC write)
        return original(tag, value, debug=debug)
    io.write_bool = slowDone
    aborts0 = io.abortCount
    with LOCK: TAGS[KT] = True               # 2HB press; the camera answers OK -> OK, Check False, Done True ...
    check("K1 the knuckle OK handshake reached the Done=True write", inFlight.wait(8))
    sendAbortFromFEServerToIOServer(r, reason="dry run K1")      # ... abort while Done=True is being written
    check("K1 aborted, back at the QR code",
          wait_for(lambda: io.abortCount > aborts0 and io.machineState.getCurrentState() in (MS.READ_QR_CODE, MS.WRITE_QR_CODE), 6),
          io.machineState.getCurrentState().name)
    time.sleep(1.0)
    del io.write_bool
    with LOCK: TAGS[KT] = False
    check("K1 Done of the aborted check is False after the abort (not left True for the next cycle)",
          TAGS[KD] is False, f"{KD}={TAGS[KD]}")
    # K2
    toggleTag = IOServer.PC_PLC_ConnectionStatusTag
    if toggleTag:
        TRACE.clear()
        check("K2 at the QR code", wait_for(lambda: io.machineState.getCurrentState() == MS.WRITE_QR_CODE, 8))
        QR_PLAN.append((ok, QR_DOST_LHS)); SCAN.set(); finish_qr_handshake()
        originalReset = io.resetForNextCycle
        def slowReset(*a, **k):
            time.sleep(5.0)                  # a slow reset (flaky link, many retries)
            return originalReset(*a, **k)
        io.resetForNextCycle = slowReset
        aborts0 = io.abortCount
        HEARTBEAT["fe"] = False               # frontend down -> the status thread aborts the cycle
        check("K2 the abort has started", wait_for(lambda: io.machineState.getCurrentState() == MS.READ_QR_CODE, 8))
        mark = len(WRITES); time.sleep(4.5)
        toggles = [v for t, v in WRITES[mark:] if t == toggleTag]
        check("K2 connection status toggled while the abort ran (>= 2 writes in 4.5 s)", len(toggles) >= 2, f"{toggles}")
        HEARTBEAT["fe"] = True
        wait_for(lambda: io.abortCount > aborts0, 6)
        del io.resetForNextCycle
        check("K2 frontend back -> QR code read asked for",
              wait_for(lambda: io.machineState.getCurrentState() == MS.WRITE_QR_CODE, 8))
    TRACE.clear()
    QR_PLAN.append((ok, QR_DOSTPLUS_RHS)); SCAN.set(); finish_qr_handshake()
    run_steps()
    check("K a full normal cycle completes afterwards", wrapped(), f"trace {TRACE}")


def run_loop_health():
    """L (2026-11-07). The IOServer's heartbeat (io2hb.q) is DEAD while its read or write loop is stuck, and ALIVE again
    once it goes round (utils/WorkerHealth.py); before, the heartbeat thread said ALIVE whatever the loops did."""
    print("\n=== L. IOServer heartbeat follows the read / write loops ===", flush=True)
    from Configuration import CosThetaConfigurator as C
    origLimit = C.getWorkLoopMaxSilence
    C.getWorkLoopMaxSilence = classmethod(lambda cls: 2.0)
    def latest():
        last = r.xrevrange(io2hbq, count=1)
        return last[0][1].get(connectionStatusKeyString.encode(), b"").decode() if last else None
    check("L0 at the QR code, heartbeat ALIVE", wait_for(lambda: latest() == ALIVE, 5), latest())
    release = threading.Event()
    original = io.getCurrentTagsToBeRead
    def stuck():
        release.wait(30)                                  # the read loop hangs (e.g. a driver call that never returns)
        return original()
    io.getCurrentTagsToBeRead = stuck
    check("L1 read loop stuck > 2 s -> IOServer heartbeat DEAD", wait_for(lambda: latest() == DEAD, 6), latest())
    release.set(); time.sleep(0.3)
    del io.getCurrentTagsToBeRead
    check("L1 read loop going round again -> ALIVE", wait_for(lambda: latest() == ALIVE, 5), latest())
    release2 = threading.Event()
    originalW = io.getCurrentTagsToBeWritten
    def stuckW():
        release2.wait(30)
        return originalW()
    io.getCurrentTagsToBeWritten = stuckW
    check("L2 write loop stuck > 2 s -> DEAD", wait_for(lambda: latest() == DEAD, 6), latest())
    release2.set(); time.sleep(0.3)
    del io.getCurrentTagsToBeWritten
    check("L2 write loop going round again -> ALIVE", wait_for(lambda: latest() == ALIVE, 5), latest())
    # L3 / L4: the emergency monitoring thread and the connection-status toggle thread (2026-11-07)
    for label, attr, tagName in (("L3 emergency monitoring", "read_bool", IOServer.PLC_PC_EmergencyAbort),
                                 ("L4 connection-status toggle", "write_bool", IOServer.PC_PLC_ConnectionStatusTag)):
        hold = threading.Event()
        orig = getattr(io, attr)
        def blocking(tag, *a, _orig=orig, _tag=tagName, _hold=hold, **k):
            if tag == _tag:
                _hold.wait(30)                    # this thread's PLC call hangs
            return _orig(tag, *a, **k)
        setattr(io, attr, blocking)
        check(f"{label} stuck > 2 s -> DEAD", wait_for(lambda: latest() == DEAD, 8), latest())
        hold.set(); time.sleep(0.3)
        delattr(io, attr)
        check(f"{label} going round again -> ALIVE", wait_for(lambda: latest() == ALIVE, 6), latest())
    C.getWorkLoopMaxSilence = origLimit
    TRACE.clear()
    check("L at the QR code", wait_for(lambda: io.machineState.getCurrentState() == MS.WRITE_QR_CODE, 8))
    QR_PLAN.append((ok, QR_DOST_LHS)); SCAN.set(); finish_qr_handshake()
    run_steps()
    check("L a full normal cycle completes afterwards", wrapped(), f"trace {TRACE}")


# COSTHETA_DRYRUN_SCENARIOS=G (or e.g. "F,G"): run only these groups (A-E are one group); default: all
import os as _os
def run_frontend_abort_request():
    """M (2026-11-08). The frontend's own, password-protected abort (FRONTEND_ABORT_REQUEST on fe2io.q): the IOServer
    tries PC_PLC_CycleAborted = True (3 attempts); only if written does it abort its cycle. No tag name, no address on
    the PLC, or the write fails: logged, the cycle goes on untouched (no abort, no QR code server message)."""
    print("\n=== M. Frontend abort request: PLC flag written -> abort; else logged, cycle untouched ===", flush=True)
    from utils.RedisUtils import sendFrontendAbortRequestFromFEServerToIOServer
    tag = "PC_PLC_CycleAborted"
    check("M properties name the tag (pc.plc.cycle.aborted.tagname)", IOServer.PC_PLC_CycleAborted == tag,
          IOServer.PC_PLC_CycleAborted)
    check("M the IOServer started although the PLC has no address / tag for it (optional tag)",
          tag not in IOServer.requiredTagNames().values())

    def untouched(label, prepare, restore):
        TRACE.clear()
        plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
        run_steps(last="HUB")
        assert wait_for(lambda: io.machineState.getCurrentState() == MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, 5)
        prepare()
        aborts0, qr0, state0 = io.abortCount, r.xlen(io2qrcodeabortq), io.machineState.getCurrentState()
        sendFrontendAbortRequestFromFEServerToIOServer(r, reason=f"dry run {label}")
        time.sleep(2.5)
        check(f"{label}: no abort, state unchanged, no message to the QR code server",
              io.abortCount == aborts0 and io.machineState.getCurrentState() == state0 and r.xlen(io2qrcodeabortq) == qr0,
              (io.abortCount - aborts0, io.machineState.getCurrentState().name))
        restore()
        run_steps(first="TOP")
        check(f"{label}: the cycle completes as if nothing happened", wrapped(), f"trace {TRACE}")

    # M1: the PLC has no address / tag for it (Siemens: none in IOServerProperties.yaml; Allen-Bradley: not on the PLC)
    if VENDOR == "allenbradley":           # the simulated controller takes any name: play a controller without the tag
        untouched("M1 no tag on the PLC", lambda: io._tagsMissingOnPlc.add(tag), lambda: io._tagsMissingOnPlc.discard(tag))
    else:
        untouched("M1 no address in IOServerProperties.yaml", lambda: None, lambda: None)
    # M2: no tag name in the properties
    untouched("M2 no tag name", lambda: setattr(IOServer, "PC_PLC_CycleAborted", ""),
              lambda: setattr(IOServer, "PC_PLC_CycleAborted", tag))
    if VENDOR == "allenbradley":
        # M3: the write fails 3 times -> logged, untouched
        attempts = []
        orig = io.write_bool
        def failing(t, v, *a, **k):
            if t == tag:
                attempts.append(v); return False
            return orig(t, v, *a, **k)
        untouched("M3 write fails", lambda: setattr(io, "write_bool", failing), lambda: delattr(io, "write_bool"))
        check("M3 ... after exactly 3 attempts", len(attempts) == 3, attempts)
        # M4: the PLC has the tag and takes the write -> the cycle is aborted
        TRACE.clear()
        plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
        run_steps(last="HUB")
        assert wait_for(lambda: io.machineState.getCurrentState() == MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, 5)
        aborts0, qr0 = io.abortCount, r.xlen(io2qrcodeabortq)
        sendFrontendAbortRequestFromFEServerToIOServer(r, reason="dry run M4")
        check("M4 tag on the PLC: PC_PLC_CycleAborted = True, cycle aborted, QR code server told, back at the QR code",
              wait_for(lambda: io.abortCount > aborts0 and io.machineState.getCurrentState() in (MS.READ_QR_CODE, MS.WRITE_QR_CODE), 6)
              and TAGS[tag] is True and r.xlen(io2qrcodeabortq) > qr0 and "dry run M4" in io.lastAbortReason,
              (io.lastAbortReason, TAGS.get(tag)))
        TRACE.clear()
        QR_PLAN.append((ok, QR_DOST_LHS)); SCAN.set(); finish_qr_handshake()
        check("M4 ... and False again once the next QR code is accepted", TAGS[tag] is False)
        run_steps()
        check("M4 the next cycle completes", wrapped())


def run_bypass():
    """N (2026-11-13). Machine Setting "Bypass Component Press and Second Tightening" (per model)."""
    print("\n=== N. Bypass of the component press and second tightening ===", flush=True)
    import _plc_sim
    import costhetaio.commonplc.PLCIOServer as PIO
    tag = "PC_PLC_Skip_Second_Tightening"
    SKIPPED = set(range(13, 20))
    QR_OK = IOServer.PC_PLC_QRCodeCheckOK
    check("N properties name the tag (pc.plc.skip.second.tightening.tagname)", IOServer.PC_PLC_Skip_Second_Tightening == tag,
          IOServer.PC_PLC_Skip_Second_Tightening)
    check("N optional tag: not needed for the IOServer to start", tag not in IOServer.requiredTagNames().values())
    sent = []                                       # (state, moveToState) of every IOServer -> frontend message
    origSend = PIO.sendDataFromIOServerToFEServer
    def spy(*a, **k):
        sent.append((k.get("currentMachineState"), k.get("moveToState", MS.INVALID_STATE)))
        return origSend(*a, **k)
    PIO.sendDataFromIOServerToFEServer = spy
    use_machine_settings("Test", model_name="DOST", bypass_press_and_tightening2=True)
    use_machine_settings("Test", model_name="DOSTPLUS", bypass_press_and_tightening2=False)
    try:
        # N1: no tag on the PLC -> full run, nothing written to it
        io._tagsMissingOnPlc.add(tag)
        TRACE.clear(); mark = len(WRITES); r.delete("cycleSettings:" + QR_DOST_LHS)
        plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
        run_steps()
        check("N1 setting on, PLC without the tag: full run (states 13-19 run), the tag not written",
              wrapped() and SKIPPED <= set(TRACE) and not writes_of((tag,), mark), f"trace {TRACE}")
        check("N1 ... not published as bypassed", "bypass_press_and_tightening2" not in readCycleSettingsForQRCode(r, QR_DOST_LHS))
        io._tagsMissingOnPlc.discard(tag)
        # N2: tag on the PLC -> True, jump after Torque 1
        TRACE.clear(); mark = len(WRITES); del sent[:]; pics0 = len(PICTURE_REQUESTS)
        r.delete("cycleSettings:" + QR_DOST_LHS)
        plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
        check("N2 state 2: True written (with the settings, Torque 2 setpoint included), before QRCodeCheckOK=True",
              TAGS[tag] is True and (tag, True) in WRITES[mark:]
              and dict(writes_of(SETTING_TAGS, mark)).get(IOServer.PC_PLC_TighteningTorque2Setpoint) == 19.0
              and WRITES.index((tag, True), mark) < WRITES.index((QR_OK, True), mark), str(writes_of((tag, QR_OK), mark)))
        check("N2 published as bypassed (the frontend marks the 4 tiles)",
              readCycleSettingsForQRCode(r, QR_DOST_LHS).get("bypass_press_and_tightening2") is True)
        run_steps(last="TORQUE1")
        check("N2 after Torque 1: straight to the split pin picture",
              wait_for(lambda: io.machineState.getCurrentState() == MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER, 5),
              io.machineState.getCurrentState().name)
        check("N2 the frontend is told moveToState = split pin picture with the Torque 1 value",
              (MS.READ_TIGHTENING_TORQUE_1, MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER) in sent, str(sent))
        run_steps(first="SPLITPIN")
        check("N2 cycle completes without states 13-19 (no bung pictures, no press, no Torque 2)",
              wrapped() and not (SKIPPED & set(TRACE)), f"trace {TRACE}")
        check("N2 no picture asked for the bung checks",
              not ({MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS, MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK}
                   & set(PICTURE_REQUESTS[pics0:])), str(PICTURE_REQUESTS[pics0:]))
        # N3: a model without the bypass -> False, full run
        TRACE.clear(); mark = len(WRITES)
        plc_start_cycle(QR_DOSTPLUS_RHS); SCAN.set(); finish_qr_handshake()
        check("N3 model without the bypass: False written (the PLC held True from the last component)",
              TAGS[tag] is False and (tag, False) in WRITES[mark:])
        run_steps()
        check("N3 full run", wrapped() and SKIPPED <= set(TRACE), f"trace {TRACE}")
        # N4: the PLC does not take the tag -> QR code NOT OK
        STUCK.add(tag)
        TRACE.clear(); mark = len(WRITES)
        plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
        qrw = writes_of((QR_OK,), mark)
        check("N4 tag not taken by the PLC -> QRCodeCheckOK=False (no OK=True), not moved on",
              (QR_OK, False) in qrw and (QR_OK, True) not in qrw and MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE.value not in TRACE
              and wait_for(lambda: io.machineState.getCurrentState() in (MS.READ_QR_CODE, MS.WRITE_QR_CODE)), f"{qrw} {TRACE}")
        STUCK.discard(tag)
        QR_PLAN.append((ok, QR_DOST_LHS))
        wait_for(lambda: io.machineState.getCurrentState() == MS.WRITE_QR_CODE)
        TRACE.clear(); SCAN.set(); finish_qr_handshake()
        run_steps(last="TORQUE1")
        assert wait_for(lambda: io.machineState.getCurrentState() == MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER, 5)
        run_steps(first="SPLITPIN")
        check("N4 re-scan once fixed: True written, bypassed cycle completes", wrapped() and not (SKIPPED & set(TRACE)),
              f"trace {TRACE}")
    finally:
        PIO.sendDataFromIOServerToFEServer = origSend
        STUCK.discard(tag)
        io._tagsMissingOnPlc.discard(tag)
        use_machine_settings("Test", model_name="DOST", bypass_press_and_tightening2=False)


_ONLY = {x.strip().upper() for x in (_os.environ.get("COSTHETA_DRYRUN_SCENARIOS") or "").split(",") if x.strip()}
def run_state_sync():
    """O (CHANGES 108). State sync phase A: the IOServer publishes every state change (io2festate.q, io:state)."""
    print("\n=== O. State sync: every IOServer state change published for the frontend ===", flush=True)
    import json, os as _os
    import costhetaio.commonplc.PLCIOServer as PIO
    def subsequence(short, long_):         # the tracer polls every 5 ms and can miss a state; the stream cannot
        it = iter(long_)
        return all(x in it for x in short)
    def published_since(seq):
        out = []
        for _, f in r.xrange(IO_STATE_STREAM, "-", "+"):
            snap = json.loads(f[b"snapshot"])
            if snap["seq"] > seq:
                out.append(snap)
        return out
    first = [json.loads(f[b"snapshot"]) for _, f in START_STATE_SNAPSHOTS]
    start = [s for s in first if s.get("reason") == "IOServer start"]
    check("O1 the IOServer published its state at start (seq 1, READ_QR_CODE, its pid, reason 'IOServer start')",
          start and start[0]["seq"] == 1 and start[0]["new"] == int(MS.READ_QR_CODE) and start[0]["pid"] == _os.getpid(),
          str(first[:2]))
    # O2: a cycle with one NOT OK (cap): every state change published, in order, nothing missing
    time.sleep(0.5); TRACE.clear()
    mark = io._stateSeq
    CAMERA_PLAN[MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP].append(notok)
    plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
    run_steps(retry_on_notok=("CAP",))
    check("O2 cycle completes", wrapped(), f"trace {TRACE}")
    time.sleep(0.5)
    snaps = published_since(mark)
    seqs = [s["seq"] for s in snaps]
    chained = all(b["old"] == a["new"] for a, b in zip(snaps, snaps[1:]))
    check("O2 sequence numbers consecutive (nothing missing), one pid, each change starts where the last ended",
          seqs == list(range(mark + 1, mark + 1 + len(snaps))) and len({s["pid"] for s in snaps}) == 1 and chained,
          f"{seqs[:6]}... chained={chained}")
    path = [s["new"] for s in snaps]
    # the simulated PLC raises CheckQRCode again at once, so the IOServer is at 2 before this cycle's knuckle and again
    # right after the cycle: the path is 3 ... 28, 1 (2 for the next one); TRACE samples the same states
    end = path.index(int(MS.READ_QR_CODE)) if int(MS.READ_QR_CODE) in path else len(path)
    check("O2 the published path is the IOServer's path (knuckle 3 ... 28, back to 1; cap retaken: 22 23 22 23)",
          path[0] == int(MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE) and path[end - 1] == int(MS.READ_FREE_ROTATION_TORQUE_1)
          and [22, 23, 22, 23] == [p for p in path[:end] if p in (22, 23)] and subsequence(TRACE[:-1], path),
          f"{path} / trace {TRACE}")
    reasons = {s["reason"] for s in snaps}
    check("O2 reasons: 'NOT OK: back to the check (retake)' for the cap, 'cycle completed' at the end",
          "NOT OK: back to the check (retake)" in reasons and snaps[end]["reason"] == "cycle completed", str(sorted(reasons)))
    cyc = {s["cycleId"] for s in snaps if s["new"] not in (int(MS.READ_QR_CODE), int(MS.WRITE_QR_CODE))}
    check("O2 cycle id and QR code carried (one cycle id from the knuckle on; the QR code of the cycle)",
          len(cyc) == 1 and any(s["qr"] == QR_DOST_LHS for s in snaps), f"{cyc} {[s['qr'][:12] for s in snaps[-3:]]}")
    key = readIOServerStateSnapshot(r)
    check("O3 io:state holds the latest snapshot (for a frontend that starts mid-cycle)",
          key is not None and key == published_since(0)[-1], str(key))
    # O4: abort -> reason carried
    time.sleep(0.5); TRACE.clear(); mark = io._stateSeq
    plc_start_cycle(QR_DOSTPLUS_RHS); SCAN.set(); finish_qr_handshake()
    run_steps(last="HUB")
    aborts0 = io.abortCount
    sendAbortFromFEServerToIOServer(r, reason="dry run O4")
    check("O4 abort handled", wait_for(lambda: io.abortCount > aborts0, 3))
    time.sleep(0.5)
    toQR = [s for s in published_since(mark) if s["new"] == int(MS.READ_QR_CODE)]
    last = toQR[-1] if toQR else {}
    check("O4 the abort's state change (to READ_QR_CODE) published with its reason ('abort: ...')",
          str(last.get("reason")).startswith("abort:") and "dry run O4" in last.get("reason", ""), str(last))
    # O5: Redis does not take one state change -> the sequence number still goes up (the frontend sees the gap), the
    # failure is logged once and the audit line says NOT published; the next change is published again
    original = PIO.publishIOServerState
    calls = {"n": 0}
    def failOnce(conn, snap):
        calls["n"] += 1
        return False if calls["n"] == 1 else original(conn, snap)
    PIO.publishIOServerState = failOnce
    try:
        time.sleep(0.5); TRACE.clear(); mark = io._stateSeq
        plc_start_cycle(QR_DOST_LHS); SCAN.set(); finish_qr_handshake()
        run_steps(last="KNUCKLE")
        time.sleep(0.5)
        got = [s["seq"] for s in published_since(mark)]
        check("O5 one state change not published: its sequence number skipped, the next ones published",
              got and got[0] == mark + 2 and not io._statePublishFailing, f"mark {mark}, got {got[:5]}")
    finally:
        PIO.publishIOServerState = original
    run_steps(first="HUB")
    check("O5 the cycle completes as usual", wrapped(), f"trace {TRACE}")

def _wanted(*letters):
    return not _ONLY or bool(_ONLY & set(letters))

try:
    if _wanted("A", "B", "C", "D", "E"): run_scenarios()
    if _wanted("F"): run_frontend_abort()
    if _wanted("G"): run_vision_result_reset()
    if _wanted("H"): run_end_of_cycle()
    if _wanted("I"): run_plc_connection()
    if _wanted("J"): run_servers_down()
    if _wanted("K"): run_abort_races()
    if _wanted("L"): run_loop_health()
    if _wanted("M"): run_frontend_abort_request()
    if _wanted("N"): run_bypass()
    if _wanted("O"): run_state_sync()
except AssertionError as e:
    check(f"scenario aborted: {e}", False)

# =====================================================================================================
failed = [n for n, ok_ in RESULTS if not ok_]
print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
import os; os._exit(1 if failed else 0)
