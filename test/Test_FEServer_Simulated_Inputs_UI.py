"""
Tests the real frontend (FE server) by simulating QRCodeServer, CameraServer and IOServer inputs over one or more full Machine 2 cycles, using fixed image paths; run from project root: python -m test.Test_FEServer_Simulated_Inputs_UI

Start the frontend first (e.g. python frontend/AshokLeylandFrontEnd.py, or the full application without the
IO/QR/camera servers). This Tk window then plays the QR, camera and IO servers on their Redis queues.

Machine 2 operator steps (15) and the frontend state each message is sent for:
 1. QR Code Scan                       READ_QR_CODE                                            (qrcode2fe)
 2. Knuckle Check (visual)             READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE                  (camera2fe)
 3. Hub & Bottom Bearing Check (vis.)  READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING   (camera2fe)
 4. Top Bearing Check (visual)         READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING              (camera2fe)
 5. Nut & Plate Washer Check (visual)  READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER      (camera2fe)
 6. Tightening Torque 1 (IO value)     READ_TIGHTENING_TORQUE_1                                (io2fe)
 7. Bunk for Component Press (visual)  READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS (camera2fe)
 8. Component Press (IO)               READ_COMPONENT_PRESS_DONE                               (io2fe)
 9. No Bunk Check (visual)             READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK                  (camera2fe)
10. Tightening Torque 2 (IO value)     READ_TIGHTENING_TORQUE_2                                (io2fe)
11. Split Pin & Washer Check (visual)  READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER      (camera2fe)
12. Cap Check (visual)                 READ_TAKE_PICTURE_FOR_CHECKING_CAP                      (camera2fe)
13. Bunk for Cap Press Check (visual)  READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS       (camera2fe)
14. Cap Press (IO)                     READ_CAP_PRESS_DONE                                     (io2fe)
15. Free Rotation Torque (IO, final)   READ_FREE_ROTATION_TORQUE_1                             (io2fe, action=moveAheadToNextComponent)

There is no separate free-rotation step on Machine 2 (free rotation happens during tightening at station 2).

Message ordering: every frontend reader trims its queue to the latest message and the IO reader discards a message whose
state differs from the frontend's current state. So, with "Wait for FE to consume each message" ticked (default), the
next message is only sent once the frontend has consumed the previous one (its queue is empty again).

Image paths: set IMAGE_BASE_DIR below (or the AL_TEST_IMAGE_DIR environment variable). A black dummy image is used if a
file is missing.
"""

import os
import sys
import datetime
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tkinter as tk
from tkinter import ttk, messagebox
import cv2
import numpy as np
from redis import Redis

from Configuration import CosThetaConfigurator
from statemachine.StateMachine import MachineState
from utils.RedisUtils import (
    sendDataFromQRCodeServerToFEServer,
    sendDataFromCameraServerToFEServer,
    sendDataFromIOServerToFEServer,
    ok, notok, updateValueCommand, moveAheadToNextComponent,
    qrcode2feq, camera2feq, io2feq,
)

# ============================================================================
# IMAGE PATH CONFIGURATION - UPDATE THESE PATHS BEFORE TESTING
# ============================================================================

IMAGE_BASE_DIR = os.environ.get("AL_TEST_IMAGE_DIR", "C:/AshokLeylandImages/Test")

KNUCKLE_IMAGE_PATH = f"{IMAGE_BASE_DIR}/1-Knuckle/test_image.png"
HUB_AND_BOTTOM_BEARING_IMAGE_PATH = f"{IMAGE_BASE_DIR}/2-HubAndBottomBearing/test_image.png"
TOP_BEARING_IMAGE_PATH = f"{IMAGE_BASE_DIR}/3-TopBearing/test_image.png"
NUT_AND_PLATE_WASHER_IMAGE_PATH = f"{IMAGE_BASE_DIR}/4-NutAndPlateWasher/test_image.png"
BUNK_FOR_COMPONENT_PRESS_IMAGE_PATH = f"{IMAGE_BASE_DIR}/7-BungAndNoBung/test_image.png"
NO_BUNK_IMAGE_PATH = f"{IMAGE_BASE_DIR}/7-BungAndNoBung/test_image.png"
SPLIT_PIN_AND_WASHER_IMAGE_PATH = f"{IMAGE_BASE_DIR}/5-SplitPinAndWasher/test_image.png"
CAP_IMAGE_PATH = f"{IMAGE_BASE_DIR}/6-Cap/test_image.png"
BUNK_FOR_CAP_PRESS_IMAGE_PATH = f"{IMAGE_BASE_DIR}/7-BungAndNoBung/test_image.png"

FALLBACK_IMAGE_PATH = f"{IMAGE_BASE_DIR}/fallback_test_image.png"

IMAGE_PATHS = {
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE: KNUCKLE_IMAGE_PATH,
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING: HUB_AND_BOTTOM_BEARING_IMAGE_PATH,
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING: TOP_BEARING_IMAGE_PATH,
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER: NUT_AND_PLATE_WASHER_IMAGE_PATH,
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS: BUNK_FOR_COMPONENT_PRESS_IMAGE_PATH,
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK: NO_BUNK_IMAGE_PATH,
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER: SPLIT_PIN_AND_WASHER_IMAGE_PATH,
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP: CAP_IMAGE_PATH,
    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS: BUNK_FOR_CAP_PRESS_IMAGE_PATH,
}


# ============================================================================
# IMAGE LOADING
# ============================================================================

def load_image(path: str, fallback_path: str = None):
    """Load an image from path, with optional fallback; a black dummy image if neither can be read."""
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

    print(f"  Creating dummy image")
    return np.zeros((480, 640, 3), dtype=np.uint8)


print("Loading test images...")
IMAGES = {state: load_image(path, FALLBACK_IMAGE_PATH) for state, path in IMAGE_PATHS.items()}
print("Images loaded.\n")

# ============================================================================
# REDIS CONNECTION
# ============================================================================

trialRedisConnection = Redis(
    CosThetaConfigurator.getInstance().getRedisHost(),
    CosThetaConfigurator.getInstance().getRedisPort(),
    retry_on_timeout=True
)

# ============================================================================
# TEST COMMANDS - Machine 2 operator steps, in frontend order
# ============================================================================

# DOST LHS 2.0T (matches qrcode.partmapping.1 in ApplicationConfiguration.properties)
DEFAULT_QR_CODE = "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"
DEFAULT_QR_DISPLAY = "DOST - LHS - 2.0T : 11770 : 09.03.2025"
DEFAULT_TIGHTENING_TORQUE_1 = 19.05
DEFAULT_TIGHTENING_TORQUE_2 = 19.02
DEFAULT_FREE_ROTATION_TORQUE_1 = 2.41


def _camera_command(name: str, description: str, state: MachineState) -> dict:
    return {
        "name": name,
        "description": description,
        "type": "camera",
        "state": state,
        "queue": camera2feq,
        "action": lambda result, s=state: sendDataFromCameraServerToFEServer(
            trialRedisConnection,
            originalImage=IMAGES[s],
            processedImage=IMAGES[s],
            result=result,
            currentMachineState=s
        ),
    }


def _io_command(name: str, description: str, cmd_type: str, state: MachineState, value: float,
                action: str = updateValueCommand, moveToState: MachineState = MachineState.INVALID_STATE,
                ends_cycle: bool = False) -> dict:
    return {
        "name": name,
        "description": description,
        "type": cmd_type,
        "state": state,
        "value": value,
        "queue": io2feq,
        "ends_cycle": ends_cycle,
        "action": lambda result: sendDataFromIOServerToFEServer(
            trialRedisConnection,
            currentMachineState=state,
            moveToState=moveToState,
            result=result,
            action=action,
            value=value
        ),
    }


commands = [
    {
        "name": "QR Code Scan",
        "description": "Send QR code to start assembly process",
        "type": "qrcode",
        "state": MachineState.READ_QR_CODE,
        "queue": qrcode2feq,
        "action": lambda result: sendDataFromQRCodeServerToFEServer(
            trialRedisConnection,
            qrCode=DEFAULT_QR_CODE,
            displayString=DEFAULT_QR_DISPLAY,
            qrCodeStatus=result
        ),
    },
    _camera_command("Knuckle Check", "Visual inspection of knuckle",
                    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE),
    _camera_command("Hub & Bottom Bearing Check", "Visual inspection of hub and bottom bearing",
                    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING),
    _camera_command("Top Bearing Check", "Visual inspection of top bearing",
                    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING),
    _camera_command("Nut & Plate Washer Check", "Visual inspection of nut and plate washer",
                    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER),
    _io_command("Tightening Torque 1", f"First tightening torque ({DEFAULT_TIGHTENING_TORQUE_1} Nm)", "io_torque",
                MachineState.READ_TIGHTENING_TORQUE_1, DEFAULT_TIGHTENING_TORQUE_1),
    _camera_command("Bunk for Component Press Check", "Visual inspection of bunk placement for component press",
                    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS),
    _io_command("Component Press", "Component press operation", "io_signal",
                MachineState.READ_COMPONENT_PRESS_DONE, -1.0),
    _camera_command("No Bunk Check (after Component Press)", "Visual inspection confirming bunk removal",
                    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK),
    _io_command("Tightening Torque 2", f"Second tightening torque ({DEFAULT_TIGHTENING_TORQUE_2} Nm)", "io_torque",
                MachineState.READ_TIGHTENING_TORQUE_2, DEFAULT_TIGHTENING_TORQUE_2),
    _camera_command("Split Pin & Washer Check", "Visual inspection of split pin and washer",
                    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER),
    _camera_command("Cap Check", "Visual inspection of cap",
                    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP),
    _camera_command("Bunk for Cap Press Check", "Visual inspection of bunk placement for cap press",
                    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS),
    _io_command("Cap Press", "Cap press operation", "io_signal",
                MachineState.READ_CAP_PRESS_DONE, -1.0),
    # Final step: the frontend ends the cycle (and sends the record to the DB server) whether OK or NOT OK
    _io_command("Free Rotation Torque", f"Final free rotation torque ({DEFAULT_FREE_ROTATION_TORQUE_1} Nm)", "io_torque",
                MachineState.READ_FREE_ROTATION_TORQUE_1, DEFAULT_FREE_ROTATION_TORQUE_1,
                action=moveAheadToNextComponent, moveToState=MachineState.READ_QR_CODE, ends_cycle=True),
]


def queue_consumed(queueName: str) -> bool:
    """True when the frontend has read (and deleted) every message on the queue."""
    try:
        return trialRedisConnection.xlen(queueName) == 0
    except Exception:
        return False


# ============================================================================
# GUI APPLICATION
# ============================================================================

class FrontEndTesterApp:
    POLL_MS = 100
    AUTO_STEP_GAP_MS = 500

    def __init__(self, root):
        self.root = root
        self.root.title("Front End Server Tester - Machine 2")
        self.current_command_index = 0
        self.cycle_count = 0
        self.pending_queue = None          # queue of the last message sent, until the frontend consumes it
        self.auto_running = False

        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)

        title_label = tk.Label(root, text="AshokLeyland Front End Tester (Machine 2)",
                               font=("Arial", 16, "bold"))
        title_label.grid(row=0, column=0, columnspan=2, pady=10)

        self.cycle_label = tk.Label(root, text="Cycle: 0", font=("Arial", 12))
        self.cycle_label.grid(row=1, column=0, columnspan=2, pady=5)

        step_frame = ttk.LabelFrame(root, text="Current Step", padding=10)
        step_frame.grid(row=2, column=0, columnspan=2, padx=10, pady=5, sticky="ew")

        self.step_label = tk.Label(step_frame, text=f"Step 1/{len(commands)}", font=("Arial", 11))
        self.step_label.pack()

        self.name_label = tk.Label(step_frame, text="QR Code Scan", font=("Arial", 14, "bold"))
        self.name_label.pack()

        self.desc_label = tk.Label(step_frame, text="Send QR code to start assembly process",
                                   font=("Arial", 10), fg="gray")
        self.desc_label.pack()

        self.type_label = tk.Label(step_frame, text="Type: QRCODE", font=("Arial", 10), fg="blue")
        self.type_label.pack()

        self.pending_label = tk.Label(step_frame, text="", font=("Arial", 10), fg="dark orange")
        self.pending_label.pack()

        button_frame = ttk.Frame(root, padding=10)
        button_frame.grid(row=3, column=0, columnspan=2, pady=10)

        self.ok_button = tk.Button(button_frame, text="Send OK",
                                   command=lambda: self.execute_command(ok),
                                   font=("Arial", 14, "bold"), width=12, height=2,
                                   bg="#4CAF50", fg="white", activebackground="#45a049")
        self.ok_button.pack(side=tk.LEFT, padx=10)

        self.notok_button = tk.Button(button_frame, text="Send NOT OK",
                                      command=lambda: self.execute_command(notok),
                                      font=("Arial", 14, "bold"), width=12, height=2,
                                      bg="#f44336", fg="white", activebackground="#da190b")
        self.notok_button.pack(side=tk.LEFT, padx=10)

        control_frame = ttk.Frame(root, padding=5)
        control_frame.grid(row=4, column=0, columnspan=2, pady=5)

        self.reset_button = tk.Button(control_frame, text="Reset to Start",
                                      command=self.reset_sequence, font=("Arial", 10), width=12)
        self.reset_button.pack(side=tk.LEFT, padx=5)

        self.auto_button = tk.Button(control_frame, text="Auto-Run All OK",
                                     command=self.auto_run_all_ok, font=("Arial", 10), width=15)
        self.auto_button.pack(side=tk.LEFT, padx=5)

        self.flush_button = tk.Button(control_frame, text="Flush Redis",
                                      command=self.flush_redis, font=("Arial", 10), width=12)
        self.flush_button.pack(side=tk.LEFT, padx=5)

        self.wait_for_fe = tk.BooleanVar(value=True)
        ttk.Checkbutton(root, text="Wait for FE to consume each message before the next one",
                        variable=self.wait_for_fe).grid(row=5, column=0, columnspan=2)

        log_frame = ttk.LabelFrame(root, text="Log", padding=5)
        log_frame.grid(row=6, column=0, columnspan=2, padx=10, pady=5, sticky="nsew")
        root.rowconfigure(6, weight=1)

        self.log_text = tk.Text(log_frame, height=10, width=60, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.update_display()
        self.log("Application started. Start the frontend, then send the steps in order.")
        self.log(f"Total operator steps per cycle: {len(commands)}")

    def log(self, message: str):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)

    def update_display(self):
        cmd = commands[self.current_command_index]
        self.step_label.config(text=f"Step {self.current_command_index + 1}/{len(commands)}")
        self.name_label.config(text=cmd["name"])
        self.desc_label.config(text=cmd["description"])
        self.type_label.config(text=f"Type: {cmd['type'].upper()} | State: {cmd['state'].name}")
        self.cycle_label.config(text=f"Cycle: {self.cycle_count}")
        waiting = self.pending_queue is not None
        self.pending_label.config(text="Waiting for FE to consume the last message..." if waiting else "")
        btn_state = tk.DISABLED if (waiting or self.auto_running) else tk.NORMAL
        self.ok_button.config(state=btn_state)
        self.notok_button.config(state=btn_state)
        self.auto_button.config(state=tk.DISABLED if self.auto_running else tk.NORMAL)

    def _send(self, result) -> bool:
        """Send the current step's message with the given result and advance the step pointer."""
        cmd = commands[self.current_command_index]
        cmd["action"](result)
        self.log(f"Sent [{'OK' if result == ok else 'NOT OK'}]: {cmd['name']} ({cmd['state'].name})")
        if result == ok or cmd.get("ends_cycle", False):
            self.current_command_index += 1
            if self.current_command_index >= len(commands):
                self.cycle_count += 1
                self.current_command_index = 0
                self.log(f"=== Cycle {self.cycle_count} complete ===")
        else:
            self.log("  Frontend stays in this step (NOT OK sent)")
        if self.wait_for_fe.get():
            self.pending_queue = cmd["queue"]
            self.root.after(self.POLL_MS, self._poll_consumed)
        self.update_display()
        return True

    def _poll_consumed(self):
        if self.pending_queue is None:
            return
        if queue_consumed(self.pending_queue):
            self.pending_queue = None
            self.update_display()
            if self.auto_running:
                self.root.after(self.AUTO_STEP_GAP_MS, self._auto_run_step)
            return
        self.root.after(self.POLL_MS, self._poll_consumed)

    def execute_command(self, result):
        if self.pending_queue is not None:
            self.log("Previous message not yet consumed by the FE - not sending")
            return
        try:
            self._send(result)
        except Exception as e:
            self.log(f"Error: {e}")
            messagebox.showerror("Error", f"Failed to send command:\n{e}")

    def reset_sequence(self):
        self.current_command_index = 0
        self.pending_queue = None
        self.auto_running = False
        self.update_display()
        self.log("Reset to start of sequence")

    def flush_redis(self):
        try:
            trialRedisConnection.flushdb()
            self.pending_queue = None
            self.update_display()
            self.log("Redis database flushed")
        except Exception as e:
            self.log(f"Error flushing Redis: {e}")

    def auto_run_all_ok(self):
        """Run one full cycle with OK results, one message per frontend state."""
        self.log("=== Starting auto-run with all OK ===")
        self.current_command_index = 0
        self.auto_running = True
        self.update_display()
        self._auto_run_step()

    def _auto_run_step(self):
        if not self.auto_running:
            return
        cycle_before = self.cycle_count
        try:
            self._send(ok)
        except Exception as e:
            self.auto_running = False
            self.update_display()
            self.log(f"Auto-run error: {e}")
            return
        if self.cycle_count != cycle_before:
            self.auto_running = False
            self.update_display()
            self.log(f"=== Auto-run complete! Cycle {self.cycle_count} ===")
            return
        if not self.wait_for_fe.get():
            self.root.after(self.AUTO_STEP_GAP_MS, self._auto_run_step)


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 60)
    print("AshokLeyland Front End Tester - Machine 2, 15 operator steps")
    print("=" * 60)
    for state, path in IMAGE_PATHS.items():
        print(f"  {state.name}: {path}")
    trialRedisConnection.flushdb()
    print("Redis flushed. Starting GUI...")
    root = tk.Tk()
    FrontEndTesterApp(root)
    root.geometry("600x560")
    root.minsize(520, 480)
    root.mainloop()


if __name__ == "__main__":
    main()
