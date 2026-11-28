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
  F13. The Tk windows that play the servers, with "Bypass ..." ticked (needs a display: xvfb-run): the simulated-inputs
      window drives a whole bypassed cycle (11 messages, record OK and bypassed); the image-files window publishes the
      bypass with the QR code and sends Torque 1 with moveToState = split pin (both move there; 4 steps 'Bypassed').
  F14. State sync phase A (CHANGES 108): IOServer states published as the IOServer does -> no mismatch over a
      whole cycle; a difference longer than the grace period -> one MISMATCH line, then RESOLVED, cycle summary;
      missing messages -> GAP; new IOServer pid -> IOSERVER-RESTART; the frontend's behaviour unchanged.
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
TILE_ROTATION = {}             # tightening-torque tile -> rotationDone passed with it
for name in [n for n in dir(CosThetaResultsContainer) if n.startswith("set") and (n.endswith("Data") or n == "setNoBungAfterComponentPress")]:
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
CLEARS = []                    # times the tiles were cleared (CHANGES 123: 5 s after the end of a cycle)
_origClear = CosThetaResultsContainer.setInProcessDefaultDisplay
def _clearWrap(self):
    CLEARS.append(time.time()); return _origClear(self)
CosThetaResultsContainer.setInProcessDefaultDisplay = _clearWrap
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
                 MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_COMPONENT_PRESS, MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNG,
                 MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER, MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP,
                 MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_CAP_PRESS]
# The 15 operator steps in Machine 2 order: (FE state, kind, value)
SEQUENCE = [
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, "picture", None),
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING, "picture", None),
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, "picture", None),
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER, "picture", None),
    (MS.READ_TIGHTENING_TORQUE_1, "io", 19.084),
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_COMPONENT_PRESS, "picture", None),
    (MS.READ_COMPONENT_PRESS_DONE, "io", -1.0),
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNG, "picture", None),
    (MS.READ_TIGHTENING_TORQUE_2, "io", 19.026),
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER, "picture", None),
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP, "picture", None),
    (MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_CAP_PRESS, "picture", None),
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
           MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_COMPONENT_PRESS: 18, MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNG: 23,
           MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER: 29, MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP: 32,
           MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_CAP_PRESS: 35}
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
        # F4c: emergency while the completed cycle's values are still on screen (CHANGES 123: the cycle was already
        # recorded and "Component Successfully Done" shown at the free-rotation torque): recorded as completed, then
        # the abort screen is shown (as while waiting for the QR code), no second record
        TILES.clear(); SCREENS.clear(); SENT_ORIGINALS.clear()
        scan(QR1, "DOST - LHS - 2.0T : 11770 : 09.03.2025")
        run_steps()
        sendEmergencyAbortFromIOServerToFEServer(r)
        rec = take_record()
        check_full_record("F4c emergency while the finished cycle's values are shown", rec, QR1)
        check("F4c ... success shown first, then the abort screen",
              wait_for(lambda: "showEmergencyAbort" in SCREENS, 5) and SCREENS[:1] == ["showSuccess"], str(SCREENS))
        check("F4c ... no second record", take_record(timeout=2) is None)
        t1 = fe.componentProcessingTimeText(); time.sleep(2.2); t2 = fe.componentProcessingTimeText()
        check("F4d processing time stops at the end of the cycle and stays on screen (not 0) until the next scan",
              t1 == t2 and t1 != "0" and fe.currentComponentEndTime is not None, f"{t1} -> {t2}")

        # ---------------- F15 (CHANGES 123) ----------------
        print("\n=== F15. End of cycle: result box at once, values kept in the tiles 5 s, then cleared ===", flush=True)
        from Configuration import CosThetaConfigurator as _CC15
        hold = _CC15.getInstance().getFrontEndFreeRotationTorqueDisplaySecs()
        check("F15 frontend.free.rotation.torque.display.sec is 5 s", abs(hold - 5.0) < 1e-9, hold)
        assert wait_for(lambda: state() == MS.READ_QR_CODE, 15)
        time.sleep(hold + 0.5)                       # the last cycle's clear has happened
        TILES.clear(); SCREENS.clear(); SENT_ORIGINALS.clear(); CLEARS.clear()
        scan(QR1, "DOST - LHS - 2.0T : 11770 : 09.03.2025")
        run_steps(upto=len(SEQUENCE) - 1)
        st, kind, value = SEQUENCE[-1]
        assert wait_for(lambda: state() == st)
        tile = fe.resultsContainer.free_rotation_torque_container.image_label
        t0 = time.time()
        sendDataFromIOServerToFEServer(r, currentMachineState=st, value=value, action=moveAheadToNextComponent, result=ok)
        shown = wait_for(lambda: "showSuccess" in SCREENS, 3)
        tShown = time.time() - t0
        check("F15 'Component Successfully Done' at once (within 1 s of the free-rotation torque)", shown and tShown < 1.0,
              f"{tShown:.2f} s")
        rec = take_record(timeout=3)
        check("F15 record sent at once", rec is not None and time.time() - t0 < 3.0)
        check("F15 back to the QR code at once (waiting for the next component)", state() == MS.READ_QR_CODE, state().name)
        time.sleep(1.0)
        check("F15 1 s later: tiles not cleared, the free-rotation torque still shown",
              CLEARS == [] and "2.41" in tile.text(), (CLEARS, tile.text()))
        check("F15 ... the QR code line still shows the component", QR_SHOWN[-1] == "DOST - LHS - 2.0T : 11770 : 09.03.2025",
              QR_SHOWN[-1:])
        cleared = wait_for(lambda: CLEARS != [], hold + 2)
        tCleared = CLEARS[0] - t0 if CLEARS else None
        check("F15 tiles cleared about 5 s after the free-rotation torque", cleared and hold - 0.3 <= tCleared <= hold + 1.0,
              tCleared)
        check("F15 ... the free-rotation tile is empty and the QR code line cleared",
              "2.41" not in tile.text() and QR_SHOWN[-1] == "", (tile.text(), QR_SHOWN[-1:]))
        check("F15 ... cleared once", (time.sleep(1.0) or True) and len(CLEARS) == 1, len(CLEARS))
        # the next component scanned while the finished one is still shown: cleared at the scan, not later
        TILES.clear(); SCREENS.clear(); SENT_ORIGINALS.clear(); CLEARS.clear()
        scan(QR1, "DOST - LHS - 2.0T : 11770 : 09.03.2025")
        run_steps(upto=len(SEQUENCE) - 1)
        assert wait_for(lambda: state() == st)
        sendDataFromIOServerToFEServer(r, currentMachineState=st, value=value, action=moveAheadToNextComponent, result=ok)
        take_record(timeout=3)
        assert wait_for(lambda: state() == MS.READ_QR_CODE, 3)
        time.sleep(1.0)
        n0 = len(TILES)
        tScan = time.time()
        scan(QR2, "DOSTPLUS - RHS - 2.5T : 22881 : 15.10.2026")
        check("F15b next QR code 1 s into the hold: tiles cleared at the scan", len(CLEARS) == 1 and CLEARS[0] - tScan < 1.0,
              [round(c - tScan, 2) for c in CLEARS])
        send_step(*SEQUENCE[0], seed=7)
        time.sleep(hold + 0.5)
        check("F15b ... the old timer does not clear the new component's tiles (knuckle tile kept)",
              len(CLEARS) == 1 and len(TILES) > n0 and state() == SEQUENCE[1][0], (len(CLEARS), TILES[n0:], state().name))
        run_steps(skip=1)
        check_full_record("F15b next component", take_record(), QR2)
        # an abort: "Aborted" box at once, the tiles cleared after 0.75 s (not 5 s; user, 2026-11-17)
        assert wait_for(lambda: state() == MS.READ_QR_CODE, 10)
        time.sleep(hold + 0.5)
        TILES.clear(); SCREENS.clear(); SENT_ORIGINALS.clear(); CLEARS.clear()
        scan(QR1, "DOST - LHS - 2.0T : 11770 : 09.03.2025")
        run_steps(upto=3)
        assert wait_for(lambda: state() == SEQUENCE[3][0])
        tAbort = time.time()
        sendEmergencyAbortFromIOServerToFEServer(r)
        shownAbort = wait_for(lambda: "showEmergencyAbort" in SCREENS, 3)
        check("F15c emergency abort: 'Aborted' box at once (within 1.5 s)", shownAbort and time.time() - tAbort < 1.5,
              f"{time.time() - tAbort:.2f} s")
        take_record(timeout=3)
        cleared = wait_for(lambda: CLEARS != [], 3)
        tCleared = CLEARS[0] - tAbort if CLEARS else None
        check("F15c ... the tiles cleared about 0.75 s later, not 5 s", cleared and tCleared <= 2.0, tCleared)

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
                       tightening_torque2_setpoint="19.99", no_of_rotations_round1="2", no_of_rotations_round2="1",
                       rotation_order_lhs="CW.CCW", rotation_order_rhs="CCW.CW"))
        check("F6 valid entries accepted (numbers as floats, the orders as given)", errors == [] and good == dict(
              rotation_rpm=32.0, tightening_torque1_setpoint=18.5, tightening_torque2_setpoint=19.99,
              no_of_rotations_round1=2.0, no_of_rotations_round2=1.0, rotation_order_lhs="CW.CCW",
              rotation_order_rhs="CCW.CW"), str((good, errors)))
        _, errors = SP.parseMachineSettingsInputs(dict(rotation_rpm="30.5", tightening_torque1_setpoint="20",
                    tightening_torque2_setpoint="1.234", no_of_rotations_round1="5", no_of_rotations_round2="x",
                    rotation_order_lhs="CW", rotation_order_rhs=""))
        check("F6 invalid entries: one message per field (RPM not whole, torque 20, 3 decimals, count 5 (1 - 4 allowed), "
              "not a number, two orders not CW.CCW / CCW.CW)", len(errors) == 7, str(errors))
        _cur = dict(P.DEFAULT_MACHINE_SETTINGS)
        _lines = SP.machineSettingsChanges(dict(_cur, rotation_order_rhs="CCW.CW", no_of_rotations_round2=3.0), _cur)
        check("F6 the changes listed at Save include a changed order and round", len(_lines) == 2
              and any("RHS" in x and "CW.CCW -> CCW.CW" in x for x in _lines) and any("2nd Round" in x for x in _lines),
              str(_lines))
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

        # ---------------- F7b ----------------
        print("\n=== F7b. Production counts read off the GUI thread (database hanging 3 s) ===", flush=True)
        import frontend.AshokLeylandFrontEnd as _feCounts
        _origCounts = _feCounts.getProductionCounts
        _countThreads = []
        def _slowCounts(connection, dbName):
            _countThreads.append(threading.current_thread() is threading.main_thread())
            time.sleep(3)                                   # a hung PostgreSQL (statement timeout 5 s)
            return (7, 41, 123)
        _feCounts.getProductionCounts = _slowCounts
        GUI_GAPS["max"] = 0.0; GUI_GAPS["last"] = time.monotonic()
        ok_ = wait_for(lambda: getattr(fe, "productionCounts", None) == (7, 41, 123), 15)
        check("F7b counts read in the worker thread, never in the GUI thread", _countThreads and not any(_countThreads),
              _countThreads)
        check("F7b a 3 s database read does not stall the GUI thread (> 0.25 s)", GUI_GAPS["max"] < 0.25,
              f"longest GUI-thread stall {GUI_GAPS['max']:.3f} s")
        wait_for(lambda: "123" in str(fe.dateTimeStatus.getText()), 5)
        _shownText = fe.dateTimeStatus.getText()
        check("F7b counts shown on screen", ok_ and "123" in str(_shownText) and "41" in str(_shownText),
              str(_shownText)[:160])
        def _failCounts(connection, dbName):
            raise RuntimeError("database down (simulated)")
        _feCounts.getProductionCounts = _failCounts
        time.sleep(6)
        check("F7b database down: the last counts stay shown", getattr(fe, "productionCounts", None) == (7, 41, 123),
              getattr(fe, "productionCounts", None))
        _feCounts.getProductionCounts = _origCounts

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
        skippedTiles = (rc.bung_for_component_press_check_container, rc.component_press_check_container,
                        rc.no_bung_after_component_press_check_container, rc.tightening_torque2_check_container)
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

        # Machine Settings dialog (2026-11-17): the password to open it; at Save the password again with the changes
        # listed - entering it is the confirmation; no Yes / No anywhere
        import frontend.SimplePopups as SP
        check("F12 Machine Settings: 'Bypass Component Press and Second Tightening' checkbox (per model)",
              ("bypass_press_and_tightening2", "Bypass Component Press and Second Tightening") in SP.MACHINE_SETTINGS_FLAG_FIELDS)
        _pw, _yn, _err = SP.getJustPassword, SP.sg.popup_yes_no, SP.showErrorMessage
        asked, prompts = [], []
        SP.showErrorMessage = lambda *a, **k: asked.append(("error", a[0] if a else ""))
        SP.sg.popup_yes_no = lambda text, **k: asked.append(("yesno", text)) or "Yes"
        new = dict(P.DEFAULT_MACHINE_SETTINGS, bypass_press_and_tightening2=True)
        old = dict(P.DEFAULT_MACHINE_SETTINGS, bypass_press_and_tightening2=False)
        SP.getJustPassword = lambda *a, **k: prompts.append(a[0] if a else k.get("textToShow", "")) or "wrong"
        check("F12 wrong password -> not saved, error shown", SP.confirmMachineSettingsChange("DOST", new, old) is False
              and [k for k, _ in asked] == ["error"], str(asked))
        asked.clear(); prompts.clear(); SP.getJustPassword = lambda *a, **k: ""
        check("F12 password cancelled -> not saved, nothing shown", SP.confirmMachineSettingsChange("DOST", new, old) is False
              and asked == [], str(asked))
        asked.clear(); prompts.clear()
        SP.getJustPassword = lambda *a, **k: prompts.append(a[0] if a else k.get("textToShow", "")) or "admin123"
        check("F12 admin123 at Save -> saved, no Yes / No; the prompt lists the change and says the password confirms it",
              SP.confirmMachineSettingsChange("DOST", new, old) is True and asked == [] and prompts
              and "Bypass Component Press and Second Tightening: Yes" in prompts[0]
              and "confirms this change" in prompts[0], str((asked, prompts)))
        # opening the dialog
        asked.clear(); prompts.clear()
        check("F12 open: right password -> opens", SP.askMachineSettingsOpenPassword() is True and asked == []
              and prompts and "open" in prompts[0].lower(), str((asked, prompts)))
        SP.getJustPassword = lambda *a, **k: "wrong"
        check("F12 open: wrong password -> not opened, error shown", SP.askMachineSettingsOpenPassword() is False
              and [k for k, _ in asked] == ["error"], str(asked))
        asked.clear(); SP.getJustPassword = lambda *a, **k: ""
        check("F12 open: cancelled -> not opened, nothing shown", SP.askMachineSettingsOpenPassword() is False
              and asked == [], str(asked))
        _db = SP.getDatabaseName
        SP.getDatabaseName = lambda: (_ for _ in ()).throw(AssertionError("dialog opened"))
        SP.getJustPassword = lambda *a, **k: "wrong"; asked.clear()
        check("F12 open: wrong password -> updateMachineSettingsDialog returns before the dialog",
              SP.updateMachineSettingsDialog("admin", "Administrator") is False and SP.aWindowIsInProgress is False
              and [k for k, _ in asked] == ["error"], str(asked))
        SP.getDatabaseName = _db
        # 2026-11-15: passwords from the properties file; the bypass switch has its own password, asked in addition
        _ms, _bp = SP.machineSettingsPassword, SP.machineSettingsBypassPassword
        SP.machineSettingsPassword, SP.machineSettingsBypassPassword = (lambda: "set-pw"), (lambda: "bypass-pw")
        answers = []
        SP.getJustPassword = lambda *a, **k: prompts.append(a[0] if a else "") or (answers.pop(0) if answers else "")
        asked.clear(); prompts.clear(); answers[:] = ["set-pw", "bypass-pw"]
        check("F12b bypass changed: settings password then bypass password (both say they confirm) -> saved",
              SP.confirmMachineSettingsChange("DOST", new, old) is True and asked == [] and len(prompts) == 2
              and all("confirms this change" in t for t in prompts), str((asked, prompts)))
        asked.clear(); answers[:] = ["set-pw", "set-pw"]
        check("F12b bypass changed, wrong bypass password -> not saved, 'Wrong bypass password' shown",
              SP.confirmMachineSettingsChange("DOST", new, old) is False and asked
              and asked[0][0] == "error" and "bypass" in asked[0][1].lower(), str(asked))
        asked.clear(); answers[:] = ["set-pw"]
        same = dict(old, rotation_rpm=float(old["rotation_rpm"]) + 1.0)
        check("F12b bypass unchanged: only the settings password asked -> saved",
              SP.confirmMachineSettingsChange("DOST", same, old) is True and answers == [], str(asked))
        asked.clear(); answers[:] = ["admin123"]
        check("F12b the properties' machine.settings.password applies (admin123 refused when it is set-pw)",
              SP.confirmMachineSettingsChange("DOST", same, old) is False, str(asked))
        asked.clear(); answers[:] = ["set-pw"]
        check("F12b open uses machine.settings.password too", SP.askMachineSettingsOpenPassword() is True)
        SP.machineSettingsPassword, SP.machineSettingsBypassPassword = _ms, _bp
        from Configuration import CosThetaConfigurator as _CC
        _c = _CC.getInstance()
        check("F12b passwords in the properties file: frontend abort / machine settings / bypass",
              all(isinstance(v, str) and v for v in (_c.getFrontendAbortPassword(), _c.getMachineSettingsPassword(),
                                                    _c.getMachineSettingsBypassPassword())),
              f"{_c.getFrontendAbortPassword()!r} {_c.getMachineSettingsPassword()!r} {_c.getMachineSettingsBypassPassword()!r}")
        SP.getJustPassword, SP.sg.popup_yes_no, SP.showErrorMessage = _pw, _yn, _err

        # ---------------- F13 (2026-11-13) ----------------
        print("\n=== F13. The Tk test windows that play the servers: bypass option ===", flush=True)
        if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):     # Windows has no DISPLAY (CHANGES 160)
            print("     (no DISPLAY: F13 needs one for Tk - run under xvfb-run)", flush=True)
        else:
            import importlib.util as _ilu, tkinter as _tk
            _here = pathlib.Path(__file__).resolve().parent
            def _load(name):
                spec = _ilu.spec_from_file_location(name, str(_here / f"{name}.py"))
                mod = _ilu.module_from_spec(spec); spec.loader.exec_module(mod); return mod
            sim = _load("Test_FEServer_Simulated_Inputs_UI")
            _root = _tk.Tk()
            simApp = sim.FrontEndTesterApp(_root)
            simApp.wait_for_fe.set(False); simApp.bypass_var.set(True)
            assert wait_for(lambda: state() == MS.READ_QR_CODE, 10)
            time.sleep(1.0); r.delete(fe2dbq)
            sent, cycle0 = [], simApp.cycle_count
            while simApp.cycle_count == cycle0 and len(sent) < 20:
                cmd = sim.commands[simApp.current_command_index]
                assert wait_for(lambda: state() == cmd["state"], 10), f"FE not in {cmd['state'].name} ({state().name})"
                time.sleep(0.3)
                simApp._send(ok); sent.append(cmd["state"])
                assert wait_for(lambda: consumed(cmd["queue"]), 10)
                _root.update()
            check("F13 Simulated-inputs window, Bypass ticked: QR, 4 pictures, Torque 1, then split pin ... free rotation "
                  "(the bung, press, bung removal and Torque 2 steps not sent)",
                  not (set(sent) & set(sim.BYPASSED_STATES)) and len(sent) == 11, [x.name for x in sent])
            rec = None; t0 = time.time()
            while time.time() - t0 < 8 and rec is None:
                got = readDataInDatabaseServerFromFEServer(r, block=200, returnRotationDone=True, returnAbort=True,
                                                           returnBypassed=True)
                rec = got if got[0] else None
            check("F13 ... the frontend's record: overall OK, marked bypassed",
                  rec is not None and rec[OVERALL_IDX] == ok and rec[-1] is True, str(rec[-3:]) if rec else None)
            simApp.bypass_var.set(False)
            check("F13 ... the next QR code without the bypass removes cycleSettings:<QR> (full run)",
                  wait_for(lambda: state() == MS.READ_QR_CODE, 10) and (simApp._send(ok) or True)
                  and r.get(f"{CYCLE_SETTINGS_KEY_PREFIX}{sim.DEFAULT_QR_CODE}") is None and not sim.BYPASS["cycle"])
            sendEmergencyAbortFromIOServerToFEServer(r)
            assert wait_for(lambda: state() == MS.READ_QR_CODE, 10)
            time.sleep(1.0); r.delete(fe2dbq)

            img = _load("Test_FEServer_Mock_With_Image_Files_UI")
            _root2 = _tk.Toplevel(_root)
            imgApp = img.ImageProcessorTestUI(_root2)
            imgApp.bypass_var.set(True)
            imgApp._send_qr_code(img.DOST_QR_CODE, "DOST")
            check("F13 Image-files window, Bypass ticked: the QR code publishes the bypass for the frontend",
                  readCycleSettingsForQRCode(r, img.DOST_QR_CODE).get("bypass_press_and_tightening2") is True)
            assert wait_for(lambda: state() == MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, 10)
            for i, st in enumerate(SEQUENCE[:4]):
                send_step(st[0], st[1], st[2], seed=500 + i)          # the 4 pictures (camera checks not run here)
            imgApp.current_step_index = 5
            imgApp._send_torque(imgApp.steps[5], 5, 18.9, 19.1)
            check("F13 ... Torque 1 sent with moveToState = split pin: the frontend and the window both go to the split pin "
                  "step, the 4 steps between marked 'Bypassed'",
                  wait_for(lambda: state() == MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER, 6)
                  and imgApp.steps[imgApp.current_step_index].state == MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER
                  and all(str(imgApp.step_widgets[i][k].cget("text")).startswith("Bypassed")
                          for i, k in ((6, "result_label"), (7, "status_label"), (8, "result_label"), (9, "value_label"))),
                  f"{state().name} {imgApp.current_step_index}")
            sendEmergencyAbortFromIOServerToFEServer(r)
            assert wait_for(lambda: state() == MS.READ_QR_CODE, 10)
            _root.destroy()

        # ---------------- F14 (CHANGES 108): state sync phase A - logging only ----------------
        print("\n=== F14. State sync: the frontend compares its state with the IOServer's and only logs ===", flush=True)
        time.sleep(1.5)
        r.xtrim(qrcode2feq, 0)                       # a QR message an earlier part (F13's windows) may have left
        if state() != MS.READ_QR_CODE:
            sendEmergencyAbortFromIOServerToFEServer(r)
        assert wait_for(lambda: state() == MS.READ_QR_CODE, 10)
        take_record(2)
        SYNC.clear()
        seqBox = {"pid": 4242, "seq": 0}
        def io_state(new, old=None, cycle=7, pid=None, seq=None, reason=None):
            if pid is not None and pid != seqBox["pid"]:
                seqBox["pid"], seqBox["seq"] = pid, 0
            seqBox["seq"] = seq if seq is not None else seqBox["seq"] + 1
            publishIOServerState(r, {"pid": seqBox["pid"], "seq": seqBox["seq"], "cycleId": cycle, "qr": "F14-QR",
                                     "old": None if old is None else int(old), "new": int(new),
                                     "newName": MS(int(new)).name, "reason": reason, "time": time.time()})
            time.sleep(0.4)
        def lines(word): return [t for _, t in SYNC if word in t]
        io_state(MS.READ_QR_CODE, reason="IOServer start")
        check("F14 first IOServer state: 'STATE-SYNC START' logged", wait_for(lambda: lines("STATE-SYNC START"), 3), SYNC)
        # a whole cycle with the IOServer's states published as the IOServer would (read state, write state while the
        # answer is awaited, value states), the frontend driven as before: no mismatch
        io_state(MS.WRITE_QR_CODE, MS.READ_QR_CODE)
        scan(QR1, "F14")
        io_state(MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, MS.WRITE_QR_CODE)
        prev = MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE
        for i, (st, kind, value) in enumerate(SEQUENCE):
            if kind == "picture":
                if prev != st: io_state(st, prev)
                io_state(MS(int(st) + 1), st)                       # the IOServer waits for the camera's answer
                prev = MS(int(st) + 1)
            else:
                if st in (MS.READ_TIGHTENING_TORQUE_1, MS.READ_TIGHTENING_TORQUE_2, MS.READ_FREE_ROTATION_TORQUE_1):
                    io_state(MS(int(st) - 1), prev); io_state(st, MS(int(st) - 1))
                else:
                    io_state(st, prev)
                prev = st
            send_step(st, kind, value, seed=700 + i, expect_next=(st != MS.READ_FREE_ROTATION_TORQUE_1))
        io_state(MS.READ_QR_CODE, MS.READ_FREE_ROTATION_TORQUE_1, reason="cycle completed")
        take_record()
        assert wait_for(lambda: state() == MS.READ_QR_CODE, 10)
        time.sleep(2.5)
        check("F14 a whole cycle with the IOServer's states as it publishes them: no MISMATCH, summary 'in sync'",
              not lines("MISMATCH") and any("STATE-SYNC CYCLE 7 completed: in sync" in t for t in lines("CYCLE")),
              [t for _, t in SYNC][-6:])
        # the IOServer somewhere the frontend is not (as if the frontend had missed it): logged after the grace period
        n0 = len(SYNC)
        io_state(MS.WRITE_RESULT_OF_CHECKING_KNUCKLE, MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, cycle=8)
        time.sleep(0.6)
        check("F14 a difference shorter than the grace period (1.5 s here) is not logged", not lines("MISMATCH"))
        mm = wait_for(lambda: lines("MISMATCH #"), 4)
        text = (lines("MISMATCH #") or [""])[0]
        check("F14 ... longer: 'STATE-SYNC MISMATCH #1' with both states, the expected ones, cycle, QR, seq and pid",
              mm and "frontend READ_QR_CODE(1) vs IOServer WRITE_RESULT_OF_CHECKING_KNUCKLE(4)" in text
              and "READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE(3)" in text and "cycle 8" in text and "QR F14-QR" in text
              and "pid 4242" in text, text)
        time.sleep(2.0)
        check("F14 ... logged once, not again while it lasts", len(lines("MISMATCH #")) == 1, len(lines("MISMATCH #")))
        check("F14 ... and the frontend changed nothing (still at the QR code)", state() == MS.READ_QR_CODE, state().name)
        io_state(MS.READ_QR_CODE, MS.WRITE_RESULT_OF_CHECKING_KNUCKLE, cycle=8, reason="abort: test")
        check("F14 in step again: 'STATE-SYNC RESOLVED #1 after n s'", wait_for(lambda: lines("RESOLVED #1"), 3), SYNC[n0:])
        check("F14 cycle summary: 'STATE-SYNC CYCLE 8 aborted in ...: 1 MISMATCH(ES)'",
              wait_for(lambda: any("CYCLE 8 aborted in WRITE_RESULT_OF_CHECKING_KNUCKLE(4): 1 MISMATCH" in t
                                   for t in lines("CYCLE")), 3), lines("CYCLE"))
        io_state(MS.READ_QR_CODE, seq=seqBox["seq"] + 3)
        check("F14 IOServer state messages missing: 'STATE-SYNC GAP ... 2 missing'",
              wait_for(lambda: any("2 missing" in t for t in lines("STATE-SYNC GAP")), 3), lines("GAP"))
        io_state(MS.READ_QR_CODE, pid=4343, reason="IOServer start")
        check("F14 IOServer restarted (new pid): 'STATE-SYNC IOSERVER-RESTART pid 4242 -> 4343'",
              wait_for(lambda: any("pid 4242 -> 4343" in t for t in lines("IOSERVER-RESTART")), 3), lines("RESTART"))
        check("F14 every line starts with 'STATE-SYNC' (one search finds them all)",
              SYNC and all(t.startswith("STATE-SYNC ") for _, t in SYNC), [t for _, t in SYNC if not t.startswith("STATE-SYNC ")])
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

# State sync (F14): the frontend's STATE-SYNC log lines captured; grace 1.5 s for the test
import frontend.AshokLeylandFrontEnd as _feModule
from Configuration import CosThetaConfigurator as _CC
_CC.getFrontEndStateSyncGraceSecs = classmethod(lambda cls: 1.5)
SYNC = []
_origSyncLog = _feModule.StateSyncWorker._log
def _syncLog(level, text):
    SYNC.append((level, text)); print(f"  [{level}] {text}", flush=True); _origSyncLog(level, text)
_feModule.StateSyncWorker._log = staticmethod(_syncLog)

fe = FE(mode="Test", username="admin", role="Administrator")
FE_WINDOW.append(fe)
threading.Thread(target=driver, daemon=True).start()
app.exec()
