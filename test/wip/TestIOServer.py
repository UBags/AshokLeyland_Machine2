"""
IOServer Test Script with PLC Simulator
Simulates PLC tags and provides GUI to step through all 29 states of the NEW process.

State Mapping:
- States 1-2: QR Code (Step 0)
- States 3-4: Knuckle (Step 1)
- States 5-6: Hub (Step 2)
- States 7-8: Hub and Second Bearing (Step 3)
- States 9-10: Nut and Plate Washer (Step 4)
- States 11-12: Tightening Torque 1 (Step 5)
- State 13: Free Rotations Done (Step 6)
- States 14-15: No Cap Bung (Step 7)
- State 16: Component Press Done (Step 8)
- States 17-18: No Bung (Step 9)
- States 19-20: Tightening Torque 2 (Step 10)
- States 21-22: Split Pin and Washer (Step 11)
- States 23-24: Cap (Step 12)
- States 25-26: Bung (Step 13)
- State 27: Cap Press Done (Step 14)
- States 28-29: Free Rotation Torque (Step 15)
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
from redis import Redis
from utils.RedisUtils import *
from Configuration import CosThetaConfigurator
from statemachine.StateMachine import MachineState


# =============================================================================
# PLC SIMULATOR CLASS
# =============================================================================

class PLCSimulator:
    """Simulates PLC tags for testing without actual PLC hardware."""

    def __init__(self):
        self.tags = {}
        self.lock = threading.Lock()
        self._initialize_tags()

    def _initialize_tags(self):
        """Initialize all PLC tags to default values."""
        # QR Code (Step 0)
        self.tags["PLC_PC_CheckQRCode"] = False
        self.tags["PC_PLC_QRCodeCheckOK"] = False
        self.tags["PC_PLC_QRCodeCheckDone"] = False

        # Knuckle (Step 1)
        self.tags["PLC_PC_CheckKnuckle"] = False
        self.tags["PC_PLC_KnuckleCheckOK"] = False
        self.tags["PC_PLC_KnuckleCheckDone"] = False

        # Hub (Step 2)
        self.tags["PLC_PC_CheckHub"] = False
        self.tags["PC_PLC_HubCheckOK"] = False
        self.tags["PC_PLC_HubCheckDone"] = False

        # Hub and Second Bearing (Step 3)
        self.tags["PLC_PC_CheckHubAndSecondBearing"] = False
        self.tags["PC_PLC_HubAndSecondBearingCheckOK"] = False
        self.tags["PC_PLC_HubAndSecondBearingCheckDone"] = False

        # Nut and Plate Washer (Step 4)
        self.tags["PLC_PC_CheckNutAndPlateWasher"] = False
        self.tags["PC_PLC_NutAndPlateWasherCheckOK"] = False
        self.tags["PC_PLC_NutAndPlateWasherCheckDone"] = False

        # Station 2 Torque (Step 5 and 10)
        self.tags["PLC_PC_Station2TorqueValueSet"] = False
        self.tags["PLC_PC_Station2TorqueValue"] = 0.0

        # Station 3 Free Rotation (Step 6)
        self.tags["PLC_PC_Station3RotationDone"] = False

        # No Cap Bung (Step 7)
        self.tags["PLC_PC_CheckNoCapBunk"] = False
        self.tags["PC_PLC_NoCapBunkCheckOK"] = False
        self.tags["PC_PLC_NoCapBunkCheckDone"] = False

        # Component Press (Step 8)
        self.tags["PLC_PC_ComponentPressDone"] = False

        # No Bung (Step 9)
        self.tags["PLC_PC_CheckNoBunk"] = False
        self.tags["PC_PLC_NoBunkCheckOK"] = False
        self.tags["PC_PLC_NoBunkCheckDone"] = False

        # Split Pin and Washer (Step 11)
        self.tags["PLC_PC_CheckSplitPinAndWasher"] = False
        self.tags["PC_PLC_SplitPinAndWasherCheckOK"] = False
        self.tags["PC_PLC_SplitPinAndWasherCheckDone"] = False

        # Cap (Step 12)
        self.tags["PLC_PC_CheckCap"] = False
        self.tags["PC_PLC_CapCheckOK"] = False
        self.tags["PC_PLC_CapCheckDone"] = False

        # Bung (Step 13)
        self.tags["PLC_PC_CheckBunk"] = False
        self.tags["PC_PLC_BunkCheckOK"] = False
        self.tags["PC_PLC_BunkCheckDone"] = False

        # Cap Press (Step 14)
        self.tags["PLC_PC_CapPressDone"] = False

        # Station 3 Torque (Step 15)
        self.tags["PLC_PC_Station3TorqueValueSet"] = False
        self.tags["PLC_PC_Station3TorqueValue"] = 0.0

        # System tags
        self.tags["PLC_PC_EmergencyAbort"] = False
        self.tags["PLC_PC_WatchTag"] = 1234
        self.tags["PC_PLC_ConnectionStatus"] = False

    def read(self, tag_name):
        with self.lock:
            return self.tags.get(tag_name, None)

    def write(self, tag_name, value):
        with self.lock:
            self.tags[tag_name] = value

    def reset_all(self):
        with self.lock:
            self._initialize_tags()


# =============================================================================
# TEST APPLICATION CLASS
# =============================================================================

class IOServerTestApp:
    def __init__(self, root):
        self.root = root
        self.root.title("IOServer Test - PLC Simulator (29 States)")
        self.root.geometry("900x750")

        # Initialize Redis
        self.redis = Redis(
            CosThetaConfigurator.getInstance().getRedisHost(),
            CosThetaConfigurator.getInstance().getRedisPort(),
            retry_on_timeout=True
        )
        self.redis.flushdb()

        # Initialize PLC Simulator
        self.plc = PLCSimulator()

        # Torque values
        self.torque1_value = tk.DoubleVar(value=50.0)
        self.torque2_value = tk.DoubleVar(value=55.0)
        self.rotation_torque_value = tk.DoubleVar(value=45.0)

        # Current step tracking
        self.current_step = 0

        # Build UI
        self._build_ui()

        # Start heartbeat simulator
        self._start_heartbeat_simulator()

    def _build_ui(self):
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Title
        title_label = ttk.Label(main_frame, text="IOServer Test - 29 State Process",
                                font=("Arial", 16, "bold"))
        title_label.pack(pady=10)

        # Status frame
        status_frame = ttk.LabelFrame(main_frame, text="Current Status", padding="10")
        status_frame.pack(fill=tk.X, pady=5)

        self.status_label = ttk.Label(status_frame, text="Ready - Click a step button to simulate PLC trigger",
                                      font=("Arial", 11))
        self.status_label.pack()

        # Steps frame with scrollable canvas
        steps_canvas_frame = ttk.LabelFrame(main_frame, text="Process Steps", padding="5")
        steps_canvas_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        canvas = tk.Canvas(steps_canvas_frame, height=400)
        scrollbar = ttk.Scrollbar(steps_canvas_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Define steps with their details
        self.steps = [
            ("Step 0", "QR Code", "States 1-2", self._trigger_qr_code, "vision"),
            ("Step 1", "Knuckle Check", "States 3-4", self._trigger_knuckle, "vision"),
            ("Step 2", "Hub Check", "States 5-6", self._trigger_hub, "vision"),
            ("Step 3", "Hub + 2nd Bearing", "States 7-8", self._trigger_hub_second_bearing, "vision"),
            ("Step 4", "Nut + Plate Washer", "States 9-10", self._trigger_nut_plate_washer, "vision"),
            ("Step 5", "Tightening Torque 1", "States 11-12", self._trigger_torque_1, "torque1"),
            ("Step 6", "Free Rotation Done", "State 13", self._trigger_free_rotation, "signal"),
            ("Step 7", "No Cap Bung Check", "States 14-15", self._trigger_no_cap_bung, "vision"),
            ("Step 8", "Component Press Done", "State 16", self._trigger_component_press, "signal"),
            ("Step 9", "No Bung Check", "States 17-18", self._trigger_no_bung, "vision"),
            ("Step 10", "Tightening Torque 2", "States 19-20", self._trigger_torque_2, "torque2"),
            ("Step 11", "Split Pin + Washer", "States 21-22", self._trigger_splitpin_washer, "vision"),
            ("Step 12", "Cap Check", "States 23-24", self._trigger_cap, "vision"),
            ("Step 13", "Bung Check", "States 25-26", self._trigger_bung, "vision"),
            ("Step 14", "Cap Press Done", "State 27", self._trigger_cap_press, "signal"),
            ("Step 15", "Free Rotation Torque", "States 28-29", self._trigger_rotation_torque, "torque3"),
        ]

        # Create step rows
        for i, (step_name, description, states, trigger_func, step_type) in enumerate(self.steps):
            row_frame = ttk.Frame(scrollable_frame)
            row_frame.pack(fill=tk.X, pady=2, padx=5)

            # Step info
            info_label = ttk.Label(row_frame, text=f"{step_name}: {description} ({states})",
                                   width=45, anchor="w")
            info_label.pack(side=tk.LEFT, padx=5)

            # Trigger button
            trigger_btn = ttk.Button(row_frame, text="Trigger", width=8,
                                     command=lambda f=trigger_func: f())
            trigger_btn.pack(side=tk.LEFT, padx=2)

            # OK/NotOK buttons for vision checks
            if step_type == "vision":
                ok_btn = ttk.Button(row_frame, text="OK", width=6,
                                    command=lambda idx=i: self._send_vision_ok(idx))
                ok_btn.pack(side=tk.LEFT, padx=2)

                notok_btn = ttk.Button(row_frame, text="NotOK", width=6,
                                       command=lambda idx=i: self._send_vision_notok(idx))
                notok_btn.pack(side=tk.LEFT, padx=2)

        # Torque input frame
        torque_frame = ttk.LabelFrame(main_frame, text="Torque Values", padding="10")
        torque_frame.pack(fill=tk.X, pady=5)

        # Torque 1
        ttk.Label(torque_frame, text="Torque 1 (Step 5):").grid(row=0, column=0, padx=5, pady=2)
        ttk.Entry(torque_frame, textvariable=self.torque1_value, width=10).grid(row=0, column=1, padx=5)
        ttk.Label(torque_frame, text="Nm").grid(row=0, column=2, padx=5)

        # Torque 2
        ttk.Label(torque_frame, text="Torque 2 (Step 10):").grid(row=0, column=3, padx=5, pady=2)
        ttk.Entry(torque_frame, textvariable=self.torque2_value, width=10).grid(row=0, column=4, padx=5)
        ttk.Label(torque_frame, text="Nm").grid(row=0, column=5, padx=5)

        # Rotation Torque
        ttk.Label(torque_frame, text="Rotation Torque (Step 15):").grid(row=1, column=0, padx=5, pady=2)
        ttk.Entry(torque_frame, textvariable=self.rotation_torque_value, width=10).grid(row=1, column=1, padx=5)
        ttk.Label(torque_frame, text="Nm").grid(row=1, column=2, padx=5)

        # Control buttons frame
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=10)

        ttk.Button(control_frame, text="Reset All Tags", command=self._reset_all).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Clear Redis", command=self._clear_redis).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Emergency Abort", command=self._emergency_abort).pack(side=tk.LEFT, padx=5)
        ttk.Button(control_frame, text="Run Full Cycle (Auto)", command=self._run_full_cycle).pack(side=tk.LEFT, padx=5)

    def _update_status(self, message):
        self.status_label.config(text=message)
        self.root.update()

    def _start_heartbeat_simulator(self):
        """Simulate heartbeat to keep IOServer happy."""

        def heartbeat_loop():
            while True:
                try:
                    sendCombinedHeartbeatFromHeartbeatServerToIOServer(self.redis, ALIVE)
                except:
                    pass
                time.sleep(1)

        thread = threading.Thread(target=heartbeat_loop, daemon=True)
        thread.start()

    # ==========================================================================
    # TRIGGER FUNCTIONS - Simulate PLC setting trigger tags
    # ==========================================================================

    def _trigger_qr_code(self):
        self._update_status("Step 0: QR Code - Trigger sent, waiting for QRCodeServer response...")
        self.plc.write("PLC_PC_CheckQRCode", True)

    def _trigger_knuckle(self):
        self._update_status("Step 1: Knuckle - Trigger sent, waiting for CameraServer response...")
        self.plc.write("PLC_PC_CheckKnuckle", True)

    def _trigger_hub(self):
        self._update_status("Step 2: Hub - Trigger sent, waiting for CameraServer response...")
        self.plc.write("PLC_PC_CheckHub", True)

    def _trigger_hub_second_bearing(self):
        self._update_status("Step 3: Hub + 2nd Bearing - Trigger sent, waiting for CameraServer response...")
        self.plc.write("PLC_PC_CheckHubAndSecondBearing", True)

    def _trigger_nut_plate_washer(self):
        self._update_status("Step 4: Nut + Plate Washer - Trigger sent, waiting for CameraServer response...")
        self.plc.write("PLC_PC_CheckNutAndPlateWasher", True)

    def _trigger_torque_1(self):
        self._update_status("Step 5: Tightening Torque 1 - Setting torque value...")
        self.plc.write("PLC_PC_Station2TorqueValue", self.torque1_value.get())
        self.plc.write("PLC_PC_Station2TorqueValueSet", True)

    def _trigger_free_rotation(self):
        self._update_status("Step 6: Free Rotation Done - Signal sent")
        self.plc.write("PLC_PC_Station3RotationDone", True)

    def _trigger_no_cap_bung(self):
        self._update_status("Step 7: No Cap Bung - Trigger sent, waiting for CameraServer response...")
        self.plc.write("PLC_PC_CheckNoCapBunk", True)

    def _trigger_component_press(self):
        self._update_status("Step 8: Component Press Done - Signal sent")
        self.plc.write("PLC_PC_ComponentPressDone", True)

    def _trigger_no_bung(self):
        self._update_status("Step 9: No Bung - Trigger sent, waiting for CameraServer response...")
        self.plc.write("PLC_PC_CheckNoBunk", True)

    def _trigger_torque_2(self):
        self._update_status("Step 10: Tightening Torque 2 - Setting torque value...")
        # Reset and reuse the same tags
        self.plc.write("PLC_PC_Station2TorqueValueSet", False)
        time.sleep(0.1)
        self.plc.write("PLC_PC_Station2TorqueValue", self.torque2_value.get())
        self.plc.write("PLC_PC_Station2TorqueValueSet", True)

    def _trigger_splitpin_washer(self):
        self._update_status("Step 11: Split Pin + Washer - Trigger sent, waiting for CameraServer response...")
        self.plc.write("PLC_PC_CheckSplitPinAndWasher", True)

    def _trigger_cap(self):
        self._update_status("Step 12: Cap - Trigger sent, waiting for CameraServer response...")
        self.plc.write("PLC_PC_CheckCap", True)

    def _trigger_bung(self):
        self._update_status("Step 13: Bung - Trigger sent, waiting for CameraServer response...")
        self.plc.write("PLC_PC_CheckBunk", True)

    def _trigger_cap_press(self):
        self._update_status("Step 14: Cap Press Done - Signal sent")
        self.plc.write("PLC_PC_CapPressDone", True)

    def _trigger_rotation_torque(self):
        self._update_status("Step 15: Free Rotation Torque - Setting torque value (FINAL)...")
        self.plc.write("PLC_PC_Station3TorqueValue", self.rotation_torque_value.get())
        self.plc.write("PLC_PC_Station3TorqueValueSet", True)

    # ==========================================================================
    # RESPONSE FUNCTIONS - Simulate QRCodeServer/CameraServer responses
    # ==========================================================================

    def _send_vision_ok(self, step_idx):
        """Send OK response for vision check steps."""
        step_name, description, _, _, _ = self.steps[step_idx]
        self._update_status(f"{step_name}: {description} - Sending OK response...")

        state_map = {
            0: None,  # QR Code - special handling
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

        if step_idx == 0:
            sendDataFromQRCodeServerToIOServer(self.redis, qrCodeStatus=ok)
        elif step_idx in state_map:
            sendDataFromCameraServerToIOServer(self.redis, result=ok,
                                               currentMachineState=state_map[step_idx])

        self._update_status(f"{step_name}: {description} - OK response sent!")

    def _send_vision_notok(self, step_idx):
        """Send NotOK response for vision check steps."""
        step_name, description, _, _, _ = self.steps[step_idx]
        self._update_status(f"{step_name}: {description} - Sending NotOK response...")

        state_map = {
            0: None,  # QR Code - special handling
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

        if step_idx == 0:
            sendDataFromQRCodeServerToIOServer(self.redis, qrCodeStatus=notok)
        elif step_idx in state_map:
            sendDataFromCameraServerToIOServer(self.redis, result=notok,
                                               currentMachineState=state_map[step_idx])

        self._update_status(f"{step_name}: {description} - NotOK response sent!")

    # ==========================================================================
    # CONTROL FUNCTIONS
    # ==========================================================================

    def _reset_all(self):
        self.plc.reset_all()
        self._update_status("All PLC tags reset to defaults")

    def _clear_redis(self):
        self.redis.flushdb()
        self._update_status("Redis cleared")

    def _emergency_abort(self):
        self.plc.write("PLC_PC_EmergencyAbort", True)
        self._update_status("Emergency Abort triggered!")

    def _run_full_cycle(self):
        """Run a complete cycle automatically with OK responses."""

        def run_cycle():
            delay = 1.5  # seconds between steps

            # Step 0: QR Code
            self._trigger_qr_code()
            time.sleep(0.5)
            self._send_vision_ok(0)
            time.sleep(delay)

            # Step 1: Knuckle
            self._trigger_knuckle()
            time.sleep(0.5)
            self._send_vision_ok(1)
            time.sleep(delay)

            # Step 2: Hub
            self._trigger_hub()
            time.sleep(0.5)
            self._send_vision_ok(2)
            time.sleep(delay)

            # Step 3: Hub + 2nd Bearing
            self._trigger_hub_second_bearing()
            time.sleep(0.5)
            self._send_vision_ok(3)
            time.sleep(delay)

            # Step 4: Nut + Plate Washer
            self._trigger_nut_plate_washer()
            time.sleep(0.5)
            self._send_vision_ok(4)
            time.sleep(delay)

            # Step 5: Torque 1
            self._trigger_torque_1()
            time.sleep(delay)

            # Step 6: Free Rotation
            self._trigger_free_rotation()
            time.sleep(delay)

            # Step 7: No Cap Bung
            self._trigger_no_cap_bung()
            time.sleep(0.5)
            self._send_vision_ok(7)
            time.sleep(delay)

            # Step 8: Component Press
            self._trigger_component_press()
            time.sleep(delay)

            # Step 9: No Bung
            self._trigger_no_bung()
            time.sleep(0.5)
            self._send_vision_ok(9)
            time.sleep(delay)

            # Step 10: Torque 2
            self._trigger_torque_2()
            time.sleep(delay)

            # Step 11: Split Pin + Washer
            self._trigger_splitpin_washer()
            time.sleep(0.5)
            self._send_vision_ok(11)
            time.sleep(delay)

            # Step 12: Cap
            self._trigger_cap()
            time.sleep(0.5)
            self._send_vision_ok(12)
            time.sleep(delay)

            # Step 13: Bung
            self._trigger_bung()
            time.sleep(0.5)
            self._send_vision_ok(13)
            time.sleep(delay)

            # Step 14: Cap Press
            self._trigger_cap_press()
            time.sleep(delay)

            # Step 15: Rotation Torque (FINAL)
            self._trigger_rotation_torque()
            time.sleep(delay)

            self._update_status("Full cycle completed! Back to State 1 (QR Code)")

        thread = threading.Thread(target=run_cycle, daemon=True)
        thread.start()


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("IOServer Test Script - PLC Simulator")
    print("=" * 60)
    print("\nNOTE: This script simulates PLC tags via Redis.")
    print("The IOServer must be modified to read from Redis instead of actual PLC.")
    print("\nAlternatively, use this script to send Camera/QRCode server responses")
    print("while manually triggering PLC tags if you have PLC access.")
    print("=" * 60)

    root = tk.Tk()
    app = IOServerTestApp(root)
    root.mainloop()