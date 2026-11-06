import threading
import time
import logging
from typing import List, Optional
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException
from statemachine.StateMachine import MachineState


class IOServer_ModbusTCP:
    def __init__(self, plc_ip: str = "192.168.1.100", plc_port: int = 502):
        # Class variables
        self.machineState = MachineState()  # Instance of MachineState
        self.shutdown = False
        self.read_interval = 0.1  # 100 ms
        self.connection_check_interval = 5  # 5 seconds
        self.torque_read_attempts = 3  # Number of reads for torque stabilization
        self.plc_ip = plc_ip
        self.plc_port = plc_port
        self.client = None
        self.read_thread = None
        self.connection_thread = None
        self.lock = threading.Lock()

        # Tag definitions
        self.read_tags = {
            "PLC_PC_CheckQRCode": {"state": MachineState.READ_QR_CODE, "type": "bool"},
            "PLC_PC_CheckBearing": {"state": MachineState.TAKE_PICTURE_FOR_CHECKING_BEARING, "type": "bool"},
            "PLC_PC_CheckHubAndSecondBearing": {"state": MachineState.TAKE_PICTURE_FOR_CHECKING_BEARING_AND_HUB,
                                                "type": "bool"},
            "PLC_PC_CheckNutAndPlateWasher": {"state": MachineState.TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER,
                                              "type": "bool"},
            "PLC_PC_Station2TorqueValue": {
                "state": [MachineState.READ_TIGHTENING_TORQUE_1, MachineState.READ_TIGHTENING_TORQUE_2,
                          MachineState.READ_TIGHTENING_TORQUE_3],
                "type": "float"
            },
            "PLC_PC_Station3TorqueValue": {
                "state": [MachineState.READ_ROTATION_TORQUE_1, MachineState.READ_ROTATION_TORQUE_2,
                          MachineState.READ_ROTATION_TORQUE_3],
                "type": "float"
            },
            "PLC_PC_CheckSplitPinAndWasher": {"state": MachineState.TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER,
                                              "type": "bool"},
            "PLC_PC_CheckCap": {"state": MachineState.TAKE_PICTURE_FOR_CHECKING_CAP, "type": "bool"},
            "PLC_PC_CheckBunk": {"state": MachineState.TAKE_PICTURE_FOR_CHECKING_BUNG, "type": "bool"},
            "PLC_PC_CapPressDone": {"state": MachineState.PRESS_COMPLETED_SUCCESSFULLY, "type": "bool"}
        }
        self.write_tags = {
            "PC_PLC_QRCodeCheckingOK": {"state": MachineState.READ_QR_CODE, "type": "bool"},
            "PC_PLC_BearingCheckingOK": {"state": MachineState.TAKE_PICTURE_FOR_CHECKING_BEARING, "type": "bool"},
            "PC_PLC_HubAndSecondBearingCheckingOK": {"state": MachineState.TAKE_PICTURE_FOR_CHECKING_BEARING_AND_HUB,
                                                     "type": "bool"},
            "PC_PLC_NutAndPlateWasherCheckingOK": {"state": MachineState.TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER,
                                                   "type": "bool"},
            "PC_PLC_SplitPinAndWasherCheckingOK": {"state": MachineState.TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER,
                                                   "type": "bool"},
            "PC_PLC_CapCheckingOK": {"state": MachineState.TAKE_PICTURE_FOR_CHECKING_CAP, "type": "bool"},
            "PC_PLC_BungCheckingOK": {"state": MachineState.TAKE_PICTURE_FOR_CHECKING_BUNG, "type": "bool"}
        }

        # Initialize logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

        # Start PLC connection and threads
        self.connect_plc()
        self.start_threads()

    def connect_plc(self) -> bool:
        """Connect to the PLC."""
        try:
            self.client = ModbusTcpClient(self.plc_ip, port=self.plc_port)
            if self.client.connect():
                self.logger.info("Connected to PLC at %s:%d", self.plc_ip, self.plc_port)
                return True
            else:
                self.logger.error("Failed to connect to PLC")
                return False
        except Exception as e:
            self.logger.error("PLC connection error: %s", e)
            return False

    def start_threads(self):
        """Start the read and connection check threads."""
        self.read_thread = threading.Thread(target=self.read_loop, daemon=True)
        self.connection_thread = threading.Thread(target=self.connection_check_loop, daemon=True)
        self.read_thread.start()
        self.connection_thread.start()
        self.logger.info("Started read and connection check threads")

    # Read functions
    def read_bool(self, tag: str, address: int, expected_value: bool, debug: bool = False) -> Optional[bool]:
        """Read a boolean tag from the PLC."""
        try:
            result = self.client.read_coils(address, 1)
            if result.isError():
                self.logger.error("Error reading bool tag %s", tag)
                return None
            value = result.bits[0]
            if debug and value == expected_value:
                self.logger.info("Read bool tag %s: %s", tag, value)
            return value
        except ModbusException as e:
            self.logger.error("Modbus error reading bool tag %s: %s", tag, e)
            return None

    def read_float(self, tag: str, address: int, expected_value: float, debug: bool = False) -> Optional[float]:
        """Read a float tag from the PLC."""
        try:
            result = self.client.read_holding_registers(address, 2)
            if result.isError():
                self.logger.error("Error reading float tag %s", tag)
                return None
            value = self.registers_to_float(result.registers)
            if debug and abs(value - expected_value) < 0.01:
                self.logger.info("Read float tag %s: %f", tag, value)
            return value
        except ModbusException as e:
            self.logger.error("Modbus error reading float tag %s: %s", tag, e)
            return None

    # Write functions
    def write_bool(self, tag: str, address: int, value: bool, debug: bool = False) -> bool:
        """Write a boolean tag to the PLC."""
        try:
            result = self.client.write_coil(address, value)
            if result.isError():
                self.logger.error("Error writing bool tag %s", tag)
                return False
            if debug:
                self.logger.info("Wrote bool tag %s: %s", tag, value)
            return True
        except ModbusException as e:
            self.logger.error("Modbus error writing bool tag %s: %s", tag, e)
            return False

    def registers_to_float(self, registers: List[int]) -> float:
        """Convert two 16-bit registers to a float (IEEE 754)."""
        combined = (registers[0] << 16) | registers[1]
        import struct
        return struct.unpack('>f', struct.pack('>I', combined))[0]

    def process_state(self) -> bool:
        """Process the current machine state and update to the next state if needed."""
        with self.lock:
            current_state = self.machineState.getCurrentState()
            tags_to_read = [
                tag for tag, info in self.read_tags.items()
                if
                (isinstance(info["state"], list) and current_state in info["state"]) or info["state"] == current_state
            ]
            tags_to_write = [
                tag for tag, info in self.write_tags.items() if info["state"] == current_state
            ]

            # Special case for READ_QR_CODE and TAKE_PICTURE_FOR_CHECKING_KNUCKLE
            if current_state == MachineState.READ_QR_CODE:
                tags_to_read.append("PLC_PC_CheckQRCode")  # Ensure QR code check is included

            # Read tags
            for tag in tags_to_read:
                tag_info = self.read_tags[tag]
                address = self.get_tag_address(tag)
                if tag_info["type"] == "bool":
                    value = self.read_bool(tag, address, expected_value=True, debug=True)
                    if value is None:
                        return False
                    if value:
                        result = self.perform_action(tag)
                        if tags_to_write:
                            write_tag = tags_to_write[0]
                            write_address = self.get_tag_address(write_tag)
                            if self.write_bool(write_tag, write_address, result, debug=True):
                                if current_state == MachineState.READ_QR_CODE:
                                    self.machineState.setCurrentState(MachineState.TAKE_PICTURE_FOR_CHECKING_BEARING)
                                else:
                                    self.machineState.incrementState()
                                return True
                elif tag_info["type"] == "float":
                    values = []
                    for _ in range(self.torque_read_attempts):
                        value = self.read_float(tag, address, expected_value=0.0, debug=True)
                        if value is None:
                            return False
                        values.append(value)
                        time.sleep(0.1)
                    if max(values) - min(values) < 0.1:  # Stability check
                        self.machineState.incrementState()
                        return True

            # For states with no write operation (e.g., PRESS_COMPLETED_SUCCESSFULLY)
            if current_state == MachineState.PRESS_COMPLETED_SUCCESSFULLY and tags_to_read:
                tag = tags_to_read[0]
                address = self.get_tag_address(tag)
                value = self.read_bool(tag, address, expected_value=True, debug=True)
                if value:
                    self.machineState.setCurrentState(MachineState.READ_QR_CODE)
                    return True

            return False

    def perform_action(self, tag: str) -> bool:
        """Placeholder for performing actions (e.g., take picture, analyze)."""
        self.logger.info("Performing action for tag %s", tag)
        return True  # Simulated success

    def get_tag_address(self, tag: str) -> int:
        """Placeholder for mapping tags to Modbus addresses."""
        return 0  # Replace with actual address mapping

    def read_loop(self):
        """Main read loop to process machine states."""
        while not self.shutdown:
            if self.client and self.client.is_socket_open():
                if self.process_state():
                    self.logger.info("State updated to %d", self.machineState.getCurrentState())
            else:
                self.logger.warning("PLC connection lost in read loop")
                self.connect_plc()
            time.sleep(self.read_interval)

    def connection_check_loop(self):
        """Loop to check PLC connection status."""
        while not self.shutdown:
            if not self.client or not self.client.is_socket_open():
                self.logger.warning("PLC connection lost in connection check")
                self.connect_plc()
            else:
                try:
                    self.client.read_coils(0, 1)
                    self.logger.debug("PLC connection alive")
                except ModbusException:
                    self.logger.warning("PLC connection test failed")
                    self.connect_plc()
            time.sleep(self.connection_check_interval)

    def __del__(self):
        """Cleanup on object destruction."""
        self.shutdown = True
        if self.client and self.client.is_socket_open():
            self.client.close()
        self.logger.info("IOServer shutdown")