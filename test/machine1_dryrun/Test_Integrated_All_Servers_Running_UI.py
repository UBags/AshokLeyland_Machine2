"""
Integrated tester that plays the IOServer's role while all the OTHER servers are running (QRCodeServer,
CameraProcessorServer, Frontend, DBServer, Heartbeat server) - run from the project root with
`python -m test.machine1_dryrun.Test_Integrated_All_Servers_Running_UI` (or `python test/machine1_dryrun/Test_Integrated_All_Servers_Running_UI.py`).
Do NOT run the real IOServer at the same time: this program replaces it (no PLC needed).

A Tk window walks through the Machine 2 (28-state) cycle as the 15 operator process steps. Each step sends exactly
what IOServer sends in that state:
  - QR                    -> io2qrcode.q   sendDataFromIOServerToQRCodeServer()           (state READ_QR_CODE)
  - 9 visual checks       -> io2camera.q   sendDataFromIOServerToCameraServer(<picture state>)
  - Tightening Torque 1/2 -> io2frontend.q sendDataFromIOServerToFEServer(READ_TIGHTENING_TORQUE_1/2, value=...)
  - Component / Cap press -> io2frontend.q sendDataFromIOServerToFEServer(READ_COMPONENT_PRESS_DONE / READ_CAP_PRESS_DONE)
  - Free Rotation Torque  -> io2frontend.q sendDataFromIOServerToFEServer(READ_FREE_ROTATION_TORQUE_1, value=...,
                             moveToState=READ_QR_CODE, action=moveAheadToNextComponent)   (final step; ends the cycle)
There is no "free rotations done" step on Machine 2.

Use "Next" to send the next step in order (it wraps back to QR after the final step), or click any step button to
send that step directly. Every reader trims its queue to the latest message, so send a step only when the frontend
is waiting for it (i.e. after the previous step's result is shown).

Options:
  --no-flush   do not clear Redis at start-up (default: Redis is flushed, as before, which also clears the queues of
               the running servers).
"""

import os
import sys
import argparse
import tkinter as tk
from tkinter import ttk
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # <root>/test/<dry-run folder>/this file
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from redis import Redis
from utils.RedisUtils import (
    sendDataFromIOServerToQRCodeServer,
    sendDataFromIOServerToCameraServer,
    sendDataFromIOServerToFEServer,
    moveAheadToNextComponent,
    ok,
)
from statemachine.StateMachine import MachineState
from Configuration import CosThetaConfigurator

# Default values sent for the IO steps (editable in the window)
DEFAULT_TIGHTENING_TORQUE_1 = 11.8
DEFAULT_TIGHTENING_TORQUE_2 = 11.6
DEFAULT_FREE_ROTATION_TORQUE = 0.9

# (description, kind, MachineState) in Machine 2 order. kind: "qr" | "camera" | "value" | "press" | "final"
STEPS = [
    ("Scan QR Code",                              "qr",     MachineState.READ_QR_CODE),
    ("Take picture for checking knuckle",         "camera", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE),
    ("Take picture for checking hub (with bottom bearing)", "camera",
                                                            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING),
    ("Take picture for checking top bearing",     "camera", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING),
    ("Take picture for checking nut and plate washer", "camera",
                                                            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER),
    ("Read tightening torque 1",                  "value",  MachineState.READ_TIGHTENING_TORQUE_1),
    ("Take picture for checking bunk for component press", "camera",
                                                            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS),
    ("Component press done",                      "press",  MachineState.READ_COMPONENT_PRESS_DONE),
    ("Take picture for checking no bunk",         "camera", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK),
    ("Read tightening torque 2",                  "value",  MachineState.READ_TIGHTENING_TORQUE_2),
    ("Take picture for checking split pin and washer", "camera",
                                                            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER),
    ("Take picture for checking cap",             "camera", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP),
    ("Take picture for checking bunk for cap press", "camera",
                                                            MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS),
    ("Cap press done",                            "press",  MachineState.READ_CAP_PRESS_DONE),
    ("Read free rotation torque (final - next component)", "final", MachineState.READ_FREE_ROTATION_TORQUE_1),
]

# Short labels for the per-step buttons (the operator's process steps)
STEP_BUTTON_LABELS = [
    "QR", "Knuckle", "Hub", "Top Bearing", "Nut & Plate Washer", "Tightening Torque 1", "Bunk - Comp Press",
    "Component Press", "No Bunk", "Tightening Torque 2", "Split Pin & Washer", "Cap", "Bunk - Cap Press",
    "Cap Press", "Free Rotation Torque",
]


class App:
    def __init__(self, root, redisConnection: Redis):
        self.root = root
        self.redis = redisConnection
        self.root.title("Integrated Test (plays IOServer) - Machine 2")
        self.current_command_index = 0
        self.sent_log = []          # (time, step index, description, ok)

        # Next button (sends the next step in Machine 2 order, wraps after the final step)
        self.next_button = tk.Button(root, text="Next", command=self.execute_next_command, font=("Arial", 14), width=10)
        self.next_button.pack(pady=10)

        # Label to show current command status
        self.status_label = tk.Label(root, text=f"Next: {STEPS[0][0]}", font=("Arial", 12))
        self.status_label.pack(pady=5)

        # Values for the IO steps
        values = ttk.Frame(root)
        values.pack(pady=5)
        self.torque1_var = tk.StringVar(value=str(DEFAULT_TIGHTENING_TORQUE_1))
        self.torque2_var = tk.StringVar(value=str(DEFAULT_TIGHTENING_TORQUE_2))
        self.free_rotation_var = tk.StringVar(value=str(DEFAULT_FREE_ROTATION_TORQUE))
        for col, (label, var) in enumerate([("Tightening Torque 1", self.torque1_var),
                                            ("Tightening Torque 2", self.torque2_var),
                                            ("Free Rotation Torque", self.free_rotation_var)]):
            ttk.Label(values, text=label).grid(row=0, column=2 * col, padx=4)
            ttk.Entry(values, textvariable=var, width=7).grid(row=0, column=2 * col + 1, padx=4)

        # One button per process step, in Machine 2 order
        steps = ttk.LabelFrame(root, text="Send a step directly")
        steps.pack(padx=10, pady=5, fill="x")
        self.step_buttons = []
        for i, label in enumerate(STEP_BUTTON_LABELS):
            b = tk.Button(steps, text=f"{i + 1}. {label}", width=22, command=lambda i=i: self.execute_step(i))
            b.grid(row=i // 3, column=i % 3, padx=3, pady=2, sticky="w")
            self.step_buttons.append(b)

        # Log of what was sent
        self.log = tk.Text(root, height=10, width=90, font=("Consolas", 9))
        self.log.pack(padx=10, pady=5, fill="both", expand=True)
        self._highlight()

    # ---------------------------------------------------------------------------------------------------------
    def _float(self, var: tk.StringVar, default: float) -> float:
        try:
            return float(var.get())
        except ValueError:
            return default

    def build_command(self, index: int):
        """Return a zero-argument callable that sends step `index` exactly as IOServer would."""
        description, kind, state = STEPS[index]
        if kind == "qr":
            return lambda: sendDataFromIOServerToQRCodeServer(self.redis)
        if kind == "camera":
            return lambda: sendDataFromIOServerToCameraServer(self.redis, currentMachineState=state)
        if kind == "press":
            return lambda: sendDataFromIOServerToFEServer(self.redis, currentMachineState=state)
        if kind == "value":
            if state == MachineState.READ_TIGHTENING_TORQUE_1:
                return lambda: sendDataFromIOServerToFEServer(
                    self.redis, currentMachineState=state, value=self._float(self.torque1_var, DEFAULT_TIGHTENING_TORQUE_1))
            return lambda: sendDataFromIOServerToFEServer(
                self.redis, currentMachineState=state, value=self._float(self.torque2_var, DEFAULT_TIGHTENING_TORQUE_2))
        if kind == "final":
            return lambda: sendDataFromIOServerToFEServer(
                self.redis, currentMachineState=state,
                value=self._float(self.free_rotation_var, DEFAULT_FREE_ROTATION_TORQUE),
                moveToState=MachineState.READ_QR_CODE, action=moveAheadToNextComponent)
        raise ValueError(kind)

    def execute_step(self, index: int) -> bool:
        description = STEPS[index][0]
        try:
            sent = bool(self.build_command(index)())
            self.status_label.config(text=f"Executed: {description}" if sent else f"Failed to send: {description}")
        except Exception as e:
            sent = False
            self.status_label.config(text=f"Error: {str(e)}")
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.sent_log.append((stamp, index, description, sent))
        self.log.insert("end", f"{stamp}  {'SENT' if sent else 'FAIL'}  {index + 1:2d}. {description}  "
                               f"[{STEPS[index][2].name}]\n")
        self.log.see("end")
        # Continue the Next sequence after the step that was just sent
        self.current_command_index = (index + 1) % len(STEPS)
        if sent:
            self.status_label.config(text=f"Executed: {description}   |   Next: {STEPS[self.current_command_index][0]}")
        self._highlight()
        return sent

    def execute_next_command(self) -> bool:
        return self.execute_step(self.current_command_index)

    def _highlight(self):
        for i, b in enumerate(self.step_buttons):
            b.config(relief="sunken" if i == self.current_command_index else "raised")


def main():
    parser = argparse.ArgumentParser(description="Integrated tester that plays the IOServer (Machine 2)")
    parser.add_argument("--no-flush", action="store_true", help="do not flush Redis at start-up")
    args = parser.parse_args()

    # Initialize Redis connection
    testRedisConnection = Redis(
        CosThetaConfigurator.getInstance().getRedisHost(),
        CosThetaConfigurator.getInstance().getRedisPort(),
        retry_on_timeout=True
    )
    if not args.no_flush:
        testRedisConnection.flushdb()

    root = tk.Tk()
    App(root, testRedisConnection)
    root.geometry("760x560")
    root.mainloop()


# Create and run the Tkinter app
if __name__ == "__main__":
    main()
