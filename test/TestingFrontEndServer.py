"""
Testing script for AshokLeylandFrontEnd with the new 29-state process flow.

This script simulates inputs from QRCodeServer, CameraServer, and IOServer
to test the complete assembly process flow.

Process Flow:
1. QR Code Scan
2. Knuckle Check (Visual)
3. Hub & Bottom Bearing Check (Visual)
4. Top Bearing Check (Visual)
5. Nut & Plate Washer Check (Visual)
6. Tightening Torque 1 (Torque)
7. Free Rotations (IO)
8. Bung for Component Press Check (Visual)
9. Component Press (IO)
10. No Bung After Component Press Check (Visual)
11. Tightening Torque 2 (Torque)
12. Split Pin & Washer Check (Visual)
13. Cap Check (Visual)
14. Bung for Cap Press Check (Visual)
15. Cap Press (IO)
16. Free Rotation Torque 1 (Torque)
"""

import tkinter as tk
from tkinter import ttk, messagebox
from redis import Redis
from utils.RedisUtils import *
from Configuration import CosThetaConfigurator
from statemachine.StateMachine import MachineState
import cv2
import os

# ============================================================================
# IMAGE PATH CONFIGURATION - UPDATE THESE PATHS BEFORE TESTING
# ============================================================================

# Base directory for test images
IMAGE_BASE_DIR = "C:/AshokLeylandImages/Test"

# Individual image paths - set these to actual image files
KNUCKLE_IMAGE_PATH = f"{IMAGE_BASE_DIR}/1-Knuckle/test_image.png"
HUB_AND_BOTTOM_BEARING_IMAGE_PATH = f"{IMAGE_BASE_DIR}/2-HubAndBottomBearing/test_image.png"
TOP_BEARING_IMAGE_PATH = f"{IMAGE_BASE_DIR}/3-TopBearing/test_image.png"
NUT_AND_PLATE_WASHER_IMAGE_PATH = f"{IMAGE_BASE_DIR}/4-NutAndPlateWasher/test_image.png"
BUNG_FOR_COMPONENT_PRESS_IMAGE_PATH = f"{IMAGE_BASE_DIR}/7-BungAndNoBung/test_image.png"
NO_BUNG_IMAGE_PATH = f"{IMAGE_BASE_DIR}/7-BungAndNoBung/test_image.png"
SPLIT_PIN_AND_WASHER_IMAGE_PATH = f"{IMAGE_BASE_DIR}/5-SplitPinAndWasher/test_image.png"
CAP_IMAGE_PATH = f"{IMAGE_BASE_DIR}/6-Cap/test_image.png"
BUNG_FOR_CAP_PRESS_IMAGE_PATH = f"{IMAGE_BASE_DIR}/7-BungAndNoBung/test_image.png"

# Fallback image if specific image not found
FALLBACK_IMAGE_PATH = f"{IMAGE_BASE_DIR}/fallback_test_image.png"


# ============================================================================
# IMAGE LOADING
# ============================================================================

def load_image(path: str, fallback_path: str = None):
    """Load an image from path, with optional fallback."""
    if os.path.exists(path):
        img = cv2.imread(path)
        if img is not None:
            return img
        print(f"Warning: Could not read image at {path}")
    else:
        print(f"Warning: Image not found at {path}")

    if fallback_path and os.path.exists(fallback_path):
        img = cv2.imread(fallback_path)
        if img is not None:
            print(f"  Using fallback image: {fallback_path}")
            return img

    # Create a dummy image if nothing works
    print(f"  Creating dummy image")
    import numpy as np
    return np.zeros((480, 640, 3), dtype=np.uint8)


# Load all images
print("Loading test images...")
knuckle_image = load_image(KNUCKLE_IMAGE_PATH, FALLBACK_IMAGE_PATH)
hub_and_bottom_bearing_image = load_image(HUB_AND_BOTTOM_BEARING_IMAGE_PATH, FALLBACK_IMAGE_PATH)
top_bearing_image = load_image(TOP_BEARING_IMAGE_PATH, FALLBACK_IMAGE_PATH)
nut_and_plate_washer_image = load_image(NUT_AND_PLATE_WASHER_IMAGE_PATH, FALLBACK_IMAGE_PATH)
bung_for_component_press_image = load_image(BUNG_FOR_COMPONENT_PRESS_IMAGE_PATH, FALLBACK_IMAGE_PATH)
no_bung_image = load_image(NO_BUNG_IMAGE_PATH, FALLBACK_IMAGE_PATH)
split_pin_and_washer_image = load_image(SPLIT_PIN_AND_WASHER_IMAGE_PATH, FALLBACK_IMAGE_PATH)
cap_image = load_image(CAP_IMAGE_PATH, FALLBACK_IMAGE_PATH)
bung_for_cap_press_image = load_image(BUNG_FOR_CAP_PRESS_IMAGE_PATH, FALLBACK_IMAGE_PATH)
print("Images loaded.\n")

# ============================================================================
# REDIS CONNECTION
# ============================================================================

print("Connecting to Redis...")
trialRedisConnection = Redis(
    CosThetaConfigurator.getInstance().getRedisHost(),
    CosThetaConfigurator.getInstance().getRedisPort(),
    retry_on_timeout=True
)
trialRedisConnection.flushdb()
print("Redis connected and flushed.\n")

# ============================================================================
# TEST COMMANDS - Matching new state machine flow
# ============================================================================

# Default test values
DEFAULT_QR_CODE = "1234 HYTREW IUYTHRE"
DEFAULT_QR_DISPLAY = "Test Component QR"
DEFAULT_TIGHTENING_TORQUE_1 = 45.5
DEFAULT_TIGHTENING_TORQUE_2 = 48.0
DEFAULT_FREE_ROTATION_TORQUE_1 = 1.2

# Command definitions with descriptions
commands = [
    # Step 0: QR Code
    {
        "name": "QR Code Scan",
        "description": "Send QR code to start assembly process",
        "type": "qrcode",
        "action": lambda result: sendDataFromQRCodeServerToFEServer(
            trialRedisConnection,
            qrCode=DEFAULT_QR_CODE,
            displayString=DEFAULT_QR_DISPLAY
        ),
    },

    # Step 1: Knuckle Check (Visual)
    {
        "name": "Knuckle Check",
        "description": "Visual inspection of knuckle",
        "type": "camera",
        "state": MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE,
        "image": knuckle_image,
        "action": lambda result: sendDataFromCameraServerToFEServer(
            trialRedisConnection,
            originalImage=knuckle_image,
            processedImage=knuckle_image,
            result=result,
            currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE
        ),
    },

    # Step 2: Hub & Bottom Bearing Check (Visual)
    {
        "name": "Hub & Bottom Bearing Check",
        "description": "Visual inspection of hub and bottom bearing",
        "type": "camera",
        "state": MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING,
        "image": hub_and_bottom_bearing_image,
        "action": lambda result: sendDataFromCameraServerToFEServer(
            trialRedisConnection,
            originalImage=hub_and_bottom_bearing_image,
            processedImage=hub_and_bottom_bearing_image,
            result=result,
            currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING
        ),
    },

    # Step 3: Top Bearing Check (Visual)
    {
        "name": "Top Bearing Check",
        "description": "Visual inspection of top bearing",
        "type": "camera",
        "state": MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING,
        "image": top_bearing_image,
        "action": lambda result: sendDataFromCameraServerToFEServer(
            trialRedisConnection,
            originalImage=top_bearing_image,
            processedImage=top_bearing_image,
            result=result,
            currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING
        ),
    },

    # Step 4: Nut & Plate Washer Check (Visual)
    {
        "name": "Nut & Plate Washer Check",
        "description": "Visual inspection of nut and plate washer",
        "type": "camera",
        "state": MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER,
        "image": nut_and_plate_washer_image,
        "action": lambda result: sendDataFromCameraServerToFEServer(
            trialRedisConnection,
            originalImage=nut_and_plate_washer_image,
            processedImage=nut_and_plate_washer_image,
            result=result,
            currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER
        ),
    },

    # Step 5: Tightening Torque 1 (Torque reading)
    {
        "name": "Tightening Torque 1",
        "description": f"First tightening torque ({DEFAULT_TIGHTENING_TORQUE_1} Nm)",
        "type": "io_torque",
        "state": MachineState.READ_TIGHTENING_TORQUE_1,
        "value": DEFAULT_TIGHTENING_TORQUE_1,
        "action": lambda result: sendDataFromIOServerToFEServer(
            trialRedisConnection,
            currentMachineState=MachineState.READ_TIGHTENING_TORQUE_1,
            result=result,
            value=DEFAULT_TIGHTENING_TORQUE_1
        ),
    },

    # Step 6: Free Rotations Done (IO signal)
    {
        "name": "Free Rotations",
        "description": "Free rotations to settle bearing",
        "type": "io_signal",
        "state": MachineState.READ_FREE_ROTATIONS_DONE,
        "action": lambda result: sendDataFromIOServerToFEServer(
            trialRedisConnection,
            currentMachineState=MachineState.READ_FREE_ROTATIONS_DONE,
            result=result,
            value=0.0
        ),
    },

    # Step 7: Bung for Component Press Check (Visual)
    {
        "name": "Bung for Component Press Check",
        "description": "Visual inspection of bung placement for component press",
        "type": "camera",
        "state": MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS,
        "image": bung_for_component_press_image,
        "action": lambda result: sendDataFromCameraServerToFEServer(
            trialRedisConnection,
            originalImage=bung_for_component_press_image,
            processedImage=bung_for_component_press_image,
            result=result,
            currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS
        ),
    },

    # Step 8: Component Press Done (IO signal)
    {
        "name": "Component Press",
        "description": "Component press operation",
        "type": "io_signal",
        "state": MachineState.READ_COMPONENT_PRESS_DONE,
        "action": lambda result: sendDataFromIOServerToFEServer(
            trialRedisConnection,
            currentMachineState=MachineState.READ_COMPONENT_PRESS_DONE,
            result=result,
            value=0.0
        ),
    },

    # Step 9: No Bung After Component Press Check (Visual)
    {
        "name": "No Bung Check (After Component Press)",
        "description": "Visual inspection confirming bung removal after component press",
        "type": "camera",
        "state": MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK,
        "image": no_bung_image,
        "action": lambda result: sendDataFromCameraServerToFEServer(
            trialRedisConnection,
            originalImage=no_bung_image,
            processedImage=no_bung_image,
            result=result,
            currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK
        ),
    },

    # Step 10: Tightening Torque 2 (Torque reading)
    {
        "name": "Tightening Torque 2",
        "description": f"Second tightening torque ({DEFAULT_TIGHTENING_TORQUE_2} Nm)",
        "type": "io_torque",
        "state": MachineState.READ_TIGHTENING_TORQUE_2,
        "value": DEFAULT_TIGHTENING_TORQUE_2,
        "action": lambda result: sendDataFromIOServerToFEServer(
            trialRedisConnection,
            currentMachineState=MachineState.READ_TIGHTENING_TORQUE_2,
            result=result,
            value=DEFAULT_TIGHTENING_TORQUE_2
        ),
    },

    # Step 11: Split Pin & Washer Check (Visual)
    {
        "name": "Split Pin & Washer Check",
        "description": "Visual inspection of split pin and washer",
        "type": "camera",
        "state": MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER,
        "image": split_pin_and_washer_image,
        "action": lambda result: sendDataFromCameraServerToFEServer(
            trialRedisConnection,
            originalImage=split_pin_and_washer_image,
            processedImage=split_pin_and_washer_image,
            result=result,
            currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER
        ),
    },

    # Step 12: Cap Check (Visual)
    {
        "name": "Cap Check",
        "description": "Visual inspection of cap",
        "type": "camera",
        "state": MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP,
        "image": cap_image,
        "action": lambda result: sendDataFromCameraServerToFEServer(
            trialRedisConnection,
            originalImage=cap_image,
            processedImage=cap_image,
            result=result,
            currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP
        ),
    },

    # Step 13: Bung for Cap Press Check (Visual)
    {
        "name": "Bung for Cap Press Check",
        "description": "Visual inspection of bung placement for cap press",
        "type": "camera",
        "state": MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS,
        "image": bung_for_cap_press_image,
        "action": lambda result: sendDataFromCameraServerToFEServer(
            trialRedisConnection,
            originalImage=bung_for_cap_press_image,
            processedImage=bung_for_cap_press_image,
            result=result,
            currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS
        ),
    },

    # Step 14: Cap Press Done (IO signal)
    {
        "name": "Cap Press",
        "description": "Cap press operation",
        "type": "io_signal",
        "state": MachineState.READ_CAP_PRESS_DONE,
        "action": lambda result: sendDataFromIOServerToFEServer(
            trialRedisConnection,
            currentMachineState=MachineState.READ_CAP_PRESS_DONE,
            result=result,
            value=0.0
        ),
    },

    # Step 15: Free Rotation Torque 1 (Final torque reading)
    {
        "name": "Free Rotation Torque 1",
        "description": f"Final free rotation torque ({DEFAULT_FREE_ROTATION_TORQUE_1} Nm)",
        "type": "io_torque",
        "state": MachineState.READ_FREE_ROTATION_TORQUE_1,
        "value": DEFAULT_FREE_ROTATION_TORQUE_1,
        "action": lambda result: sendDataFromIOServerToFEServer(
            trialRedisConnection,
            currentMachineState=MachineState.READ_FREE_ROTATION_TORQUE_1,
            result=result,
            value=DEFAULT_FREE_ROTATION_TORQUE_1
        ),
    },
]


# ============================================================================
# GUI APPLICATION
# ============================================================================

class FrontEndTesterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Front End Server Tester - New State Machine")
        self.current_command_index = 0
        self.cycle_count = 0

        # Configure grid
        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)

        # Title
        title_label = tk.Label(root, text="AshokLeyland Front End Tester",
                               font=("Arial", 16, "bold"))
        title_label.grid(row=0, column=0, columnspan=2, pady=10)

        # Cycle counter
        self.cycle_label = tk.Label(root, text="Cycle: 0", font=("Arial", 12))
        self.cycle_label.grid(row=1, column=0, columnspan=2, pady=5)

        # Current step frame
        step_frame = ttk.LabelFrame(root, text="Current Step", padding=10)
        step_frame.grid(row=2, column=0, columnspan=2, padx=10, pady=5, sticky="ew")

        self.step_label = tk.Label(step_frame, text="Step 0/16", font=("Arial", 11))
        self.step_label.pack()

        self.name_label = tk.Label(step_frame, text="QR Code Scan",
                                   font=("Arial", 14, "bold"))
        self.name_label.pack()

        self.desc_label = tk.Label(step_frame, text="Send QR code to start assembly process",
                                   font=("Arial", 10), fg="gray")
        self.desc_label.pack()

        self.type_label = tk.Label(step_frame, text="Type: QR Code",
                                   font=("Arial", 10), fg="blue")
        self.type_label.pack()

        # Buttons frame
        button_frame = ttk.Frame(root, padding=10)
        button_frame.grid(row=3, column=0, columnspan=2, pady=10)

        # OK button (green)
        self.ok_button = tk.Button(button_frame, text="Send OK",
                                   command=lambda: self.execute_command(ok),
                                   font=("Arial", 14, "bold"),
                                   width=12, height=2,
                                   bg="#4CAF50", fg="white",
                                   activebackground="#45a049")
        self.ok_button.pack(side=tk.LEFT, padx=10)

        # NOT OK button (red)
        self.notok_button = tk.Button(button_frame, text="Send NOT OK",
                                      command=lambda: self.execute_command(notok),
                                      font=("Arial", 14, "bold"),
                                      width=12, height=2,
                                      bg="#f44336", fg="white",
                                      activebackground="#da190b")
        self.notok_button.pack(side=tk.LEFT, padx=10)

        # Control buttons frame
        control_frame = ttk.Frame(root, padding=5)
        control_frame.grid(row=4, column=0, columnspan=2, pady=5)

        # Reset button
        self.reset_button = tk.Button(control_frame, text="Reset to Start",
                                      command=self.reset_sequence,
                                      font=("Arial", 10), width=12)
        self.reset_button.pack(side=tk.LEFT, padx=5)

        # Auto-run all OK button
        self.auto_button = tk.Button(control_frame, text="Auto-Run All OK",
                                     command=self.auto_run_all_ok,
                                     font=("Arial", 10), width=15)
        self.auto_button.pack(side=tk.LEFT, padx=5)

        # Flush Redis button
        self.flush_button = tk.Button(control_frame, text="Flush Redis",
                                      command=self.flush_redis,
                                      font=("Arial", 10), width=12)
        self.flush_button.pack(side=tk.LEFT, padx=5)

        # Status/Log frame
        log_frame = ttk.LabelFrame(root, text="Log", padding=5)
        log_frame.grid(row=5, column=0, columnspan=2, padx=10, pady=5, sticky="nsew")
        root.rowconfigure(5, weight=1)

        self.log_text = tk.Text(log_frame, height=10, width=60, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=scrollbar.set)

        # Initialize display
        self.update_display()
        self.log("Application started. Ready to test.")
        self.log(f"Total steps in sequence: {len(commands)}")

    def log(self, message: str):
        """Add message to log."""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)

    def update_display(self):
        """Update the display with current command info."""
        cmd = commands[self.current_command_index]

        self.step_label.config(text=f"Step {self.current_command_index}/{len(commands) - 1}")
        self.name_label.config(text=cmd["name"])
        self.desc_label.config(text=cmd["description"])

        type_text = f"Type: {cmd['type'].upper()}"
        if "state" in cmd:
            type_text += f" | State: {cmd['state'].name}"
        self.type_label.config(text=type_text)

        self.cycle_label.config(text=f"Cycle: {self.cycle_count}")

    def execute_command(self, result):
        """Execute the current command with given result."""
        cmd = commands[self.current_command_index]

        try:
            cmd["action"](result)
            result_str = "OK" if result == ok else "NOT OK"
            self.log(f"✓ Sent [{result_str}]: {cmd['name']}")

            # Only advance to next command if result is OK
            # NOT OK means frontend will retry the same state
            if result == ok:
                self.current_command_index += 1

                # Check if we completed a cycle
                if self.current_command_index >= len(commands):
                    self.cycle_count += 1
                    self.current_command_index = 0
                    self.log(f"=== Cycle {self.cycle_count} complete! Starting new cycle ===")
            else:
                self.log(f"  ↻ Frontend will retry this step (NOT OK sent)")

            self.update_display()

        except Exception as e:
            self.log(f"✗ Error: {str(e)}")
            messagebox.showerror("Error", f"Failed to send command:\n{str(e)}")

    def reset_sequence(self):
        """Reset to the beginning of the sequence."""
        self.current_command_index = 0
        self.update_display()
        self.log("Reset to start of sequence")

    def flush_redis(self):
        """Flush Redis database."""
        try:
            trialRedisConnection.flushdb()
            self.log("Redis database flushed")
        except Exception as e:
            self.log(f"Error flushing Redis: {e}")

    def auto_run_all_ok(self):
        """Automatically run all commands with OK result."""
        self.log("=== Starting auto-run with all OK ===")

        # Reset to start
        self.current_command_index = 0
        self.update_display()

        # Schedule commands with delay
        self._auto_run_step(0)

    def _auto_run_step(self, step: int):
        """Execute one step of auto-run and schedule next."""
        if step >= len(commands):
            self.cycle_count += 1
            self.current_command_index = 0
            self.update_display()
            self.log(f"=== Auto-run complete! Cycle {self.cycle_count} ===")
            return

        self.current_command_index = step
        self.update_display()

        cmd = commands[step]
        try:
            cmd["action"](ok)
            self.log(f"✓ Auto-sent [OK]: {cmd['name']}")
        except Exception as e:
            self.log(f"✗ Auto-run error at step {step}: {e}")
            return

        # Schedule next step (500ms delay)
        self.root.after(500, lambda: self._auto_run_step(step + 1))


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("AshokLeyland Front End Tester")
    print("New State Machine - 16 Step Process")
    print("=" * 60)
    print()
    print("Image paths configured:")
    print(f"  Knuckle: {KNUCKLE_IMAGE_PATH}")
    print(f"  Hub & Bottom Bearing: {HUB_AND_BOTTOM_BEARING_IMAGE_PATH}")
    print(f"  Top Bearing: {TOP_BEARING_IMAGE_PATH}")
    print(f"  Nut & Plate Washer: {NUT_AND_PLATE_WASHER_IMAGE_PATH}")
    print(f"  Bung for Component Press: {BUNG_FOR_COMPONENT_PRESS_IMAGE_PATH}")
    print(f"  No Bung: {NO_BUNG_IMAGE_PATH}")
    print(f"  Split Pin & Washer: {SPLIT_PIN_AND_WASHER_IMAGE_PATH}")
    print(f"  Cap: {CAP_IMAGE_PATH}")
    print(f"  Bung for Cap Press: {BUNG_FOR_CAP_PRESS_IMAGE_PATH}")
    print()
    print("Starting GUI...")
    print("=" * 60)

    root = tk.Tk()
    app = FrontEndTesterApp(root)
    root.geometry("550x500")
    root.minsize(500, 450)
    root.mainloop()