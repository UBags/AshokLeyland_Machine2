"""
Test_IOServer_With_Mock_PLC_UI - tests the REAL costhetaio/commonplc/PLCIOServer.py (Machine 2, 28 states) against the in-memory
MockPLC, with a Tk window to raise PLC signals and send QR-code / camera responses.

Run from the project root (Redis must be running; PostgreSQL is optional):
    python test/machine1_dryrun/Test_IOServer_With_Mock_PLC_UI.py
    (or)  python -m test.machine1_dryrun.Test_IOServer_With_Mock_PLC_UI

What it does:
1. Starts the REAL IOServer with mockInstance=True (MockPLCDataIO on the costhetaio.commonplc.MockPLC singleton instead of a PLC).
2. The GUI plays the PLC (sets trigger / torque / press-done tags in MockPLC), the QR-code server and the camera
   server (sends results on the Redis queues IOServer reads), so IOServer's own code walks the cycle.
3. The "PLC Tag Monitor" tab shows every MockPLC tag, including the cycle settings IOServer writes in state 2
   (WRITE_QR_CODE), before QRCodeCheckOK / Done. Only tags with a configured name are written. Both PLCs
   (cycle.settings.source=database): PC_PLC_LH_RH_Selection (1=LHS, 2=RHS, from the QR code), and
   PC_PLC_TighteningTorque1/2Setpoint, PC_PLC_RotationRPM, PC_PLC_NoOfRotation1CCW / 1CW (all REAL) from
   machine_settings (frontend: Machine Settings).
4. "Auto Cycle (All OK)" drives one complete cycle (15 process steps, states 1 -> 28 -> 1) and reports the state
   trace IOServer actually walked and the state-2 settings it wrote.
5. Camera answer timeout: after "Trigger" the IOServer waits plc.camera.answer.timeout.sec (20 s) for the camera's
   answer, then answers the PLC NOT OK itself (CHANGES 81). Pressing OK / Not OK by hand can take longer: the
   "Camera answer timeout" box (this window's IOServer runs in this process) sets it for this session; the step
   shows when the IOServer has answered NOT OK on its own.
6. No heartbeat server: the IOServer then applies no server-down rules, as when it is commissioned on its own
   (before 2026-11-07 the window sent "combined ALIVE" messages; the IOServer now takes those for a heartbeat server,
   and holds cycles when they stop).
7. "Reset All" resets the MockPLC tags and aborts the cycle in the IOServer with its own abort (abortCycle: delayed
   resets cancelled, camera request dropped, per-cycle tags reset, CheckQRCode armed), as an emergency abort does.

Machine 2 process steps (15) and IOServer states (28):
    Step 0  QR Code                    states 1-2    (QR server)
    Step 1  Knuckle                    states 3-4    (vision)
    Step 2  Hub (with bottom bearing)  states 5-6    (vision)
    Step 3  Top Bearing                states 7-8    (vision)
    Step 4  Nut & Plate Washer         states 9-10   (vision)
    Step 5  Tightening Torque 1        states 11-12  (PLC: Station 2 torque)
    Step 6  Bunk for Component Press   states 13-14  (vision)
    Step 7  Component Press Done       state  15     (PLC signal)
    Step 8  No Bunk                    states 16-17  (vision)
    Step 9  Tightening Torque 2        states 18-19  (PLC: Station 2 torque)
    Step 10 Split Pin & Washer         states 20-21  (vision)
    Step 11 Cap                        states 22-23  (vision)
    Step 12 Bunk for Cap Press         states 24-25  (vision)
    Step 13 Cap Press Done             state  26     (PLC signal)
    Step 14 Free Rotation Torque       states 27-28  (PLC: Station 3 torque; final, wraps to state 1)
"""

import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # <root>/test/<dry-run folder>/this file
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Import the REAL IOServer and the same MockPLC module it uses (costhetaio.commonplc.MockPLC -> same singleton)
from costhetaio.commonplc.PLCIOServer import PLCIOServer
from costhetaio.commonplc.MockPLC import get_mock_plc
from redis import Redis
from utils.RedisUtils import *
from Configuration import CosThetaConfigurator
from statemachine.StateMachine import MachineState

MS = MachineState

QR, VISION, TORQUE, SIGNAL = "qrcode", "vision", "torque", "signal"

# One row per operator-visible process step.
#   (index, label, kind, trigger tag (IOServer attribute) or torque key, first state, last state,
#    IOServer attributes of all PLC tags belonging to the step - for the tag monitor / info tab)
STEPS = [
    (0, "QR Code", QR, "PLC_PC_CheckQRCode", MS.READ_QR_CODE, MS.WRITE_QR_CODE,
     ("PLC_PC_CheckQRCode", "PC_PLC_QRCodeCheckOK", "PC_PLC_QRCodeCheckDone")),
    (1, "Knuckle", VISION, "PLC_PC_CheckKnuckle",
     MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, MS.WRITE_RESULT_OF_CHECKING_KNUCKLE,
     ("PLC_PC_CheckKnuckle", "PC_PLC_KnuckleCheckOK", "PC_PLC_KnuckleCheckDone")),
    (2, "Hub (with bottom bearing)", VISION, "PLC_PC_CheckHub",
     MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING, MS.WRITE_RESULT_OF_CHECKING_HUB_AND_BOTTOM_BEARING,
     ("PLC_PC_CheckHub", "PC_PLC_HubCheckOK", "PC_PLC_HubCheckDone")),
    (3, "Top Bearing", VISION, "PLC_PC_CheckHubAndSecondBearing",
     MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, MS.WRITE_RESULT_OF_CHECKING_TOP_BEARING,
     ("PLC_PC_CheckHubAndSecondBearing", "PC_PLC_HubAndSecondBearingCheckOK", "PC_PLC_HubAndSecondBearingCheckDone")),
    (4, "Nut & Plate Washer", VISION, "PLC_PC_CheckNutAndPlateWasher",
     MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER, MS.WRITE_RESULT_OF_CHECKING_NUT_AND_PLATEWASHER,
     ("PLC_PC_CheckNutAndPlateWasher", "PC_PLC_NutAndPlateWasherCheckOK", "PC_PLC_NutAndPlateWasherCheckDone")),
    (5, "Tightening Torque 1", TORQUE, "torque1", MS.READ_TIGHTENING_TORQUE_1_DONE, MS.READ_TIGHTENING_TORQUE_1,
     ("PLC_PC_Station2TorqueValueSet", "PLC_PC_Station2TorqueValue")),
    (6, "Bunk for Component Press", VISION, "PLC_PC_CheckNoCapBunk",
     MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS, MS.WRITE_RESULT_OF_CHECKING_BUNK_FOR_COMPONENT_PRESS,
     ("PLC_PC_CheckNoCapBunk", "PC_PLC_NoCapBunkCheckOK", "PC_PLC_NoCapBunkCheckDone")),
    (7, "Component Press Done", SIGNAL, "PLC_PC_ComponentPressDone", MS.READ_COMPONENT_PRESS_DONE,
     MS.READ_COMPONENT_PRESS_DONE, ("PLC_PC_ComponentPressDone",)),
    (8, "No Bunk", VISION, "PLC_PC_CheckNoBunk",
     MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK, MS.WRITE_RESULT_OF_CHECKING_NO_BUNK,
     ("PLC_PC_CheckNoBunk", "PC_PLC_NoBunkCheckOK", "PC_PLC_NoBunkCheckDone")),
    (9, "Tightening Torque 2", TORQUE, "torque2", MS.READ_TIGHTENING_TORQUE_2_DONE, MS.READ_TIGHTENING_TORQUE_2,
     ("PLC_PC_Station2TorqueValueSet", "PLC_PC_Station2TorqueValue")),
    (10, "Split Pin & Washer", VISION, "PLC_PC_CheckSplitPinAndWasher",
     MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER, MS.WRITE_RESULT_OF_CHECKING_SPLITPIN_AND_WASHER,
     ("PLC_PC_CheckSplitPinAndWasher", "PC_PLC_SplitPinAndWasherCheckOK", "PC_PLC_SplitPinAndWasherCheckDone")),
    (11, "Cap", VISION, "PLC_PC_CheckCap",
     MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP, MS.WRITE_RESULT_OF_CHECKING_CAP,
     ("PLC_PC_CheckCap", "PC_PLC_CapCheckOK", "PC_PLC_CapCheckDone")),
    (12, "Bunk for Cap Press", VISION, "PLC_PC_CheckBunk",
     MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS, MS.WRITE_RESULT_OF_CHECKING_BUNK_FOR_CAP_PRESS,
     ("PLC_PC_CheckBunk", "PC_PLC_BunkCheckOK", "PC_PLC_BunkCheckDone")),
    (13, "Cap Press Done", SIGNAL, "PLC_PC_CapPressDone", MS.READ_CAP_PRESS_DONE, MS.READ_CAP_PRESS_DONE,
     ("PLC_PC_CapPressDone",)),
    (14, "Free Rotation Torque", TORQUE, "torque_frt", MS.READ_FREE_ROTATION_TORQUE_1_DONE,
     MS.READ_FREE_ROTATION_TORQUE_1, ("PLC_PC_Station3TorqueValueSet", "PLC_PC_Station3TorqueValue")),
]

# state -> step index; every one of the 28 states belongs to exactly one step
STATE_TO_STEP = {}
for _idx, _label, _kind, _trig, _first, _last, _tags in STEPS:
    for _v in range(_first.value, _last.value + 1):
        STATE_TO_STEP[MS(_v)] = _idx
ALL_STATES = [s for s in MS if s != MS.INVALID_STATE]
assert sorted(STATE_TO_STEP) == ALL_STATES, "STEPS table does not cover the 28 Machine 2 states exactly"

# Torque steps: key -> (IOServer attribute of the 'value set' bool, of the value float)
TORQUE_TAGS = {
    "torque1": ("PLC_PC_Station2TorqueValueSet", "PLC_PC_Station2TorqueValue"),
    "torque2": ("PLC_PC_Station2TorqueValueSet", "PLC_PC_Station2TorqueValue"),
    "torque_frt": ("PLC_PC_Station3TorqueValueSet", "PLC_PC_Station3TorqueValue"),
}

# Cycle settings written by IOServer.writeCycleSettingsToPLC() in state 2 (WRITE_QR_CODE)
CYCLE_SETTING_ATTRS = tuple(a for a in ("PC_PLC_LH_RH_Selection", "PC_PLC_ModelSelection",
                                         "PC_PLC_TighteningTorque1Setpoint", "PC_PLC_TighteningTorque2Setpoint",
                                         "PC_PLC_RotationRPM1", "PC_PLC_RotationRPM2",
                                         "PC_PLC_RotationRPM", "PC_PLC_NoOfRotation1CCW", "PC_PLC_NoOfRotation1CW")
                            if getattr(PLCIOServer, a, ""))       # empty tag name = not on this PLC
SYSTEM_ATTRS = ("PLC_PC_EmergencyAbort", "PLC_PC_WatchTag", "PC_PLC_ConnectionStatusTag")

# QR codes per model/side (part numbers from qrcode.partmapping.* in ApplicationConfiguration.properties)
QR_CODES = {
    ("DOST", "LHS"): "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
    ("DOST", "RHS"): "7204838$400102VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNURH$SNPL-MAT$",
    ("DOSTPLUS", "LHS"): "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
    ("DOSTPLUS", "RHS"): "8201206$400102VA1C$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNURH$SNPL-MAT$",
}

WAIT_FOR_STATE_TIMEOUT = 5.0    # s, for the IOServer read loop (plc.read.interval) to react to a tag


def _tag(attr: str) -> str:
    return getattr(PLCIOServer, attr)


def _decode_setting(attr: str, value) -> str:
    if attr == "PC_PLC_LH_RH_Selection":
        return {PLCIOServer.LHS_CODE: "LHS", PLCIOServer.RHS_CODE: "RHS"}.get(value, "not set")
    if attr == "PC_PLC_ModelSelection":
        return {v: k for k, v in PLCIOServer.MODEL_CODES.items()}.get(value, "not set")
    return ""


class TestIOServerGUI:
    """GUI for testing the real IOServer with MockPLC."""

    def __init__(self, root, io_server: PLCIOServer):
        self.root = root
        self.io = io_server
        self.plc = get_mock_plc()  # Same MockPLC singleton instance used by IOServer (mockInstance=True)

        # Redis connection for sending responses
        self.redis = Redis(
            CosThetaConfigurator.getInstance().getRedisHost(),
            CosThetaConfigurator.getInstance().getRedisPort(),
            retry_on_timeout=True
        )

        self.root.title("Test REAL IOServer with MockPLC - Machine 2 (28 states)")
        self.root.geometry("1250x900")

        # Torque values reported by the "PLC"
        self.torque_vars = {
            "torque1": tk.DoubleVar(value=19.0),
            "torque2": tk.DoubleVar(value=19.0),
            "torque_frt": tk.DoubleVar(value=2.4),
        }
        self.model_var = tk.StringVar(value="DOSTPLUS")
        # The IOServer's camera answer timeout for this session (the IOServer runs in this process)
        # (read by the IOServer's write loop: a plain float, kept up to date from the Tk variable on the GUI thread -
        # a Tk variable must not be read from another thread)
        self._camera_timeout = float(CosThetaConfigurator.getPlcCameraAnswerTimeout())
        self.camera_timeout_var = tk.DoubleVar(value=self._camera_timeout)
        def _onTimeoutChanged(*_):
            try:
                self._camera_timeout = max(1.0, float(self.camera_timeout_var.get()))
            except Exception:
                pass                              # half-typed value: keep the last good one
        self.camera_timeout_var.trace_add("write", _onTimeoutChanged)
        _gui = self
        CosThetaConfigurator.getPlcCameraAnswerTimeout = classmethod(lambda cls: _gui._camera_timeout)
        self._triggered_at = {}             # step index -> time.monotonic() when the IOServer reached its WRITE state

        self.step_widgets = {}
        self.step_by_idx = {s[0]: s for s in STEPS}
        self._last_tag_text = ""

        # State trace of the real IOServer (sampled every 10 ms) - used by the auto cycle report
        self.state_trace = []
        self._trace_last = None          # last state the tracer recorded; shared with the reset in _run_auto_cycle
        self._trace_lock = threading.Lock()
        self.auto_running = False
        self.last_auto_result = None   # dict filled in at the end of an auto cycle

        self._build_ui()
        self._start_state_tracer()
        self._schedule_monitor()

    # ------------------------------------------------------------------ helpers

    def _ui(self, fn, *args, **kwargs):
        """Run a widget update on the Tk main loop (safe to call from worker threads)."""
        self.root.after(0, lambda: fn(*args, **kwargs))

    def _set_step_status(self, idx, text, color):
        self._ui(self.step_widgets[idx]['status'].config, text=text, foreground=color)

    def _set_status(self, text):
        self._ui(self.status_label.config, text=text)

    def _bg(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _state(self) -> MachineState:
        return self.io.machineState.getCurrentState()

    def _get_current_step_from_io(self) -> int:
        """Current process step from IOServer's machine state (by enum member, not by str())."""
        return STATE_TO_STEP.get(self._state(), 0)

    def _wait_for_state(self, states, timeout=WAIT_FOR_STATE_TIMEOUT) -> bool:
        states = set(states)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._state() in states:
                return True
            time.sleep(0.02)
        return False

    def _wait_while_state(self, state, timeout=WAIT_FOR_STATE_TIMEOUT) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._state() != state:
                return True
            time.sleep(0.02)
        return False

    def _read_cycle_settings(self) -> dict:
        return {attr: self.plc.get_tag(_tag(attr)) for attr in CYCLE_SETTING_ATTRS}

    # ------------------------------------------------------------------ UI

    def _update_button_states(self):
        """Enable/disable buttons based on current step."""
        current = self._get_current_step_from_io()

        for step_idx, widgets in self.step_widgets.items():
            buttons = [b for b in (widgets['trigger'], widgets['ok'], widgets['notok'],
                                   widgets['lhs_qr'], widgets['rhs_qr'], widgets['reject_qr']) if b]
            if step_idx < current:
                for b in buttons:
                    b.config(state='disabled')
                widgets['status'].config(text="✓ Completed", foreground="green")
            elif step_idx == current:
                for b in buttons:
                    b.config(state='normal')
                if "Completed" in widgets['status'].cget("text") or "Waiting" in widgets['status'].cget("text"):
                    widgets['status'].config(text="◄ Current Step", foreground="blue")
            else:
                for b in buttons:
                    b.config(state='disabled')
                widgets['status'].config(text="Waiting...", foreground="gray")

    def _build_ui(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Tab 1: Controls
        ctrl_tab = ttk.Frame(notebook)
        notebook.add(ctrl_tab, text="Controls")
        self._build_controls(ctrl_tab)

        # Tab 2: Tag Monitor
        monitor_tab = ttk.Frame(notebook)
        notebook.add(monitor_tab, text="PLC Tag Monitor")
        self._build_monitor(monitor_tab)

        # Tab 3: IOServer Info
        info_tab = ttk.Frame(notebook)
        notebook.add(info_tab, text="IOServer Info")
        self._build_info(info_tab)

    def _build_controls(self, parent):
        # Info banner
        info_frame = ttk.Frame(parent)
        info_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(info_frame, text="⚡ Testing REAL PLCIOServer.py with MockPLC - Machine 2 (15 steps, 28 states) ⚡",
                  font=("Arial", 11, "bold"), foreground="darkgreen").pack()

        # Status
        status_frame = ttk.LabelFrame(parent, text="Current State", padding="10")
        status_frame.pack(fill=tk.X, padx=5, pady=5)

        self.state_label = ttk.Label(status_frame, text="Loading...", font=("Arial", 12, "bold"))
        self.state_label.pack()

        self.status_label = ttk.Label(status_frame, text="Ready", font=("Arial", 10), wraplength=1150)
        self.status_label.pack()

        model_frame = ttk.Frame(status_frame)
        model_frame.pack(pady=(6, 0))
        ttk.Label(model_frame, text="Model for QR code:").pack(side=tk.LEFT)
        ttk.Combobox(model_frame, textvariable=self.model_var, values=["DOST", "DOSTPLUS"], width=10,
                     state="readonly").pack(side=tk.LEFT, padx=4)
        ttk.Label(model_frame, text="   Camera answer timeout (s):").pack(side=tk.LEFT)
        ttk.Spinbox(model_frame, from_=1, to=3600, increment=10, textvariable=self.camera_timeout_var,
                    width=6).pack(side=tk.LEFT, padx=4)
        ttk.Label(model_frame, text="(after Trigger, OK / Not OK must come within this, else the IOServer answers "
                                    "NOT OK itself)", foreground="gray").pack(side=tk.LEFT)

        # Steps
        steps_frame = ttk.LabelFrame(parent, text="Process Steps", padding="5")
        steps_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        canvas = tk.Canvas(steps_frame, height=350)
        scrollbar = ttk.Scrollbar(steps_frame, orient="vertical", command=canvas.yview)
        scrollable = ttk.Frame(canvas)

        scrollable.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for idx, label, kind, trig, first, last, _tags in STEPS:
            frame = ttk.Frame(scrollable)
            frame.pack(fill=tk.X, pady=2, padx=5)

            states = f"{first.value}" if first == last else f"{first.value}-{last.value}"
            ttk.Label(frame, text=f"Step {idx}: {label}", width=30, anchor="w").pack(side=tk.LEFT)
            ttk.Label(frame, text=f"[states {states}]", width=13, anchor="w", foreground="gray").pack(side=tk.LEFT)

            trigger_btn = ok_btn = notok_btn = lhs_qr_btn = rhs_qr_btn = reject_qr_btn = None

            if kind == QR:
                lhs_qr_btn = ttk.Button(frame, text="Send LHS QRCode", width=15,
                                        command=lambda: self._send_qr_code("LHS"))
                lhs_qr_btn.pack(side=tk.LEFT, padx=2)

                rhs_qr_btn = ttk.Button(frame, text="Send RHS QRCode", width=15,
                                        command=lambda: self._send_qr_code("RHS"))
                rhs_qr_btn.pack(side=tk.LEFT, padx=2)

                reject_qr_btn = ttk.Button(frame, text="Reject QR", width=10,
                                           command=lambda: self._reject_qr_code())
                reject_qr_btn.pack(side=tk.LEFT, padx=2)
            elif kind == VISION:
                trigger_btn = ttk.Button(frame, text="Trigger", width=8,
                                         command=lambda i=idx: self._trigger(i))
                trigger_btn.pack(side=tk.LEFT, padx=2)

                ok_btn = ttk.Button(frame, text="OK", width=6,
                                    command=lambda i=idx: self._send_ok(i))
                ok_btn.pack(side=tk.LEFT, padx=2)

                notok_btn = ttk.Button(frame, text="Not OK", width=6,
                                       command=lambda i=idx: self._send_notok(i))
                notok_btn.pack(side=tk.LEFT, padx=2)
            elif kind == TORQUE:
                trigger_btn = ttk.Button(frame, text="Trigger", width=8,
                                         command=lambda i=idx: self._trigger(i))
                trigger_btn.pack(side=tk.LEFT, padx=2)
                ttk.Entry(frame, textvariable=self.torque_vars[trig], width=6).pack(side=tk.LEFT, padx=2)
                ttk.Label(frame, text="Nm").pack(side=tk.LEFT)
            else:  # signal
                trigger_btn = ttk.Button(frame, text="Trigger", width=8,
                                         command=lambda i=idx: self._trigger(i))
                trigger_btn.pack(side=tk.LEFT, padx=2)
                ttk.Label(frame, text="", width=14).pack(side=tk.LEFT)

            status_lbl = ttk.Label(frame, text="Waiting...", width=48, anchor="w", foreground="gray")
            status_lbl.pack(side=tk.LEFT, padx=5)

            self.step_widgets[idx] = {
                'trigger': trigger_btn,
                'ok': ok_btn,
                'notok': notok_btn,
                'lhs_qr': lhs_qr_btn,
                'rhs_qr': rhs_qr_btn,
                'reject_qr': reject_qr_btn,
                'status': status_lbl,
                'type': kind
            }

        # Control buttons
        ctrl_frame = ttk.Frame(parent)
        ctrl_frame.pack(fill=tk.X, padx=5, pady=10)

        ttk.Button(ctrl_frame, text="Reset All", command=self._reset).pack(side=tk.LEFT, padx=5)
        ttk.Button(ctrl_frame, text="Print Tags", command=self._print_tags).pack(side=tk.LEFT, padx=5)
        ttk.Button(ctrl_frame, text="Auto Cycle (All OK)", command=self._auto_cycle).pack(side=tk.LEFT, padx=5)

    def _build_monitor(self, parent):
        self.tag_text = scrolledtext.ScrolledText(parent, font=("Courier", 9))
        self.tag_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _build_info(self, parent):
        info_text = scrolledtext.ScrolledText(parent, font=("Courier", 9), wrap=tk.WORD)
        info_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        config = CosThetaConfigurator.getInstance()
        info = "IOServer Configuration (Machine 2)\n"
        info += "=" * 78 + "\n\n"
        info += f"PLC IP: {PLCIOServer.PLC_IP}\n"
        info += f"Using MockPLC: True\n\n"

        info += "Cycle settings written by IOServer in state 2 (WRITE_QR_CODE)\n"
        info += "-" * 78 + "\n"
        source = config.getCycleSettingsSource()
        info += f"  Source of the setpoints: {source}\n"
        if source == "database":
            from persistence.Persistence import getMachineSettings, getDatabaseName
            configured = getMachineSettings(getDatabaseName())
            values = {"PC_PLC_TighteningTorque1Setpoint": configured.get("tightening_torque1_setpoint"),
                      "PC_PLC_TighteningTorque2Setpoint": configured.get("tightening_torque2_setpoint"),
                      "PC_PLC_RotationRPM": configured.get("rotation_rpm"),
                      "PC_PLC_NoOfRotation1CCW": configured.get("no_of_rotation1_ccw"),
                      "PC_PLC_NoOfRotation1CW": configured.get("no_of_rotation1_cw")}
        else:
            values = {"PC_PLC_TighteningTorque1Setpoint": config.getTighteningTorque1Setpoint(),
                      "PC_PLC_TighteningTorque2Setpoint": config.getTighteningTorque2Setpoint(),
                      "PC_PLC_RotationRPM1": config.getRotation1RPM(), "PC_PLC_RotationRPM2": config.getRotation2RPM()}
        values["PC_PLC_LH_RH_Selection"] = "from the QR code (1=LHS, 2=RHS)"
        values["PC_PLC_ModelSelection"] = "from the QR code (1=DOST, 2=DOSTPLUS)"
        for attr in CYCLE_SETTING_ATTRS:
            info += f"  {attr:<34} {_tag(attr):<34} = {values.get(attr)}\n"
        if source != "database":
            info += "  (a configured value of -1 means IOServer does not write that tag)\n"
        info += "\n"

        info += "Tag Names per Process Step\n"
        info += "-" * 78 + "\n"
        for idx, label, kind, trig, first, last, tags in STEPS:
            states = f"{first.value}" if first == last else f"{first.value}-{last.value}"
            info += f"Step {idx}: {label}  [states {states}: {first.name}"
            info += "]\n" if first == last else f" .. {last.name}]\n"
            for attr in tags:
                info += f"    {attr + ':':40s} {_tag(attr)}\n"
        info += "\nSystem Tags\n" + "-" * 78 + "\n"
        for attr in SYSTEM_ATTRS:
            info += f"    {attr + ':':40s} {_tag(attr)}\n"

        info_text.insert(tk.END, info)
        info_text.config(state='disabled')

    # ------------------------------------------------------------------ actions (synchronous cores)

    def _do_trigger(self, step_idx) -> bool:
        """Act as the PLC for a step: raise the trigger tag (vision / signal) or report a torque value."""
        idx, label, kind, trig, first, last, _tags = self.step_by_idx[step_idx]

        if kind == TORQUE:
            setTag, valueTag = (_tag(a) for a in TORQUE_TAGS[trig])
            value = float(self.torque_vars[trig].get())
            if trig == "torque2":
                # Station 2 tags are shared with torque 1 - make sure the PLC raises a fresh 'value set' edge
                self.plc.set_tag(setTag, False)
                time.sleep(0.05)
            self.plc.set_tag(valueTag, value)
            self.plc.set_tag(setTag, True)
            self._set_step_status(step_idx, f"Triggered: torque={value} Nm", "orange")
            self._set_status(f"Step {step_idx}: {setTag}=True, {valueTag}={value}")
            # IOServer resets these tags itself (UpdateTagsToDefaultProcessor) after reading the value
            return True

        tag = _tag(trig)
        self.plc.set_tag(tag, True)

        if kind == SIGNAL:
            # A pulse: drop the signal again once IOServer has left the READ state
            left = self._wait_while_state(first)
            self.plc.set_tag(tag, False)
            if left:
                self._set_step_status(step_idx, f"Pulsed {tag} → IOServer moved on", "green")
                self._set_status(f"Step {step_idx}: {tag} pulsed; IOServer now in {self._state().name}")
            else:
                self._set_step_status(step_idx, f"{tag} pulsed but IOServer did not react", "red")
                self._set_status(f"Step {step_idx}: IOServer stayed in {self._state().name}")
            return left

        # Vision: IOServer sends the take-picture command and moves READ -> WRITE
        reached = self._wait_for_state([last])
        if reached:
            self._triggered_at[step_idx] = time.monotonic()
            self._set_step_status(step_idx, f"Triggered: {tag}=True → now OK / Not OK", "orange")
            self._set_status(f"Step {step_idx}: Triggered - IOServer in {last.name}")
        else:
            self._set_step_status(step_idx, f"Triggered {tag}=True, IOServer not in WRITE state", "red")
            self._set_status(f"Step {step_idx}: IOServer is in {self._state().name}, not {first.name}")
        return reached

    def _do_vision_result(self, step_idx, result_ok: bool) -> bool:
        """Act as the camera server: send OK / NotOK for the step's WRITE state."""
        idx, label, kind, trig, first, last, _tags = self.step_by_idx[step_idx]
        if kind != VISION:
            return False

        if not self._wait_for_state([last]):
            since = self._triggered_at.pop(step_idx, None)
            timeout = CosThetaConfigurator.getPlcCameraAnswerTimeout()
            if since is not None and time.monotonic() - since >= timeout and self._state() == first:
                self._set_step_status(step_idx, f"Too late: the IOServer answered NOT OK itself after {timeout:g} s"
                                                f" - press Trigger again", "red")
                self._set_status(f"Step {step_idx}: no camera answer within {timeout:g} s (camera answer timeout), so "
                                 f"the IOServer reported NOT OK to the PLC and went back to {first.name}")
            else:
                self._set_step_status(step_idx, "IOServer not in WRITE state - press Trigger first", "red")
                self._set_status(f"Step {step_idx}: IOServer is in {self._state().name}, expected {last.name}")
            return False
        self._triggered_at.pop(step_idx, None)

        # Act as the PLC: drop the trigger (IOServer does not read it in the WRITE state), so that on NotOK
        # IOServer's return to the READ state does not immediately re-trigger
        self.plc.set_tag(_tag(trig), False)
        sendDataFromCameraServerToIOServer(self.redis, result=(ok if result_ok else notok), currentMachineState=first)

        moved = self._wait_while_state(last)
        if result_ok:
            self._set_step_status(step_idx, "OK sent → IOServer wrote OK=True, Done=True", "green")
            self._set_status(f"Step {step_idx}: OK sent - IOServer now in {self._state().name}")
        else:
            self._set_step_status(step_idx, "NotOK sent → trigger reset, ready to retry", "red")
            self._set_status(f"Step {step_idx}: NotOK sent - IOServer now in {self._state().name}")
        return moved

    def _do_qr(self, qr_type: str, accept: bool = True) -> bool:
        """Act as PLC + QR-code server for step 0."""
        model = self.model_var.get()
        qr_code = QR_CODES[(model, qr_type)] if accept else ""

        # PLC asks for a QR check (IOServer also arms this tag itself at the end of every cycle)
        self.plc.set_tag(PLCIOServer.PLC_PC_CheckQRCode, True)
        if not self._wait_for_state([MS.WRITE_QR_CODE]):
            self._set_step_status(0, "IOServer did not reach WRITE_QR_CODE", "red")
            self._set_status(f"Step 0: IOServer is in {self._state().name}")
            return False
        self.plc.set_tag(PLCIOServer.PLC_PC_CheckQRCode, False)

        sendDataFromQRCodeServerToIOServer(self.redis, qrCodeStatus=(ok if accept else notok), qrCode=qr_code)

        if not accept:
            # IOServer consumes the rejection and stays in WRITE_QR_CODE waiting for a good QR code. Wait until it has
            # taken the message off qrcode2io.q, as a real second scan would: the IOServer's reader trims the queue
            # after each read, so a QR code sent before then could be dropped with it.
            t0 = time.time()
            while self.redis.xlen(qrcode2ioq) and time.time() - t0 < WAIT_FOR_STATE_TIMEOUT:
                time.sleep(0.02)
            self._set_step_status(0, "QR rejected → IOServer waits for another QR code", "red")
            self._set_status("Step 0: QR code rejected")
            return True

        moved = self._wait_while_state(MS.WRITE_QR_CODE)
        if not moved:
            left = self.redis.xlen(qrcode2ioq)
            why = ("the QR code is still on qrcode2io.q: the IOServer is not reading it - see its console "
                   "(\"held: ...\")" if left else
                   "the QR code was taken off qrcode2io.q but this IOServer did not act on it: another IOServer "
                   "(Run_IOServer_Alone.py, MainProgram.py) on the same Redis may have read it")
            self._set_step_status(0, "QR code not taken by the IOServer - see status", "red")
            self._set_status(f"Step 0: IOServer still in WRITE_QR_CODE after {WAIT_FOR_STATE_TIMEOUT} s; {why}")
            print(f"[Test] QR code not taken: {why}")
            return False
        s = self._read_cycle_settings()
        settings = ", ".join(f"{_tag(a)}={s[a]}" + (f" ({_decode_setting(a, s[a])})" if _decode_setting(a, s[a]) else "")
                             for a in CYCLE_SETTING_ATTRS)
        self._set_step_status(0, f"Sent {model} {qr_type} QR code", "green")
        self._set_status(f"Step 0: sent {model} {qr_type} QR. State 2 wrote: {settings}")
        print(f"[Test] State-2 cycle settings in MockPLC: {settings}")
        return moved

    # ------------------------------------------------------------------ button handlers (non-blocking)

    def _trigger(self, step_idx):
        self._bg(self._do_trigger, step_idx)

    def _send_qr_code(self, qr_type: str):
        self._bg(self._do_qr, qr_type, True)

    def _reject_qr_code(self):
        self._bg(self._do_qr, "LHS", False)

    def _send_ok(self, idx):
        self._bg(self._do_vision_result, idx, True)

    def _send_notok(self, idx):
        self._bg(self._do_vision_result, idx, False)

    def _reset(self):
        self.plc.reset_all_tags()
        self.redis.flushdb()
        self._triggered_at.clear()
        # Bring the real IOServer back to the start of a cycle with its own abort (as on an emergency abort): it
        # cancels the delayed resets, drops the camera request, resets the per-cycle tags and arms CheckQRCode
        self.io.abortCycle("Reset All in the test window", notifyQRCodeServer=False, notifyFrontend=False,
                           clearQueues=True)

        for idx, widgets in self.step_widgets.items():
            widgets['status'].config(text="Waiting...", foreground="gray")

        self._update_button_states()
        self.status_label.config(text="Reset complete - MockPLC tags reset to defaults, IOServer back at READ_QR_CODE "
                                      "(it re-arms the QR check itself and moves to WRITE_QR_CODE)")

    def _print_tags(self):
        self.plc.print_tags()
        self.status_label.config(text="Tag values printed to console")

    def _auto_cycle(self):
        """Run one complete cycle (all OK) through the same actions as the buttons."""
        if self.auto_running:
            self.status_label.config(text="Auto cycle already running")
            return
        if self._state() not in (MS.READ_QR_CODE, MS.WRITE_QR_CODE):
            self.status_label.config(text=f"Auto cycle needs IOServer at state 1/2 (now {self._state().name}); "
                                          f"press Reset All first")
            return
        self._bg(self._run_auto_cycle, "LHS")

    def _run_auto_cycle(self, side: str = "LHS"):
        self.auto_running = True
        with self._trace_lock:                  # restart the trace and the tracer's "last seen" together, so the
            current = self._state().value       # tracer cannot record the starting state a second time
            self.state_trace = [current]
            self._trace_last = current
        settings = {}
        error = None
        try:
            for idx, label, kind, trig, first, last, _tags in STEPS:
                self._set_status(f"Auto: Step {idx}: {label}")
                if kind == QR:
                    good = self._do_qr(side, True)
                    settings = self._read_cycle_settings()
                else:
                    if not self._wait_for_state([first]):
                        raise RuntimeError(f"IOServer did not reach {first.name} (in {self._state().name})")
                    good = self._do_trigger(idx)
                    if good and kind == VISION:
                        time.sleep(0.3)   # "camera" processing time
                        good = self._do_vision_result(idx, True)
                    elif good and kind == TORQUE:
                        good = self._wait_while_state(first) and self._wait_while_state(last)
                        # IOServer clears the torque tags 1.5-2 s after reading them; wait for that, as the PLC
                        # would, so the shared Station 2 'value set' tag cannot fire Tightening Torque 2 early
                        setTag = _tag(TORQUE_TAGS[trig][0])
                        t0 = time.time()
                        while good and self.plc.get_tag(setTag) and time.time() - t0 < WAIT_FOR_STATE_TIMEOUT:
                            time.sleep(0.05)
                if not good:
                    raise RuntimeError(f"Step {idx} ({label}) did not complete; IOServer in {self._state().name}")
                self._ui(self._update_button_states)

            # Free-rotation torque is the last state: IOServer wraps to READ_QR_CODE (and re-arms the QR check)
            if not self._wait_for_state([MS.READ_QR_CODE, MS.WRITE_QR_CODE]):
                raise RuntimeError(f"IOServer did not wrap to READ_QR_CODE (in {self._state().name})")
            time.sleep(0.2)
        except Exception as e:
            error = str(e)
        finally:
            self.auto_running = False

        with self._trace_lock:
            trace = list(self.state_trace)
        # The pass up to the last state plus the wrap. A cycle starts at state 1, or at 2 when IOServer re-armed
        # the QR check itself at the end of the previous cycle; after the wrap it may already be at 2 again.
        last_value = ALL_STATES[-1].value
        walked = trace[:trace.index(last_value) + 2] if last_value in trace else trace
        expected = list(range(trace[0], last_value + 1)) if trace else []
        passed = (error is None and len(walked) == len(expected) + 1 and walked[:-1] == expected
                  and trace[0] in (MS.READ_QR_CODE.value, MS.WRITE_QR_CODE.value)
                  and walked[-1] in (MS.READ_QR_CODE.value, MS.WRITE_QR_CODE.value))
        self.last_auto_result = {"passed": passed, "error": error, "trace": trace, "walked": walked,
                                 "settings": settings}
        settings_txt = ", ".join(f"{_tag(a)}={settings.get(a)}" for a in CYCLE_SETTING_ATTRS)
        if passed:
            msg = (f"Auto cycle complete! 15 steps, IOServer walked states {walked[0]}→...→{walked[-2]}→{walked[-1]}. "
                   f"State 2 wrote: {settings_txt}")
        else:
            msg = f"Auto cycle FAILED: {error or 'unexpected state trace'}; trace={trace}"
        print(f"[Test] {msg}")
        self._set_status(msg)
        self._ui(self._update_button_states)

    # ------------------------------------------------------------------ monitors

    def _start_state_tracer(self):
        def trace():
            while True:
                value = self._state().value
                with self._trace_lock:
                    if value != self._trace_last:
                        self.state_trace.append(value)
                        del self.state_trace[:-500]
                        self._trace_last = value
                time.sleep(0.01)

        threading.Thread(target=trace, daemon=True).start()

    def _format_tags(self) -> str:
        tags = self.plc.get_all_tags()
        listed = set()
        text = "MockPLC Tag Values (used by REAL IOServer):\n"
        text += "=" * 78 + "\n"

        text += "\nCycle settings (written by IOServer in state 2, WRITE_QR_CODE):\n"
        for attr in CYCLE_SETTING_ATTRS:
            name = _tag(attr)
            listed.add(name)
            decoded = _decode_setting(attr, tags.get(name))
            text += f"  {name}: {tags.get(name)}" + (f"  ({decoded})" if decoded else "") + "\n"

        for idx, label, kind, trig, first, last, step_tags in STEPS:
            names = [_tag(a) for a in step_tags]
            if all(n in listed for n in names):
                continue   # Station 2 torque tags are shared by steps 5 and 9
            shared = " (also Step 9)" if idx == 5 else ""
            text += f"\nStep {idx}: {label}{shared}:\n"
            for n in names:
                listed.add(n)
                text += f"  {n}: {tags.get(n)}\n"

        text += "\nSystem Tags:\n"
        for attr in SYSTEM_ATTRS:
            listed.add(_tag(attr))
            text += f"  {_tag(attr)}: {tags.get(_tag(attr))}\n"

        others = {t: v for t, v in sorted(tags.items()) if t not in listed}
        if others:
            text += "\nOther Tags:\n"
            for t, v in others.items():
                text += f"  {t}: {v}\n"
        return text

    def _schedule_monitor(self):
        """Refresh state, buttons and tag monitor every 500 ms on the Tk main loop."""
        try:
            state = self._state()
            self.state_label.config(text=f"State {state.value}: {state.name}   "
                                         f"(Step {self._get_current_step_from_io()})")
            self._update_button_states()

            text = self._format_tags()
            if text != self._last_tag_text:
                pos = self.tag_text.yview()[0]
                self.tag_text.delete(1.0, tk.END)
                self.tag_text.insert(tk.END, text)
                self.tag_text.yview_moveto(pos)
                self._last_tag_text = text
        except Exception as e:
            print(f"Monitor error: {e}")
        self.root.after(500, self._schedule_monitor)

    # No heartbeat sender: an IOServer without a heartbeat server applies no server-down rules (commissioning).
    # The "combined ALIVE" messages this window used to send now make the IOServer take them for a heartbeat server
    # (CHANGES 81) - and hold the cycles when they stop.


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("Test REAL IOServer with MockPLC - Machine 2 (28 states)")
    print("=" * 70)
    print()
    print("This script tests your ACTUAL PLCIOServer.py code using a MockPLC.")
    print("The MockPLC simulates all PLC tag read/write operations in memory.")
    print()
    print("Starting IOServer with mockInstance=True...")
    print()

    try:
        # Start the REAL IOServer with MockPLC
        from redis import Redis as _Redis
        _cfg = CosThetaConfigurator.getInstance()
        # Redis cleared first, as MainProgram does: messages left by an earlier run (a heartbeat-server status, a QR code,
        # "ioserver:started") would otherwise be taken by this IOServer - a stale heartbeat status made it hold every
        # cycle 8 s later ("heartbeat server down").
        clearQueues(_Redis(_cfg.getRedisHost(), _cfg.getRedisPort()))
        io = PLCIOServer(mockInstance=True)

        print()
        print("IOServer started successfully with MockPLC!")
        print("Starting GUI...")
        print()

        # Start GUI
        root = tk.Tk()
        app = TestIOServerGUI(root, io)
        root.mainloop()

    except Exception as e:
        print(f"\nError starting IOServer: {e}")
        print("\nCheck that Redis is running and that you started this from the project root:")
        print("  python test/machine1_dryrun/Test_IOServer_With_Mock_PLC_UI.py")
        sys.exit(1)
