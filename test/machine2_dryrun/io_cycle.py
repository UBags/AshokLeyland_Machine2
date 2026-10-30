"""Synthetic dry-run of costhetaio/IOServer.py (Allen-Bradley path) through full Machine-2 cycles.

Real Redis; pycomm3.LogixDriver replaced by an in-memory tag table driven by a PLC simulator thread.
QR server and camera server are simulated on their Redis queues. Records:
  - the IOServer state trace, the IO->FE messages, and the cycle settings written in state 2.
"""
import sys, time, threading, collections
import pathlib
ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

TAGS = collections.defaultdict(lambda: False)
WRITES = []                      # (tag, value) every PC write
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


import costhetaio.IOServer as IOS
IOS.LogixDriver = FakeLogix
from costhetaio.IOServer import IOServer
from statemachine.StateMachine import MachineState as MS
from utils.RedisUtils import *

r = Redis('localhost', 6379); clearQueues(r)
QR = "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"
TAGS[IOServer.PLC_PC_WatchTag] = 1234

# ---------- simulated QR + camera servers ----------
SCAN = threading.Event()          # operator scans the knuckle only when a cycle starts
def qr_and_camera():
    rq = Redis('localhost', 6379)
    pending = False
    while True:
        st, data = getData(rq, io2qrcodeq, block=100)
        if data: pending = True
        if pending and SCAN.is_set():
            SCAN.clear(); pending = False
            sendDataFromQRCodeServerToIOServer(rq, qrCode=QR, qrCodeStatus=ok)
        t, state, take = readDataInCameraServerFromIOServer(rq, block=100)
        if take:
            time.sleep(0.3)
            sendDataFromCameraServerToIOServer(rq, result=ok, currentMachineState=state)
threading.Thread(target=qr_and_camera, daemon=True).start()

# ---------- simulated FE consumer of io2feq ----------
FE_MSGS = []
def fe():
    rf = Redis('localhost', 6379)
    while True:
        cur, mv, action, value, result = readDataInFEServerFromIOServer(rf, block=200)
        if cur != MS.INVALID_STATE:
            FE_MSGS.append((cur.name, value, action))
threading.Thread(target=fe, daemon=True).start()

io = IOServer(mode="Test")

def wait_for(pred, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred(): return True
        time.sleep(0.05)
    return False

def plc_check(trigger, done):
    """PLC raises trigger, waits for PC Done, then drops trigger and Done (PLC-side reset)."""
    with LOCK: TAGS[trigger] = True
    assert wait_for(lambda: TAGS[done] is True), f"timeout waiting for {done}"
    time.sleep(0.3)
    with LOCK: TAGS[trigger] = False; TAGS[done] = False

def plc_value(setTag, valTag, value):
    with LOCK: TAGS[valTag] = value; TAGS[setTag] = True
    time.sleep(2.5)      # IOServer resets these after 1.5/2.0 s via UpdateTagsToDefaultProcessor

def plc_pulse(tag):
    with LOCK: TAGS[tag] = True
    time.sleep(0.8)
    with LOCK: TAGS[tag] = False

TRACE = []
def tracer():
    last = None
    while True:
        s = io.machineState.getCurrentState()
        if s != last: TRACE.append(s.value); last = s
        time.sleep(0.01)
threading.Thread(target=tracer, daemon=True).start()

def one_cycle(n, t1, t2, frt):
    print(f"--- cycle {n} ---", flush=True)
    WRITES.clear()
    SCAN.set()
    # IOServer arms the QR read itself at start/after a cycle; on first start the PLC raises it
    with LOCK: TAGS[IOServer.PLC_PC_CheckQRCode] = True
    assert wait_for(lambda: TAGS[IOServer.PC_PLC_QRCodeCheckDone] is True), "QR done timeout"
    time.sleep(0.5)
    with LOCK: TAGS[IOServer.PLC_PC_CheckQRCode] = False; TAGS[IOServer.PC_PLC_QRCodeCheckDone] = False
    plc_check(IOServer.PLC_PC_CheckKnuckle, IOServer.PC_PLC_KnuckleCheckDone)
    plc_check(IOServer.PLC_PC_CheckHub, IOServer.PC_PLC_HubCheckDone)
    plc_check(IOServer.PLC_PC_CheckHubAndSecondBearing, IOServer.PC_PLC_HubAndSecondBearingCheckDone)
    plc_check(IOServer.PLC_PC_CheckNutAndPlateWasher, IOServer.PC_PLC_NutAndPlateWasherCheckDone)
    plc_value(IOServer.PLC_PC_Station2TorqueValueSet, IOServer.PLC_PC_Station2TorqueValue, t1)
    plc_check(IOServer.PLC_PC_CheckNoCapBunk, IOServer.PC_PLC_NoCapBunkCheckDone)
    plc_pulse(IOServer.PLC_PC_ComponentPressDone)
    plc_check(IOServer.PLC_PC_CheckNoBunk, IOServer.PC_PLC_NoBunkCheckDone)
    plc_value(IOServer.PLC_PC_Station2TorqueValueSet, IOServer.PLC_PC_Station2TorqueValue, t2)
    plc_check(IOServer.PLC_PC_CheckSplitPinAndWasher, IOServer.PC_PLC_SplitPinAndWasherCheckDone)
    plc_check(IOServer.PLC_PC_CheckCap, IOServer.PC_PLC_CapCheckDone)
    plc_check(IOServer.PLC_PC_CheckBunk, IOServer.PC_PLC_BunkCheckDone)
    plc_pulse(IOServer.PLC_PC_CapPressDone)
    plc_value(IOServer.PLC_PC_Station3TorqueValueSet, IOServer.PLC_PC_Station3TorqueValue, frt)
    assert wait_for(lambda: 28 in TRACE and TRACE[-1] in (1, 2)), "did not wrap to state 1"   # PC self-arms QR: 1 -> 2 immediately
    time.sleep(0.5)

time.sleep(6)            # IOServer __init__ sleeps 5 s before starting threads
TRACE.clear()
one_cycle(1, 19.08, 19.03, 2.41)
settings = {t: v for t, v in WRITES if t in (IOServer.PC_PLC_LH_RH_Selection, IOServer.PC_PLC_ModelSelection,
            IOServer.PC_PLC_TighteningTorque1Setpoint, IOServer.PC_PLC_TighteningTorque2Setpoint,
            IOServer.PC_PLC_RotationRPM1, IOServer.PC_PLC_RotationRPM2)}
trace1 = list(TRACE); TRACE.clear(); fe1 = list(FE_MSGS); FE_MSGS.clear()
one_cycle(2, 18.97, 19.11, 2.36)
trace2 = list(TRACE)

print("RESULT state trace cycle 1:", trace1)
print("RESULT state trace cycle 2:", trace2)
print("RESULT expected            :", list(range(1, 29)) + [1])
print("RESULT cycle-settings written in state 2:", settings)
print("RESULT IO->FE messages cycle 1:", fe1)
print("RESULT CheckQRCode re-armed by PC at end:", TAGS[IOServer.PLC_PC_CheckQRCode])
import os; os._exit(0)
