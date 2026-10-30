"""
Interactive Test GUI for MockPLC IOServer

This script:
1. Starts the MockPLC IOServer (no actual PLC required)
2. Provides buttons to simulate PLC triggers
3. Provides buttons to send Camera/QRCode server responses
4. Shows current state and tag values

Run this script to test the full 29-state process without any hardware.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import time
import sys

# Start MockIOServer first
from MockPLCIOServer import startMockIOServer, getMockPLC, getMockIOServer
from utils.RedisUtils import *
from statemachine.StateMachine import MachineState
from Configuration import CosThetaConfigurator
from redis import Redis


class TestGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("IOServer Test with MockPLC - 29 State Process")
        self.root.geometry("1000x800")

        # Get MockPLC instance
        self.plc = getMockPLC()

        # Redis for responses
        self.redis = Redis(
            CosThetaConfigurator.getInstance().getRedisHost(),
            CosThetaConfigurator.getInstance().getRedisPort(),
            retry_on_timeout=True
        )

        # Torque values
        self.torque1 = tk.DoubleVar(value=50.0)
        self.torque2 = tk.DoubleVar(value=55.0)
        self.torque3 = tk.DoubleVar(value=45.0)

        self._build_ui()
        self._start_state_monitor()
        self._start_heartbeat()

    def _build_ui(self):
        # Main notebook for tabs
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Tab 1: Main Controls
        main_tab = ttk.Frame(notebook)
        notebook.add(main_tab, text="Main Controls")
        self._build_main_tab(main_tab)

        # Tab 2: Tag Monitor
        monitor_tab = ttk.Frame(notebook)
        notebook.add(monitor_tab, text="Tag Monitor")
        self._build_monitor_tab(monitor_tab)

    def _build_main_tab(self, parent):
        # Status frame
        status_frame = ttk.LabelFrame(parent, text="Current Status", padding="10")
        status_frame.pack(fill=tk.X, padx=5, pady=5)

        self.state_label = ttk.Label(status_frame, text="State: Loading...", font=("Arial", 12, "bold"))
        self.state_label.pack()

        self.status_label = ttk.Label(status_frame, text="Ready", font=("Arial", 10))
        self.status_label.pack()

        # Create scrollable frame for steps
        canvas_frame = ttk.Frame(parent)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        canvas = tk.Canvas(canvas_frame)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        scrollable = ttk.Frame(canvas)

        scrollable.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Steps configuration
        steps = [
            ("Step 0: QR Code", "States 1-2", "PLC_PC_CheckQRCode", "vision", 0),
            ("Step 1: Knuckle", "States 3-4", "PLC_PC_CheckKnuckle", "vision", 1),
            ("Step 2: Hub", "States 5-6", "PLC_PC_CheckHub", "vision", 2),
            ("Step 3: Hub+2nd Bearing", "States 7-8", "PLC_PC_CheckHubAndSecondBearing", "vision", 3),
            ("Step 4: Nut+Plate Washer", "States 9-10", "PLC_PC_CheckNutAndPlateWasher", "vision", 4),
            ("Step 5: Torque 1", "States 11-12", "PLC_PC_Station2TorqueValueSet", "torque1", 5),
            ("Step 6: Free Rotation", "State 13", "PLC_PC_Station3RotationDone", "signal", 6),
            ("Step 7: No Cap Bung", "States 14-15", "PLC_PC_CheckNoCapBunk", "vision", 7),
            ("Step 8: Component Press", "State 16", "PLC_PC_ComponentPressDone", "signal", 8),
            ("Step 9: No Bung", "States 17-18", "PLC_PC_CheckNoBunk", "vision", 9),
            ("Step 10: Torque 2", "States 19-20", "PLC_PC_Station2TorqueValueSet", "torque2", 10),
            ("Step 11: Split Pin+Washer", "States 21-22", "PLC_PC_CheckSplitPinAndWasher", "vision", 11),
            ("Step 12: Cap", "States 23-24", "PLC_PC_CheckCap", "vision", 12),
            ("Step 13: Bung", "States 25-26", "PLC_PC_CheckBunk", "vision", 13),
            ("Step 14: Cap Press", "State 27", "PLC_PC_CapPressDone", "signal", 14),
            ("Step 15: Rotation Torque", "States 28-29", "PLC_PC_Station3TorqueValueSet", "torque3", 15),
        ]

        # Machine state to step mapping for vision responses
        self.vision_states = {
            0: None,  # QR Code
            1: MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE,
            2: MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING,
            3: MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING,
            4: MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER,
            7: MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS,
            9: MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK,
            11: MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER,
            12: MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP,
            13: MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS,
        }

        for name, states, tag, step_type, idx in steps:
            row = ttk.Frame(scrollable)
            row.pack(fill=tk.X, pady=3, padx=5)

            # Label
            ttk.Label(row, text=f"{name} ({states})", width=35, anchor="w").pack(side=tk.LEFT)

            # Trigger button (sets PLC tag to True)
            ttk.Button(row, text="Trigger", width=8,
                       command=lambda t=tag, st=step_type, i=idx: self._trigger(t, st, i)).pack(side=tk.LEFT, padx=2)

            # OK/NotOK for vision steps
            if step_type == "vision":
                ttk.Button(row, text="OK", width=6,
                           command=lambda i=idx: self._send_ok(i)).pack(side=tk.LEFT, padx=2)
                ttk.Button(row, text="NotOK", width=6,
                           command=lambda i=idx: self._send_notok(i)).pack(side=tk.LEFT, padx=2)

        # Torque inputs
        torque_frame = ttk.LabelFrame(parent, text="Torque Values (Nm)", padding="10")
        torque_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(torque_frame, text="Torque 1:").grid(row=0, column=0, padx=5)
        ttk.Entry(torque_frame, textvariable=self.torque1, width=8).grid(row=0, column=1, padx=5)

        ttk.Label(torque_frame, text="Torque 2:").grid(row=0, column=2, padx=5)
        ttk.Entry(torque_frame, textvariable=self.torque2, width=8).grid(row=0, column=3, padx=5)

        ttk.Label(torque_frame, text="Rotation:").grid(row=0, column=4, padx=5)
        ttk.Entry(torque_frame, textvariable=self.torque3, width=8).grid(row=0, column=5, padx=5)

        # Control buttons
        ctrl_frame = ttk.Frame(parent)
        ctrl_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(ctrl_frame, text="Reset All Tags", command=self._reset_all).pack(side=tk.LEFT, padx=5)
        ttk.Button(ctrl_frame, text="Run Full Cycle (Auto)", command=self._auto_cycle).pack(side=tk.LEFT, padx=5)
        ttk.Button(ctrl_frame, text="Clear Redis", command=lambda: self.redis.flushdb()).pack(side=tk.LEFT, padx=5)

    def _build_monitor_tab(self, parent):
        self.tag_text = scrolledtext.ScrolledText(parent, width=80, height=40, font=("Courier", 9))
        self.tag_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _trigger(self, tag, step_type, idx):
        """Trigger a PLC tag (simulate PLC setting the tag to True)."""
        if step_type == "torque1":
            self.plc.write("PLC_PC_Station2TorqueValue", self.torque1.get())
            self.plc.write(tag, True)
            self._update_status(f"Step 5: Set Torque 1 = {self.torque1.get()} Nm")
        elif step_type == "torque2":
            # Reset first, then set
            self.plc.write("PLC_PC_Station2TorqueValueSet", False)
            time.sleep(0.05)
            self.plc.write("PLC_PC_Station2TorqueValue", self.torque2.get())
            self.plc.write(tag, True)
            self._update_status(f"Step 10: Set Torque 2 = {self.torque2.get()} Nm")
        elif step_type == "torque3":
            self.plc.write("PLC_PC_Station3TorqueValue", self.torque3.get())
            self.plc.write(tag, True)
            self._update_status(f"Step 15: Set Rotation Torque = {self.torque3.get()} Nm")
        else:
            self.plc.write(tag, True)
            self._update_status(f"Triggered: {tag}")

    def _send_ok(self, idx):
        """Send OK response for vision check."""
        if idx == 0:
            sendDataFromQRCodeServerToIOServer(self.redis, qrCodeStatus=ok)
            self._update_status("QR Code: OK sent")
        elif idx in self.vision_states:
            sendDataFromCameraServerToIOServer(self.redis, result=ok, currentMachineState=self.vision_states[idx])
            self._update_status(f"Step {idx}: OK sent")

    def _send_notok(self, idx):
        """Send NotOK response for vision check."""
        if idx == 0:
            sendDataFromQRCodeServerToIOServer(self.redis, qrCodeStatus=notok)
            self._update_status("QR Code: NotOK sent")
        elif idx in self.vision_states:
            sendDataFromCameraServerToIOServer(self.redis, result=notok, currentMachineState=self.vision_states[idx])
            self._update_status(f"Step {idx}: NotOK sent")

    def _reset_all(self):
        self.plc.reset_all()
        self._update_status("All tags reset")

    def _update_status(self, msg):
        self.status_label.config(text=msg)

    def _start_state_monitor(self):
        """Monitor and display current state."""

        def monitor():
            while True:
                try:
                    io = getMockIOServer()
                    if io:
                        state = io.machineState.getCurrentState()
                        self.state_label.config(text=f"State: {state}")

                    # Update tag monitor
                    tags = self.plc.get_all_tags()
                    text = "PLC Tag Values:\n" + "=" * 50 + "\n"
                    for tag, value in sorted(tags.items()):
                        text += f"{tag}: {value}\n"

                    self.tag_text.delete(1.0, tk.END)
                    self.tag_text.insert(tk.END, text)
                except:
                    pass
                time.sleep(0.5)

        threading.Thread(target=monitor, daemon=True).start()

    def _start_heartbeat(self):
        """Send heartbeat to keep IOServer happy."""

        def heartbeat():
            while True:
                try:
                    sendCombinedHeartbeatFromHeartbeatServerToIOServer(self.redis, ALIVE)
                except:
                    pass
                time.sleep(1)

        threading.Thread(target=heartbeat, daemon=True).start()

    def _auto_cycle(self):
        """Run a complete automatic test cycle."""

        def cycle():
            delay = 1.0

            # Step 0: QR Code
            self._trigger("PLC_PC_CheckQRCode", "vision", 0)
            time.sleep(0.3)
            self._send_ok(0)
            time.sleep(delay)

            # Step 1: Knuckle
            self._trigger("PLC_PC_CheckKnuckle", "vision", 1)
            time.sleep(0.3)
            self._send_ok(1)
            time.sleep(delay)

            # Step 2: Hub
            self._trigger("PLC_PC_CheckHub", "vision", 2)
            time.sleep(0.3)
            self._send_ok(2)
            time.sleep(delay)

            # Step 3: Hub + 2nd Bearing
            self._trigger("PLC_PC_CheckHubAndSecondBearing", "vision", 3)
            time.sleep(0.3)
            self._send_ok(3)
            time.sleep(delay)

            # Step 4: Nut + Plate Washer
            self._trigger("PLC_PC_CheckNutAndPlateWasher", "vision", 4)
            time.sleep(0.3)
            self._send_ok(4)
            time.sleep(delay)

            # Step 5: Torque 1
            self._trigger("PLC_PC_Station2TorqueValueSet", "torque1", 5)
            time.sleep(delay)

            # Step 6: Free Rotation
            self._trigger("PLC_PC_Station3RotationDone", "signal", 6)
            time.sleep(delay)

            # Step 7: No Cap Bung
            self._trigger("PLC_PC_CheckNoCapBunk", "vision", 7)
            time.sleep(0.3)
            self._send_ok(7)
            time.sleep(delay)

            # Step 8: Component Press
            self._trigger("PLC_PC_ComponentPressDone", "signal", 8)
            time.sleep(delay)

            # Step 9: No Bung
            self._trigger("PLC_PC_CheckNoBunk", "vision", 9)
            time.sleep(0.3)
            self._send_ok(9)
            time.sleep(delay)

            # Step 10: Torque 2
            self._trigger("PLC_PC_Station2TorqueValueSet", "torque2", 10)
            time.sleep(delay)

            # Step 11: Split Pin + Washer
            self._trigger("PLC_PC_CheckSplitPinAndWasher", "vision", 11)
            time.sleep(0.3)
            self._send_ok(11)
            time.sleep(delay)

            # Step 12: Cap
            self._trigger("PLC_PC_CheckCap", "vision", 12)
            time.sleep(0.3)
            self._send_ok(12)
            time.sleep(delay)

            # Step 13: Bung
            self._trigger("PLC_PC_CheckBunk", "vision", 13)
            time.sleep(0.3)
            self._send_ok(13)
            time.sleep(delay)

            # Step 14: Cap Press
            self._trigger("PLC_PC_CapPressDone", "signal", 14)
            time.sleep(delay)

            # Step 15: Rotation Torque (Final)
            self._trigger("PLC_PC_Station3TorqueValueSet", "torque3", 15)
            time.sleep(delay)

            self._update_status("Full cycle completed!")

        threading.Thread(target=cycle, daemon=True).start()


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Starting IOServer Test with MockPLC")
    print("=" * 60)

    # Start MockIOServer
    print("Starting MockIOServer...")
    io = startMockIOServer()
    time.sleep(2)

    # Start GUI
    print("Starting Test GUI...")
    root = tk.Tk()
    app = TestGUI(root)
    root.mainloop()