"""
IOServer Test Script - Redis Response Simulator
Sends QRCodeServer and CameraServer responses via Redis.

This script works with the actual IOServer - it simulates the responses that
would come from QRCodeServer and CameraServer through Redis queues.

For testing WITHOUT a PLC:
- You need to modify IOServer to use a mock PLC (see MockPLCIOServer below)
- OR use this with actual PLC hardware

For testing WITH a PLC:
- Run IOServer normally
- Use this script to send OK/NotOK responses when IOServer requests them
"""

import tkinter as tk
from tkinter import ttk
import threading
import time
from redis import Redis
from utils.RedisUtils import *
from Configuration import CosThetaConfigurator
from statemachine.StateMachine import MachineState


# =============================================================================
# REDIS RESPONSE SENDER
# =============================================================================

class ResponseSenderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("IOServer Test - Response Simulator")
        self.root.geometry("700x600")

        # Initialize Redis
        self.redis = Redis(
            CosThetaConfigurator.getInstance().getRedisHost(),
            CosThetaConfigurator.getInstance().getRedisPort(),
            retry_on_timeout=True
        )

        # Torque values
        self.torque1_value = tk.DoubleVar(value=50.0)
        self.torque2_value = tk.DoubleVar(value=55.0)
        self.rotation_torque_value = tk.DoubleVar(value=45.0)

        self._build_ui()
        self._start_heartbeat()

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Title
        ttk.Label(main_frame, text="IOServer Response Simulator",
                  font=("Arial", 14, "bold")).pack(pady=5)

        # Status
        status_frame = ttk.LabelFrame(main_frame, text="Status", padding="5")
        status_frame.pack(fill=tk.X, pady=5)
        self.status_label = ttk.Label(status_frame, text="Ready", font=("Arial", 10))
        self.status_label.pack()

        # Vision checks frame
        vision_frame = ttk.LabelFrame(main_frame, text="Vision Check Responses", padding="10")
        vision_frame.pack(fill=tk.X, pady=5)

        # Define vision check steps
        vision_steps = [
            ("Step 0: QR Code", self._send_qr_ok, self._send_qr_notok),
            ("Step 1: Knuckle",
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, ok),
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE, notok)),
            ("Step 2: Hub",
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING, ok),
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING,
                                                notok)),
            ("Step 3: Hub + 2nd Bearing",
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, ok),
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING, notok)),
            ("Step 4: Nut + Plate Washer",
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER, ok),
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER,
                                                notok)),
            ("Step 7: No Cap Bung",
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS,
                                                ok),
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS,
                                                notok)),
            ("Step 9: No Bung",
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK, ok),
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK, notok)),
            ("Step 11: Split Pin + Washer",
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER, ok),
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER,
                                                notok)),
            ("Step 12: Cap",
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP, ok),
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP, notok)),
            ("Step 13: Bung",
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS, ok),
             lambda: self._send_camera_response(MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS, notok)),
        ]

        for i, (name, ok_func, notok_func) in enumerate(vision_steps):
            row = ttk.Frame(vision_frame)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=name, width=25, anchor="w").pack(side=tk.LEFT)
            ttk.Button(row, text="OK", width=8, command=ok_func).pack(side=tk.LEFT, padx=2)
            ttk.Button(row, text="NotOK", width=8, command=notok_func).pack(side=tk.LEFT, padx=2)

        # Quick actions frame
        quick_frame = ttk.LabelFrame(main_frame, text="Quick Actions", padding="10")
        quick_frame.pack(fill=tk.X, pady=5)

        ttk.Button(quick_frame, text="Clear Redis", command=self._clear_redis).pack(side=tk.LEFT, padx=5)
        ttk.Button(quick_frame, text="Send All OK (Full Cycle)", command=self._send_all_ok).pack(side=tk.LEFT, padx=5)

        # Instructions
        instr_frame = ttk.LabelFrame(main_frame, text="Instructions", padding="10")
        instr_frame.pack(fill=tk.X, pady=5)

        instructions = """
1. Start IOServer (with PLC connected, or use MockPLCIOServer)
2. When IOServer reaches a vision check state, it sends request to Camera/QRCode server
3. Click the corresponding OK or NotOK button here to respond
4. IOServer will process the response and move to next state

Signal-only states (Steps 5, 6, 8, 10, 14, 15) don't need responses from this tool
- they are triggered by PLC signals directly.
"""
        ttk.Label(instr_frame, text=instructions, justify=tk.LEFT).pack()

    def _update_status(self, msg):
        self.status_label.config(text=msg)
        self.root.update()

    def _start_heartbeat(self):
        def loop():
            while True:
                try:
                    sendCombinedHeartbeatFromHeartbeatServerToIOServer(self.redis, ALIVE)
                except:
                    pass
                time.sleep(1)

        threading.Thread(target=loop, daemon=True).start()

    def _send_qr_ok(self):
        sendDataFromQRCodeServerToIOServer(self.redis, qrCodeStatus=ok)
        self._update_status("QR Code: OK sent")

    def _send_qr_notok(self):
        sendDataFromQRCodeServerToIOServer(self.redis, qrCodeStatus=notok)
        self._update_status("QR Code: NotOK sent")

    def _send_camera_response(self, state, result):
        sendDataFromCameraServerToIOServer(self.redis, result=result, currentMachineState=state)
        self._update_status(f"{state}: {result} sent")

    def _clear_redis(self):
        self.redis.flushdb()
        self._update_status("Redis cleared")

    def _send_all_ok(self):
        """Send OK responses for a complete cycle with delays."""

        def cycle():
            steps = [
                ("QR Code", lambda: sendDataFromQRCodeServerToIOServer(self.redis, qrCodeStatus=ok)),
                ("Knuckle", lambda: sendDataFromCameraServerToIOServer(self.redis, ok,
                                                                       MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)),
                ("Hub", lambda: sendDataFromCameraServerToIOServer(self.redis, ok,
                                                                   MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_BOTTOM_BEARING)),
                ("Hub+2ndBearing", lambda: sendDataFromCameraServerToIOServer(self.redis, ok,
                                                                              MachineState.READ_TAKE_PICTURE_FOR_CHECKING_TOP_BEARING)),
                ("Nut+PlateWasher", lambda: sendDataFromCameraServerToIOServer(self.redis, ok,
                                                                               MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER)),
                ("NoCapBung", lambda: sendDataFromCameraServerToIOServer(self.redis, ok,
                                                                         MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_COMPONENT_PRESS)),
                ("NoBung", lambda: sendDataFromCameraServerToIOServer(self.redis, ok,
                                                                      MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NO_BUNK)),
                ("SplitPin+Washer", lambda: sendDataFromCameraServerToIOServer(self.redis, ok,
                                                                               MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER)),
                ("Cap", lambda: sendDataFromCameraServerToIOServer(self.redis, ok,
                                                                   MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP)),
                ("Bung", lambda: sendDataFromCameraServerToIOServer(self.redis, ok,
                                                                    MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNK_FOR_CAP_PRESS)),
            ]
            for name, func in steps:
                self._update_status(f"Sending OK for: {name}")
                func()
                time.sleep(2)
            self._update_status("All OK responses sent!")

        threading.Thread(target=cycle, daemon=True).start()


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    root = tk.Tk()
    app = ResponseSenderApp(root)
    root.mainloop()