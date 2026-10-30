"""
MockPLC IOServer Test - Complete simulation without PLC hardware

This creates a MockPLC that simulates all PLC tag read/write operations,
allowing you to test the full IOServer flow without any hardware.

Run this script to:
1. Start a MockPLC-based IOServer
2. Use the GUI to trigger PLC signals and send responses

Features:
- Buttons are disabled for states that have been crossed
- Per-step status labels show progress/results
- Auto Cycle runs through all steps automatically
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import time
from redis import Redis
from utils.RedisUtils import *
from Configuration import CosThetaConfigurator
from statemachine.StateMachine import MachineState, MachineStateMachine


# =============================================================================
# MOCK PLC CLASS
# =============================================================================

class MockPLC:
    """Simulates PLC tag operations using an in-memory dictionary."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.tags = {}
        self.tag_lock = threading.Lock()
        self.connected = True
        self._init_tags()

    def _init_tags(self):
        """Initialize all PLC tags to default values."""
        defaults = {
            # QR Code (Step 0)
            "PLC_PC_CheckQRCode": False,
            "PC_PLC_QRCodeCheckOK": False,
            "PC_PLC_QRCodeCheckDone": False,
            # Knuckle (Step 1)
            "PLC_PC_CheckKnuckle": False,
            "PC_PLC_KnuckleCheckOK": False,
            "PC_PLC_KnuckleCheckDone": False,
            # Hub (Step 2)
            "PLC_PC_CheckHub": False,
            "PC_PLC_HubCheckOK": False,
            "PC_PLC_HubCheckDone": False,
            # Hub + 2nd Bearing (Step 3)
            "PLC_PC_CheckHubAndSecondBearing": False,
            "PC_PLC_HubAndSecondBearingCheckOK": False,
            "PC_PLC_HubAndSecondBearingCheckDone": False,
            # Nut + Plate Washer (Step 4)
            "PLC_PC_CheckNutAndPlateWasher": False,
            "PC_PLC_NutAndPlateWasherCheckOK": False,
            "PC_PLC_NutAndPlateWasherCheckDone": False,
            # Station 2 Torque (Step 5 and 10)
            "PLC_PC_Station2TorqueValueSet": False,
            "PLC_PC_Station2TorqueValue": 0.0,
            # Station 3 Rotation (Step 6)
            "PLC_PC_Station3RotationDone": False,
            # No Cap Bung (Step 7)
            "PLC_PC_CheckNoCapBunk": False,
            "PC_PLC_NoCapBunkCheckOK": False,
            "PC_PLC_NoCapBunkCheckDone": False,
            # Component Press (Step 8)
            "PLC_PC_ComponentPressDone": False,
            # No Bung (Step 9)
            "PLC_PC_CheckNoBunk": False,
            "PC_PLC_NoBunkCheckOK": False,
            "PC_PLC_NoBunkCheckDone": False,
            # Split Pin + Washer (Step 11)
            "PLC_PC_CheckSplitPinAndWasher": False,
            "PC_PLC_SplitPinAndWasherCheckOK": False,
            "PC_PLC_SplitPinAndWasherCheckDone": False,
            # Cap (Step 12)
            "PLC_PC_CheckCap": False,
            "PC_PLC_CapCheckOK": False,
            "PC_PLC_CapCheckDone": False,
            # Bung (Step 13)
            "PLC_PC_CheckBunk": False,
            "PC_PLC_BunkCheckOK": False,
            "PC_PLC_BunkCheckDone": False,
            # Cap Press (Step 14)
            "PLC_PC_CapPressDone": False,
            # Station 3 Torque (Step 15)
            "PLC_PC_Station3TorqueValueSet": False,
            "PLC_PC_Station3TorqueValue": 0.0,
            # System
            "PLC_PC_EmergencyAbort": False,
            "PLC_PC_WatchTag": 1234,
            "PC_PLC_ConnectionStatus": False,
        }
        with self.tag_lock:
            self.tags = defaults.copy()

    def read(self, tag_name):
        with self.tag_lock:
            return _MockResult(self.tags.get(tag_name))

    def write(self, tag_name, value):
        with self.tag_lock:
            self.tags[tag_name] = value

    def reset_all(self):
        self._init_tags()

    def get_all(self):
        with self.tag_lock:
            return self.tags.copy()


class _MockResult:
    def __init__(self, value):
        self.value = value


def get_mock_plc():
    return MockPLC()


# =============================================================================
# SIMPLIFIED MOCK IOSERVER
# =============================================================================

class SimpleMockIOServer:
    """Simplified IOServer using MockPLC for testing."""

    PLC_SLEEPTIME = 0.2
    FALSE_FLOAT = -1234.0

    def __init__(self, on_state_change=None):
        self.plc = get_mock_plc()
        self.machineState = MachineStateMachine()
        self.shutdown = False
        self.on_state_change = on_state_change  # Callback for state changes

        # Redis
        self.redis = Redis(
            CosThetaConfigurator.getInstance().getRedisHost(),
            CosThetaConfigurator.getInstance().getRedisPort(),
            retry_on_timeout=True
        )
        self.redis.flushdb()

        self.read_interval = 0.2
        self.write_interval = 0.1

        self._start_threads()
        print("SimpleMockIOServer started!")

    def _notify_state_change(self, old_state, new_state, result_ok=True):
        """Notify GUI of state change."""
        if self.on_state_change:
            self.on_state_change(old_state, new_state, result_ok)

    def _get_read_tags(self):
        """Get tags to read based on current state."""
        state = self.machineState.getCurrentState()

        tag_map = {
            MachineState.READ_QR_CODE: ("PLC_PC_CheckQRCode", bool),
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE: ("PLC_PC_CheckKnuckle", bool),
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING: ("PLC_PC_CheckHub", bool),
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING: ("PLC_PC_CheckHubAndSecondBearing", bool),
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER: ("PLC_PC_CheckNutAndPlateWasher", bool),
            MachineState.READ_TIGHTENING_TORQUE_1_DONE: ("PLC_PC_Station2TorqueValueSet", bool),
            MachineState.READ_TIGHTENING_TORQUE_1: ("PLC_PC_Station2TorqueValue", float),
            MachineState.READ_FREE_ROTATIONS_DONE: ("PLC_PC_Station3RotationDone", bool),
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS: ("PLC_PC_CheckNoCapBunk", bool),
            MachineState.READ_COMPONENT_PRESS_DONE: ("PLC_PC_ComponentPressDone", bool),
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK: ("PLC_PC_CheckNoBunk", bool),
            MachineState.READ_TIGHTENING_TORQUE_2_DONE: ("PLC_PC_Station2TorqueValueSet", bool),
            MachineState.READ_TIGHTENING_TORQUE_2: ("PLC_PC_Station2TorqueValue", float),
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER: ("PLC_PC_CheckSplitPinAndWasher", bool),
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP: ("PLC_PC_CheckCap", bool),
            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS: ("PLC_PC_CheckBunk", bool),
            MachineState.READ_CAP_PRESS_DONE: ("PLC_PC_CapPressDone", bool),
            MachineState.READ_FREE_ROTATION_TORQUE_1_DONE: ("PLC_PC_Station3TorqueValueSet", bool),
            MachineState.READ_FREE_ROTATION_TORQUE_1: ("PLC_PC_Station3TorqueValue", float),
        }

        return tag_map.get(state, (None, None))

    def _get_write_tags(self):
        """Get tags to write based on current state."""
        state = self.machineState.getCurrentState()

        tag_map = {
            MachineState.WRITE_QR_CODE:
                ("PC_PLC_QRCodeCheckOK", "PC_PLC_QRCodeCheckDone", "PLC_PC_CheckQRCode"),
            MachineState.WRITE_RESULT_OF_CHECKING_KNUCKLE:
                ("PC_PLC_KnuckleCheckOK", "PC_PLC_KnuckleCheckDone", "PLC_PC_CheckKnuckle"),
            MachineState.WRITE_RESULT_OF_CHECKING_HUB_AND_BOTTOM_BEARING:
                ("PC_PLC_HubCheckOK", "PC_PLC_HubCheckDone", "PLC_PC_CheckHub"),
            MachineState.WRITE_RESULT_OF_CHECKING_TOP_BEARING:
                ("PC_PLC_HubAndSecondBearingCheckOK", "PC_PLC_HubAndSecondBearingCheckDone", "PLC_PC_CheckHubAndSecondBearing"),
            MachineState.WRITE_RESULT_OF_CHECKING_NUT_AND_PLATEWASHER:
                ("PC_PLC_NutAndPlateWasherCheckOK", "PC_PLC_NutAndPlateWasherCheckDone", "PLC_PC_CheckNutAndPlateWasher"),
            MachineState.WRITE_RESULT_OF_CHECKING_BUNK_FOR_COMPONENT_PRESS:
                ("PC_PLC_NoCapBunkCheckOK", "PC_PLC_NoCapBunkCheckDone", "PLC_PC_CheckNoCapBunk"),
            MachineState.WRITE_RESULT_OF_CHECKING_NO_BUNK:
                ("PC_PLC_NoBunkCheckOK", "PC_PLC_NoBunkCheckDone", "PLC_PC_CheckNoBunk"),
            MachineState.WRITE_RESULT_OF_CHECKING_SPLITPIN_AND_WASHER:
                ("PC_PLC_SplitPinAndWasherCheckOK", "PC_PLC_SplitPinAndWasherCheckDone", "PLC_PC_CheckSplitPinAndWasher"),
            MachineState.WRITE_RESULT_OF_CHECKING_CAP:
                ("PC_PLC_CapCheckOK", "PC_PLC_CapCheckDone", "PLC_PC_CheckCap"),
            MachineState.WRITE_RESULT_OF_CHECKING_BUNK_FOR_CAP_PRESS:
                ("PC_PLC_BunkCheckOK", "PC_PLC_BunkCheckDone", "PLC_PC_CheckBunk"),
        }

        return tag_map.get(state, None)

    def _process_read(self, value):
        """Process read value and send to appropriate queue."""
        if value is None:
            return False

        state = self.machineState.getCurrentState()
        sent = False

        if isinstance(value, bool) and value:
            # Send to appropriate server based on state
            if state == MachineState.READ_QR_CODE:
                sent = sendDataFromIOServerToQRCodeServer(self.redis)
            elif state in [MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE,
                           MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING,
                           MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING,
                           MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER,
                           MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS,
                           MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK,
                           MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER,
                           MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP,
                           MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS]:
                sent = sendDataFromIOServerToCameraServer(self.redis, currentMachineState=state)
            elif state in [MachineState.READ_TIGHTENING_TORQUE_1_DONE,
                          MachineState.READ_TIGHTENING_TORQUE_2_DONE,
                          MachineState.READ_FREE_ROTATION_TORQUE_1_DONE]:
                sent = True
            elif state in [MachineState.READ_FREE_ROTATIONS_DONE,
                          MachineState.READ_COMPONENT_PRESS_DONE,
                          MachineState.READ_CAP_PRESS_DONE]:
                sent = sendDataFromIOServerToFEServer(self.redis, currentMachineState=state)

            if sent:
                old_state = state
                print(f"State {state} -> ", end="")
                self.machineState.incrementState()
                new_state = self.machineState.getCurrentState()
                print(f"{new_state}")
                self._notify_state_change(old_state, new_state, True)

        elif isinstance(value, float) and value != self.FALSE_FLOAT:
            # Torque readings
            if state == MachineState.READ_TIGHTENING_TORQUE_1:
                sent = sendDataFromIOServerToFEServer(self.redis, currentMachineState=state, value=value)
            elif state == MachineState.READ_TIGHTENING_TORQUE_2:
                sent = sendDataFromIOServerToFEServer(self.redis, currentMachineState=state, value=value)
            elif state == MachineState.READ_FREE_ROTATION_TORQUE_1:
                sent = sendDataFromIOServerToFEServer(self.redis, currentMachineState=state, value=value,
                                                      moveToState=MachineState.READ_QR_CODE,
                                                      action=moveAheadToNextComponent)

            if sent:
                old_state = state
                print(f"State {state} (torque={value}) -> ", end="")
                self.machineState.incrementState()
                new_state = self.machineState.getCurrentState()
                print(f"{new_state}")
                self._notify_state_change(old_state, new_state, True)

        # Reset all tags when back to QR Code state
        if self.machineState.getCurrentState() == MachineState.READ_QR_CODE:
            self.plc.reset_all()

        return sent

    def _perform_write(self, tags, result):
        """Write result to PLC tags."""
        self.plc.write(tags[0], result)
        time.sleep(self.PLC_SLEEPTIME)
        self.plc.write(tags[1], True)
        # Reset trigger tag
        self.plc.write(tags[2], False)

    def _start_threads(self):
        # Read loop
        def read_loop():
            while not self.shutdown:
                try:
                    tag, tag_type = self._get_read_tags()
                    if tag:
                        result = self.plc.read(tag)
                        if result.value is not None:
                            if tag_type == bool and result.value:
                                self._process_read(result.value)
                            elif tag_type == float and result.value != 0.0:
                                self._process_read(result.value)
                except Exception as e:
                    print(f"Read error: {e}")
                time.sleep(self.read_interval)

        # Write loop
        def write_loop():
            while not self.shutdown:
                try:
                    tags = self._get_write_tags()
                    if tags:
                        state = self.machineState.getCurrentState()

                        if state == MachineState.WRITE_QR_CODE:
                            success = readDataInIOServerFromQRCodeServer(self.redis)
                            if success:
                                self._perform_write(tags, True)
                                old_state = state
                                print(f"State {state} -> ", end="")
                                self.machineState.incrementState()
                                new_state = self.machineState.getCurrentState()
                                print(f"{new_state}")
                                self._notify_state_change(old_state, new_state, True)
                        else:
                            prev_state, evaluation = readDataInIOServerFromCameraServer(self.redis)
                            if evaluation:
                                self._perform_write(tags, True)
                                old_state = state
                                print(f"State {state} -> ", end="")
                                self.machineState.incrementState()
                                new_state = self.machineState.getCurrentState()
                                print(f"{new_state}")
                                self._notify_state_change(old_state, new_state, True)
                            elif prev_state != MachineState.INVALID_STATE:
                                self._perform_write(tags, False)
                                old_state = state
                                print(f"State {state} - NotOK, decrementing")
                                self.machineState.decrementState()
                                new_state = self.machineState.getCurrentState()
                                self._notify_state_change(old_state, new_state, False)
                except Exception as e:
                    pass
                time.sleep(self.write_interval)

        # Heartbeat
        def heartbeat():
            while not self.shutdown:
                try:
                    sendHeartbeatFromIOServerToHeartbeatServer(self.redis, ALIVE)
                except:
                    pass
                time.sleep(1)

        threading.Thread(target=read_loop, daemon=True, name="ReadLoop").start()
        threading.Thread(target=write_loop, daemon=True, name="WriteLoop").start()
        threading.Thread(target=heartbeat, daemon=True, name="Heartbeat").start()


# =============================================================================
# TEST GUI
# =============================================================================

class MockPLCTestGUI:
    def __init__(self, root, io_server):
        self.root = root
        self.io = io_server
        self.plc = get_mock_plc()

        # Set callback for state changes
        self.io.on_state_change = self._on_state_change

        self.root.title("MockPLC IOServer Test - Complete Simulation")
        self.root.geometry("1050x800")

        # Torque values
        self.torque1 = tk.DoubleVar(value=50.0)
        self.torque2 = tk.DoubleVar(value=55.0)
        self.torque3 = tk.DoubleVar(value=45.0)

        # Store button and label references for each step
        self.step_widgets = {}  # {step_idx: {'trigger': btn, 'ok': btn, 'notok': btn, 'status': label}}

        # Track current step (0-15)
        self.current_step = 0

        self._build_ui()
        self._start_monitor()
        self._start_heartbeat()

    def _on_state_change(self, old_state, new_state, result_ok):
        """Callback when IOServer state changes."""
        # Update button states on main thread
        self.root.after(0, self._update_button_states)

    def _get_current_step_from_io(self):
        """Determine current step from IOServer's machine state."""
        state = self.io.machineState.getCurrentState()
        state_str = str(state)

        # Map MachineState to step index
        state_to_step = {
            'READ_QR_CODE': 0, 'WRITE_QR_CODE': 0,
            'READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE': 1, 'WRITE_RESULT_OF_CHECKING_KNUCKLE': 1,
            'READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING': 2, 'WRITE_RESULT_OF_CHECKING_HUB_AND_BOTTOM_BEARING': 2,
            'READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING': 3, 'WRITE_RESULT_OF_CHECKING_TOP_BEARING': 3,
            'READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER': 4, 'WRITE_RESULT_OF_CHECKING_NUT_AND_PLATEWASHER': 4,
            'READ_TIGHTENING_TORQUE_1_DONE': 5, 'READ_TIGHTENING_TORQUE_1': 5,
            'READ_FREE_ROTATIONS_DONE': 6,
            'READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS': 7, 'WRITE_RESULT_OF_CHECKING_BUNK_FOR_COMPONENT_PRESS': 7,
            'READ_COMPONENT_PRESS_DONE': 8,
            'READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK': 9, 'WRITE_RESULT_OF_CHECKING_NO_BUNK': 9,
            'READ_TIGHTENING_TORQUE_2_DONE': 10, 'READ_TIGHTENING_TORQUE_2': 10,
            'READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER': 11, 'WRITE_RESULT_OF_CHECKING_SPLITPIN_AND_WASHER': 11,
            'READ_TAKE_PICTURE_FOR_CHECKING_CAP': 12, 'WRITE_RESULT_OF_CHECKING_CAP': 12,
            'READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS': 13, 'WRITE_RESULT_OF_CHECKING_BUNK_FOR_CAP_PRESS': 13,
            'READ_CAP_PRESS_DONE': 14,
            'READ_FREE_ROTATION_TORQUE_1_DONE': 15, 'READ_FREE_ROTATION_TORQUE_1': 15,
        }

        for state_name, step in state_to_step.items():
            if state_name in state_str:
                return step
        return 0

    def _update_button_states(self):
        """Enable/disable buttons based on current step."""
        current = self._get_current_step_from_io()

        for step_idx, widgets in self.step_widgets.items():
            if step_idx < current:
                # Past step - disable all buttons, show completed
                widgets['trigger'].config(state='disabled')
                if widgets['ok']:
                    widgets['ok'].config(state='disabled')
                if widgets['notok']:
                    widgets['notok'].config(state='disabled')
                widgets['status'].config(text="✓ Completed", foreground="green")
            elif step_idx == current:
                # Current step - enable buttons
                widgets['trigger'].config(state='normal')
                if widgets['ok']:
                    widgets['ok'].config(state='normal')
                if widgets['notok']:
                    widgets['notok'].config(state='normal')
                if "Completed" in widgets['status'].cget("text") or "Waiting" in widgets['status'].cget("text"):
                    widgets['status'].config(text="◄ Current Step", foreground="blue")
            else:
                # Future step - disable buttons
                widgets['trigger'].config(state='disabled')
                if widgets['ok']:
                    widgets['ok'].config(state='disabled')
                if widgets['notok']:
                    widgets['notok'].config(state='disabled')
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
        notebook.add(monitor_tab, text="Tag Monitor")
        self._build_monitor(monitor_tab)

    def _build_controls(self, parent):
        # Status
        status_frame = ttk.LabelFrame(parent, text="Current State", padding="10")
        status_frame.pack(fill=tk.X, padx=5, pady=5)

        self.state_label = ttk.Label(status_frame, text="Loading...", font=("Arial", 12, "bold"))
        self.state_label.pack()

        self.status_label = ttk.Label(status_frame, text="Ready", font=("Arial", 10))
        self.status_label.pack()

        # Steps
        steps_frame = ttk.LabelFrame(parent, text="Process Steps (Trigger + Response)", padding="5")
        steps_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        canvas = tk.Canvas(steps_frame, height=380)
        scrollbar = ttk.Scrollbar(steps_frame, orient="vertical", command=canvas.yview)
        scrollable = ttk.Frame(canvas)

        scrollable.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Vision state mapping for responses
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

        # Steps definition
        steps = [
            ("Step 0: QR Code", "PLC_PC_CheckQRCode", "vision", 0),
            ("Step 1: Knuckle", "PLC_PC_CheckKnuckle", "vision", 1),
            ("Step 2: Hub", "PLC_PC_CheckHub", "vision", 2),
            ("Step 3: Hub+2nd Bearing", "PLC_PC_CheckHubAndSecondBearing", "vision", 3),
            ("Step 4: Nut+Plate Washer", "PLC_PC_CheckNutAndPlateWasher", "vision", 4),
            ("Step 5: Torque 1", "torque1", "torque", 5),
            ("Step 6: Free Rotation", "PLC_PC_Station3RotationDone", "signal", 6),
            ("Step 7: No Cap Bung", "PLC_PC_CheckNoCapBung", "vision", 7),
            ("Step 8: Component Press", "PLC_PC_ComponentPressDone", "signal", 8),
            ("Step 9: No Bung", "PLC_PC_CheckNoBung", "vision", 9),
            ("Step 10: Torque 2", "torque2", "torque", 10),
            ("Step 11: Split Pin+Washer", "PLC_PC_CheckSplitPinAndWasher", "vision", 11),
            ("Step 12: Cap", "PLC_PC_CheckCap", "vision", 12),
            ("Step 13: Bung", "PLC_PC_CheckBung", "vision", 13),
            ("Step 14: Cap Press", "PLC_PC_CapPressDone", "signal", 14),
            ("Step 15: Rotation Torque", "torque3", "torque", 15),
        ]

        for name, tag, step_type, idx in steps:
            row = ttk.Frame(scrollable)
            row.pack(fill=tk.X, pady=3, padx=5)

            # Step name label
            ttk.Label(row, text=name, width=22, anchor="w").pack(side=tk.LEFT)

            # Trigger button
            trigger_btn = ttk.Button(row, text="Trigger", width=8,
                                    command=lambda t=tag, st=step_type, i=idx: self._trigger(t, st, i))
            trigger_btn.pack(side=tk.LEFT, padx=2)

            ok_btn = None
            notok_btn = None

            # OK/NotOK for vision steps
            if step_type == "vision":
                ok_btn = ttk.Button(row, text="OK", width=6,
                                   command=lambda i=idx: self._send_ok(i))
                ok_btn.pack(side=tk.LEFT, padx=2)

                notok_btn = ttk.Button(row, text="Not OK", width=10,
                                      command=lambda i=idx: self._send_notok(i))
                notok_btn.pack(side=tk.LEFT, padx=2)
            else:
                # Spacer for alignment
                ttk.Label(row, text="", width=18).pack(side=tk.LEFT)

            # Status label for this step
            status_lbl = ttk.Label(row, text="Waiting...", width=50, anchor="w", foreground="gray")
            status_lbl.pack(side=tk.LEFT, padx=10)

            # Store widget references
            self.step_widgets[idx] = {
                'trigger': trigger_btn,
                'ok': ok_btn,
                'notok': notok_btn,
                'status': status_lbl
            }

        # Initial button state update
        self._update_button_states()

        # Torque values
        torque_frame = ttk.LabelFrame(parent, text="Torque Values (Nm)", padding="10")
        torque_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(torque_frame, text="Torque 1:").grid(row=0, column=0, padx=5)
        ttk.Entry(torque_frame, textvariable=self.torque1, width=8).grid(row=0, column=1, padx=5)
        ttk.Label(torque_frame, text="Torque 2:").grid(row=0, column=2, padx=5)
        ttk.Entry(torque_frame, textvariable=self.torque2, width=8).grid(row=0, column=3, padx=5)
        ttk.Label(torque_frame, text="Rotation:").grid(row=0, column=4, padx=5)
        ttk.Entry(torque_frame, textvariable=self.torque3, width=8).grid(row=0, column=5, padx=5)

        # Quick actions
        actions = ttk.Frame(parent)
        actions.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(actions, text="Reset All", command=self._reset).pack(side=tk.LEFT, padx=5)
        ttk.Button(actions, text="Auto Cycle (All OK)", command=self._auto_cycle).pack(side=tk.LEFT, padx=5)

    def _build_monitor(self, parent):
        self.tag_text = scrolledtext.ScrolledText(parent, font=("Courier", 9))
        self.tag_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def _trigger(self, tag, step_type, step_idx):
        """Trigger a PLC tag."""
        widgets = self.step_widgets[step_idx]

        if step_type == "torque":
            if tag == "torque1":
                self.plc.write("PLC_PC_Station2TorqueValue", self.torque1.get())
                self.plc.write("PLC_PC_Station2TorqueValueSet", True)
                widgets['status'].config(text=f"Triggered (T={self.torque1.get()} Nm)", foreground="orange")
            elif tag == "torque2":
                self.plc.write("PLC_PC_Station2TorqueValueSet", False)
                time.sleep(0.05)
                self.plc.write("PLC_PC_Station2TorqueValue", self.torque2.get())
                self.plc.write("PLC_PC_Station2TorqueValueSet", True)
                widgets['status'].config(text=f"Triggered (T={self.torque2.get()} Nm)", foreground="orange")
            elif tag == "torque3":
                self.plc.write("PLC_PC_Station3TorqueValue", self.torque3.get())
                self.plc.write("PLC_PC_Station3TorqueValueSet", True)
                widgets['status'].config(text=f"Triggered (T={self.torque3.get()} Nm)", foreground="orange")
        else:
            self.plc.write(tag, True)
            widgets['status'].config(text="Triggered, awaiting response...", foreground="orange")

        self.status_label.config(text=f"Triggered Step {step_idx}")

    def _send_ok(self, idx):
        widgets = self.step_widgets[idx]

        if idx == 0:
            sendDataFromQRCodeServerToIOServer(self.io.redis, qrCodeStatus=ok)
        elif idx in self.vision_states:
            sendDataFromCameraServerToIOServer(self.io.redis, result=ok,
                                               currentMachineState=self.vision_states[idx])

        widgets['status'].config(text="OK clicked → Moving to next step", foreground="green")
        self.status_label.config(text=f"Step {idx}: OK sent")

    def _send_notok(self, idx):
        widgets = self.step_widgets[idx]

        if idx == 0:
            sendDataFromQRCodeServerToIOServer(self.io.redis, qrCodeStatus=notok)
        elif idx in self.vision_states:
            sendDataFromCameraServerToIOServer(self.io.redis, result=notok,
                                               currentMachineState=self.vision_states[idx])

        widgets['status'].config(text="NotOK clicked → Staying at step", foreground="red")
        self.status_label.config(text=f"Step {idx}: NotOK - staying at current step")

    def _reset(self):
        self.plc.reset_all()
        self.io.redis.flushdb()
        self.io.machineState = MachineStateMachine()  # Reset state machine
        self.current_step = 0

        # Reset all status labels
        for idx, widgets in self.step_widgets.items():
            widgets['status'].config(text="Waiting...", foreground="gray")

        self._update_button_states()
        self.status_label.config(text="Reset complete - starting fresh")

    def _auto_cycle(self):
        """Run complete cycle automatically."""
        def cycle():
            steps = [
                ("PLC_PC_CheckQRCode", "vision", 0),
                ("PLC_PC_CheckKnuckle", "vision", 1),
                ("PLC_PC_CheckHub", "vision", 2),
                ("PLC_PC_CheckHubAndSecondBearing", "vision", 3),
                ("PLC_PC_CheckNutAndPlateWasher", "vision", 4),
                ("torque1", "torque", 5),
                ("PLC_PC_Station3RotationDone", "signal", 6),
                ("PLC_PC_CheckNoCapBunk", "vision", 7),
                ("PLC_PC_ComponentPressDone", "signal", 8),
                ("PLC_PC_CheckNoBunk", "vision", 9),
                ("torque2", "torque", 10),
                ("PLC_PC_CheckSplitPinAndWasher", "vision", 11),
                ("PLC_PC_CheckCap", "vision", 12),
                ("PLC_PC_CheckBunk", "vision", 13),
                ("PLC_PC_CapPressDone", "signal", 14),
                ("torque3", "torque", 15),
            ]

            for tag, step_type, idx in steps:
                self.root.after(0, lambda i=idx: self.status_label.config(text=f"Auto: Running Step {i}"))
                self._trigger(tag, step_type, idx)
                time.sleep(0.3)
                if step_type == "vision":
                    self._send_ok(idx)
                time.sleep(1.0)
                self.root.after(0, self._update_button_states)

            self.root.after(0, lambda: self.status_label.config(text="Auto cycle complete! All 16 steps done."))

        threading.Thread(target=cycle, daemon=True).start()

    def _start_monitor(self):
        def monitor():
            while True:
                try:
                    state = self.io.machineState.getCurrentState()
                    self.state_label.config(text=f"State: {state}")

                    # Update button states periodically
                    self.root.after(0, self._update_button_states)

                    tags = self.plc.get_all()
                    text = "PLC Tags:\n" + "=" * 50 + "\n"
                    for tag, value in sorted(tags.items()):
                        text += f"{tag}: {value}\n"

                    self.tag_text.delete(1.0, tk.END)
                    self.tag_text.insert(tk.END, text)
                except:
                    pass
                time.sleep(0.5)

        threading.Thread(target=monitor, daemon=True).start()

    def _start_heartbeat(self):
        def hb():
            while True:
                try:
                    sendCombinedHeartbeatFromHeartbeatServerToIOServer(self.io.redis, ALIVE)
                except:
                    pass
                time.sleep(1)
        threading.Thread(target=hb, daemon=True).start()


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("MockPLC IOServer Test - Complete Simulation")
    print("=" * 60)
    print("Starting MockPLC IOServer...")

    io = SimpleMockIOServer()
    time.sleep(1)

    print("Starting GUI...")
    root = tk.Tk()
    app = MockPLCTestGUI(root, io)
    root.mainloop()