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
  F1 also: PLC_PC_Station2RotationDuringTighteningDone sent with each tightening torque (True at torque 1, False at
      torque 2) -> each tightening tile shows the torque on the first line and "Rotation Done" / "Rotation Not Done"
      below it in a smaller font; the record carries both values.
  F8. Manual abort in the frontend (Ctrl+Alt+Z): the abort is sent to the IOServer on fe2io.q (it then goes back to
      the QR code), the abort screen is shown and the abort record sent.
  F12. Bypass of the component press and second tightening (2026-11-13): the IOServer published the bypass for the QR
      code -> the 4 skipped tiles show 'Bypassed'; Torque 1 with moveToState = split pin picture -> the frontend skips
      to it; the record: overall OK, 'Bypassed' / no datetime / torque 2 -1.0 / no pictures for the skipped steps,
      marked bypassed; the next component runs in full. Machine Settings: the checkbox, password, Yes / No.
  F6. Machine Settings menu: present and enabled for an Administrator; only Administrator and Supervisor may change
      the settings (an Operator is refused before the dialog opens, nothing saved); the dialog's entry checks
      (RPM whole number 20-40, torques 0.01-19.99 with at most 2 decimals, rotation counts 1 or 2).

Not covered here (needs real pictures, the models or the camera): running the real camera/Check*.py algorithms on
image files and the live camera - use the Test_FEServer_*_UI.py programs for that.

Needs Redis and PostgreSQL (the frontend's counters read the TEST database). Headless:
    QT_QPA_PLATFORM=offscreen python <project>/test/machine1_dryrun/DryRun_FEServer_Cycles.py
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
TILE_ROTATION = {}             # tightening-torque tile -> rotationDone passed with it
for name in [n for n in dir(CosThetaResultsContainer) if n.startswith("set") and (n.endswith("Data") or n == "setNoBunkAfterComponentPress")]:
    orig = getattr(CosThetaResultsContainer, name)
    def wrapper(self, value, result, *args, _orig=orig, _name=name, **kwargs):
        TILES.append((_name, result))
        if "rotationDone" in kwargs: TILE_ROTATION[_name] = kwargs["rotationDone"]
        return _orig(self, value, result, *args, **kwargs)
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
# PLC_PC_Station2RotationDuringTighteningDone sent by the IOServer with each tightening torque
ROTATION = {MS.READ_TIGHTENING_TORQUE_1: True, MS.READ_TIGHTENING_TORQUE_2: False}
TORQUE_TEXT = {}               # tightening-torque state -> text shown on its tile right after the value arrived

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
        sendDataFromIOServerToFEServer(r, currentMachineState=st, value=value, action=action, result=result,
                                       rotationDone=ROTATION.get(st))
        q = io2feq
    assert wait_for(lambda: consumed(q)), f"{q} not consumed in {st.name}"
    if expect_next:
        assert wait_for(lambda: state() != st), f"FE stayed in {st.name}"
    if st in ROTATION and result == ok:
        tile = (fe.resultsContainer.tightening_torque1_check_container if st == MS.READ_TIGHTENING_TORQUE_1
                else fe.resultsContainer.tightening_torque2_check_container)
        time.sleep(0.2)
        TORQUE_TEXT[st] = tile.image_label.text()

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
        rec = readDataInDatabaseServerFromFEServer(r, block=200, returnRotationDone=True, returnAbort=True)
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
ROTATION_IDX = (44, 45)        # tightening torque 1 / 2 rotation done (returnRotationDone=True)

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
    check(f"{tag} not marked aborted", rec[-2] is False and rec[-1] == "", str(rec[-2:]))
    check(f"{tag} rotation done with torque 1 / torque 2 in the record (True / False)",
          (rec[ROTATION_IDX[0]], rec[ROTATION_IDX[1]]) == (True, False), str(rec[44:46]))

import re as _re
from PySide6.QtGui import QFont, QFontMetrics
FE_WINDOW = []
def _secondSize(text):
    sizes = _re.findall(r"font-size:(\d+)pt", text)
    return int(sizes[1]) if len(sizes) > 1 else None

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
        t1, t2 = TORQUE_TEXT.get(MS.READ_TIGHTENING_TORQUE_1, ""), TORQUE_TEXT.get(MS.READ_TIGHTENING_TORQUE_2, "")
        check("F1 torque 1 tile: value on the first line, 'Rotation Done' below it in a smaller font",
              "19.08" in t1 and "Rotation Done" in t1 and t1.index("19.08") < t1.index("Rotation Done")
              and "font-size:36pt" in t1 and _secondSize(t1) is not None and _secondSize(t1) <= 13, t1)
        check("F1 torque 2 tile: value, then 'Rotation Not Done' in red", "19.03" in t2 and "Rotation Not Done" in t2
              and "color:#d00000" in t2, t2)
        _tile2 = FE_WINDOW[0].resultsContainer.tightening_torque2_check_container.image_label
        _f = QFont(_tile2.font()); _f.setBold(True); _f.setPointSize(_secondSize(t2) or 99)
        check("F1 'Rotation Not Done' (13 pt or less) fits inside the tile with a margin",
              QFontMetrics(_f).horizontalAdvance("Rotation Not Done") <= 0.9 * _tile2.width(),
              (_secondSize(t2), QFontMetrics(_f).horizontalAdvance("Rotation Not Done"), _tile2.width()))
        check("F1 'Rotation Done' not in red", "color:#d00000" not in t1, t1)
        check("F1 rotationDone passed to the two tightening tiles (True / False)",
              (TILE_ROTATION.get("setTighteningTorque1Data"), TILE_ROTATION.get("setTighteningTorque2Data")) == (True, False),
              str(TILE_ROTATION))
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
            check("F4 record marked aborted, reason: the emergency button", rec[-2] is True and "emergency" in rec[-1],
                  str(rec[-2:]))
        check("F4 emergency-abort screen shown", wait_for(lambda: "showEmergencyAbort" in SCREENS, 5), str(SCREENS))
        check("F4 back to READ_QR_CODE", wait_for(lambda: state() == MS.READ_QR_CODE), state().name)
        TILES.clear(); SENT_ORIGINALS.clear()
        scan(QR1, "DOST - LHS - 2.0T : 11770 : 09.03.2025")
        run_steps()
        check_full_record("F4 next cycle", take_record(), QR1)
        # F4b: emergency while waiting for the QR code: shown, no record
        assert wait_for(lambda: state() == MS.READ_QR_CODE, 10)
        SCREENS.clear(); time.sleep(0.5)
        sendEmergencyAbortFromIOServerToFEServer(r)
        check("F4b emergency while waiting for the QR code: abort screen shown",
              wait_for(lambda: "showEmergencyAbort" in SCREENS, 5), str(SCREENS))
        check("F4b ... no record sent, still waiting for the QR code",
              take_record(timeout=3) is None and state() == MS.READ_QR_CODE, state().name)
        # F4c: emergency while the completed cycle's free-rotation torque is on screen: shown, record as completed
        TILES.clear(); SCREENS.clear(); SENT_ORIGINALS.clear()
        scan(QR1, "DOST - LHS - 2.0T : 11770 : 09.03.2025")
        run_steps()
        sendEmergencyAbortFromIOServerToFEServer(r)
        rec = take_record()
        check_full_record("F4c emergency during the 2 s torque display", rec, QR1)
        check("F4c ... abort screen shown instead of success",
              wait_for(lambda: "showEmergencyAbort" in SCREENS, 5) and "showSuccess" not in SCREENS, str(SCREENS))
        t1 = fe.componentProcessingTimeText(); time.sleep(2.2); t2 = fe.componentProcessingTimeText()
        check("F4d processing time stops at the end of the cycle and stays on screen (not 0) until the next scan",
              t1 == t2 and t1 != "0" and fe.currentComponentEndTime is not None, f"{t1} -> {t2}")

        # ---------------- F5 ----------------
        print("\n=== F5. Final free-rotation torque NOT OK ===", flush=True)
        SCREENS.clear(); SENT_ORIGINALS.clear()
        assert wait_for(lambda: state() == MS.READ_QR_CODE, 10)
        scan(QR1, "DOST - LHS - 2.0T : 11770 : 09.03.2025")
        check("F4d ... the next scan restarts it from 0 and it counts again",
              wait_for(lambda: fe.currentComponentEndTime is None, 5)
              and fe.componentProcessingTimeText() in ("0:00:00", "0:00:01"),
              fe.componentProcessingTimeText())
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
                    tightening_torque2_setpoint="1.234", no_of_rotation1_ccw="5", no_of_rotation1_cw="x"))
        check("F6 invalid entries: one message per field (RPM not whole, torque 20, 3 decimals, count 5 (1 - 4 allowed), not a number)",
              len(errors) == 5, str(errors))
        # ---------------- F7 ----------------
        print("\n=== F7. GUI thread never blocked by Redis reads ===", flush=True)
        GUI_GAPS["max"] = 0.0; GUI_GAPS["last"] = time.monotonic()
        time.sleep(7)
        check("F7 idle for 7 s (no emergency or status message): GUI thread never busy > 0.25 s",
              GUI_GAPS["max"] < 0.25, f"longest GUI-thread stall {GUI_GAPS['max']:.3f} s")
        check("F7 emergency checked every 1 s (frontend.emergency.status.sleepinterval.sec), FE heartbeat every 0.5 s",
              fe.emergencyAbortUpdateWorker.updateTimer.interval() == 1000 and fe.heartbeatWorker.timer.interval() == 500,
              (fe.emergencyAbortUpdateWorker.updateTimer.interval(), fe.heartbeatWorker.timer.interval()))
        from utils.RedisUtils import sendHeartbeatsFromHeartbeatServerToFEServer as _hb, ALIVE as _A, DEAD as _D
        from frontend.AshokLeylandFrontEnd import AshokLeylandFrontEnd as _FE
        _hb(r, qrCodeServerStatus=_A, cameraServerStatus=_D, ioServerStatus=_A, dbServerStatus=_A)
        ok_ = wait_for(lambda: _FE.cameraServerConnectionStatus is False and _FE.ioServerConnectionStatus is True, 5)
        check("F7 status message shown on the tiles (camera down, IO up)", ok_,
              (_FE.cameraServerConnectionStatus, _FE.ioServerConnectionStatus))
        time.sleep(2.5)
        check("F7 no new status message for 2.5 s: tiles keep their last status (IO still up, no flicker to down)",
              _FE.ioServerConnectionStatus is True, _FE.ioServerConnectionStatus)
        stale = 3 * max(2.0, 2.0)
        ok_ = wait_for(lambda: _FE.ioServerConnectionStatus is False, stale + 3)
        check("F7 heartbeat server silent for 3 intervals (6 s): all shown down", ok_, _FE.ioServerConnectionStatus)
        GUI_GAPS["max"] = 0.0; GUI_GAPS["last"] = time.monotonic()
        assert wait_for(lambda: state() == MS.READ_QR_CODE, 10)
        scan(QR2, "DOSTPLUS - RHS - 2.5T : 22881 : 15.10.2026")
        run_steps(upto=1)
        TILES.clear(); SCREENS.clear(); time.sleep(0.3)
        t0 = time.monotonic(); sendEmergencyAbortFromIOServerToFEServer(r)
        ok_ = wait_for(lambda: "showEmergencyAbort" in SCREENS, 5)
        take_record(timeout=5)
        check("F7 emergency abort shown within about 1 s", ok_ and time.monotonic() - t0 < 2.0,
              f"{time.monotonic() - t0:.2f} s, {SCREENS}")

        # ---------------- F8 ----------------
        print("\n=== F8. Frontend abort (Ctrl+Alt+Z): password, frontend only, PLC-flag request to the IOServer ===", flush=True)
        import frontend.AshokLeylandFrontEnd as _feModule
        from utils.RedisUtils import FRONTEND_ABORT_REQUEST
        _savedPassword = _feModule.getJustPassword
        _savedError = _feModule.showErrorMessage
        _errors = []
        _feModule.showErrorMessage = lambda *a, **k: _errors.append(a)
        assert wait_for(lambda: state() == MS.READ_QR_CODE, 10)
        scan(QR1, "DOST - LHS - 2.0T : 11770 : 09.03.2025")
        run_steps(upto=1)
        for label, entered in (("wrong password", "admin"), ("cancelled", "")):
            r.delete(fe2ioq); SCREENS.clear(); _errors.clear(); st0 = state(); qr0 = r.xlen(io2qrcodeabortq)
            _feModule.getJustPassword = lambda *a, _e=entered, **k: _e
            _gui.call.emit(fe.manualAbort)
            time.sleep(1.5)
            check(f"F8 {label}: nothing aborted, nothing sent, frontend stays on its step"
                  + (", 'Wrong password' shown" if entered else ""),
                  r.xlen(fe2ioq) == 0 and "showEmergencyAbort" not in SCREENS and state() == st0
                  and r.xlen(io2qrcodeabortq) == qr0 and take_record(timeout=1) is None and (bool(_errors) == bool(entered)),
                  f"{state().name} {SCREENS} {_errors}")
        r.delete(fe2ioq); SCREENS.clear()
        qr0, cam0 = r.xlen(io2qrcodeabortq), r.xlen(io2cameraq)
        _feModule.getJustPassword = lambda *a, **k: "admin123"
        _gui.call.emit(fe.manualAbort)
        sent = wait_for(lambda: r.xlen(fe2ioq) > 0, 5)
        msgs = [{k.decode(): v.decode() for k, v in m[1].items()} for m in r.xrange(fe2ioq)]
        check("F8 password admin123 -> only the PLC-flag request to the IOServer (FrontendAbortRequest on fe2io.q), no ABORT",
              sent and [x.get("action") for x in msgs] == [FRONTEND_ABORT_REQUEST], str(msgs))
        check("F8 ... which the IOServer reads as a request, not an abort",
              readAbortInIOServerFromFEServer(r, block=100, withKind=True)[0] == "request")
        check("F8 nothing sent to the QR code server or the camera server",
              r.xlen(io2qrcodeabortq) == qr0 and r.xlen(io2cameraq) == cam0)
        rec = take_record(timeout=5) if wait_for(lambda: "showEmergencyAbort" in SCREENS, 5) else None
        check("F8 the frontend shows the abort screen and sends the abort record, marked aborted (manual abort)",
              rec is not None and rec[-2] is True and "manual" in rec[-1], f"{SCREENS} {rec[-2:] if rec else None}")
        check("F8 the frontend is back at READ_QR_CODE", wait_for(lambda: state() == MS.READ_QR_CODE, 10), state().name)
        r.delete(fe2ioq); SCREENS.clear()
        _gui.call.emit(fe.manualAbort)
        check("F8 at the QR code (nothing in process): abort screen, nothing sent, no record",
              wait_for(lambda: "showEmergencyAbort" in SCREENS, 5) and r.xlen(fe2ioq) == 0 and take_record(timeout=2) is None,
              f"{SCREENS} {r.xlen(fe2ioq)}")
        _feModule.getJustPassword = _savedPassword
        _feModule.showErrorMessage = _savedError

        # ---------------- F9 (2026-11-06) ----------------
        print("\n=== F9. Notices from the IOServer: shown top right, flashing; removed when gone ===", flush=True)
        for k in r.scan_iter("feNotice:*"):
            r.delete(k)
        setFrontendNotice(r, "db", "Database down: continue till end of cycle", ttlSecs=30)
        shown = wait_for(lambda: fe.noticeLabel.isVisible() and "continue till end of cycle" in fe.noticeLabel.text(), 4)
        styles = set()
        for _ in range(8):
            styles.add(fe.noticeLabel.styleSheet()); time.sleep(0.15)
        check("F9 notice shown on the frontend (top right)", shown, fe.noticeLabel.text())
        check("F9 ... flashing (two colour schemes alternate)", len(styles) == 2, str(len(styles)))
        setFrontendNotice(r, "camera", "Picture NOT OK: camera server down", ttlSecs=30)
        check("F9 two notices shown together", wait_for(lambda: "camera server down" in fe.noticeLabel.text()
              and "Database down" in fe.noticeLabel.text(), 4), fe.noticeLabel.text())
        clearFrontendNotice(r, "db"); clearFrontendNotice(r, "camera")
        check("F9 notices gone -> label hidden", wait_for(lambda: not fe.noticeLabel.isVisible(), 4))
        setFrontendNotice(r, "check", "Picture NOT OK: check timed out after 12 s", ttlSecs=1.5)
        check("F9 a notice with an expiry disappears by itself", wait_for(lambda: fe.noticeLabel.isVisible(), 3)
              and wait_for(lambda: not fe.noticeLabel.isVisible(), 5))

        # ---------------- F10 (2026-11-06) ----------------
        print("\n=== F10. A camera result the IOServer no longer waits for is not shown; the IOServer's NOT OK is ===", flush=True)
        assert wait_for(lambda: state() == MS.READ_QR_CODE, 10)
        scan(QR1, "DOST - LHS - 2.0T : 11770 : 09.03.2025")
        assert wait_for(lambda: state() == MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)
        n0 = len(TILES)
        setActiveCameraRequest(r, "9-1:timeout")              # the IOServer gave up on request 9-1
        sendDataFromCameraServerToFEServer(r, originalImage=picture("late", 5), processedImage=picture("late", 6),
                                           result=ok, currentMachineState=MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE,
                                           requestId="9-1")
        check("F10 late camera result of request 9-1 dropped, not shown (frontend stays on the knuckle)",
              wait_for(lambda: consumed(camera2feq), 4) and (time.sleep(1.0) or True)
              and state() == MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE and len(TILES) == n0, f"{state().name} {TILES[n0:]}")
        sendDataFromCameraServerToFEServer(r, originalImage=None, processedImage=None, result=notok,
                                           currentMachineState=MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE,
                                           aProducer="IOServer", requestId="9-1:timeout", reason="no answer from the camera server in 20 s")
        check("F10 the IOServer's NOT OK (request 9-1:timeout) shown on the knuckle tile, frontend stays on the step",
              wait_for(lambda: any(res == notok for _, res in TILES[n0:]), 5)
              and state() == MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, str(TILES[n0:]))
        setActiveCameraRequest(r, None)
        time.sleep(1.0)
        send_step(MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, "picture", None, result=ok, seed=77)
        check("F10 the retake's result (no request id / active cleared) is shown and the frontend moves on",
              state() != MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, state().name)
        sendEmergencyAbortFromIOServerToFEServer(r)
        check("F10 cycle aborted to finish", wait_for(lambda: state() == MS.READ_QR_CODE, 10))
        # ---------------- F11 (2026-11-07) ----------------
        print("\n=== F11. Heartbeat DEAD while the GUI thread is frozen ===", flush=True)
        from Configuration import CosThetaConfigurator as _C
        _origLimit = _C.getWorkLoopMaxSilence
        _C.getWorkLoopMaxSilence = classmethod(lambda cls: 2.0)
        def _hb():
            last = r.xrevrange(fe2hbq, count=1)
            return last[0][1].get(connectionStatusKeyString.encode(), b"").decode() if last else None
        check("F11 GUI thread running: heartbeat ALIVE", wait_for(lambda: _hb() == ALIVE, 5), _hb())
        _gui.call.emit(lambda: time.sleep(5))                 # the GUI thread is busy for 5 s (frozen screen)
        check("F11 GUI thread frozen > 2 s: heartbeat DEAD (sent by the heartbeat thread)",
              wait_for(lambda: _hb() == DEAD, 5), _hb())
        check("F11 GUI thread running again: ALIVE", wait_for(lambda: _hb() == ALIVE, 6), _hb())
        _gui.call.emit(lambda: fe._runDialog(time.sleep, 5))  # a menu dialog (FreeSimpleGUI) open for 5 s
        time.sleep(3.5)
        check("F11 a menu dialog open on the GUI thread (Machine Settings, reports): heartbeat stays ALIVE",
              not wait_for(lambda: _hb() == DEAD, 1.5), _hb())
        check("F11 ... and the dialog count is back to 0 once it closes",
              wait_for(lambda: FE.dialogsOpen == 0, 4), FE.dialogsOpen)
        _C.getWorkLoopMaxSilence = _origLimit

        # ---------------- F12 (2026-11-13) ----------------
        print("\n=== F12. Bypass of the component press and second tightening (Machine Setting per model) ===", flush=True)
        from frontend.CosThetaStylesheets import BYPASSED_TEXT
        rc = fe.resultsContainer
        skippedTiles = (rc.bunk_for_component_press_check_container, rc.component_press_check_container,
                        rc.no_bunk_after_component_press_check_container, rc.tightening_torque2_check_container)
        assert wait_for(lambda: state() == MS.READ_QR_CODE, 10)
        time.sleep(1.0); r.delete(fe2dbq)          # F10's abort record (not read there)
        TILES.clear(); SCREENS.clear(); SENT_ORIGINALS.clear()
        publishCycleSettingsForQRCode(r, QR1, {"rotation_rpm": 30.0, "bypass_press_and_tightening2": True})  # IOServer, state 2
        scan(QR1, "DOST - LHS - 2.0T : 11770 : 09.03.2025")
        run_steps(upto=1)
        check("F12 bypass published for the QR code -> the 4 skipped tiles show 'Bypassed' after the knuckle result",
              wait_for(lambda: all(t.getResult() == BYPASSED_TEXT for t in skippedTiles), 4),
              [t.getResult() for t in skippedTiles])
        run_steps(skip=1, upto=4)
        assert wait_for(lambda: state() == MS.READ_TIGHTENING_TORQUE_1)
        time.sleep(0.3)
        sendDataFromIOServerToFEServer(r, currentMachineState=MS.READ_TIGHTENING_TORQUE_1, value=19.084,
                                       moveToState=MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER,
                                       action=updateValueCommand, result=ok, rotationDone=True)
        check("F12 Torque 1 with moveToState = split pin picture -> the frontend goes straight to the split pin picture",
              wait_for(lambda: state() == MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER, 5), state().name)
        check("F12 ... the 4 skipped tiles still 'Bypassed' (grey, not Not OK)",
              all(t.getResult() == BYPASSED_TEXT for t in skippedTiles), [t.getResult() for t in skippedTiles])
        run_steps(skip=9)
        rec = None
        t0 = time.time()
        while time.time() - t0 < 8 and rec is None:
            got = readDataInDatabaseServerFromFEServer(r, block=200, returnRotationDone=True, returnAbort=True,
                                                       returnBypassed=True)
            rec = got if got[0] else None
        check("F12 one record sent", rec is not None)
        if rec is not None:
            skippedResults = [rec[i] for i in (19, 21, 24, 27)]
            check("F12 record: overall OK (the skipped steps are excluded, not failed)", rec[OVERALL_IDX] == ok, rec[OVERALL_IDX])
            check("F12 record: 'Bypassed' for bung, component press, bung removal and Torque 2", skippedResults == [BYPASSED_TEXT] * 4,
                  str(skippedResults))
            check("F12 record: no datetimes for the skipped steps, Torque 2 -1.0, no rotation done with Torque 2",
                  [rec[i] for i in (20, 22, 25, 28)] == [""] * 4 and rec[TORQUE_IDX["torque2"]] == -1.0
                  and rec[ROTATION_IDX[1]] is None, str(([rec[i] for i in (20, 22, 25, 28)], rec[26], rec[45])))
            check("F12 record: no pictures for the two bung checks", rec[18] is None and rec[23] is None)
            check("F12 record: the other 10 steps OK, Torque 1 19.08", all(rec[i] == ok for i in RESULT_IDX if i not in (19, 21, 24, 27))
                  and rec[TORQUE_IDX["torque1"]] == 19.08, str([rec[i] for i in RESULT_IDX]))
            check("F12 record marked bypassed (bypassed_press_and_tightening2)", rec[-1] is True, str(rec[-3:]))
        check("F12 success screen shown", wait_for(lambda: "showSuccess" in SCREENS, 5), str(SCREENS))
        assert wait_for(lambda: state() == MS.READ_QR_CODE, 10)
        TILES.clear(); SENT_ORIGINALS.clear()
        scan(QR2, "DOSTPLUS - RHS - 2.5T : 22881 : 15.10.2026")
        check("F12 next component (no bypass published): the 4 tiles back to 'Result Awaited'",
              wait_for(lambda: all(t.getResult() == "Result Awaited" for t in skippedTiles), 4),
              [t.getResult() for t in skippedTiles])
        run_steps()
        rec = None
        t0 = time.time()
        while time.time() - t0 < 8 and rec is None:
            got = readDataInDatabaseServerFromFEServer(r, block=200, returnRotationDone=True, returnAbort=True,
                                                       returnBypassed=True)
            rec = got if got[0] else None
        check("F12 ... full run, record not marked bypassed, all 14 steps OK",
              rec is not None and rec[-1] is False and all(rec[i] == ok for i in RESULT_IDX), str(rec[-3:]) if rec else None)

        # Machine Settings dialog: the bypass checkbox; Save asks for the password, then Yes / No
        import frontend.SimplePopups as SP
        check("F12 Machine Settings: 'Bypass Component Press and Second Tightening' checkbox (per model)",
              ("bypass_press_and_tightening2", "Bypass Component Press and Second Tightening") in SP.MACHINE_SETTINGS_FLAG_FIELDS)
        _pw, _yn, _err = SP.getJustPassword, SP.sg.popup_yes_no, SP.showErrorMessage
        asked = []
        SP.showErrorMessage = lambda *a, **k: asked.append(("error", a[0] if a else ""))
        SP.sg.popup_yes_no = lambda text, **k: asked.append(("yesno", text)) or "Yes"
        new = dict(P.DEFAULT_MACHINE_SETTINGS, bypass_press_and_tightening2=True)
        old = dict(P.DEFAULT_MACHINE_SETTINGS, bypass_press_and_tightening2=False)
        SP.getJustPassword = lambda *a, **k: "wrong"
        check("F12 wrong password -> not saved, no Yes / No asked", SP.confirmMachineSettingsChange("DOST", new, old) is False
              and [k for k, _ in asked] == ["error"], str(asked))
        asked.clear(); SP.getJustPassword = lambda *a, **k: ""
        check("F12 password cancelled -> not saved, nothing shown", SP.confirmMachineSettingsChange("DOST", new, old) is False
              and asked == [], str(asked))
        asked.clear(); SP.getJustPassword = lambda *a, **k: "admin123"
        check("F12 admin123 + Yes -> saved; the confirmation names the change",
              SP.confirmMachineSettingsChange("DOST", new, old) is True and asked and asked[0][0] == "yesno"
              and "Bypass Component Press and Second Tightening: Yes" in asked[0][1], str(asked))
        asked.clear(); SP.sg.popup_yes_no = lambda text, **k: "No"
        check("F12 admin123 + No -> not saved", SP.confirmMachineSettingsChange("DOST", new, old) is False)
        SP.getJustPassword, SP.sg.popup_yes_no, SP.showErrorMessage = _pw, _yn, _err
    except AssertionError as e:
        check(f"scenario aborted: {e}", False)
    except Exception as e:
        import traceback; traceback.print_exc()
        check(f"scenario crashed: {e!r}", False)
    failed = [n for n, ok_ in RESULTS if not ok_]
    print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""), flush=True)
    os._exit(1 if failed else 0)

# GUI-thread responsiveness probe (F7): a 20 ms timer in the GUI thread; the largest gap between two ticks is the
# longest time the GUI thread was busy (a blocking call there freezes the screen).
from PySide6.QtCore import QTimer as _QTimer, QObject as _QObject, Signal as _Signal
class _GuiCall(_QObject):               # runs a function in the GUI thread (dialogs, widgets) from the driver thread
    call = _Signal(object)
_gui = _GuiCall(); _gui.call.connect(lambda f: f())
GUI_GAPS = {"last": time.monotonic(), "max": 0.0}
def _guiTick():
    now = time.monotonic()
    GUI_GAPS["max"] = max(GUI_GAPS["max"], now - GUI_GAPS["last"]); GUI_GAPS["last"] = now
_guiProbe = _QTimer(); _guiProbe.timeout.connect(_guiTick); _guiProbe.start(20)

fe = FE(mode="Test", username="admin", role="Administrator")
FE_WINDOW.append(fe)
threading.Thread(target=driver, daemon=True).start()
app.exec()
