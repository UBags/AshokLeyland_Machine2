"""Drives Test_IOServer_With_Mock_PLC_UI.py through its own handlers, with automatic PASS/FAIL checks (exit 1 on failure).

The real window (Tk) with the real IOServer on MockPLC (mockInstance=True): auto cycle DOSTPLUS LHS / RHS and DOST LHS
(states 1 -> 28 -> wrap, side written in state 2), a rejected QR then an accepted one, knuckle NOT OK then a retry,
Reset All mid-cycle then a full auto cycle, the tag monitor. Needs Redis, PostgreSQL and a display (on Linux without one:
xvfb-run). Vendor: COSTHETA_PLC_VENDOR, else plc.vendor.

    python test/<this folder>/DryRun_IOServer_Mock_PLC_Window.py
"""
import sys, os, time, threading, importlib.util, pathlib
HERE = pathlib.Path(__file__).resolve().parent; ROOT = str(HERE.parents[1]); sys.path.insert(0, ROOT)
from persistence import Persistence as P
P.setDatabaseName("Test", createDB=True)
P.insertNewMachineSettings(P.getDatabaseName(), updated_by="dryrun", **P.DEFAULT_MACHINE_SETTINGS)
spec = importlib.util.spec_from_file_location("ui", str(HERE / "Test_IOServer_With_Mock_PLC_UI.py")); ui = importlib.util.module_from_spec(spec); spec.loader.exec_module(ui)
from redis import Redis
from utils.RedisUtils import clearQueues
clearQueues(Redis("localhost", 6379))
import tkinter as tk
MS = ui.MachineState
io = ui.PLCIOServer(mockInstance=True)
root = tk.Tk(); app = ui.TestIOServerGUI(root, io)
res = []
def check(n, ok, d=""): res.append((n, bool(ok))); print(("PASS " if ok else "FAIL ") + n + (f"  [{d}]" if d and not ok else ""), flush=True)

def scenario():
    try:
        time.sleep(2)
        check("U1 window built; IOServer on MockPLCDataIO", type(io.dataIOForReads).__name__ == "MockPLCDataIO")
        for model, side in (("DOSTPLUS", "LHS"), ("DOSTPLUS", "RHS"), ("DOST", "LHS")):
            app.model_var.set(model)
            app._run_auto_cycle(side)
            r = app.last_auto_result
            s = r["settings"]
            check(f"U2 auto cycle {model} {side}: states walked to 28 and wrapped", r["passed"], f"{r['error']} {r['walked']}")
            check(f"U3 {model} {side}: side written in state 2 = {1 if side == 'LHS' else 2}",
                  s.get("PC_PLC_LH_RH_Selection") == (1 if side == "LHS" else 2), str(s))
        # rejected QR, then accepted
        app.model_var.set("DOST")
        check("U4 rejected QR -> IOServer stays in WRITE_QR_CODE", app._do_qr("LHS", accept=False)
              and app._state() == MS.WRITE_QR_CODE, app._state().name)
        check("U5 accepted QR after the rejection -> past the QR code", app._do_qr("LHS", True)
              and app._state() not in (MS.READ_QR_CODE, MS.WRITE_QR_CODE), app._state().name)
        # knuckle NOT OK then retry
        app._wait_for_state([MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE])
        ok1 = app._do_trigger(1) and app._do_vision_result(1, False)
        check("U6 knuckle NOT OK -> back to the knuckle picture state", ok1 and
              app._wait_for_state([MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE]), app._state().name)
        time.sleep(1.0)                                    # rule (a): the PC cleared the trigger with the NOT OK; Done goes False after 0.4 s
        ok2 = app._do_trigger(1) and app._do_vision_result(1, True)
        check("U7 knuckle retry OK -> on to the hub", ok2 and
              app._wait_for_state([MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING]), app._state().name)
        # camera answer timeout (box set to 2 s): the IOServer answers NOT OK itself; the window says "Too late"
        app._camera_timeout = 2.0
        trig = app._do_trigger(2); time.sleep(3.0)
        sent = app._do_vision_result(2, True)
        st = app.step_widgets[2]['status'].cget("text")
        check("U11 camera answer timeout box (2 s): no answer -> IOServer back at the hub picture, window says 'Too late'",
              trig and not sent and st.startswith("Too late"), f"{trig} {sent} {st!r} {app._state().name}")
        app._camera_timeout = float(app.camera_timeout_var.get())
        # Reset All mid-cycle, then a full auto cycle
        app._reset(); time.sleep(1.5)
        check("U8 Reset All -> IOServer back at the QR code", app._state() in (MS.READ_QR_CODE, MS.WRITE_QR_CODE), app._state().name)
        app.model_var.set("DOSTPLUS"); app._run_auto_cycle("LHS")
        check("U9 auto cycle after Reset All", app.last_auto_result["passed"], str(app.last_auto_result["error"]))
        txt = app._format_tags()
        check("U10 tag monitor lists the vendor's tags (CheckQRCode, settings)",
              io.PLC_PC_CheckQRCode in txt and io.PC_PLC_LH_RH_Selection in txt)
    except Exception as e:
        import traceback; traceback.print_exc(); check("scenario raised", False, str(e))
    finally:
        print(f"RESULT {sum(o for _, o in res)}/{len(res)}", flush=True)
        os._exit(0 if all(o for _, o in res) else 1)

threading.Thread(target=scenario, daemon=True).start()
root.mainloop()
