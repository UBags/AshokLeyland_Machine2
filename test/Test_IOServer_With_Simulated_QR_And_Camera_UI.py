"""
Tests a running IOServer by playing the QRCodeServer and CameraProcessorServer towards it (results only) - run from
the project root with `python -m test.Test_IOServer_With_Simulated_QR_And_Camera_UI`
(or `python test/Test_IOServer_With_Simulated_QR_And_Camera_UI.py`).
Run it alongside an IOServer (real PLC, or e.g. test/Test_IOServer_With_Mock_PLC_UI.py), with NO real QRCodeServer or
CameraProcessorServer running.

A Tk window steps through the 10 results the IOServer waits for in a Machine 2 (28-state) cycle:
  - QR code             -> qrcode2io.q  sendDataFromQRCodeServerToIOServer(qrCode=..., ok)   (IOServer state WRITE_QR_CODE;
                           the QR text decides the LHS/RHS + model tags IOServer writes to the PLC in state 2)
  - 9 visual checks     -> camera2io.q  sendDataFromCameraServerToIOServer(OK / NOT OK, <picture state>), in M2 order:
                           knuckle, hub (with bottom bearing), top bearing, nut & plate washer, bunk for component press,
                           no bunk, split pin & washer, cap, bunk for cap press.
Torques, component/cap press and the free-rotation torque come from the PLC, not from this program.
"Send OK" / "Send NOT OK" send the next result in order (wrapping back to the QR code); send each one only while the
IOServer is in the matching WRITE_... state, because IOServer trims camera2io.q to the latest message.

Options:
  --no-flush   do not clear Redis at start-up (default: Redis is flushed, as before).
"""

import os
import sys
import argparse
import tkinter as tk
from tkinter import ttk
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from redis import Redis
from utils.RedisUtils import (
    sendDataFromQRCodeServerToIOServer,
    sendDataFromCameraServerToIOServer,
    ok, notok,
)
from statemachine.StateMachine import MachineState
from Configuration import CosThetaConfigurator

# A QR code that matches qrcode.partmapping.1 (DOST - LHS - 2.0T) in ApplicationConfiguration.properties
DEFAULT_QR_CODE = "7204838$400112VA1D$11770$09.03.2025$C5211701$MEK$40cR4-B$1$SPDL ASSY-KNULH$SNPL-MAT$"

# (description, picture state or None for the QR code) in Machine 2 order
STEPS = [
    ("QR Code", None),
    ("checking knuckle", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE),
    ("checking hub (with bottom bearing)", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING),
    ("checking top bearing", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING),
    ("checking nut and plate washer", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER),
    ("checking bunk for component press", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS),
    ("checking no bunk", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK),
    ("checking split pin and washer", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER),
    ("checking cap", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP),
    ("checking bunk for cap press", MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS),
]


class App:
    def __init__(self, root, redisConnection: Redis):
        self.root = root
        self.redis = redisConnection
        self.root.title("Camera/QR to IO Server Result Inputs - Machine 2")
        self.current_command_index = 0
        self.sent_log = []      # (time, step index, result, sent)

        qr = ttk.Frame(root)
        qr.pack(pady=(10, 0))
        ttk.Label(qr, text="QR code:").pack(side="left")
        self.qr_var = tk.StringVar(value=DEFAULT_QR_CODE)
        ttk.Entry(qr, textvariable=self.qr_var, width=70).pack(side="left", padx=4)

        buttons = ttk.Frame(root)
        buttons.pack(pady=10)
        self.next_button = tk.Button(buttons, text="Send OK", command=self.execute_next_command,
                                     font=("Arial", 14), width=12)
        self.next_button.pack(side="left", padx=5)
        self.notok_button = tk.Button(buttons, text="Send NOT OK", command=lambda: self.execute_next_command(ok_result=False),
                                      font=("Arial", 14), width=12)
        self.notok_button.pack(side="left", padx=5)

        # Label to show current command status
        self.status_label = tk.Label(root, text="Click Send OK to give QR Code input", font=("Arial", 12))
        self.status_label.pack(pady=10)

    def describe(self, index: int) -> str:
        return f"Send result for {STEPS[index][0]}"

    def execute_step(self, index: int, ok_result: bool = True) -> bool:
        description, state = STEPS[index]
        result = ok if ok_result else notok
        try:
            if state is None:
                sent = bool(sendDataFromQRCodeServerToIOServer(self.redis, qrCode=self.qr_var.get().strip(),
                                                                qrCodeStatus=result))
            else:
                sent = bool(sendDataFromCameraServerToIOServer(redisConnection=self.redis, result=result,
                                                               currentMachineState=state))
            self.status_label.config(text=f"Executed: {'OK' if ok_result else 'NOT OK'} for {description}")
        except Exception as e:
            sent = False
            self.status_label.config(text=f"Error: {str(e)}")
        self.sent_log.append((datetime.now().strftime("%H:%M:%S.%f")[:-3], index, result, sent))
        return sent

    def execute_next_command(self, ok_result: bool = True) -> bool:
        sent = self.execute_step(self.current_command_index, ok_result)
        # A NOT OK result is re-checked by the PLC, so stay on the same step; OK moves on (wrapping to the QR code)
        if sent and ok_result:
            self.current_command_index = (self.current_command_index + 1) % len(STEPS)
        nxt = STEPS[self.current_command_index][0]
        self.status_label.config(text=f"{self.status_label.cget('text')}   |   Next: {nxt}")
        return sent


def main():
    parser = argparse.ArgumentParser(description="Simulated QR/Camera results towards a running IOServer (Machine 2)")
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
    root.geometry("720x220")
    root.mainloop()


# Create and run the Tkinter app
if __name__ == "__main__":
    main()
