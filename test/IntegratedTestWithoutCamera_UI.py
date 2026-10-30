"""
Tkinter Test UI for Ashok Leyland Image Processor
Simulates the full inspection pipeline for testing camera check functionality.

Usage:
    Run this script from the project root directory where all the modules are accessible.
    python ImageProcessorTestUI.py

The UI simulates sending QR codes and camera check results to the FE Server for testing
the image processing pipeline without needing the physical hardware.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import random
import cv2
import numpy as np
from redis import Redis
from typing import Union, Tuple, Callable, Dict, Optional
import time
import copy

# Import project modules
from statemachine.StateMachine import MachineState
from Configuration import CosThetaConfigurator
from utils.RedisUtils import (
    sendDataFromQRCodeServerToFEServer,
    sendComponentQRCodeFromQRCodeServerToCameraServer,
    sendDataFromCameraServerToFEServer,
    sendDataFromIOServerToFEServer,
    ok, notok, updateValueCommand,
    knucklePictureKeyString,
    hubAndBottomBearingPictureKeyString,
    topBearingPictureKeyString,
    nutAndPlateWasherPictureKeyString,
    componentPressBunkPictureKeyString,
    splitPinAndWasherPictureKeyString,
    noBunkPictureKeyString,
    capPictureKeyString,
    capPressBunkPictureKeyString
)
from utils.QRCodeHelper import getModel_LHSRHS_AndTonnage

# Import check modules - using the class-based approach
from camera.CheckKnuckle import CheckKnuckle
from camera.CheckHubAndBottomBearing import CheckHubAndBottomBearing
from camera.CheckTopBearing import CheckTopBearing
from camera.CheckNutAndPlateWasher import CheckNutAndPlateWasher
from camera.CheckBunk import CheckBunk
from camera.CheckNoBunk import CheckNoBunk
from camera.CheckSplitPinAndWasher import CheckSplitPinAndWasher
from camera.CheckCap import CheckCap
from camera.CheckNoCapBunk import CheckNoCapBunk

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

class TestStep:
    """Represents a single step in the test pipeline."""

    def __init__(self, name: str, state: MachineState, step_type: str,
                 check_name: str = None, write_state: MachineState = None):
        self.name = name
        self.state = state  # The state this step corresponds to
        self.step_type = step_type  # 'qrcode', 'camera', 'torque', 'done'
        self.check_name = check_name  # Name of the check to run
        self.write_state = write_state  # The WRITE state for camera checks
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
                                                      componentPressBunkPictureKeyString : None,
                                                      noBunkPictureKeyString : None,
                                                      capPressBunkPictureKeyString : None}

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Ashok Leyland Image Processor Test UI")
        self.root.geometry("900x800")

        # Redis connection
        self.redis_connection: Union[Redis, None] = None
        self.connect_to_redis()

        # Current QR code
        self.current_qr_code: str = ""

        # Current state index (which step we're on)
        self.current_step_index: int = 0

        # Storage for intermediate images (needed for comparison checks)
        self.stored_images: Dict[str, np.ndarray] = {}

        # Storage for bearing geometry (from CheckTopBearing, used by CheckNutAndPlateWasher)
        self.bearing_geometry: Optional[dict] = None

        # Define all test steps in order
        self.steps = self._create_steps()

        # UI widgets storage
        self.step_widgets = {}

        # Build UI
        self._build_ui()
        self._update_button_states()

    def _create_steps(self) -> list:
        """Create the ordered list of test steps."""
        return [
            # Step 0: QR Code
            TestStep("QR Code", MachineState.READ_QR_CODE, "qrcode"),

            # Step 1: Knuckle
            TestStep("Knuckle", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, "camera",
                     check_name="knuckle",
                     write_state=MachineState.WRITE_RESULT_OF_CHECKING_KNUCKLE),

            # Step 2: Hub and Bottom Bearing
            TestStep("Hub & Bottom Bearing", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING,
                     "camera",
                     check_name="hub",
                     write_state=MachineState.WRITE_RESULT_OF_CHECKING_HUB_AND_BOTTOM_BEARING),

            # Step 3: Top Bearing
            TestStep("Top Bearing", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, "camera",
                     check_name="top_bearing",
                     write_state=MachineState.WRITE_RESULT_OF_CHECKING_TOP_BEARING),

            # Step 4: Nut and Plate Washer
            TestStep("Nut & Plate Washer", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER, "camera",
                     check_name="nut_platewasher",
                     write_state=MachineState.WRITE_RESULT_OF_CHECKING_NUT_AND_PLATEWASHER),

            # Step 5: Tightening Torque 1 (value only, state 12)
            TestStep("Tightening Torque 1", MachineState.READ_TIGHTENING_TORQUE_1, "torque"),

            # Step 6: Free Rotations Done
            TestStep("Free Rotations Done", MachineState.READ_FREE_ROTATIONS_DONE, "done"),

            # Step 7: Bung for Component Press
            TestStep("Bung (Component Press)", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS,
                     "camera",
                     check_name="no_cap_bung",
                     write_state=MachineState.WRITE_RESULT_OF_CHECKING_BUNK_FOR_COMPONENT_PRESS),

            # Step 8: Component Press Done
            TestStep("Component Press Done", MachineState.READ_COMPONENT_PRESS_DONE, "done"),

            # Step 9: No Bung (after press)
            TestStep("No Bung", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK, "camera",
                     check_name="no_bung",
                     write_state=MachineState.WRITE_RESULT_OF_CHECKING_NO_BUNK),

            # Step 10: Tightening Torque 2 (value only, state 20)
            TestStep("Tightening Torque 2", MachineState.READ_TIGHTENING_TORQUE_2, "torque"),

            # Step 11: Split Pin and Washer
            TestStep("Split Pin & Washer", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER, "camera",
                     check_name="splitpin_washer",
                     write_state=MachineState.WRITE_RESULT_OF_CHECKING_SPLITPIN_AND_WASHER),

            # Step 12: Cap
            TestStep("Cap", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP, "camera",
                     check_name="cap",
                     write_state=MachineState.WRITE_RESULT_OF_CHECKING_CAP),

            # Step 13: Bung for Cap Press
            TestStep("Bung (Cap Press)", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS, "camera",
                     check_name="bung",
                     write_state=MachineState.WRITE_RESULT_OF_CHECKING_BUNK_FOR_CAP_PRESS),

            # Step 14: Cap Press Done
            TestStep("Cap Press Done", MachineState.READ_CAP_PRESS_DONE, "done"),

            # Step 15: Free Rotation Torque 1 (value only, state 29)
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

        # Build each step's UI
        for idx, step in enumerate(self.steps):
            self._build_step_ui(scrollable_frame, idx, step)

        # Reset button at bottom
        reset_frame = ttk.Frame(scrollable_frame, padding="10")
        reset_frame.pack(fill="x", padx=5, pady=10)

        reset_btn = ttk.Button(reset_frame, text="Reset All", command=self._reset_all)
        reset_btn.pack(side="right")

    def _build_step_ui(self, parent: ttk.Frame, idx: int, step: TestStep):
        """Build UI for a single step."""
        frame = ttk.LabelFrame(parent, text=f"Step {idx}: {step.name}", padding="5")
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

            # QR Code display
            self.qr_display = ttk.Label(frame, text="No QR Code selected")
            self.qr_display.pack(side="left", padx=20)

        elif step.step_type == "camera":
            # File chooser
            file_frame = ttk.Frame(frame)
            file_frame.pack(fill="x")

            choose_btn = ttk.Button(file_frame, text="Choose Image",
                                    command=lambda s=step, i=idx: self._choose_file(s, i))
            choose_btn.pack(side="left", padx=5)
            widgets["choose_btn"] = choose_btn

            file_label = ttk.Label(file_frame, text="No file selected", width=50)
            file_label.pack(side="left", padx=5)
            widgets["file_label"] = file_label

            # Run check button
            run_btn = ttk.Button(file_frame, text=f"Run {step.name} Check",
                                 command=lambda s=step, i=idx: self._run_camera_check(s, i))
            run_btn.pack(side="left", padx=5)
            run_btn.state(["disabled"])  # Initially disabled until file is chosen
            widgets["run_btn"] = run_btn

            # Result label
            result_label = ttk.Label(file_frame, text="")
            result_label.pack(side="right", padx=5)
            widgets["result_label"] = result_label

        elif step.step_type == "torque":
            # Torque value button (17.0 - 19.0)
            btn = ttk.Button(frame, text=f"Send {step.name} (17.0-19.0)",
                             command=lambda s=step, i=idx: self._send_torque(s, i, 17.0, 19.0))
            btn.pack(side="left", padx=5)
            widgets["send_btn"] = btn

            value_label = ttk.Label(frame, text="")
            value_label.pack(side="left", padx=10)
            widgets["value_label"] = value_label

        elif step.step_type == "torque_final":
            # Final torque value button (2.0 - 6.0)
            btn = ttk.Button(frame, text=f"Send {step.name} (2.0-6.0)",
                             command=lambda s=step, i=idx: self._send_torque(s, i, 2.0, 6.0))
            btn.pack(side="left", padx=5)
            widgets["send_btn"] = btn

            value_label = ttk.Label(frame, text="")
            value_label.pack(side="left", padx=10)
            widgets["value_label"] = value_label

        elif step.step_type == "done":
            # Done button
            btn = ttk.Button(frame, text=f"Send {step.name}",
                             command=lambda s=step, i=idx: self._send_done(s, i))
            btn.pack(side="left", padx=5)
            widgets["send_btn"] = btn

            status_label = ttk.Label(frame, text="")
            status_label.pack(side="left", padx=10)
            widgets["status_label"] = status_label

        self.step_widgets[idx] = widgets

    def _update_button_states(self):
        """Enable/disable buttons based on current step."""
        for idx, widgets in self.step_widgets.items():
            step = self.steps[idx]
            is_current = (idx == self.current_step_index)

            if step.step_type == "qrcode":
                state = ["!disabled"] if is_current else ["disabled"]
                widgets["dost_btn"].state(state)
                widgets["dostplus_btn"].state(state)

            elif step.step_type == "camera":
                # Choose button enabled only at current step
                choose_state = ["!disabled"] if is_current else ["disabled"]
                widgets["choose_btn"].state(choose_state)

                # Run button enabled only if file is selected AND at current step
                if is_current and step.file_path:
                    widgets["run_btn"].state(["!disabled"])
                else:
                    widgets["run_btn"].state(["disabled"])

            elif step.step_type in ("torque", "torque_final", "done"):
                state = ["!disabled"] if is_current else ["disabled"]
                widgets["send_btn"].state(state)

        # Update state label
        if self.current_step_index < len(self.steps):
            current_step = self.steps[self.current_step_index]
            self.state_label.config(text=f"Current Step: {current_step.name}")
        else:
            self.state_label.config(text="All steps completed!")

    def _send_qr_code(self, qr_code: str, model_name: str):
        """Send QR code to FE server and Camera server."""
        if not self.redis_connection:
            messagebox.showerror("Error", "Redis not connected")
            return

        try:
            self.status_label.config(text=f"Sending {model_name} QR Code...")
            self.root.update()

            # Get display string
            display_string = createDisplayString(qr_code)
            print(f"{display_string = }")

            # Send to FE Server
            sendDataFromQRCodeServerToFEServer(
                redisConnection=self.redis_connection,
                qrCode=qr_code,
                displayString=display_string,
                qrCodeStatus=ok,
                aProducer="TestUI"
            )

            # Send to Camera Server
            sendComponentQRCodeFromQRCodeServerToCameraServer(
                redisConnection=self.redis_connection,
                componentQRCode=qr_code,
                aProducer="TestUI"
            )

            # Update state
            self.current_qr_code = qr_code
            self.qr_display.config(text=f"{model_name}: {display_string[:50]}...")
            self.current_step_index = 1  # Move to Knuckle step
            self._update_button_states()

            self.status_label.config(text=f"{model_name} QR Code sent successfully")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to send QR code: {e}")
            self.status_label.config(text=f"Error: {e}")

    def _choose_file(self, step: TestStep, step_idx: int):
        """Open file chooser for camera check."""
        file_path = filedialog.askopenfilename(
            title=f"Select image for {step.name}",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.bmp"),
                ("All files", "*.*")
            ]
        )

        if file_path:
            step.file_path = file_path
            # Update label with truncated path
            display_path = file_path if len(file_path) < 50 else f"...{file_path[-47:]}"
            self.step_widgets[step_idx]["file_label"].config(text=display_path)
            self._update_button_states()

    def _run_camera_check(self, step: TestStep, step_idx: int):
        """Run the camera check in a separate thread."""
        if not step.file_path:
            messagebox.showerror("Error", "No file selected")
            return

        if not self.redis_connection:
            messagebox.showerror("Error", "Redis not connected")
            return

        # Disable button during processing
        self.step_widgets[step_idx]["run_btn"].state(["disabled"])
        self.status_label.config(text=f"Running {step.name} check...")
        self.root.update()

        # Run in thread to avoid UI freeze
        thread = threading.Thread(
            target=self._execute_camera_check,
            args=(step, step_idx),
            daemon=True
        )
        thread.start()

    def _execute_camera_check(self, step: TestStep, step_idx: int):
        """Execute the camera check (runs in thread)."""
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
                currentMachineState=step.write_state,
                aProducer="TestUI"
            )

            # Update UI in main thread
            self.root.after(0, lambda: self._handle_check_result(step, step_idx, result))

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.root.after(0, lambda: self._handle_check_error(step_idx, str(e)))

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
            processed_image, result = CheckKnuckle.checkKnuckle(anImage=image, currentPictures=ImageProcessorTestUI.currentPictures, componentQRCode=self.current_qr_code)
            return result, processed_image

        elif check_name == "hub":
            # CheckHubAndBottomBearing.checkHubAndBottomBearing(image, qr_code) -> (result_image, success, winning_gamma)
            ImageProcessorTestUI.currentPictures[hubAndBottomBearingPictureKeyString] = image
            result_image, success, _ = CheckHubAndBottomBearing.checkHubAndBottomBearing(anImage=image, componentQRCode=self.current_qr_code, use_smart_gamma=True)
            return success, result_image

        elif check_name == "top_bearing":
            # CheckTopBearing.checkTopBearing(...) -> (annotated_image, is_bearing_present, geometry)
            ImageProcessorTestUI.currentPictures[topBearingPictureKeyString] = image
            annotated_image, is_present, geometry = CheckTopBearing.checkTopBearing(
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
            processed_image, result = CheckNutAndPlateWasher.checkNutAndPlateWasher(
                anImage=image,
                currentPictures=ImageProcessorTestUI.currentPictures,
                componentQRCode=self.current_qr_code,
                bearing_geometry=self.bearing_geometry
            )
            return result, processed_image

        elif check_name == "no_cap_bung":
            # CheckNoCapBunk.checkNoCapBung(image, qr_code) -> (result_image, success, winning_gamma)
            ImageProcessorTestUI.currentPictures[componentPressBunkPictureKeyString] = image
            result_image, success, _ = CheckNoCapBunk.checkNoCapBunk(anImage=image, componentQRCode=self.current_qr_code)
            return success, result_image

        elif check_name == "no_bung":
            # CheckNoBunk.checkNoBunk(image, currentPictures, qr_code) -> (annotated_image, nut_present)
            ImageProcessorTestUI.currentPictures[noBunkPictureKeyString] = image
            processed_image, result = CheckNoBunk.checkNoBunk(anImage=image, currentPictures=ImageProcessorTestUI.currentPictures,
                                                              componentQRCode=self.current_qr_code)
            return result, processed_image

        elif check_name == "splitpin_washer":
            # CheckSplitPinAndWasher.checkSplitPinAndWasher(image, qr_code) -> (processed_image, result)
            ImageProcessorTestUI.currentPictures[splitPinAndWasherPictureKeyString] = image
            processed_image, result = CheckSplitPinAndWasher.checkSplitPinAndWasher(anImage=image, currentPictures=ImageProcessorTestUI.currentPictures,
                                                           componentQRCode=self.current_qr_code)
            return result, processed_image

        elif check_name == "cap":
            # CheckCap.checkCap(image, currentPictures, qr_code)
            ImageProcessorTestUI.currentPictures[capPictureKeyString] = image
            processed_image, result = CheckCap.checkCap(anImage=image, currentPictures=ImageProcessorTestUI.currentPictures,
                                                           componentQRCode=self.current_qr_code)
            return result, processed_image

        elif check_name == "bung":
            # CheckBunk.checkBung(image, qr_code) -> (result_image, success, winning_gamma)
            ImageProcessorTestUI.currentPictures[capPressBunkPictureKeyString] = image
            result_image, success, _ = CheckBunk.checkBunk(anImage=image, componentQRCode=self.current_qr_code)
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
            self.current_step_index += 1
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

            # Send to FE Server
            sendDataFromIOServerToFEServer(
                redisConnection=self.redis_connection,
                currentMachineState=step.state,
                result=ok,
                action=updateValueCommand,
                value=value,
                aProducer="TestUI"
            )

            # Update UI
            self.step_widgets[step_idx]["value_label"].config(text=f"Sent: {value}")
            self.status_label.config(text=f"{step.name} sent: {value}")
            self.current_step_index += 1
            self._update_button_states()

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
            self.current_step_index += 1
            self._update_button_states()

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
                                                    componentPressBunkPictureKeyString: None,
                                                    noBunkPictureKeyString: None,
                                                    capPressBunkPictureKeyString: None}
            self.current_step_index = 0
            self.current_qr_code = ""
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
                    widgets["result_label"].config(text="")
                elif step.step_type in ("torque", "torque_final"):
                    widgets["value_label"].config(text="")
                elif step.step_type == "done":
                    widgets["status_label"].config(text="")

            self._update_button_states()
            self.status_label.config(text="Reset complete - Select QR Code to begin")


def main():
    root = tk.Tk()
    app = ImageProcessorTestUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()