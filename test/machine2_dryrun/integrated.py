"""Integrated dry-run: PLC sim -> IOServer -> camera/QR sim -> Frontend -> DBServer -> PostgreSQL, plus HeartbeatServer.
Everything except the PLC, camera and QR scanner is the real Machine-2 code. One full cycle, then checks.
"""
import sys, time, threading, collections, types
import pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
if sys.platform != 'win32': sys.modules['winsound'] = types.SimpleNamespace(PlaySound=lambda *a, **k: None, SND_FILENAME=0, SND_ASYNC=0, Beep=lambda *a: None)
import numpy as np, cv2, psycopg2
from redis import Redis

# ---------------- fake Allen-Bradley PLC ----------------
TAGS = collections.defaultdict(lambda: False); LOCK = threading.RLock(); WRITES = []
class _V:
    def __init__(self, v): self.value = v
class FakeLogix:
    def __init__(self, ip): self.connected = False
    def open(self): self.connected = True
    def close(self): self.connected = False
    def read(self, *t):
        with LOCK: return _V(TAGS[t[0]])
    def write(self, t, v):
        with LOCK: TAGS[t] = v; WRITES.append((t, v))
import costhetaio.IOServer as IOS
IOS.LogixDriver = FakeLogix
from costhetaio.IOServer import IOServer
from statemachine.StateMachine import MachineState as MS
from utils.RedisUtils import *
import persistence.Persistence as P

r = Redis('localhost', 6379); clearQueues(r)
P.setDatabaseName("Production"); P.makeFoldersForImages(mode_data_subfolder=P.getDataSubFolder())
TAGS[IOServer.PLC_PC_WatchTag] = 1234

src = cv2.imread(str(HERE / "m1_frontend_sample.png"))
COLS = [0, 273, 546, 819, 1092, 1366, 1638]; ROWS = [255, 537]
def photo(rw, c): x0, y0 = COLS[c], ROWS[rw]; return src[y0 + 42: y0 + 238, x0 + 8: x0 + 266].copy()
IMG = {MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE: photo(0, 0), MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING: photo(0, 1),
       MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING: photo(0, 2), MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER: photo(0, 3),
       MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS: photo(0, 6)[:, :250], MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK: photo(1, 1),
       MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER: photo(1, 3), MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP: photo(1, 4),
       MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS: photo(1, 5)}
QR = "8201206$400102VA1C$22881$15.10.2026$C5211702$MEK$40cR4-B$1$SPDL ASSY-KNURH$SNPL-MAT$"   # DOSTPLUS RHS

# ---------------- simulated QR scanner + camera servers (incl. their heartbeats) ----------------
SCAN = threading.Event()
def qr_and_camera():
    rq = Redis('localhost', 6379); pending = False; lastHb = 0
    while True:
        if time.time() - lastHb > 0.5:
            sendHeartbeatFromCameraServerToHeartbeatServer(rq, ALIVE); sendHeartbeatFromQRCodeServerToHeartbeatServer(rq, ALIVE); lastHb = time.time()
        _, d = getData(rq, io2qrcodeq, block=50)
        if d: pending = True
        if pending and SCAN.is_set():
            SCAN.clear(); pending = False
            sendDataFromQRCodeServerToFEServer(rq, qrCode=QR, displayString="DOSTPLUS - RHS - 2.5T : 22881 : 15.10.2026", qrCodeStatus=ok)
            sendDataFromQRCodeServerToIOServer(rq, qrCode=QR, qrCodeStatus=ok)
        _, st, take = readDataInCameraServerFromIOServer(rq, block=50)
        if take:
            time.sleep(0.4)
            sendDataFromCameraServerToFEServer(rq, originalImage=IMG[st], processedImage=IMG[st], result=ok, currentMachineState=st)
            sendDataFromCameraServerToIOServer(rq, result=ok, currentMachineState=st)
threading.Thread(target=qr_and_camera, daemon=True).start()

# ---------------- PLC sequence ----------------
def wait_for(pred, timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred(): return True
        time.sleep(0.05)
    return False
def check(trig, done):
    with LOCK: TAGS[trig] = True
    assert wait_for(lambda: TAGS[done] is True), f"timeout {done}"
    time.sleep(1.5)                      # leave time for the frontend to show the image
    with LOCK: TAGS[trig] = False; TAGS[done] = False
def value(setT, valT, v):
    with LOCK: TAGS[valT] = v; TAGS[setT] = True
    time.sleep(2.5)
def pulse(t):
    with LOCK: TAGS[t] = True
    time.sleep(1.0)
    with LOCK: TAGS[t] = False

RESULT = {}
def plc():
    time.sleep(12)                       # IOServer + frontend start-up
    SCAN.set()
    with LOCK: TAGS[IOServer.PLC_PC_CheckQRCode] = True
    assert wait_for(lambda: TAGS[IOServer.PC_PLC_QRCodeCheckDone] is True), "QR"
    RESULT['settings'] = {t: v for t, v in WRITES if t in (IOServer.PC_PLC_LH_RH_Selection, IOServer.PC_PLC_ModelSelection,
                          IOServer.PC_PLC_TighteningTorque1Setpoint, IOServer.PC_PLC_TighteningTorque2Setpoint,
                          IOServer.PC_PLC_RotationRPM1, IOServer.PC_PLC_RotationRPM2)}
    time.sleep(1.0)
    with LOCK: TAGS[IOServer.PLC_PC_CheckQRCode] = False; TAGS[IOServer.PC_PLC_QRCodeCheckDone] = False
    check(IOServer.PLC_PC_CheckKnuckle, IOServer.PC_PLC_KnuckleCheckDone)
    check(IOServer.PLC_PC_CheckHub, IOServer.PC_PLC_HubCheckDone)
    check(IOServer.PLC_PC_CheckHubAndSecondBearing, IOServer.PC_PLC_HubAndSecondBearingCheckDone)
    check(IOServer.PLC_PC_CheckNutAndPlateWasher, IOServer.PC_PLC_NutAndPlateWasherCheckDone)
    value(IOServer.PLC_PC_Station2TorqueValueSet, IOServer.PLC_PC_Station2TorqueValue, 19.08)
    check(IOServer.PLC_PC_CheckNoCapBunk, IOServer.PC_PLC_NoCapBunkCheckDone)
    pulse(IOServer.PLC_PC_ComponentPressDone)
    check(IOServer.PLC_PC_CheckNoBunk, IOServer.PC_PLC_NoBunkCheckDone)
    value(IOServer.PLC_PC_Station2TorqueValueSet, IOServer.PLC_PC_Station2TorqueValue, 19.03)
    check(IOServer.PLC_PC_CheckSplitPinAndWasher, IOServer.PC_PLC_SplitPinAndWasherCheckDone)
    check(IOServer.PLC_PC_CheckCap, IOServer.PC_PLC_CapCheckDone)
    check(IOServer.PLC_PC_CheckBunk, IOServer.PC_PLC_BunkCheckDone)
    pulse(IOServer.PLC_PC_CapPressDone)
    value(IOServer.PLC_PC_Station3TorqueValueSet, IOServer.PLC_PC_Station3TorqueValue, 2.41)
    RESULT['plc_done'] = True

# ---------------- real servers ----------------
from persistence.DBServer import startDBServer
from monitorAllConnections.HeartbeatAndAlarmServer import startHeartbeatAndAlarmServer
threading.Thread(target=startDBServer, kwargs=dict(mode="Production", username="admin"), daemon=True).start()
threading.Thread(target=startHeartbeatAndAlarmServer, daemon=True).start()
threading.Thread(target=lambda: IOServer(mode="Production"), daemon=True).start()

from frontend.AshokLeylandFrontEnd import AshokLeylandFrontEnd, app
from PySide6.QtCore import QTimer
orig_final = AshokLeylandFrontEnd.doFinalProcessing
def patched_final(self):
    if self.largeImageWindow.isVisible(): self.largeImageWindow.hide()
    self.grab().save(str(HERE / "integrated_end_of_cycle.png"))
    orig_final(self)
    QTimer.singleShot(14000, app.quit)        # DB server polls every 5 s
AshokLeylandFrontEnd.doFinalProcessing = patched_final
gfe = AshokLeylandFrontEnd(mode="Production", username="admin", role="Administrator")
threading.Thread(target=plc, daemon=True).start()
QTimer.singleShot(150000, app.quit)
app.exec()

c = psycopg2.connect(user="postgres", password="postgres", host="127.0.0.1", database="ashok_leyland_production"); cur = c.cursor()
cur.execute("""select model_name, lhs_rhs, model_tonnage, nut_tightening_torque_1, nut_tightening_torque_2, free_rotation_torque_1,
               check_knuckle_result, check_bunk_for_component_press_result, component_press_done, check_no_bunk_result,
               check_splitpin_and_washer_result, check_cap_result, check_bunk_cap_press_result, cap_press_done,
               ok_notok_result, username from al_hub_and_disc_assembly_schema.hub_and_disc_assembly_data
               where qr_code = %s order by record_id desc limit 1""", (QR,))
print("RESULT plc sequence completed:", RESULT.get('plc_done'))
print("RESULT state-2 settings written:", RESULT.get('settings'))
print("RESULT DB row:", cur.fetchone())
import os; os._exit(0)
