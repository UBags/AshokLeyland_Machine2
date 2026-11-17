"""
Tests the real frontend (FE server) over a full Machine 2 cycle, either from image files you pick (checks run locally) or with the live camera via CameraProcessorServer; run from project root: python -m test.machine1_dryrun.Test_FEServer_With_Live_Camera_UI

Start the frontend first. This Tk UI plays the QR server and the IO server (and, in file mode, the camera server):
  - QR step: sends the QR code to the FE (qrcode2fe) and to the camera server (qrcode2camera).
  - Camera steps, file mode: loads the chosen image, runs the real camera/Check*.py algorithm locally and sends
    image + ok/notok + READ_TAKE_PICTURE_FOR_CHECKING_* state to the FE (camera2fe).
  - Camera steps, "Use Live Camera" mode: sends a takePicture command (io2camera) for the READ_TAKE_PICTURE_FOR_CHECKING_*
    state to a running CameraProcessorServer (camera/CameraProcessorServer.py), which takes the pictures, runs the checks
    and sends the result to the FE (camera2fe) and to the IO server (camera2io). This UI reads camera2io to learn the
    result, so do NOT run the real IOServer at the same time.
  - IO steps: Tightening Torque 1, Component Press, Tightening Torque 2, Cap Press, and finally Free Rotation Torque
    (READ_FREE_ROTATION_TORQUE_1, action=moveAheadToNextComponent), sent to the FE on io2fe.
There is no free-rotation step on Machine 2 (free rotation happens during tightening at station 2).

Every frontend reader trims its queue to the latest message, so after each message the UI disables its buttons
until the FE (or, for take-picture commands, the camera server) has consumed it.
"""

import sys
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]  # <root>/test/<dry-run folder>/this file
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import random
import cv2
import numpy as np
from redis import Redis
from typing import Union, Tuple, Dict, Optional
import copy
import time
import importlib

# Import project modules
from statemachine.StateMachine import MachineState
from Configuration import CosThetaConfigurator
from utils.RedisUtils import (
    publishCycleSettingsForQRCode, CYCLE_SETTINGS_KEY_PREFIX,
    sendDataFromQRCodeServerToFEServer,
    sendComponentQRCodeFromQRCodeServerToCameraServer,
    sendDataFromCameraServerToFEServer,
    sendDataFromIOServerToFEServer,
    sendDataFromIOServerToCameraServer,
    readDataInIOServerFromCameraServer,
    takePictureCommand,
    ok, notok, updateValueCommand, moveAheadToNextComponent,
    qrcode2feq, camera2feq, io2feq, io2cameraq, camera2ioq,
    knucklePictureKeyString,
    hubAndBottomBearingPictureKeyString,
    topBearingPictureKeyString,
    nutAndPlateWasherPictureKeyString,
    componentPressBungPictureKeyString,
    splitPinAndWasherPictureKeyString,
    noBungPictureKeyString,
    capPictureKeyString,
    capPressBungPictureKeyString
)

# Import check modules - using the class-based approach. Some need torch/models; a module that cannot be imported
# only disables its own file-based check (the error is shown when that check is run).
CHECK_IMPORT_ERRORS: Dict[str, str] = {}

def _importCheck(name: str):
    try:
        return getattr(importlib.import_module(f"camera.{name}"), name)
    except Exception as e:
        CHECK_IMPORT_ERRORS[name] = f"{type(e).__name__}: {e}"
        print(f"Warning: camera.{name} unavailable ({CHECK_IMPORT_ERRORS[name]}); its file-based check is disabled")
        return None

CheckKnuckle = _importCheck("CheckKnuckle")
CheckHubAndBottomBearing = _importCheck("CheckHubAndBottomBearing")
CheckTopBearing = _importCheck("CheckTopBearing")
CheckNutAndPlateWasher = _importCheck("CheckNutAndPlateWasher")
CheckBung = _importCheck("CheckBung")
CheckNoBung = _importCheck("CheckNoBung")
CheckSplitPinAndWasher = _importCheck("CheckSplitPinAndWasher")
CheckCap = _importCheck("CheckCap")
CheckNoCapBung = _importCheck("CheckNoCapBung")

def _requireCheck(checkClass, name: str):
    if checkClass is None:
        raise RuntimeError(f"camera.{name} could not be imported: {CHECK_IMPORT_ERRORS.get(name)}")
    return checkClass

# QR Code constants
DOST_QR_CODE = "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"
DOSTPLUS_QR_CODE = "8201206$400112VA1C$11770$09.03.2025$C5211701$MEK$40Cr4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"

def createDisplayString(qrCode: str, printQRCode: bool = False) -> Union[str, None]:
    """Generates a human-readable display string for the QR code, checking for duplicates."""
    if (qrCode is None) or qrCode == "":
        displayString = ""
        return displayString
    copyOfQRCode = copy.deepcopy(qrCode)
    tempDisplayValue: str = ""
    remainingQrCode: str = copy.deepcopy(qrCode)
    for key, value in CosThetaConfigurator.getInstance().getQRCodePartMappingPatterns().items():
        if copyOfQRCode.startswith(key):
            tempDisplayValue = f"{value}"
            remainingQrCode = copyOfQRCode.removeprefix(f"{key}$")
            break
    remainingParts = remainingQrCode.split("$")
    if len(remainingParts) >= 2:
        suffix = f" : {remainingParts[0]} : {remainingParts[1]}"
        tempDisplayValue = f"{tempDisplayValue}{suffix}"
    displayString = tempDisplayValue
    return displayString

# Steps a bypassed cycle skips (Machine Setting "Bypass Component Press and Second Tightening")
BYPASSED_STATES = (MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_COMPONENT_PRESS, MachineState.READ_COMPONENT_PRESS_DONE,
                   MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNG, MachineState.READ_TIGHTENING_TORQUE_2)


class TestStep:
    """Represents a single step in the test pipeline."""

    def __init__(self, name: str, state: MachineState, step_type: str, check_name: str = None):
        self.name = name
        self.state = state  # The frontend state this step's message is meant for (always a READ_* state)
        self.step_type = step_type  # 'qrcode', 'camera', 'torque', 'torque_final', 'done'
        self.check_name = check_name  # Name of the check to run
        self.file_path = None  # For camera checks - selected file
        self.result = None  # Last result


class ImageProcessorTestUI:
    """Main test UI application."""

    currentPictures : Dict[str, np.ndarray | None] = {knucklePictureKeyString : None,
                                                      hubAndBottomBearingPictureKeyString : None,
                                                      topBearingPictureKeyString : None,
                                                      nutAndPlateWasherPictureKeyString : None,
                                                      splitPinAndWasherPictureKeyString : None,
                                                      capPictureKeyString : None,
                                                      componentPressBungPictureKeyString : None,
                                                      noBungPictureKeyString : None,
                                                      capPressBungPictureKeyString : None}

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Ashok Leyland Image Processor Test UI")
        self.root.geometry("900x800")

        # Redis connection
        self.redis_connection: Union[Redis, None] = None
        self.connect_to_redis()

        # Current QR code
        self.current_qr_code: str = ""

        # Bypass of the component press and second tightening (Machine Setting per model, 2026-11-13): taken when the
        # QR code is sent; then this window plays the IOServer as when it wrote PC_PLC_Skip_Second_Tightening = True
        self.bypass_var = tk.BooleanVar(self.root, value=False)
        self.cycle_bypass: bool = False

        # Current state index (which step we're on)
        self.current_step_index: int = 0

        # Storage for intermediate images (needed for comparison checks)
        self.stored_images: Dict[str, np.ndarray] = {}

        # Storage for bearing geometry (from CheckTopBearing, used by CheckNutAndPlateWasher)
        self.bearing_geometry: Optional[dict] = None

        # Live camera mode flag
        self.use_live_camera = tk.BooleanVar(self.root, value=False)

        # Set while the last message has not yet been consumed (buttons are disabled meanwhile)
        self.waiting_for: Optional[str] = None

        # Define all test steps in order
        self.steps = self._create_steps()

        # UI widgets storage
        self.step_widgets = {}

        # Build UI
        self._build_ui()
        self._update_button_states()

    def _create_steps(self) -> list:
        """Create the ordered list of the 15 Machine 2 operator steps (in frontend order)."""
        return [
            TestStep("QR Code", MachineState.READ_QR_CODE, "qrcode"),
            TestStep("Knuckle", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, "camera",
                     check_name="knuckle"),
            TestStep("Hub & Bottom Bearing", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING,
                     "camera", check_name="hub"),
            TestStep("Top Bearing", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, "camera",
                     check_name="top_bearing"),
            TestStep("Nut & Plate Washer", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER, "camera",
                     check_name="nut_platewasher"),
            TestStep("Tightening Torque 1", MachineState.READ_TIGHTENING_TORQUE_1, "torque"),
            TestStep("Bung for Component Press", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_COMPONENT_PRESS,
                     "camera", check_name="no_cap_bung"),
            TestStep("Component Press", MachineState.READ_COMPONENT_PRESS_DONE, "done"),
            TestStep("No Bung (after component press)", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNG, "camera",
                     check_name="no_bung"),
            TestStep("Tightening Torque 2", MachineState.READ_TIGHTENING_TORQUE_2, "torque"),
            TestStep("Split Pin & Washer", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER, "camera",
                     check_name="splitpin_washer"),
            TestStep("Cap", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP, "camera",
                     check_name="cap"),
            TestStep("Bung for Cap Press", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG_FOR_CAP_PRESS, "camera",
                     check_name="bung"),
            TestStep("Cap Press", MachineState.READ_CAP_PRESS_DONE, "done"),
            # Final step - the frontend ends the cycle on this message and sends the record to the DB server
            TestStep("Free Rotation Torque", MachineState.READ_FREE_ROTATION_TORQUE_1, "torque_final"),
        ]

    def connect_to_redis(self):
        """Establish Redis connection."""
        try:
            host = CosThetaConfigurator.getInstance().getRedisHost()
            port = CosThetaConfigurator.getInstance().getRedisPort()
            self.redis_connection = Redis(host, port, retry_on_timeout=True)
            self.redis_connection.ping()
        except Exception as e:
            messagebox.showerror("Redis Error", f"Could not connect to Redis: {e}")
            self.redis_connection = None

    def _build_ui(self):
        """Build the main UI."""
        # Main frame with scrollbar
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Canvas for scrolling
        canvas = tk.Canvas(main_frame)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Pack scrollbar and canvas
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        # Status bar at top
        status_frame = ttk.LabelFrame(scrollable_frame, text="Status", padding="5")
        status_frame.pack(fill="x", padx=5, pady=5)

        self.status_label = ttk.Label(status_frame, text="Ready - Select QR Code to begin")
        self.status_label.pack(side="left")

        self.state_label = ttk.Label(status_frame, text="Current Step: QR Code")
        self.state_label.pack(side="right")

        # Redis status
        redis_status = "Connected" if self.redis_connection else "Disconnected"
        self.redis_label = ttk.Label(status_frame, text=f"Redis: {redis_status}")
        self.redis_label.pack(side="right", padx=20)

        bypass_frame = ttk.Frame(scrollable_frame, padding="5")
        bypass_frame.pack(fill="x", padx=5)
        ttk.Checkbutton(bypass_frame, variable=self.bypass_var,
                        text="Bypass Component Press and Second Tightening (taken when the QR code is sent: the bung, "
                             "component press, bung removal and Torque 2 steps are skipped)").pack(side="left")

        # Live Camera Mode checkbox
        mode_frame = ttk.LabelFrame(scrollable_frame, text="Camera Mode", padding="5")
        mode_frame.pack(fill="x", padx=5, pady=5)

        self.live_camera_checkbox = ttk.Checkbutton(
            mode_frame,
            text="Use Live Camera (triggers CameraProcessorServer instead of loading files)",
            variable=self.use_live_camera,
            command=self._on_camera_mode_changed
        )
        self.live_camera_checkbox.pack(side="left")

        self.camera_mode_label = ttk.Label(mode_frame, text="Mode: File-based", foreground="blue")
        self.camera_mode_label.pack(side="right")

        # Build each step's UI
        for idx, step in enumerate(self.steps):
            self._build_step_ui(scrollable_frame, idx, step)

        # Reset button at bottom
        reset_frame = ttk.Frame(scrollable_frame, padding="10")
        reset_frame.pack(fill="x", padx=5, pady=10)

        reset_btn = ttk.Button(reset_frame, text="Reset All", command=self._reset_all)
        reset_btn.pack(side="right")

    def _on_camera_mode_changed(self):
        """Handle camera mode checkbox change."""
        if self.use_live_camera.get():
            self.camera_mode_label.config(text="Mode: LIVE CAMERA", foreground="green")
            self.status_label.config(text="Live Camera Mode - CameraProcessorServer will handle processing")
        else:
            self.camera_mode_label.config(text="Mode: File-based", foreground="blue")
            self.status_label.config(text="File Mode - Select files and run checks locally")
        self._update_button_states()

    def _build_step_ui(self, parent: ttk.Frame, idx: int, step: TestStep):
        """Build UI for a single step."""
        frame = ttk.LabelFrame(parent, text=f"Step {idx + 1}: {step.name}", padding="5")
        frame.pack(fill="x", padx=5, pady=3)

        widgets = {"frame": frame}

        if step.step_type == "qrcode":
            # QR Code buttons
            btn_frame = ttk.Frame(frame)
            btn_frame.pack(fill="x")

            dost_btn = ttk.Button(btn_frame, text="Send DOST QR Code",
                                  command=lambda: self._send_qr_code(DOST_QR_CODE, "DOST"))
            dost_btn.pack(side="left", padx=5)
            widgets["dost_btn"] = dost_btn

            dostplus_btn = ttk.Button(btn_frame, text="Send DOSTPLUS QR Code",
                                      command=lambda: self._send_qr_code(DOSTPLUS_QR_CODE, "DOSTPLUS"))
            dostplus_btn.pack(side="left", padx=5)
            widgets["dostplus_btn"] = dostplus_btn

            # QR display
            self.qr_display = ttk.Label(frame, text="No QR Code selected")
            self.qr_display.pack(side="left", padx=20)
            widgets["qr_display"] = self.qr_display

        elif step.step_type == "camera":
            # Camera check - file selection and run button
            btn_frame = ttk.Frame(frame)
            btn_frame.pack(fill="x")

            # File selection button (only visible in file-based mode)
            select_btn = ttk.Button(btn_frame, text="Select File",
                                    command=lambda s=step, i=idx: self._select_file(s, i))
            select_btn.pack(side="left", padx=5)
            widgets["select_btn"] = select_btn

            # Run button
            run_btn = ttk.Button(btn_frame, text="Run Check",
                                 command=lambda s=step, i=idx: self._run_camera_check(s, i))
            run_btn.pack(side="left", padx=5)
            widgets["run_btn"] = run_btn

            # File label (shows file path in file mode, or "Live Camera" in live mode)
            file_label = ttk.Label(frame, text="No file selected")
            file_label.pack(side="left", padx=10)
            widgets["file_label"] = file_label

            # Result label
            result_label = ttk.Label(frame, text="")
            result_label.pack(side="right", padx=10)
            widgets["result_label"] = result_label

        elif step.step_type in ("torque", "torque_final"):
            # Torque step
            btn_frame = ttk.Frame(frame)
            btn_frame.pack(fill="x")

            # Same ranges as Test_FEServer_Mock_With_Image_Files_UI (tightening setpoint is ~19 Nm on Machine 2)
            if step.step_type == "torque_final":
                min_val, max_val = 2.0, 6.0
            else:
                min_val, max_val = 17.0, 19.0

            send_btn = ttk.Button(btn_frame, text=f"Send Random ({min_val}-{max_val})",
                                  command=lambda s=step, i=idx, mn=min_val, mx=max_val: self._send_torque(s, i, mn, mx))
            send_btn.pack(side="left", padx=5)
            widgets["send_btn"] = send_btn

            value_label = ttk.Label(frame, text="")
            value_label.pack(side="right", padx=10)
            widgets["value_label"] = value_label

        elif step.step_type == "done":
            # Done step (simple signal)
            btn_frame = ttk.Frame(frame)
            btn_frame.pack(fill="x")

            done_btn = ttk.Button(btn_frame, text="Send Done",
                                  command=lambda s=step, i=idx: self._send_done(s, i))
            done_btn.pack(side="left", padx=5)
            widgets["done_btn"] = done_btn

            status_label = ttk.Label(frame, text="")
            status_label.pack(side="right", padx=10)
            widgets["status_label"] = status_label

        self.step_widgets[idx] = widgets

    def _update_button_states(self):
        """Update button states based on current step and camera mode."""
        is_live_camera = self.use_live_camera.get()

        for idx, step in enumerate(self.steps):
            widgets = self.step_widgets[idx]
            # Nothing can be sent while the previous message is still waiting to be consumed
            is_current = (idx == self.current_step_index) and (self.waiting_for is None)

            if step.step_type == "qrcode":
                # Enable QR buttons only if current step
                state = ["!disabled"] if is_current else ["disabled"]
                widgets["dost_btn"].state(state)
                widgets["dostplus_btn"].state(state)

            elif step.step_type == "camera":
                # In live camera mode, hide file selection; in file mode, show it
                if is_live_camera:
                    widgets["select_btn"].state(["disabled"])
                    widgets["file_label"].config(text="Using Live Camera")
                    # Enable run button if current step
                    run_state = ["!disabled"] if is_current else ["disabled"]
                    widgets["run_btn"].state(run_state)
                else:
                    # File mode - enable select if current, run if current and file selected
                    select_state = ["!disabled"] if is_current else ["disabled"]
                    widgets["select_btn"].state(select_state)

                    has_file = step.file_path is not None
                    run_state = ["!disabled"] if (is_current and has_file) else ["disabled"]
                    widgets["run_btn"].state(run_state)

                    if not step.file_path:
                        widgets["file_label"].config(text="No file selected")

            elif step.step_type in ("torque", "torque_final"):
                state = ["!disabled"] if is_current else ["disabled"]
                widgets["send_btn"].state(state)

            elif step.step_type == "done":
                state = ["!disabled"] if is_current else ["disabled"]
                widgets["done_btn"].state(state)

        # Update state label
        if self.current_step_index < len(self.steps):
            current_step = self.steps[self.current_step_index]
            waiting = f" (waiting for {self.waiting_for})" if self.waiting_for else ""
            self.state_label.config(text=f"Current Step {self.current_step_index + 1}/{len(self.steps)}: {current_step.name}{waiting}")
        else:
            self.state_label.config(text="All steps complete!")

    # ---------------------------------------------------------------------------------------------
    # Message ordering: the FE trims each queue to the latest message, so wait until it has been read
    # ---------------------------------------------------------------------------------------------

    POLL_MS = 100

    def _queue_consumed(self, queueName: str) -> bool:
        try:
            return self.redis_connection.xlen(queueName) == 0
        except Exception:
            return False

    def _wait_until_consumed(self, queueName: str, who: str = "FE"):
        """Disable sending until the consumer has read the message just put on queueName."""
        self.waiting_for = who
        self._update_button_states()

        def poll():
            if self.waiting_for is None:
                return
            if self._queue_consumed(queueName):
                self.waiting_for = None
                self._update_button_states()
            else:
                self.root.after(self.POLL_MS, poll)
        self.root.after(self.POLL_MS, poll)

    def _advance(self):
        """Move to the next step; after the final step start the next cycle at the QR step."""
        self.current_step_index += 1
        # bypassed cycle: after Torque 1 straight to the split pin picture (as the IOServer and the PLC)
        while self.cycle_bypass and self.current_step_index < len(self.steps) and \
                self.steps[self.current_step_index].state in BYPASSED_STATES:
            self._mark_bypassed(self.current_step_index)
            self.current_step_index += 1
        if self.current_step_index >= len(self.steps):
            self.current_step_index = 0
            self.status_label.config(text="Cycle complete - FE sends the record to the DB server. Send the next QR Code.")

    def _mark_bypassed(self, idx: int):
        step, widgets = self.steps[idx], self.step_widgets[idx]
        label = {"camera": "result_label", "torque": "value_label", "torque_final": "value_label",
                 "done": "status_label"}.get(step.step_type)
        if label and label in widgets:
            widgets[label].config(text="Bypassed (Machine Setting)", foreground="gray")

    def _publish_bypass(self, qr_code: str):
        """What the IOServer publishes in state 2: cycleSettings:<QR> with the bypass (the frontend marks the 4 tiles
        'Bypassed'); without the bypass the key is removed."""
        self.cycle_bypass = bool(self.bypass_var.get())
        for widgets in self.step_widgets.values():          # "Bypassed" marks of the previous cycle
            for label in ("result_label", "value_label", "status_label"):
                if label in widgets and str(widgets[label].cget("text")).startswith("Bypassed"):
                    widgets[label].config(text="", foreground="")
        if self.cycle_bypass:
            publishCycleSettingsForQRCode(self.redis_connection, qr_code, {"bypass_press_and_tightening2": True})
        else:
            self.redis_connection.delete(f"{CYCLE_SETTINGS_KEY_PREFIX}{qr_code}")

    def _send_qr_code(self, qr_code: str, model_name: str):
        """Send QR code to FE Server and Camera Server."""
        if not self.redis_connection:
            messagebox.showerror("Error", "Redis not connected")
            return

        try:
            self.status_label.config(text=f"Sending {model_name} QR Code...")
            self.root.update()

            # Send to FE Server for display
            display_string = createDisplayString(qr_code)
            self._publish_bypass(qr_code)
            sendDataFromQRCodeServerToFEServer(
                redisConnection=self.redis_connection,
                qrCode=qr_code,
                displayString=display_string,
                qrCodeStatus=ok,
                aProducer="TestUI"
            )

            # Send to Camera Server so it knows the component
            sendComponentQRCodeFromQRCodeServerToCameraServer(
                redisConnection=self.redis_connection,
                componentQRCode=qr_code,
                aProducer="TestUI"
            )

            # Store locally
            self.current_qr_code = qr_code

            # Update UI
            self.qr_display.config(text=f"{model_name}: {display_string}")
            self.status_label.config(text=f"{model_name} QR Code sent successfully")
            self._advance()
            self._wait_until_consumed(qrcode2feq)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to send QR code: {e}")
            self.status_label.config(text=f"Error: {e}")

    def _select_file(self, step: TestStep, step_idx: int):
        """Open file dialog to select an image file."""
        file_path = filedialog.askopenfilename(
            title=f"Select image for {step.name}",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")]
        )

        if file_path:
            step.file_path = file_path
            # Update label with truncated path
            display_path = file_path if len(file_path) < 50 else f"...{file_path[-47:]}"
            self.step_widgets[step_idx]["file_label"].config(text=display_path)
            self._update_button_states()

    def _run_camera_check(self, step: TestStep, step_idx: int):
        """Run the camera check - either via CameraProcessorServer (live) or locally (file)."""
        if not self.redis_connection:
            messagebox.showerror("Error", "Redis not connected")
            return

        if self.use_live_camera.get():
            # NEW: Live camera mode - send command to CameraProcessorServer
            self._run_live_camera_check(step, step_idx)
        else:
            # Original: File-based mode
            if not step.file_path:
                messagebox.showerror("Error", "No file selected")
                return
            self._run_file_based_check(step, step_idx)

    LIVE_RESULT_TIMEOUT_S = 60

    def _run_live_camera_check(self, step: TestStep, step_idx: int):
        """Send a takePicture command to CameraProcessorServer and wait for its result on camera2io.

        CameraProcessorServer sends the pictures and ok/notok to the FE itself (camera2fe); the result it also sends
        to the IO server (camera2io) is read here to decide whether to advance, as the IO server would.
        """
        try:
            self.step_widgets[step_idx]["run_btn"].state(["disabled"])
            self.status_label.config(text=f"Sending takePicture command for {step.name}...")
            self.root.update()

            self.redis_connection.xtrim(camera2ioq, 0)     # drop any stale result
            sendDataFromIOServerToCameraServer(
                redisConnection=self.redis_connection,
                currentMachineState=step.state,   # READ_TAKE_PICTURE_FOR_CHECKING_* tells it which check to run
                action=takePictureCommand,
                aProducer="TestUI"
            )

            self.step_widgets[step_idx]["result_label"].config(text="Command sent - waiting for camera server",
                                                               foreground="blue")
            self.status_label.config(
                text=f"takePicture command sent for {step.name}. CameraProcessorServer is processing...")
            self.waiting_for = "camera server result"
            self._update_button_states()

            threading.Thread(target=self._wait_for_live_camera_result, args=(step, step_idx), daemon=True).start()

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.waiting_for = None
            self.step_widgets[step_idx]["result_label"].config(text="ERROR", foreground="red")
            self.status_label.config(text=f"Error: {e}")
            messagebox.showerror("Error", f"Failed to send command: {e}")
            self._update_button_states()

    def _wait_for_live_camera_result(self, step: TestStep, step_idx: int):
        """Runs in a thread: read the camera server's result from camera2io."""
        conn = Redis(CosThetaConfigurator.getInstance().getRedisHost(), CosThetaConfigurator.getInstance().getRedisPort())
        deadline = time.time() + self.LIVE_RESULT_TIMEOUT_S
        while time.time() < deadline:
            if conn.xlen(camera2ioq) > 0:
                resultState, result = readDataInIOServerFromCameraServer(conn, block=500)
                self.root.after(0, lambda: self._handle_live_camera_result(step, step_idx, result, resultState))
                return
            time.sleep(0.1)
        self.root.after(0, lambda: self._handle_live_camera_result(step, step_idx, None, MachineState.INVALID_STATE))

    def _handle_live_camera_result(self, step: TestStep, step_idx: int, result: Optional[bool], resultState: MachineState):
        self.waiting_for = None
        if result is None:
            self.step_widgets[step_idx]["result_label"].config(text="NO RESPONSE", foreground="red")
            self.status_label.config(text=f"No result from CameraProcessorServer within {self.LIVE_RESULT_TIMEOUT_S} s "
                                          f"(io2camera pending: {self.redis_connection.xlen(io2cameraq)})")
            self._update_button_states()
            return
        self._handle_check_result(step, step_idx, result)
        if resultState == MachineState.INVALID_STATE:
            # CameraProcessorServer reports INVALID_STATE when it could not take pictures (e.g. camera not connected)
            self.status_label.config(text=f"{step.name}: camera server could not take pictures (camera not connected?) - retry")

    def _run_file_based_check(self, step: TestStep, step_idx: int):
        """Run the camera check from file in a separate thread (original behavior)."""
        # Disable button during processing
        self.step_widgets[step_idx]["run_btn"].state(["disabled"])
        self.status_label.config(text=f"Running {step.name} check...")
        self.root.update()

        # Run in thread to avoid UI freeze
        thread = threading.Thread(
            target=self._execute_file_based_check,
            args=(step, step_idx),
            daemon=True
        )
        thread.start()

    def _execute_file_based_check(self, step: TestStep, step_idx: int):
        """Execute the camera check from file (runs in thread)."""
        try:
            # Load image
            image = cv2.imread(step.file_path)
            if image is None:
                self.root.after(0, lambda: self._handle_check_error(step_idx, "Could not load image"))
                return

            # Run the appropriate check based on check_name
            result, processed_image = self._run_check(step.check_name, image)

            # Store intermediate images if needed for later comparison checks
            self._store_intermediate_image(step.check_name, image)

            # Convert result to ok/notok
            result_str = ok if result else notok

            # Send to FE Server
            sendDataFromCameraServerToFEServer(
                redisConnection=self.redis_connection,
                originalImage=image,
                processedImage=processed_image if processed_image is not None else image,
                result=result_str,
                currentMachineState=step.state,
                aProducer="TestUI"
            )

            # Update UI in main thread; then wait for the FE to read the picture before allowing the next send
            def done():
                self._handle_check_result(step, step_idx, result)
                self._wait_until_consumed(camera2feq)
            self.root.after(0, done)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.root.after(0, lambda err=str(e): self._handle_check_error(step_idx, err))

    def _run_check(self, check_name: str, image: np.ndarray) -> Tuple[bool, Optional[np.ndarray]]:
        """
        Run the appropriate check function based on check_name.

        Returns:
            Tuple of (success: bool, processed_image: np.ndarray or None)
        """
        if check_name == "knuckle":
            # CheckKnuckle.checkKnuckle(image, qr_code) -> (processed_image, result)
            print(f"Invoking checkKnuckle with {self.current_qr_code}")
            ImageProcessorTestUI.currentPictures[knucklePictureKeyString] = image
            processed_image, result = _requireCheck(CheckKnuckle, "CheckKnuckle").checkKnuckle(anImage=image, currentPictures=ImageProcessorTestUI.currentPictures, componentQRCode=self.current_qr_code)
            return result, processed_image

        elif check_name == "hub":
            # CheckHubAndBottomBearing.checkHubAndBottomBearing(image, qr_code) -> (result_image, success, winning_gamma)
            ImageProcessorTestUI.currentPictures[hubAndBottomBearingPictureKeyString] = image
            result_image, success, _ = _requireCheck(CheckHubAndBottomBearing, "CheckHubAndBottomBearing").checkHubAndBottomBearing(anImage=image, componentQRCode=self.current_qr_code, use_smart_gamma=True)
            return success, result_image

        elif check_name == "top_bearing":
            # CheckTopBearing.checkTopBearing(...) -> (annotated_image, is_bearing_present, geometry)
            ImageProcessorTestUI.currentPictures[topBearingPictureKeyString] = image
            annotated_image, is_present, geometry = _requireCheck(CheckTopBearing, "CheckTopBearing").checkTopBearing(
                anImage=image,
                currentPictures=ImageProcessorTestUI.currentPictures,
                componentQRCode=self.current_qr_code
            )
            # Store geometry for washer detection in the nut_platewasher step
            self.bearing_geometry = geometry
            return is_present, annotated_image

        elif check_name == "nut_platewasher":
            # CheckNutAndPlateWasher.checkNutAndPlateWasher(image, currentPictures, qr_code, bearing_geometry)
            ImageProcessorTestUI.currentPictures[nutAndPlateWasherPictureKeyString] = image
            processed_image, result = _requireCheck(CheckNutAndPlateWasher, "CheckNutAndPlateWasher").checkNutAndPlateWasher(
                anImage=image,
                currentPictures=ImageProcessorTestUI.currentPictures,
                componentQRCode=self.current_qr_code,
                bearing_geometry=self.bearing_geometry
            )
            return result, processed_image

        elif check_name == "no_cap_bung":
            # CheckNoCapBung.checkNoCapBung(image, qr_code) -> (result_image, success, winning_gamma)
            ImageProcessorTestUI.currentPictures[componentPressBungPictureKeyString] = image
            result_image, success, _ = _requireCheck(CheckNoCapBung, "CheckNoCapBung").checkNoCapBung(anImage=image, componentQRCode=self.current_qr_code)
            return success, result_image

        elif check_name == "no_bung":
            # CheckNoBung.checkNoBung(image, currentPictures, qr_code) -> (annotated_image, nut_present)
            ImageProcessorTestUI.currentPictures[noBungPictureKeyString] = image
            processed_image, result = _requireCheck(CheckNoBung, "CheckNoBung").checkNoBung(anImage=image, currentPictures=ImageProcessorTestUI.currentPictures,
                                                              componentQRCode=self.current_qr_code)
            return result, processed_image

        elif check_name == "splitpin_washer":
            # CheckSplitPinAndWasher.checkSplitPinAndWasher(image, qr_code) -> (processed_image, result)
            ImageProcessorTestUI.currentPictures[splitPinAndWasherPictureKeyString] = image
            processed_image, result = _requireCheck(CheckSplitPinAndWasher, "CheckSplitPinAndWasher").checkSplitPinAndWasher(anImage=image, currentPictures=ImageProcessorTestUI.currentPictures,
                                                           componentQRCode=self.current_qr_code)
            return result, processed_image

        elif check_name == "cap":
            # CheckCap.checkCap(image, currentPictures, qr_code)
            ImageProcessorTestUI.currentPictures[capPictureKeyString] = image
            processed_image, result = _requireCheck(CheckCap, "CheckCap").checkCap(anImage=image, currentPictures=ImageProcessorTestUI.currentPictures,
                                                           componentQRCode=self.current_qr_code)
            return result, processed_image

        elif check_name == "bung":
            # CheckBung.checkBung(image, qr_code) -> (result_image, success, winning_gamma)
            ImageProcessorTestUI.currentPictures[capPressBungPictureKeyString] = image
            result_image, success, _ = _requireCheck(CheckBung, "CheckBung").checkBung(anImage=image, componentQRCode=self.current_qr_code)
            return success, result_image

        else:
            raise ValueError(f"Unknown check name: {check_name}")

    def _store_intermediate_image(self, check_name: str, image: np.ndarray):
        """Store images needed for later comparison checks."""
        if check_name == "hub":
            self.stored_images[hubAndBottomBearingPictureKeyString] = image.copy()
        elif check_name == "nut_platewasher":
            self.stored_images[nutAndPlateWasherPictureKeyString] = image.copy()
        elif check_name == "cap":
            self.stored_images[capPictureKeyString] = image.copy()

    def _handle_check_result(self, step: TestStep, step_idx: int, result: bool):
        """Handle camera check result (runs in main thread)."""
        step.result = result

        result_text = "PASS ✓" if result else "FAIL ✗"
        result_color = "green" if result else "red"

        self.step_widgets[step_idx]["result_label"].config(text=result_text, foreground=result_color)

        if result:
            self.status_label.config(text=f"{step.name} check PASSED")
            self._advance()
        else:
            self.status_label.config(text=f"{step.name} check FAILED - retry or select new image")

        self._update_button_states()

    def _handle_check_error(self, step_idx: int, error_msg: str):
        """Handle camera check error (runs in main thread)."""
        self.step_widgets[step_idx]["result_label"].config(text="ERROR", foreground="red")
        self.status_label.config(text=f"Error: {error_msg}")
        messagebox.showerror("Check Error", error_msg)
        self._update_button_states()

    def _send_torque(self, step: TestStep, step_idx: int, min_val: float, max_val: float):
        """Send random torque value to FE server."""
        if not self.redis_connection:
            messagebox.showerror("Error", "Redis not connected")
            return

        try:
            # Generate random value
            value = round(random.uniform(min_val, max_val), 2)

            self.status_label.config(text=f"Sending {step.name}: {value}")
            self.root.update()

            # Send to FE Server. The final free-rotation torque ends the cycle, as the IO server does it.
            is_final = (step.step_type == "torque_final")
            sendDataFromIOServerToFEServer(
                redisConnection=self.redis_connection,
                currentMachineState=step.state,
                moveToState=(MachineState.READ_QR_CODE if is_final
                             else MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER
                             if self.cycle_bypass and step.state == MachineState.READ_TIGHTENING_TORQUE_1
                             else MachineState.INVALID_STATE),
                result=ok,
                action=moveAheadToNextComponent if is_final else updateValueCommand,
                value=value,
                aProducer="TestUI"
            )

            # Update UI
            self.step_widgets[step_idx]["value_label"].config(text=f"Sent: {value}")
            self.status_label.config(text=f"{step.name} sent: {value}")
            self._advance()
            self._wait_until_consumed(io2feq)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to send torque: {e}")
            self.status_label.config(text=f"Error: {e}")

    def _send_done(self, step: TestStep, step_idx: int):
        """Send 'done' signal to FE server."""
        if not self.redis_connection:
            messagebox.showerror("Error", "Redis not connected")
            return

        try:
            self.status_label.config(text=f"Sending {step.name}...")
            self.root.update()

            # Send to FE Server with result=ok
            sendDataFromIOServerToFEServer(
                redisConnection=self.redis_connection,
                currentMachineState=step.state,
                result=ok,
                action=updateValueCommand,
                value=-1.0,
                aProducer="TestUI"
            )

            # Update UI
            self.step_widgets[step_idx]["status_label"].config(text="Sent ✓", foreground="green")
            self.status_label.config(text=f"{step.name} sent successfully")
            self._advance()
            self._wait_until_consumed(io2feq)

        except Exception as e:
            messagebox.showerror("Error", f"Failed to send done: {e}")
            self.status_label.config(text=f"Error: {e}")

    def _reset_all(self):
        """Reset all state and UI."""
        if messagebox.askyesno("Confirm Reset", "Reset all steps and start over?"):
            ImageProcessorTestUI.currentPictures = {knucklePictureKeyString: None,
                                                    hubAndBottomBearingPictureKeyString: None,
                                                    topBearingPictureKeyString: None,
                                                    nutAndPlateWasherPictureKeyString: None,
                                                    splitPinAndWasherPictureKeyString: None,
                                                    capPictureKeyString: None,
                                                    componentPressBungPictureKeyString: None,
                                                    noBungPictureKeyString: None,
                                                    capPressBungPictureKeyString: None}
            self.current_step_index = 0
            self.waiting_for = None
            self.current_qr_code = ""
            self.cycle_bypass = False
            self.stored_images = {}  # Clear stored intermediate images
            self.bearing_geometry = None  # Clear bearing geometry
            self.qr_display.config(text="No QR Code selected")

            # Reset all steps
            for idx, step in enumerate(self.steps):
                step.file_path = None
                step.result = None

                widgets = self.step_widgets[idx]

                if step.step_type == "camera":
                    widgets["file_label"].config(text="No file selected")
                    widgets["result_label"].config(text="", foreground="")
                elif step.step_type in ("torque", "torque_final"):
                    widgets["value_label"].config(text="", foreground="")
                elif step.step_type == "done":
                    widgets["status_label"].config(text="", foreground="")

            self._update_button_states()
            self.status_label.config(text="Reset complete - Select QR Code to begin")


def main():
    root = tk.Tk()
    app = ImageProcessorTestUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()