"""Dry run: how MachineStateMachine delivers state changes to its listener (CHANGES 110, 112). No Redis, no PLC.

  D1 the listener runs outside the state lock: while it is slow (a Redis call with timeouts, in the IOServer),
     another thread changes the state at once instead of waiting for it
  D2 every change is delivered once, in the order the changes were made, also when two threads change the state
     while a delivery is in progress
  D3 a reason given with setCurrentState(..., reason=) is delivered with that change only - not taken by a change
     another thread makes at the same time (the abort's reason, CHANGES 112)
  D4 nested state-changing calls (incrementStateFrom -> incrementState -> setter) deliver one change, once

Run from the project root:  python test/machine2_dryrun/DryRun_State_Change_Delivery.py   (exit code 1 on failure)
"""
import os, pathlib, sys, threading, time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("COSTHETA_PLC_VENDOR", "siemens" if HERE.name.startswith("machine2") else "allenbradley")

RESULTS = []
def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""), flush=True)

from statemachine.StateMachine import MachineStateMachine, MachineState as MS

# ------------------------------------------------------------------------------------------------ D1, D2, D3
print("\n=== D1-D3. A slow listener; two threads changing the state ===", flush=True)
sm = MachineStateMachine()
delivered = []
slowFirst = threading.Event()
def listener(old, new, reason=None):
    delivered.append((int(new), reason))
    if not slowFirst.is_set():          # the first delivery is slow (Redis with timeouts)
        slowFirst.set()
        time.sleep(1.0)
sm.stateChangeListener = listener

t0 = time.monotonic()
a = threading.Thread(target=lambda: sm.setCurrentState(MS.WRITE_QR_CODE))           # 1 -> 2, delivered slowly
a.start()
slowFirst.wait(2)
tB = time.monotonic()
sm.setCurrentState(MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)                          # 2 -> 3 while A delivers
bTook = time.monotonic() - tB
sm.setCurrentState(MS.READ_QR_CODE, reason="abort: test")                             # 3 -> 1 with a reason
a.join(5)
time.sleep(0.1)
check("D1 another thread changes the state while the listener is slow (no wait for it)", bTook < 0.2,
      f"{bTook:.3f} s (listener 1.0 s)")
check("D2 every change delivered once, in order", [n for n, _ in delivered] == [2, 3, 1], delivered)
check("D3 the reason only with its own change", delivered == [(2, None), (3, None), (1, "abort: test")], delivered)

# ------------------------------------------------------------------------------------------------ D4
print("\n=== D4. Nested calls ===", flush=True)
sm2 = MachineStateMachine()
got = []
sm2.stateChangeListener = lambda old, new: got.append((int(old), int(new)))
ok_ = sm2.incrementStateFrom(MS.READ_QR_CODE)
check("D4 incrementStateFrom delivers one change, once (two-argument listener)", ok_ and got == [(1, 2)], got)

failed = [n for n, ok_ in RESULTS if not ok_]
print(f"\nRESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""),
      flush=True)
os._exit(1 if failed else 0)
