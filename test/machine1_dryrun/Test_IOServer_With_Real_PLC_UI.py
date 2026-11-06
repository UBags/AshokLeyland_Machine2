"""
Test_IOServer_With_Real_PLC_UI - drives a REAL IOServer (costhetaio/commonplc/PLCIOServer.py, Machine 2, 28 states) that is connected
to the REAL PLC: this Tk window plays the QR-code server and the camera server, and shows PLC tag values.

Run from the project root, alongside the IOServer (which must already be running and connected to the PLC):
    Terminal 1: start the IOServer as usual (e.g. via MainProgram.py, or costhetaio.commonplc.PLCIOServer.startIOServer())
    Terminal 2: python test/machine1_dryrun/Test_IOServer_With_Real_PLC_UI.py
                (or)  python -m test.machine1_dryrun.Test_IOServer_With_Real_PLC_UI
NOTE: on start-up this script FLUSHES the Redis database (as before), and "Clear Redis" flushes it again.
"Reset to Step 0" aborts the cycle in the IOServer as well (the abort message the frontend's manual abort sends, on
fe2io.q): the IOServer resets its handshake tags and goes back to the QR code; this window waits until it is there.
The PLC's own sequence is not reset by the PC: if the PLC is in the middle of a cycle, reset it on the machine.

This script tests the IOServer with actual PLC hardware by:
1. Sending QRCode/Camera server responses via Redis (including QR code strings)
2. Providing a GUI to step through the entire process
3. Requesting PLC tag values from IOServer via the Redis lists publishTags / receiveTags
4. Properly waiting for PLC-driven steps (torque, press) to complete, by polling IOServer's state

Machine 2 process steps (15) and IOServer states (28):
- Step 0:  QR Code                   (States 1-2)   - QR server (this GUI)
- Step 1:  Knuckle                   (States 3-4)   - Vision
- Step 2:  Hub (with bottom bearing) (States 5-6)   - Vision
- Step 3:  Top Bearing               (States 7-8)   - Vision
- Step 4:  Nut + Plate Washer        (States 9-10)  - Vision
- Step 5:  Tightening Torque 1       (States 11-12) - Signal (PLC sets Station 2 torque, IOServer reads it)
- Step 6:  Bunk for Component Press  (States 13-14) - Vision
- Step 7:  Component Press Done      (State 15)     - Signal (PLC only)
- Step 8:  No Bunk                   (States 16-17) - Vision
- Step 9:  Tightening Torque 2       (States 18-19) - Signal (PLC sets Station 2 torque, IOServer reads it)
- Step 10: Split Pin + Washer        (States 20-21) - Vision
- Step 11: Cap                       (States 22-23) - Vision
- Step 12: Bunk for Cap Press        (States 24-25) - Vision
- Step 13: Cap Press Done            (State 26)     - Signal (PLC only)
- Step 14: Free Rotation Torque      (States 27-28) - Signal (PLC sets Station 3 torque, IOServer reads it; last state,
                                                      IOServer then wraps to state 1)
There is no separate free-rotation step on Machine 2 (free rotation happens during tightening at station 2).

In state 2 (WRITE_QR_CODE) IOServer writes the cycle settings to the PLC (only tags with a configured name), before
QRCodeCheckOK / Done. Both PLCs: PC_PLC_LH_RH_Selection (1=LHS, 2=RHS, from the QR code), and
PC_PLC_TighteningTorque1/2Setpoint, PC_PLC_RotationRPM, PC_PLC_NoOfRotation1CCW / 1CW (REAL) from machine_settings
in the database (frontend: Machine Settings).
They are shown in the tag panel when IOServer's tag dump includes them.

Torque Step Workflow (Steps 5, 9, 14):
- State X_DONE: IOServer waits for the PLC's "torque value set" signal
- State X:      IOServer reads the torque value from the PLC, sends it to the frontend, then resets the tags
- The PLC must write the torque value before (or together with) the "value set" signal
"""

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # <root>/test/<dry-run folder>/this file
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import time
import json
from redis import Redis
from utils.RedisUtils import *
from Configuration import CosThetaConfigurator
from statemachine.StateMachine import MachineState

# Redis queue names for tag request/response
PUBLISH_TAGS_QUEUE = "publishTags"
RECEIVE_TAGS_QUEUE = "receiveTags"

# Initialize Redis
testRedisConnection = Redis(
    CosThetaConfigurator.getInstance().getRedisHost(),
    CosThetaConfigurator.getInstance().getRedisPort(),
    retry_on_timeout=True
)

MS = MachineState
_cfg = CosThetaConfigurator.getInstance()

# PLC tag names (same configuration source that IOServer uses), grouped per Machine 2 process step
CYCLE_SETTING_TAGS = [
    _cfg.getPcPlcLHRHSelectionTagName(), _cfg.getPcPlcModelSelectionTagName(),
    _cfg.getPcPlcTighteningTorque1SetpointTagName(), _cfg.getPcPlcTighteningTorque2SetpointTagName(),
    _cfg.getPcPlcRotation1RPMTagName(), _cfg.getPcPlcRotation2RPMTagName(),
    _cfg.getPcPlcRotationRPMTagName(), _cfg.getPcPlcNoOfRotation1CCWTagName(), _cfg.getPcPlcNoOfRotation1CWTagName(),
]
CYCLE_SETTING_TAGS = [t for t in CYCLE_SETTING_TAGS if t]     # empty name = not on this PLC
TAG_GROUPS = [
    ("Step 0: QR Code", [_cfg.getPlcPcCheckQRCodeTagName(), _cfg.getPcPlcQRCodeOKTagName(),
                         _cfg.getPcPlcQRCodeDoneTagName()]),
    ("Cycle Settings (written in state 2)", CYCLE_SETTING_TAGS),
    ("Step 1: Knuckle", [_cfg.getPlcPcCheckKnuckleTagName(), _cfg.getPcPlcKnuckleCheckOKTagName(),
                         _cfg.getPcPlcKnuckleCheckDoneTagName()]),
    ("Step 2: Hub", [_cfg.getPlcPcCheckHubTagName(), _cfg.getPcPlcHubCheckOKTagName(),
                     _cfg.getPcPlcHubCheckDoneTagName()]),
    ("Step 3: Top Bearing", [_cfg.getPlcPcCheckHubAndSecondBearingTagName(),
                             _cfg.getPcPlcHubAndSecondBearingCheckOKTagName(),
                             _cfg.getPcPlcHubAndSecondBearingCheckDoneTagName()]),
    ("Step 4: Nut+Plate Washer", [_cfg.getPlcPcCheckNutAndPlateWasherTagName(),
                                  _cfg.getPcPlcNutAndPlateWasherOKTagName(),
                                  _cfg.getPcPlcNutAndPlateWasherDoneTagName()]),
    ("Step 5 & 9: Station2 Torque", [_cfg.getPlcPcStation2TorqueValueSetTagName(),
                                     _cfg.getPlcPcStation2TorqueValueTagName()]
                                    + ([_cfg.getPlcPcStation2RotationDuringTighteningDoneTagName()]   # Siemens only
                                       if _cfg.getPlcPcStation2RotationDuringTighteningDoneTagName() else [])),
    ("Step 6: Bunk for Comp. Press", [_cfg.getPlcPcCheckNoCapBunkTagName(), _cfg.getPcPlcNoCapBunkCheckOKTagName(),
                                      _cfg.getPcPlcNoCapBunkCheckDoneTagName()]),
    ("Step 7: Component Press", [_cfg.getPlcPcComponentPressDoneTagName()]),
    ("Step 8: No Bunk", [_cfg.getPlcPcCheckNoBunkTagName(), _cfg.getPcPlcNoBunkCheckOKTagName(),
                         _cfg.getPcPlcNoBunkCheckDoneTagName()]),
    ("Step 10: Split Pin+Washer", [_cfg.getPlcPcCheckSplitPinAndWasherTagName(),
                                   _cfg.getPcPlcSplitPinAndWasherCheckOKTagName(),
                                   _cfg.getPcPlcSplitPinAndWasherCheckDoneTagName()]),
    ("Step 11: Cap", [_cfg.getPlcPcCheckCapTagName(), _cfg.getPcPlcCapCheckOKTagName(),
                      _cfg.getPcPlcCapCheckDoneTagName()]),
    ("Step 12: Bunk for Cap Press", [_cfg.getPlcPcCheckBunkTagName(), _cfg.getPcPlcBunkCheckOKTagName(),
                                     _cfg.getPcPlcBunkCheckDoneTagName()]),
    ("Step 13: Cap Press", [_cfg.getPlcPcCapPressDoneTagName()]),
    ("Step 14: Station3 Torque", [_cfg.getPlcPcStation3TorqueValueSetTagName(),
                                  _cfg.getPlcPcStation3TorqueValueTagName()]),
    ("System", [_cfg.getPlcPcEmergencyAbortTagName(), _cfg.getPlcPcWatchTagName(),
                _cfg.getPcPlcConnectionStatusTagName()]),
]

# QR codes per model/side (part numbers from qrcode.partmapping.* in ApplicationConfiguration.properties)
QR_CODES = {
    ("DOST", "LHS"): "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
    ("DOST", "RHS"): "7204838$400102VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNURH$SNPL-MAT$",
    ("DOSTPLUS", "LHS"): "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4-B$1$SPDL ASSY-KNULH$SNPL-MAT$",
    ("DOSTPLUS", "RHS"): "8201206$400102VA1C$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNURH$SNPL-MAT$",
}


def _format_value(value) -> str:
    if isinstance(value, bool):
        return "TRUE ●" if value else "False ○"
    if isinstance(value, float):
        return f"{value:.2f}"
    if value is None:
        return "None"
    return str(value)


def _parse_machine_state(state_str) -> MachineState:
    """IOServer reports str(state): 'MachineState.X' on Python <= 3.10, the plain number on 3.11+."""
    if state_str is None:
        return MachineState.INVALID_STATE
    return MachineState.getMachineStateFromString(str(state_str))


class IOServerTestApp:
    def __init__(self, root):
        self.root = root
        self.root.title("IOServer Test - Real PLC (Machine 2, 28 States)")
        self.root.geometry("1400x850")

        self.current_step = 0
        self.step_widgets = {}  # {step_idx: {'ok': btn, 'notok': btn, 'status': label}}
        self.tag_request_pending = False
        self.signal_monitor_active = False  # Flag to track if signal monitoring is active
        self.stop_signal_monitor = False  # Flag to stop signal monitoring thread
        self.stop_full_cycle = False  # Flag to stop a running "Run Full Cycle"
        self.full_cycle_running = False

        # Model used for the QR code buttons (side is chosen by the button)
        self.model_var = tk.StringVar(value="DOSTPLUS")
        self.cycle_complete = False   # set when a "Run Full Cycle" has seen IOServer wrap back to state 1

        self._build_ui()
        self._start_heartbeat()
        self._update_button_states()

    def _build_ui(self):
        # Main horizontal paned window (left 2/3, right 1/3)
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Left frame (2/3) - Controls
        left_frame = ttk.Frame(main_paned)
        main_paned.add(left_frame, weight=2)

        # Right frame (1/3) - Tag display
        right_frame = ttk.LabelFrame(main_paned, text="PLC Tag Values", padding="5")
        main_paned.add(right_frame, weight=1)

        # Build left side
        self._build_controls(left_frame)

        # Build right side - Tag display
        self._build_tag_display(right_frame)

    def _build_controls(self, parent):
        # Title
        title = ttk.Label(parent, text="IOServer Test - Real PLC Mode",
                          font=("Arial", 14, "bold"))
        title.pack(pady=5)

        # Status frame
        status_frame = ttk.LabelFrame(parent, text="Status", padding="10")
        status_frame.pack(fill=tk.X, pady=5, padx=5)

        self.status_text = scrolledtext.ScrolledText(status_frame, height=4, wrap=tk.WORD)
        self.status_text.pack(fill=tk.X)
        self._update_status("Ready. Click buttons to send responses to IOServer.")

        model_frame = ttk.Frame(status_frame)
        model_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(model_frame, text="Model for QR code:").pack(side=tk.LEFT)
        ttk.Combobox(model_frame, textvariable=self.model_var, values=["DOST", "DOSTPLUS"], width=10,
                     state="readonly").pack(side=tk.LEFT, padx=4)

        # Process steps frame with scrollbar
        steps_frame = ttk.LabelFrame(parent, text="Process Steps (15 Steps, 28 States)", padding="10")
        steps_frame.pack(fill=tk.BOTH, expand=True, pady=5, padx=5)

        # Add canvas with scrollbar for steps
        canvas = tk.Canvas(steps_frame)
        scrollbar = ttk.Scrollbar(steps_frame, orient="vertical", command=canvas.yview)
        scrollable = ttk.Frame(canvas)

        scrollable.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Define all steps (index in this list == step index)
        self.steps = [
            ("Step 0", "QR Code", "States 1-2", "vision", 0),
            ("Step 1", "Knuckle", "States 3-4", "vision", 1),
            ("Step 2", "Hub", "States 5-6", "vision", 2),
            ("Step 3", "Top Bearing", "States 7-8", "vision", 3),
            ("Step 4", "Nut + Plate Washer", "States 9-10", "vision", 4),
            ("Step 5", "Tightening Torque 1", "States 11-12", "signal", 5),
            ("Step 6", "Bunk for Comp. Press", "States 13-14", "vision", 6),
            ("Step 7", "Component Press Done", "State 15", "signal", 7),
            ("Step 8", "No Bunk", "States 16-17", "vision", 8),
            ("Step 9", "Tightening Torque 2", "States 18-19", "signal", 9),
            ("Step 10", "Split Pin + Washer", "States 20-21", "vision", 10),
            ("Step 11", "Cap", "States 22-23", "vision", 11),
            ("Step 12", "Bunk for Cap Press", "States 24-25", "vision", 12),
            ("Step 13", "Cap Press Done", "State 26", "signal", 13),
            ("Step 14", "Free Rotation Torque", "States 27-28", "signal", 14),
        ]
        self.final_step = len(self.steps) - 1

        # Map step index to MachineState for camera responses
        self.vision_state_map = {
            1: MS.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE,
            2: MS.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING,
            3: MS.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING,
            4: MS.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER,
            6: MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS,
            8: MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK,
            10: MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER,
            11: MS.READ_TAKE_PICTURE_FOR_CHECKING_CAP,
            12: MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS,
        }
        # WRITE state in which IOServer consumes the QR / camera response of each step
        self.write_state_map = {
            0: MS.WRITE_QR_CODE,
            1: MS.WRITE_RESULT_OF_CHECKING_KNUCKLE,
            2: MS.WRITE_RESULT_OF_CHECKING_HUB_AND_BOTTOM_BEARING,
            3: MS.WRITE_RESULT_OF_CHECKING_TOP_BEARING,
            4: MS.WRITE_RESULT_OF_CHECKING_NUT_AND_PLATEWASHER,
            6: MS.WRITE_RESULT_OF_CHECKING_BUNK_FOR_COMPONENT_PRESS,
            8: MS.WRITE_RESULT_OF_CHECKING_NO_BUNK,
            10: MS.WRITE_RESULT_OF_CHECKING_SPLITPIN_AND_WASHER,
            11: MS.WRITE_RESULT_OF_CHECKING_CAP,
            12: MS.WRITE_RESULT_OF_CHECKING_BUNK_FOR_CAP_PRESS,
        }

        # Map signal step index to the MachineState that indicates completion
        # For torque steps (5, 9, 14): IOServer goes through TWO states:
        #   1. DONE state: Waits for PLC signal that the torque value is set
        #   2. VALUE state: Reads torque value from PLC, resets tags, transitions
        # Signal step is marked complete when IOServer has passed the VALUE state
        self.signal_completion_state_map = {
            5: MS.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS,  # Torque 1: states 11 -> 12 -> 13
            7: MS.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK,                   # Component Press: state 15 -> 16
            9: MS.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER,       # Torque 2: states 18 -> 19 -> 20
            13: MS.READ_FREE_ROTATION_TORQUE_1_DONE,                        # Cap Press: state 26 -> 27
            14: MS.READ_QR_CODE,                                            # Free rotation torque: 27 -> 28 -> 1
        }

        # Build step rows
        for step_name, description, states, step_type, idx in self.steps:
            row = ttk.Frame(scrollable)
            row.pack(fill=tk.X, pady=3, padx=5)

            # Step label
            label_text = f"{step_name}: {description}"
            label = ttk.Label(row, text=label_text, width=30, anchor="w")
            label.pack(side=tk.LEFT)
            label = ttk.Label(row, text=f"[{states}]", width=13, anchor="w", foreground="gray")
            label.pack(side=tk.LEFT)

            # Type indicator
            type_label = ttk.Label(row, text=f"[{step_type.upper()}]", width=8)
            type_label.pack(side=tk.LEFT, padx=2)

            ok_btn = None
            notok_btn = None
            lhs_qr_btn = None
            rhs_qr_btn = None
            reject_qr_btn = None

            if step_type == "vision":
                if idx == 0:
                    # Special buttons for QR Code step
                    lhs_qr_btn = ttk.Button(row, text="Send LHS QRCode", width=16,
                                           command=lambda: self._send_qr_code("LHS"))
                    lhs_qr_btn.pack(side=tk.LEFT, padx=2)

                    rhs_qr_btn = ttk.Button(row, text="Send RHS QRCode", width=16,
                                           command=lambda: self._send_qr_code("RHS"))
                    rhs_qr_btn.pack(side=tk.LEFT, padx=2)

                    # Add a reject button for testing failures
                    reject_qr_btn = ttk.Button(row, text="Reject QR", width=10,
                                              command=lambda: self._reject_qr_code())
                    reject_qr_btn.pack(side=tk.LEFT, padx=2)
                else:
                    # OK button for other vision steps
                    ok_btn = ttk.Button(row, text="OK", width=6,
                                        command=lambda i=idx: self._send_ok(i))
                    ok_btn.pack(side=tk.LEFT, padx=2)

                    # NotOK button
                    notok_btn = ttk.Button(row, text="Not OK", width=8,
                                           command=lambda i=idx: self._send_notok(i))
                    notok_btn.pack(side=tk.LEFT, padx=2)
            else:
                # Signal steps - no response needed, add spacer
                ttk.Label(row, text="", width=44).pack(side=tk.LEFT)

            # Status label (width=50)
            status_lbl = ttk.Label(row, text="Waiting...", width=50, anchor="w", foreground="gray")
            status_lbl.pack(side=tk.LEFT, padx=5)

            # Store widget references
            self.step_widgets[idx] = {
                'ok': ok_btn,
                'notok': notok_btn,
                'lhs_qr': lhs_qr_btn,
                'rhs_qr': rhs_qr_btn,
                'reject_qr': reject_qr_btn,
                'status': status_lbl,
                'type': step_type
            }

        # Quick actions frame
        actions_frame = ttk.LabelFrame(parent, text="Quick Actions", padding="10")
        actions_frame.pack(fill=tk.X, pady=5, padx=5)

        ttk.Button(actions_frame, text="Clear Redis",
                   command=self._clear_redis).pack(side=tk.LEFT, padx=5)
        ttk.Button(actions_frame, text="Run Full Cycle (All OK)",
                   command=self._run_full_cycle).pack(side=tk.LEFT, padx=5)
        ttk.Button(actions_frame, text="Send Next OK",
                   command=self._send_next_ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(actions_frame, text="Reset to Step 0",
                   command=self._abort_and_reset).pack(side=tk.LEFT, padx=5)

    def _build_tag_display(self, parent):
        """Build the right panel for tag display."""
        # Button frame at top
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=5)

        self.get_tags_btn = ttk.Button(btn_frame, text="Get Tag Values",
                                        command=self._request_tag_values)
        self.get_tags_btn.pack(side=tk.LEFT, padx=5)

        self.tag_status_label = ttk.Label(btn_frame, text="", font=("Arial", 9))
        self.tag_status_label.pack(side=tk.LEFT, padx=10)

        # Scrolled text for tag display
        self.tag_display = scrolledtext.ScrolledText(parent, font=("Courier", 9), wrap=tk.WORD)
        self.tag_display.pack(fill=tk.BOTH, expand=True)

        # Initial message
        self._show_initial_tag_message()

    def _show_initial_tag_message(self):
        """Show initial message in tag display."""
        self.tag_display.delete(1.0, tk.END)
        msg = """Click 'Get Tag Values' to fetch current
PLC tag values from IOServer.

Requirements:
• IOServer must be running
• IOServer must be connected to PLC
• IOServer must have the tag request
  listener thread running

The tags will be grouped by process
step for easy reading.
"""
        self.tag_display.insert(tk.END, msg)

    def _request_tag_values(self):
        """Request tag values from IOServer via Redis queue."""
        if self.tag_request_pending:
            return

        self.tag_request_pending = True
        self.get_tags_btn.config(state='disabled')
        self.tag_status_label.config(text="Requesting...", foreground="orange")

        def fetch_tags():
            try:
                # Send request (drop any late reply to an earlier request first)
                testRedisConnection.delete(RECEIVE_TAGS_QUEUE)
                testRedisConnection.lpush(PUBLISH_TAGS_QUEUE, "getTagValues")

                # Wait for response with timeout
                response = testRedisConnection.brpop(RECEIVE_TAGS_QUEUE, timeout=5)

                if response:
                    _, data = response
                    tags = json.loads(data)

                    # Update display on main thread
                    self.root.after(0, lambda: self._display_tags(tags))
                    self.root.after(0, lambda: self.tag_status_label.config(
                        text=f"Updated: {time.strftime('%H:%M:%S')}", foreground="green"))

                    # Also print to terminal
                    self._print_tags_to_terminal(tags)
                else:
                    self.root.after(0, lambda: self.tag_status_label.config(
                        text="Timeout - no response", foreground="red"))

            except Exception as e:
                self.root.after(0, lambda err=str(e): self.tag_status_label.config(
                    text=f"Error: {err[:30]}", foreground="red"))
            finally:
                self.tag_request_pending = False
                self.root.after(0, lambda: self.get_tags_btn.config(state='normal'))

        thread = threading.Thread(target=fetch_tags, daemon=True)
        thread.start()

    def _display_tags(self, tags: dict):
        """Display tag values in the right panel, grouped by state."""
        text = self._format_tags_grouped(tags)
        self.tag_display.delete(1.0, tk.END)
        self.tag_display.insert(tk.END, text)

    def _format_tags_grouped(self, tags: dict) -> str:
        """Format tags grouped by process step for display (exact tag names from the configuration)."""
        lines = []
        lines.append("PLC TAG VALUES")
        lines.append("═" * 40)
        lines.append(f"Fetched: {time.strftime('%H:%M:%S')}")

        # Show current machine state if available
        if "_currentMachineState" in tags:
            state = _parse_machine_state(tags["_currentMachineState"])
            lines.append(f"State: {state.value} {state.name}")

        lines.append("═" * 40)
        lines.append("")

        listed = set()
        for group_name, group_tags in TAG_GROUPS:
            lines.append(f"▶ {group_name}")
            lines.append("─" * 38)
            for full_tag in group_tags:
                listed.add(full_tag)
                short_tag = full_tag.split(".")[-1] if "." in full_tag else full_tag
                if len(short_tag) > 30:
                    short_tag = "..." + short_tag[-27:]
                lines.append(f"  {short_tag}:")
                if full_tag in tags:
                    lines.append(f"    {_format_value(tags[full_tag])}")
                else:
                    lines.append("    (not in IOServer tag dump)")
            lines.append("")

        others = [t for t in sorted(tags) if not t.startswith("_") and t not in listed]
        if others:
            lines.append("▶ Other")
            lines.append("─" * 38)
            for t in others:
                lines.append(f"  {t}:")
                lines.append(f"    {_format_value(tags[t])}")
            lines.append("")

        return "\n".join(lines)

    def _print_tags_to_terminal(self, tags: dict):
        """Print all PLC tag values to terminal."""
        print("\n" + "=" * 70)
        print("PLC TAG VALUES (from IOServer)")
        print("=" * 70)

        if "_currentMachineState" in tags:
            state = _parse_machine_state(tags["_currentMachineState"])
            print(f"Current State: {state.value} {state.name}")
            print("-" * 70)

        for group_name, group_tags in TAG_GROUPS:
            print(f"\n▶ {group_name}")
            print("-" * 50)
            for tag in group_tags:
                print(f"  {tag}: {_format_value(tags[tag]) if tag in tags else '(not in IOServer tag dump)'}")

        print("\n" + "=" * 70)

    def _update_status(self, message):
        """Update the status text area (safe to call from worker threads)."""
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda m=message: self._update_status(m))
            return
        timestamp = time.strftime("%H:%M:%S")
        self.status_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.status_text.see(tk.END)

    def _update_button_states(self):
        """Enable/disable buttons based on current step."""
        for idx, widgets in self.step_widgets.items():
            if widgets['type'] == "vision":
                if idx == self.current_step and not self.signal_monitor_active:
                    # Enable buttons for current vision step (only if not waiting for signals)
                    if idx == 0:
                        # Enable QR code buttons for step 0
                        if widgets['lhs_qr']:
                            widgets['lhs_qr'].config(state='normal')
                        if widgets['rhs_qr']:
                            widgets['rhs_qr'].config(state='normal')
                        if widgets['reject_qr']:
                            widgets['reject_qr'].config(state='normal')
                    else:
                        # Enable OK/NotOK buttons for other vision steps
                        if widgets['ok']:
                            widgets['ok'].config(state='normal')
                        if widgets['notok']:
                            widgets['notok'].config(state='normal')
                else:
                    # Disable buttons for other steps
                    if idx == 0:
                        if widgets['lhs_qr']:
                            widgets['lhs_qr'].config(state='disabled')
                        if widgets['rhs_qr']:
                            widgets['rhs_qr'].config(state='disabled')
                        if widgets['reject_qr']:
                            widgets['reject_qr'].config(state='disabled')
                    else:
                        if widgets['ok']:
                            widgets['ok'].config(state='disabled')
                        if widgets['notok']:
                            widgets['notok'].config(state='disabled')

    def _start_heartbeat(self):
        """Send heartbeat to keep IOServer happy."""

        def heartbeat():
            while True:
                try:
                    sendCombinedHeartbeatFromHeartbeatServerToIOServer(testRedisConnection, ALIVE)
                except:
                    pass
                time.sleep(1)

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()

    def _get_ioserver_current_state(self) -> MachineState:
        """Request current machine state from IOServer via Redis."""
        try:
            # Send request (drop any late reply to an earlier request first)
            testRedisConnection.delete(RECEIVE_TAGS_QUEUE)
            testRedisConnection.lpush(PUBLISH_TAGS_QUEUE, "getTagValues")

            # Wait for response with timeout
            response = testRedisConnection.brpop(RECEIVE_TAGS_QUEUE, timeout=3)

            if response:
                _, data = response
                tags = json.loads(data)

                if "_currentMachineState" in tags:
                    return _parse_machine_state(tags["_currentMachineState"])
        except Exception as e:
            self._update_status(f"Error getting IOServer state: {e}")

        return MachineState.INVALID_STATE

    def _monitor_signal_steps(self, start_step_idx: int):
        """
        Monitor IOServer state and update GUI when signal steps complete.
        This runs in a background thread.
        """
        self.signal_monitor_active = True
        self.stop_signal_monitor = False

        current_signal_step = start_step_idx

        while current_signal_step < len(self.steps) and not self.stop_signal_monitor:
            step_name, description, states, step_type, idx = self.steps[current_signal_step]

            if step_type != "signal":
                # Reached a vision step, stop monitoring
                break

            # Update GUI to show waiting
            self.root.after(0, lambda i=current_signal_step: self.step_widgets[i]['status'].config(
                text="⏳ Waiting for PLC...", foreground="orange"))

            # Get the completion state for this signal step
            completion_state = self.signal_completion_state_map.get(current_signal_step)

            if completion_state is None:
                # Unknown signal step, just mark as done and continue
                self.root.after(0, lambda i=current_signal_step: self.step_widgets[i]['status'].config(
                    text="✓ Signal step (PLC handled)", foreground="green"))
                current_signal_step += 1
                continue

            # Poll IOServer state until we reach or pass the completion state
            self.root.after(0, lambda sn=step_name, desc=description: self._update_status(
                f"Waiting for {sn}: {desc}..."))

            while not self.stop_signal_monitor:
                current_io_state = self._get_ioserver_current_state()

                if current_io_state == MachineState.INVALID_STATE:
                    # Couldn't get state, wait and retry
                    time.sleep(0.5)
                    continue

                current_io_state_int = MachineState.getMachineStateAsInt(current_io_state)
                completion_state_int = MachineState.getMachineStateAsInt(completion_state)

                # Special handling for cycle wrap-around (last step -> Step 0)
                if current_signal_step == self.final_step:
                    # Cycle complete when IOServer is back at READ_QR_CODE (state 1). IOServer re-arms the QR check
                    # itself at the end of a cycle, so it may already have moved on to WRITE_QR_CODE (state 2).
                    if current_io_state in (MS.READ_QR_CODE, MS.WRITE_QR_CODE):
                        self.cycle_complete = True
                        break
                else:
                    # Normal case: signal step complete when IOServer state >= completion state.
                    # Signal steps are only monitored after a vision step (state >= 3), so state 1/2 here means
                    # IOServer already finished the cycle between two polls (e.g. Cap Press -> final torque -> wrap).
                    if current_io_state_int >= completion_state_int or \
                            current_io_state in (MS.READ_QR_CODE, MS.WRITE_QR_CODE):
                        break

                # Update status with current IOServer state (less frequently to reduce spam)
                # self.root.after(0, lambda s=current_io_state: self._update_status(
                #     f"IOServer at state: {s.name}"))

                time.sleep(0.5)  # Poll every 500ms

            if self.stop_signal_monitor:
                break

            # Signal step complete - capture values for lambda
            completed_step = current_signal_step
            completed_name = step_name
            self.root.after(0, lambda i=completed_step: self.step_widgets[i]['status'].config(
                text="✓ Complete (PLC done)", foreground="green"))
            self.root.after(0, lambda sn=completed_name: self._update_status(f"{sn} completed!"))

            current_signal_step += 1

        # Update current_step and enable next vision step
        self.current_step = current_signal_step
        self.signal_monitor_active = False

        # Update button states on main thread
        self.root.after(0, self._update_button_states)

        if current_signal_step < len(self.steps):
            next_step = self.steps[current_signal_step]
            self.root.after(0, lambda ns=next_step: self._update_status(
                f"Ready for {ns[0]}: {ns[1]}"))

    def _send_qr_code(self, qr_type: str):
        """Send QR code to IOServer mimicking QRCodeServer."""
        widgets = self.step_widgets[0]

        # Select the QR code for the chosen model and side
        model = self.model_var.get()
        qr_code = QR_CODES[(model, qr_type)]
        self.cycle_complete = False

        # Send to IOServer
        sendDataFromQRCodeServerToIOServer(testRedisConnection, qrCodeStatus=ok, qrCode=qr_code)

        # Update status
        widgets['status'].config(text=f"Sent {model} {qr_type} QR code → IOServer processing...", foreground="green")
        self._update_status(f"Step 0: QR Code - Sent {model} {qr_type} QR code: {qr_code[:50]}...")

        # Move to next step
        next_step_idx = 1

        # Check if next step(s) are signal steps
        if next_step_idx < len(self.steps) and self.steps[next_step_idx][3] == "signal":
            # Start background thread to monitor signal steps
            self.current_step = next_step_idx
            self._update_button_states()  # Disable all buttons while waiting

            monitor_thread = threading.Thread(
                target=self._monitor_signal_steps,
                args=(next_step_idx,),
                daemon=True
            )
            monitor_thread.start()
        else:
            # Next step is a vision step, just advance
            self.current_step = next_step_idx
            self._update_button_states()

    def _reject_qr_code(self):
        """Send QR code rejection to IOServer mimicking QRCodeServer failure."""
        widgets = self.step_widgets[0]

        # Send rejection to IOServer
        sendDataFromQRCodeServerToIOServer(testRedisConnection, qrCodeStatus=notok, qrCode="")

        # Update status
        widgets['status'].config(text="QR code rejected → IOServer will retry", foreground="red")
        self._update_status("Step 0: QR Code - Rejection sent, IOServer will stay at this step")

        # Don't advance step - stay at step 0

    def _send_ok(self, step_idx):
        """Send OK response for a vision step."""
        step_name, description, _, step_type, _ = self.steps[step_idx]
        widgets = self.step_widgets[step_idx]

        if step_type != "vision":
            self._update_status(f"{step_name} is a signal step - no response needed")
            return

        if step_idx == 0:
            # QR Code step has dedicated buttons now, this shouldn't be called
            self._update_status("Use 'Send LHS QRCode' or 'Send RHS QRCode' buttons for QR code")
            return

        # Camera vision check
        state = self.vision_state_map.get(step_idx)
        if state:
            sendDataFromCameraServerToIOServer(testRedisConnection, result=ok,
                                               currentMachineState=state)
        widgets['status'].config(text="OK sent → Moving to next step", foreground="green")
        self._update_status(f"{step_name}: {description} - OK sent")

        # Move to next step
        next_step_idx = step_idx + 1

        # Check if next step(s) are signal steps
        if next_step_idx < len(self.steps) and self.steps[next_step_idx][3] == "signal":
            # Start background thread to monitor signal steps
            self.current_step = next_step_idx
            self._update_button_states()  # Disable all buttons while waiting

            monitor_thread = threading.Thread(
                target=self._monitor_signal_steps,
                args=(next_step_idx,),
                daemon=True
            )
            monitor_thread.start()
        else:
            # Next step is a vision step, just advance
            self.current_step = next_step_idx
            self._update_button_states()

    def _send_notok(self, step_idx):
        """Send NotOK response for a vision step."""
        step_name, description, _, step_type, _ = self.steps[step_idx]
        widgets = self.step_widgets[step_idx]

        if step_type != "vision":
            self._update_status(f"{step_name} is a signal step - no response needed")
            return

        if step_idx == 0:
            # QR Code step has dedicated buttons now, this shouldn't be called
            self._update_status("Use 'Send LHS QRCode' or 'Send RHS QRCode' buttons for QR code")
            return

        # Camera vision check
        state = self.vision_state_map.get(step_idx)
        if state:
            sendDataFromCameraServerToIOServer(testRedisConnection, result=notok,
                                               currentMachineState=state)
        widgets['status'].config(text="NotOK sent → Staying at this step", foreground="red")
        self._update_status(f"{step_name}: {description} - NotOK sent (will retry)")

    def _send_next_ok(self):
        """Send OK for the next vision step in sequence."""
        if self.signal_monitor_active:
            self._update_status("Waiting for signal steps to complete...")
            return

        # Find next vision step
        while self.current_step < len(self.steps):
            _, _, _, step_type, _ = self.steps[self.current_step]
            if step_type == "vision":
                self._send_ok(self.current_step)
                return
            self.current_step += 1
            self._update_button_states()

        # Cycle complete, reset
        self._reset_to_start()
        self._update_status("Cycle complete! Reset to Step 0")

    def _clear_redis(self):
        self.stop_signal_monitor = True  # Stop any active monitoring
        time.sleep(0.2)  # Give monitor thread time to stop
        testRedisConnection.flushdb()
        self._reset_to_start()
        self._show_initial_tag_message()
        self.tag_status_label.config(text="")
        self._update_status("Redis cleared - ready for new cycle")

    def _abort_and_reset(self):
        """'Reset to Step 0': abort the cycle in the IOServer too (as the frontend's manual abort does), not only in
        this window. Sends the abort on the frontend -> IOServer queue, then waits for the IOServer to be back at the
        QR code (READ_QR_CODE, or WRITE_QR_CODE once it has re-armed the QR check)."""
        self._reset_to_start()
        sendAbortFromFEServerToIOServer(testRedisConnection, reason="Reset to Step 0 in the test window")
        self._update_status("Abort sent to the IOServer; waiting for it to go back to the QR code...")

        def confirm():
            deadline = time.time() + 10
            while time.time() < deadline:
                state = self._get_ioserver_current_state()
                if state in (MS.READ_QR_CODE, MS.WRITE_QR_CODE):
                    self.root.after(0, lambda s=state: self._update_status(
                        f"IOServer aborted the cycle and is back at the QR code ({s.name}). If the PLC is still in the "
                        f"middle of its sequence, reset it on the machine (Emergency) before the next QR code."))
                    return
                time.sleep(0.5)
            self.root.after(0, lambda: self._update_status(
                "IOServer did NOT go back to the QR code within 10 s: is it running, and is it a version with the "
                "frontend abort listener (git pull)?"))

        threading.Thread(target=confirm, daemon=True).start()

    def _reset_to_start(self):
        """Reset tracking to Step 0 (this window only; see _abort_and_reset for the IOServer)."""
        self.stop_signal_monitor = True  # Stop any active monitoring
        self.stop_full_cycle = True  # Stop a running full cycle
        self.signal_monitor_active = False
        self.current_step = 0
        for idx, widgets in self.step_widgets.items():
            widgets['status'].config(text="Waiting...", foreground="gray")
        self._update_button_states()
        self._update_status("Reset to Step 0 - Ready")

    def _wait_for_ioserver_state(self, wanted, leave: bool = False) -> bool:
        """Poll IOServer (via the tag dump) until it is in / has left state `wanted`; False if the cycle is stopped."""
        while not self.stop_full_cycle:
            state = self._get_ioserver_current_state()
            if state != MS.INVALID_STATE and ((state != wanted) if leave else (state == wanted)):
                return True
            time.sleep(0.3)
        return False

    def _run_full_cycle(self):
        """Send OK responses for every QR / vision step of one cycle, each once IOServer is waiting for it.
        The PLC (real machine) drives the triggers, torques and presses; this waits for IOServer to get there."""
        if self.signal_monitor_active or self.full_cycle_running:
            self._update_status("Cannot run full cycle while a cycle or signal monitoring is in progress")
            return

        def cycle():
            vision_steps = [
                (0, "QR Code", "qr"),
                (1, "Knuckle", "vision"),
                (2, "Hub", "vision"),
                (3, "Top Bearing", "vision"),
                (4, "Nut + Plate Washer", "vision"),
                (6, "Bunk for Comp. Press", "vision"),
                (8, "No Bunk", "vision"),
                (10, "Split Pin + Washer", "vision"),
                (11, "Cap", "vision"),
                (12, "Bunk for Cap Press", "vision"),
            ]
            self.full_cycle_running = True
            self.stop_full_cycle = False
            self.cycle_complete = False
            try:
                for idx, name, step_category in vision_steps:
                    # Wait if signal monitoring is active
                    while self.signal_monitor_active and not self.stop_full_cycle:
                        time.sleep(0.5)

                    # Camera / QR responses are consumed only in the step's WRITE state, and IOServer keeps just the
                    # latest message, so send each one only when IOServer is waiting for it
                    write_state = self.write_state_map[idx]
                    self._update_status(f"Waiting for IOServer to reach {write_state.name} ({name})...")
                    if not self._wait_for_ioserver_state(write_state):
                        self._update_status("Full cycle stopped.")
                        return

                    self._update_status(f"Sending response for: {name}...")
                    if step_category == "qr":
                        # Send LHS QR code for auto cycle
                        self.root.after(0, lambda: self._send_qr_code("LHS"))
                    else:
                        self.root.after(0, lambda i=idx: self._send_ok(i))

                    # Wait for IOServer to consume it, then for any signal steps that follow to complete
                    if not self._wait_for_ioserver_state(write_state, leave=True):
                        self._update_status("Full cycle stopped.")
                        return
                    time.sleep(1.0)
                    while self.signal_monitor_active:
                        time.sleep(0.5)

                if self.cycle_complete:
                    self._update_status("Full cycle complete! All OK responses sent; IOServer is back at the start.")
                else:
                    self._update_status("Full cycle stopped before IOServer returned to the start.")
            finally:
                self.full_cycle_running = False

        thread = threading.Thread(target=cycle, daemon=True)
        thread.start()


# =============================================================================
# MAIN
# =============================================================================

def main():
    # Clear Redis on startup
    testRedisConnection.flushdb()

    print("=" * 60)
    print("IOServer Test Script - Real PLC Mode (Machine 2, 28 states)")
    print("=" * 60)
    print("\nThis script works WITH IOServer connected to real PLC.")
    print("It sends OK/NotOK responses and properly waits for signal steps.")
    print("\nSignal steps (torque, press, etc.) are handled by PLC hardware.")
    print("The script will poll IOServer state and wait for them to complete.\n")

    root = tk.Tk()
    app = IOServerTestApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()