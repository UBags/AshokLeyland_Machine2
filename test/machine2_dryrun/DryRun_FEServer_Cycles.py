"""Dry run of the FRONTEND alone: the real frontend/AshokLeylandFrontEnd.py (PySide6) with the QR, camera and IO servers
simulated on their Redis queues, and the frontend's cycle record captured from fe2db.q (no DBServer, no IOServer).
Automatic PASS/FAIL checks; exit code 1 on failure. Covers what test/Test_FEServer_Simulated_Inputs_UI.py,
Test_FEServer_Mock_With_Image_Files_UI.py and Test_FEServer_With_Live_Camera_UI.py drive by hand:

  F1. All-OK cycle (DOST LHS). After each QR / camera / IO message the frontend moves to the next of its 15 steps;
      each tile gets the result; the QR display string is shown; at the end the success screen is shown, the frontend
      sends ONE record to fe2db.q and returns to READ_QR_CODE. The record carries the QR code, all 14 step results OK,
      the torques rounded to 2 decimals (19.084 -> 19.08, 19.026 -> 19.03, 2.411 -> 2.41), real datetimes (not 1970)
      and the 9 original pictures, pixel-identical to what the camera sent.
  F2. Camera NOT OK, then OK (knuckle and cap, as when the IOServer makes the camera retake). After NOT OK the tile
      shows NOT OK and the frontend stays on that step; after OK it moves on; the record has OK for both steps.
  F3. IO message for the wrong state (torque-2 message while the frontend waits for torque 1) is consumed and
      ignored; the right message then moves the frontend on.
  F4. Emergency abort part-way (after the hub check): the abort screen is shown, a record is sent with the reached
      steps OK and the rest NOT OK / 1970 / torque 0.0 / no picture, overall NOT OK, and the frontend returns to
      READ_QR_CODE; the next cycle then runs normally.
  F5. Final free-rotation torque reported NOT OK: the record's overall result is NOT OK and the failure screen
      (not the success screen) is shown.
  F6. Machine Settings menu: present and enabled for an Administrator; only Administrator and Supervisor may change
      the settings (an Operator is refused before the dialog opens, nothing saved); the dialog's entry checks
      (RPM whole number 20-40, torques 0.01-19.99 with at most 2 decimals, rotation counts 1 or 2).

Not covered here (needs real pictures, the models or the camera): running the real camera/Check*.py algorithms on
image files and the live camera - use the Test_FEServer_*_UI.py programs for that.

Needs Redis and PostgreSQL (the frontend's counters read the TEST database). Headless:
    QT_QPA_PLATFORM=offscreen python <project>/test/machine2_dryrun/DryRun_FEServer_Cycles.py
"""
import os, sys, time, threading, types
import pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.modules.setdefault('winsound', types.SimpleNamespace(PlaySound=lambda *a, **k: None, SND_FILENAME=0, SND_ASYNC=0, Beep=lambda *a: None))
import numpy as np, cv2
from redis import Redis
from statemachine.StateMachine import MachineState as MS
from utils.RedisUtils import *
import persistence.Persistence as P

RESULTS = []
def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""), flush=True)

r = Redis('localhost', 6379); clearQueues(r)
P.setDatabaseName("Test")

# ---------- record what the frontend shows (wrap its display methods before the window is built) ----------
from frontend.newwidgets.CosThetaResultsContainer import CosThetaResultsContainer
TILES = []                     # (method name, result)
for name in [n for n in dir(CosThetaResultsContainer) if n.startswith("set") and (n.endswith("Data") or n == "setNoBunkAfterComponentPress")]:
    orig = getattr(CosThetaResultsContainer, name)
    def wrapper(self, value, result, _orig=orig, _name=name):
        TILES.append((_name, result)); return _orig(self, value, result)
    setattr(CosThetaResultsContainer, name, wrapper)

from frontend.AshokLeylandFrontEnd import AshokLeylandFrontEnd as FE, LargeImageWindow, app
SCREENS = []                   # end-of-cycle screens
for name in ("showSuccess", "showFailure", "showEmergencyAbort"):
    orig = getattr(LargeImageWindow, name)
    def wrapper(self, _orig=orig, _name=name):
        SCREENS.append(_name); return _orig(self)
    setattr(LargeImageWindow, name, wrapper)
QR_SHOWN = []
_origQR = FE.setQRCodeContainerDisplay
def _qrWrap(self, displayString=""):
    QR_SHOWN.append(displayString); return _origQR(self, displayString=displayString)
FE.setQRCodeContainerDisplay = _qrWrap

# ---------- helpers ----------
def state(): return FE.machineState.getCurrentState()

def wait_for(pred, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred(): return True
        time.sleep(0.05)
    return False

def consumed(q): return r.xlen(q) == 0

def picture(label, seed):
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 255, (480, 640, 3), dtype=np.uint8)
    cv2.putText(a, label, (40, 240), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 4)
    return a

PICTURE_STEPS = [MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING,
                 MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER,
                 MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS, MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK,
                 MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER, MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP,
                 MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS]
# The 15 operator steps in Machine 2 order: (FE state, kind, value)
SEQUENCE = [
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, "picture", None),
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING, "picture", None),
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, "picture", None),
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER, "picture", None),
    (MS.READ_TIGHTENING_TORQUE_1, "io", 19.084),
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS, "picture", None),
    (MS.READ_COMPONENT_PRESS_DONE, "io", -1.0),
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK, "picture", None),
    (MS.READ_TIGHTENING_TORQUE_2, "io", 19.026),
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER, "picture", None),
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP, "picture", None),
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS, "picture", None),
    (MS.READ_CAP_PRESS_DONE, "io", -1.0),
    (MS.READ_FREE_ROTATION_TORQUE_1, "io", 2.411),
]
SENT_ORIGINALS = {}

def scan(code, display):
    assert wait_for(lambda: state() == MS.READ_QR_CODE and consumed(qrcode2feq), 15), f"FE not waiting for a QR code ({state().name})"
    sendDataFromQRCodeServerToFEServer(r, qrCode=code, displayString=display, qrCodeStatus=ok)
    assert wait_for(lambda: state() == MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE), f"QR not accepted ({state().name})"

def send_step(st, kind, value, result=ok, expect_next=True, seed=0):
    assert wait_for(lambda: state() == st), f"FE not in {st.name} (in {state().name})"
    time.sleep(0.3)
    if kind == "picture":
        orig, proc = picture(f"{st.name[:20]} orig", seed), picture(f"{st.name[:20]} proc", seed + 1)
        if result == ok: SENT_ORIGINALS[st] = orig
        sendDataFromCameraServerToFEServer(r, originalImage=orig, processedImage=proc, result=result, currentMachineState=st)
        q = camera2feq
    else:
        action = moveAheadToNextComponent if st == MS.READ_FREE_ROTATION_TORQUE_1 else updateValueCommand
        sendDataFromIOServerToFEServer(r, currentMachineState=st, value=value, action=action, result=result)
        q = io2feq
    assert wait_for(lambda: consumed(q)), f"{q} not consumed in {st.name}"
    if expect_next:
        assert wait_for(lambda: state() != st), f"FE stayed in {st.name}"

def run_steps(upto=None, skip=0, notok_once=(), wrong_state_at=None, final_result=ok):
    for i, (st, kind, value) in enumerate(SEQUENCE[skip:upto], start=skip):
        if st == wrong_state_at:
            sendDataFromIOServerToFEServer(r, currentMachineState=MS.READ_TIGHTENING_TORQUE_2, value=55.5, action=updateValueCommand, result=ok)
            ignored = wait_for(lambda: consumed(io2feq)) and (time.sleep(1.2) or True) and state() == st
            check("F3 wrong-state IO message consumed and ignored (frontend still waits for torque 1)", ignored, state().name)
        if st in notok_once:
            n0 = len(TILES)
            send_step(st, kind, value, result=notok, expect_next=False, seed=1000 + i)
            time.sleep(1.5)
            check(f"F2 {st.name}: NOT OK shown on its tile, frontend stays on the step",
                  state() == st and any(res == notok for _, res in TILES[n0:]), f"{state().name}, {TILES[n0:]}")
        res = final_result if st == MS.READ_FREE_ROTATION_TORQUE_1 else ok
        send_step(st, kind, value, result=res, expect_next=(st != MS.READ_FREE_ROTATION_TORQUE_1), seed=10 * i)

def take_record(timeout=8):
    t0 = time.time()
    while time.time() - t0 < timeout:
        rec = readDataInDatabaseServerFromFEServer(r, block=200)
        if rec[0]: return rec
    return None

# record tuple indices (see utils/RedisUtils.readDataInDatabaseServerFromFEServer)
PIC_IDX = {MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE: 3, MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING: 6,
           MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING: 9, MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER: 12,
           MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS: 18, MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK: 23,
           MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER: 29, MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP: 32,
           MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS: 35}
RESULT_IDX = [4, 7, 10, 13, 16, 19, 21, 24, 27, 30, 33, 36, 38, 41]            # the 14 step results, in order
DATETIME_IDX = [5, 8, 11, 14, 17, 20, 22, 25, 28, 31, 34, 37, 39, 42]
TORQUE_IDX = {"torque1": 15, "torque2": 26, "freeRotation": 40}
OVERALL_IDX = 43

def check_full_record(tag, rec, code):
    check(f"{tag} one record sent to fe2db.q", rec is not None)
    if rec is None: return
    check(f"{tag} record QR code", rec[2] == code, rec[2])
    check(f"{tag} all 14 step results OK", all(rec[i] == ok for i in RESULT_IDX), str([rec[i] for i in RESULT_IDX]))
    check(f"{tag} torques rounded to 2 decimals (19.08 / 19.03 / 2.41)",
          (rec[TORQUE_IDX["torque1"]], rec[TORQUE_IDX["torque2"]], rec[TORQUE_IDX["freeRotation"]]) == (19.08, 19.03, 2.41),
          str((rec[15], rec[26], rec[40])))
    check(f"{tag} 14 real datetimes (none 1970)", all(not str(rec[i]).startswith("1970") for i in DATETIME_IDX))
    same = [st.name for st, i in PIC_IDX.items() if rec[i] is not None and np.array_equal(rec[i], SENT_ORIGINALS.get(st))]
    check(f"{tag} the 9 original pictures pixel-identical to what the camera sent", len(same) == 9, f"{len(same)}/9")
    check(f"{tag} overall result OK", rec[OVERALL_IDX] == ok, rec[OVERALL_IDX])

QR1 = "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"
QR2 = "8201206$400102VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNURH$SNPL-MAT$"

def driver():
    try:
        time.sleep(4)
        # ---------------- F1 ----------------
        print("\n=== F1. All-OK cycle ===", flush=True)
        TILES.clear(); SCREENS.clear(); SENT_ORIGINALS.clear()
        scan(QR1, "DOST - LHS - 2.0T : 11770 : 09.03.2025")
        check("F1 QR display string shown", "DOST - LHS - 2.0T : 11770 : 09.03.2025" in QR_SHOWN, str(QR_SHOWN[-2:]))
        visited = []
        for i, (st, kind, value) in enumerate(SEQUENCE):
            visited.append(state())
            send_step(st, kind, value, expect_next=(st != MS.READ_FREE_ROTATION_TORQUE_1), seed=10 * i)
        check("F1 frontend walked the 14 steps after the QR code in Machine 2 order", visited == [s for s, _, _ in SEQUENCE],
              str([s.name for s in visited]))
        rec = take_record()
        check_full_record("F1", rec, QR1)
        check("F1 success screen shown", wait_for(lambda: "showSuccess" in SCREENS, 5), str(SCREENS))
        check("F1 every tile set with OK (14 tiles: 9 pictures, 2 tightening torques, 2 presses, free-rotation torque)", len(TILES) == 14 and all(res == ok for _, res in TILES), str(TILES))
        check("F1 back to READ_QR_CODE", wait_for(lambda: state() == MS.READ_QR_CODE), state().name)
        check("F1 exactly one record per cycle", take_record(timeout=2) is None)

        # ---------------- F2 + F3 ----------------
        print("\n=== F2/F3. Camera NOT OK then OK; IO message for the wrong state ===", flush=True)
        TILES.clear(); SCREENS.clear(); SENT_ORIGINALS.clear()
        scan(QR2, "DOSTPLUS - RHS - 2.5T : 22881 : 15.10.2026")
        run_steps(notok_once=(MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP),
                  wrong_state_at=MS.READ_TIGHTENING_TORQUE_1)
        rec = take_record()
        check_full_record("F2", rec, QR2)
        check("F2 success screen shown", wait_for(lambda: "showSuccess" in SCREENS, 5), str(SCREENS))
        check("F3 wrong-state value (55.5) not recorded as torque 1", rec is not None and rec[15] == 19.08, str(rec[15] if rec else None))

        # ---------------- F4 ----------------
        print("\n=== F4. Emergency abort after the hub check ===", flush=True)
        TILES.clear(); SCREENS.clear(); SENT_ORIGINALS.clear()
        assert wait_for(lambda: state() == MS.READ_QR_CODE, 10)
        scan(QR1, "DOST - LHS - 2.0T : 11770 : 09.03.2025")
        run_steps(upto=2)
        assert wait_for(lambda: state() == MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING)
        sendEmergencyAbortFromIOServerToFEServer(r)
        rec = take_record(timeout=10)
        check("F4 abort record sent to fe2db.q", rec is not None)
        if rec:
            check("F4 reached steps (knuckle, hub) OK, the other 12 NOT OK", [rec[i] for i in RESULT_IDX] == [ok, ok] + [notok] * 12,
                  str([rec[i] for i in RESULT_IDX]))
            check("F4 unreached steps have 1970 datetimes", all(str(rec[i]).startswith("1970") for i in DATETIME_IDX[2:]),
                  str([rec[i] for i in DATETIME_IDX]))
            check("F4 torques 0.0", (rec[15], rec[26], rec[40]) == (0.0, 0.0, 0.0), str((rec[15], rec[26], rec[40])))
            pics = [st.name for st, i in PIC_IDX.items() if rec[i] is not None]
            check("F4 pictures only for the knuckle and hub", pics == [PICTURE_STEPS[0].name, PICTURE_STEPS[1].name], str(pics))
            check("F4 overall NOT OK", rec[OVERALL_IDX] == notok, rec[OVERALL_IDX])
        check("F4 emergency-abort screen shown", wait_for(lambda: "showEmergencyAbort" in SCREENS, 5), str(SCREENS))
        check("F4 back to READ_QR_CODE", wait_for(lambda: state() == MS.READ_QR_CODE), state().name)
        TILES.clear(); SENT_ORIGINALS.clear()
        scan(QR1, "DOST - LHS - 2.0T : 11770 : 09.03.2025")
        run_steps()
        check_full_record("F4 next cycle", take_record(), QR1)

        # ---------------- F5 ----------------
        print("\n=== F5. Final free-rotation torque NOT OK ===", flush=True)
        SCREENS.clear(); SENT_ORIGINALS.clear()
        assert wait_for(lambda: state() == MS.READ_QR_CODE, 10)
        scan(QR1, "DOST - LHS - 2.0T : 11770 : 09.03.2025")
        run_steps(final_result=notok)
        rec = take_record()
        check("F5 record sent with free-rotation result NOT OK and overall NOT OK",
              rec is not None and rec[41] == notok and rec[OVERALL_IDX] == notok, str((rec[41], rec[OVERALL_IDX]) if rec else None))
        check("F5 failure screen shown (not the success screen)", wait_for(lambda: SCREENS != [], 5) and SCREENS[-1] == "showFailure", str(SCREENS))
        check("F5 back to READ_QR_CODE", wait_for(lambda: state() == MS.READ_QR_CODE), state().name)
        # ---------------- F6 ----------------
        print("\n=== F6. Machine Settings menu and role restriction ===", flush=True)
        import frontend.SimplePopups as SP
        acts = [a for m in fe.menuBar().actions() if m.menu() for a in m.menu().actions() if "Machine Settings" in a.text()]
        check("F6 Machine Settings action in the menu, enabled for an Administrator", len(acts) == 1 and acts[0].isEnabled(),
              str([(a.text(), a.isEnabled()) for a in acts]))
        check("F6 Administrator and Supervisor may change the settings, Operator may not",
              (SP.canChangeMachineSettings("Administrator"), SP.canChangeMachineSettings("Supervisor"),
               SP.canChangeMachineSettings("Operator")) == (True, True, False))
        shown = []; origErr = SP.showErrorMessage
        SP.showErrorMessage = lambda msg, *a, **k: shown.append(msg)
        before = P.getMachineSettings(P.getDatabaseName())
        try:
            refused = SP.updateMachineSettingsDialog(currentUser="op1", currentRole="Operator") is False
        finally:
            SP.showErrorMessage = origErr
        check("F6 Operator refused (message shown, dialog not opened, nothing saved)",
              refused and shown and "Administrator or a Supervisor" in shown[0]
              and P.getMachineSettings(P.getDatabaseName()).get("setting_id") == before.get("setting_id"), str(shown))
        good, errors = SP.parseMachineSettingsInputs(dict(rotation_rpm="32", tightening_torque1_setpoint="18.5",
                       tightening_torque2_setpoint="19.99", no_of_rotation1_ccw="2", no_of_rotation1_cw="1"))
        check("F6 valid entries accepted as floats", errors == [] and good == dict(rotation_rpm=32.0,
              tightening_torque1_setpoint=18.5, tightening_torque2_setpoint=19.99, no_of_rotation1_ccw=2.0,
              no_of_rotation1_cw=1.0), str((good, errors)))
        _, errors = SP.parseMachineSettingsInputs(dict(rotation_rpm="30.5", tightening_torque1_setpoint="20",
                    tightening_torque2_setpoint="1.234", no_of_rotation1_ccw="3", no_of_rotation1_cw="x"))
        check("F6 invalid entries: one message per field (RPM not whole, torque 20, 3 decimals, count 3, not a number)",
              len(errors) == 5, str(errors))
    except AssertionError as e:
        check(f"scenario aborted: {e}", False)
    except Exception as e:
        import traceback; traceback.print_exc()
        check(f"scenario crashed: {e!r}", False)
    failed = [n for n, ok_ in RESULTS if not ok_]
    print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
    os._exit(1 if failed else 0)

fe = FE(mode="Test", username="admin", role="Administrator")
threading.Thread(target=driver, daemon=True).start()
app.exec()
