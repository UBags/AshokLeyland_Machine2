# from redis import *
# from utils.RedisUtils import *
# from Configuration import CosThetaConfigurator
#
# testRedisConnection = Redis(CosThetaConfigurator.getInstance().getRedisHost(), CosThetaConfigurator.getInstance().getRedisPort(), retry_on_timeout=True)
# # testRedisConnection.flushdb()
#
# # TESTING NORMAL FUNCTIONING
#
# # Activate QR Code reading
# sendDataFromIOServerToQRCodeServer(testRedisConnection)
#
# # Activate CameraServer to take picture
# sendDataFromIOServerToCameraServer(testRedisConnection, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)
# sendDataFromIOServerToCameraServer(testRedisConnection, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BEARING)
# sendDataFromIOServerToCameraServer(testRedisConnection, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_SECOND_BEARING)
# sendDataFromIOServerToCameraServer(testRedisConnection, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER)

import tkinter as tk
from redis import Redis
from utils.RedisUtils import *
from Configuration import CosThetaConfigurator
from enum import Enum

# Initialize Redis connection
testRedisConnection = Redis(
    CosThetaConfigurator.getInstance().getRedisHost(),
    CosThetaConfigurator.getInstance().getRedisPort(),
    retry_on_timeout=True
)

testRedisConnection.flushdb()

# List of functions to execute in sequence
commands = [
    lambda: sendDataFromIOServerToQRCodeServer(testRedisConnection),
    lambda: sendDataFromIOServerToCameraServer(testRedisConnection,
                                               currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE),
    lambda: sendDataFromIOServerToCameraServer(testRedisConnection,
                                               currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_FIRST_BEARING),
    lambda: sendDataFromIOServerToCameraServer(testRedisConnection,
                                               currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SECOND_BEARING),
    lambda: sendDataFromIOServerToCameraServer(testRedisConnection,
                                               currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER),
    lambda: sendDataFromIOServerToFEServer(testRedisConnection,
                                           currentMachineState=MachineState.READ_TIGHTENING_TORQUE_1, value=11.8),
    lambda: sendDataFromIOServerToFEServer(testRedisConnection,
                                           currentMachineState=MachineState.READ_ROTATION_TORQUE_1, value=0.9),
    lambda: sendDataFromIOServerToFEServer(testRedisConnection,
                                           currentMachineState=MachineState.READ_TIGHTENING_TORQUE_2, value=11.6),
    lambda: sendDataFromIOServerToFEServer(testRedisConnection,
                                           currentMachineState=MachineState.READ_ROTATION_TORQUE_2, value=0.1),
    lambda: sendDataFromIOServerToFEServer(testRedisConnection,
                                           currentMachineState=MachineState.READ_TIGHTENING_TORQUE_3, value=11.4),
    lambda: sendDataFromIOServerToFEServer(testRedisConnection,
                                           currentMachineState=MachineState.READ_ROTATION_TORQUE_3, value=1.2),
    lambda: sendDataFromIOServerToCameraServer(testRedisConnection,
                                               currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER),
    lambda: sendDataFromIOServerToCameraServer(testRedisConnection,
                                               currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP),
    lambda: sendDataFromIOServerToCameraServer(testRedisConnection,
                                               currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG),
    lambda: sendDataFromIOServerToFEServer(testRedisConnection, currentMachineState=MachineState.READ_PRESS_RESULT_COMPLETED_SUCCESSFULLY,
                                                                          moveToState=MachineState.READ_QR_CODE, action=moveAheadToNextComponent)
]


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Camera Server Control")
        self.current_command_index = 0

        # Create and pack the Next button
        self.next_button = tk.Button(root, text="Next", command=self.execute_next_command, font=("Arial", 14), width=10)
        self.next_button.pack(pady=20)

        # Label to show current command status
        self.status_label = tk.Label(root, text="Scan QR Code", font=("Arial", 12))
        self.status_label.pack(pady=10)

        # Command descriptions for display
        self.command_descriptions = [
            "Scan QR Code",
            "Take picture for checking knuckle",
            "Take picture for checking bearing",
            "Take picture for checking hub and second bearing",
            "Take picture for checking split pin and washer",
            "Read tightening torque 1",
            "Read free rotation torque 1",
            "Read tightening torque 2",
            "Read free rotation torque 2",
            "Read tightening torque 3",
            "Read free rotation torque 3",
            "Take picture for checking split pin and washer",
            "Take picture for checking cap",
            "Take picture for checking bung",
            "Check if cap pressed"
        ]

    def execute_next_command(self):
        if self.current_command_index < len(commands):
            # Execute the current command
            try:
                commands[self.current_command_index]()
                self.status_label.config(text=f"Executed: {self.command_descriptions[self.current_command_index]}")
            except Exception as e:
                self.status_label.config(text=f"Error: {str(e)}")

            # Move to the next command
            self.current_command_index += 1

            # Update button and label for next step or completion
            if self.current_command_index < len(commands):
                self.status_label.config(text=f"Next: {self.command_descriptions[self.current_command_index]}")
            # else:
            #     self.next_button.config(state="disabled")
            #     self.status_label.config(text="All commands executed")
        else:
            # self.status_label.config(text="No more commands to execute")
            self.current_command_index = 0
            # Execute the current command
            try:
                commands[self.current_command_index]()
                self.status_label.config(text=f"Executed: {self.command_descriptions[self.current_command_index]}")
            except Exception as e:
                self.status_label.config(text=f"Error: {str(e)}")

            # Move to the next command
            self.current_command_index += 1

            # Update button and label for next step or completion
            if self.current_command_index < len(commands):
                self.status_label.config(text=f"Next: {self.command_descriptions[self.current_command_index]}")
            self.status_label.config(text="Click Next to start QR Code reading")

# Create and run the Tkinter app
if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.geometry("500x200")
    root.mainloop()