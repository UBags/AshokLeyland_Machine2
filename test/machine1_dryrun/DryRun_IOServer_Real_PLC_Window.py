"""Drives Test_IOServer_With_Real_PLC_UI.py through its own handlers, with automatic PASS/FAIL checks (exit 1 on failure).

The real window (Tk) against the real IOServer (PLCIOServer) on the simulated PLC of _plc_sim.py, in this process (the
window itself only talks to the IOServer over Redis, as with the real PLC): state read over Redis, "Get Tag Values" (every
tag in the dump), "Send LHS QRCode", a PLC trigger then "OK" for the knuckle (OK before Done), "Reset to Step 0" (abort to
the IOServer, back at the QR code). Needs Redis, PostgreSQL and a display (on Linux without one: xvfb-run). Vendor:
COSTHETA_PLC_VENDOR, else plc.vendor.

    python test/<this folder>/DryRun_IOServer_Real_PLC_Window.py
"""
import sys, os, time, threading, importlib.util, pathlib
HERE = pathlib.Path(__file__).resolve().parent; ROOT = str(HERE.parents[1]); win = HERE / "Test_IOServer_With_Real_PLC_UI.py"
sys.path.insert(0, ROOT); sys.path.insert(0, str(win.parent))
from _plc_sim import setup, WRITES
IOServer, TAGS, _W, LOCK, VENDOR = setup()
TAGS[IOServer.PLC_PC_WatchTag] = 1234
from redis import Redis
from utils.RedisUtils import clearQueues
clearQueues(Redis("localhost", 6379))
io = IOServer(mode="Test")
spec = importlib.util.spec_from_file_location("ui", str(win)); ui = importlib.util.module_from_spec(spec); spec.loader.exec_module(ui)
import tkinter as tk
MS = ui.MachineState
root = tk.Tk(); app = ui.IOServerTestApp(root)
res = []
def check(n, ok, d=""): res.append((n, bool(ok))); print(("PASS " if ok else "FAIL ") + n + (f"  [{d}]" if d and not ok else ""), flush=True)
def until(f, t=10):
    t0 = time.time()
    while time.time() - t0 < t:
        if f(): return True
        time.sleep(0.1)
    return False
def scenario():
    try:
        time.sleep(1)
        check(f"R1 {VENDOR}: window reads the IOServer state over Redis (WRITE_QR_CODE after start)",
              until(lambda: app._get_ioserver_current_state() == MS.WRITE_QR_CODE), app._get_ioserver_current_state().name)
        root.after(0, app._request_tag_values)
        check("R2 'Get Tag Values': tag dump received and shown",
              until(lambda: app.tag_status_label.cget("text").startswith("Updated")), app.tag_status_label.cget("text"))
        time.sleep(0.5); dump = app.tag_display.get("1.0", "end")
        shown = lambda t: t if len(t) <= 30 else "..." + t[-27:]          # the window shortens names over 30 chars
        missing = [n for n in IOServer.tagNamesUsed().values() if shown(n) + ":" not in dump]
        check("R3 the dump shows every tag the IOServer uses, none '(not in IOServer tag dump)'",
              dump and not missing and "not in IOServer tag dump" not in dump, f"missing {missing[:5]}")
        app.model_var.set("DOSTPLUS")
        root.after(0, lambda: app._send_qr_code("LHS"))
        check("R4 'Send LHS QRCode' -> IOServer past the QR code, side 1 written",
              until(lambda: io.machineState.getCurrentState() not in (MS.READ_QR_CODE, MS.WRITE_QR_CODE))
              and TAGS[IOServer.PC_PLC_LH_RH_Selection] == 1, io.machineState.getCurrentState().name)
        TAGS[IOServer.PLC_PC_CheckKnuckle] = True                       # the PLC asks for the knuckle picture
        check("R5 PLC trigger -> IOServer waits for the knuckle result",
              until(lambda: io.machineState.getCurrentState() == MS.WRITE_RESULT_OF_CHECKING_KNUCKLE),
              io.machineState.getCurrentState().name)
        mark = len(WRITES)
        root.after(0, lambda: app._send_ok(1))
        check("R6 'OK' for the knuckle -> KnuckleCheckOK True then Done True written, state moves on",
              until(lambda: (IOServer.PC_PLC_KnuckleCheckDone, True) in WRITES[mark:])
              and WRITES[mark:].index((IOServer.PC_PLC_KnuckleCheckOK, True)) < WRITES[mark:].index((IOServer.PC_PLC_KnuckleCheckDone, True))
              and until(lambda: io.machineState.getCurrentState() != MS.WRITE_RESULT_OF_CHECKING_KNUCKLE), str(WRITES[mark:][:6]))
        TAGS[IOServer.PLC_PC_CheckKnuckle] = False
        root.after(0, app._abort_and_reset)
        check("R7 'Reset to Step 0' -> abort sent, IOServer back at the QR code, window confirms",
              until(lambda: "back at the QR code" in app.status_text.get("1.0", "end"), 15)
              and io.machineState.getCurrentState() in (MS.READ_QR_CODE, MS.WRITE_QR_CODE), app.status_text.get("1.0", "end")[-160:])
    except Exception as e:
        import traceback; traceback.print_exc(); check("scenario raised", False, str(e))
    finally:
        print(f"RESULT {sum(o for _, o in res)}/{len(res)}", flush=True); os._exit(0 if all(o for _, o in res) else 1)
threading.Thread(target=scenario, daemon=True).start()
root.mainloop()
