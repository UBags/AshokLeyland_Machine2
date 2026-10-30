from utils.RedisUtils import *
import cv2

# trialRedisConnection = Redis("localhost", 6379)
# image1 = cv2.imread("C:/Temp/TrialImages/Knuckle/2025-05-23-12-28-50-305682-0.png")
# image2 = cv2.imread("C:/Temp/TrialImages/Knuckle/2025-05-23-12-28-50-305682-1.png")


# def execute(step : int = 1):

    # if step == 1:
    #     sendDataFromQRCodeServerToIOServer(trialRedisConnection)
    # elif step == 2:
    #     sendDataFromCameraServerToIOServer(trialRedisConnection, result=ok, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)
    # elif step == 3:
    #     sendDataFromCameraServerToIOServer(trialRedisConnection, result=ok, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BEARING)
    # elif step == 4:
    #     sendDataFromCameraServerToIOServer(trialRedisConnection, result=ok, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_SECOND_BEARING)
    # elif step == 5:
    #     sendDataFromCameraServerToIOServer(trialRedisConnection, result=ok, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER)
    # elif step == 6:
    #     sendDataFromCameraServerToIOServer(trialRedisConnection, result=ok, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER)
    # elif step == 7:
    #     sendDataFromCameraServerToIOServer(trialRedisConnection, result=ok, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP)
    # elif step == 8:
    #     sendDataFromCameraServerToIOServer(trialRedisConnection, result=ok, currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG)

# execute(1)

# def executeSequence1():
#     time.sleep(5)
#     sendDataFromQRCodeServerToIOServer(redisConnection=trialRedisConnection)
#     time.sleep(10)
#     # sendDataFromCameraServerToIOServer(redisConnection=trialRedisConnection, result = notok, currentMachineState = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)
#     # time.sleep(15)
#     sendDataFromCameraServerToIOServer(redisConnection=trialRedisConnection, result = ok, currentMachineState = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE)
#     time.sleep(10)
#     # sendDataFromCameraServerToIOServer(redisConnection=trialRedisConnection, result = notok, currentMachineState = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BEARING)
#     # time.sleep(15)
#     sendDataFromCameraServerToIOServer(redisConnection=trialRedisConnection, result = ok, currentMachineState = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BEARING)
#     time.sleep(10)
#     # sendDataFromCameraServerToIOServer(redisConnection=trialRedisConnection, result = notok, currentMachineState = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_SECOND_BEARING)
#     # time.sleep(15)
#     sendDataFromCameraServerToIOServer(redisConnection=trialRedisConnection, result = ok, currentMachineState = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_SECOND_BEARING)
#     time.sleep(10)
#     # sendDataFromCameraServerToIOServer(redisConnection=trialRedisConnection, result = notok, currentMachineState = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER)
#     # time.sleep(15)
#     sendDataFromCameraServerToIOServer(redisConnection=trialRedisConnection, result = ok, currentMachineState = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER)
#     time.sleep(10)

# def executeSequence2():
#     time.sleep(10)
#     # sendDataFromCameraServerToIOServer(redisConnection=trialRedisConnection, result = notok, currentMachineState = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER)
#     # time.sleep(10)
#     sendDataFromCameraServerToIOServer(redisConnection=trialRedisConnection, result = ok, currentMachineState = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER)
#     time.sleep(10)
#     # sendDataFromCameraServerToIOServer(redisConnection=trialRedisConnection, result = notok, currentMachineState = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP)
#     # time.sleep(20)
#     sendDataFromCameraServerToIOServer(redisConnection=trialRedisConnection, result = ok, currentMachineState = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP)
#     time.sleep(10)
#     # sendDataFromCameraServerToIOServer(redisConnection=trialRedisConnection, result = notok, currentMachineState = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG)
#     # time.sleep(20)
#     sendDataFromCameraServerToIOServer(redisConnection=trialRedisConnection, result = ok, currentMachineState = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG)

# clearQueues(trialRedisConnection)
# executeSequence1()
# executeSequence2()

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
    lambda: sendDataFromQRCodeServerToIOServer(testRedisConnection),
    lambda: sendDataFromCameraServerToIOServer(testRedisConnection, result=ok,
                                               currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_KNUCKLE),
    lambda: sendDataFromCameraServerToIOServer(testRedisConnection, result=ok,
                                               currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_HUB_AND_FIRST_BEARING),
    lambda: sendDataFromCameraServerToIOServer(testRedisConnection, result=ok,
                                               currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SECOND_BEARING),
    lambda: sendDataFromCameraServerToIOServer(testRedisConnection, result=ok,
                                               currentMachineState=MachineState.READ_TAKE_PICTURE_FOR_CHECKING_NUT_AND_PLATEWASHER),
    lambda: sendDataFromCameraServerToIOServer(redisConnection=testRedisConnection, result = ok,
                                               currentMachineState = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_SPLITPIN_AND_WASHER),
    lambda: sendDataFromCameraServerToIOServer(redisConnection=testRedisConnection, result = ok,
                                               currentMachineState = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_CAP),
    lambda: sendDataFromCameraServerToIOServer(redisConnection=testRedisConnection, result = ok,
                                               currentMachineState = MachineState.READ_TAKE_PICTURE_FOR_CHECKING_BUNG)
]


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Camera to IO Server Result Inputs")
        self.current_command_index = 0

        # Create and pack the Next button
        self.next_button = tk.Button(root, text="Send OK", command=self.execute_next_command, font=("Arial", 14), width=10)
        self.next_button.pack(pady=20)

        # Label to show current command status
        self.status_label = tk.Label(root, text="Click Send OK to give QR Code input", font=("Arial", 12))
        self.status_label.pack(pady=10)

        # Command descriptions for display
        self.command_descriptions = [
            "Send OK for QR Code",
            "Send OK for checking knuckle",
            "Send OK for checking hub and 1st bearing",
            "Send OK for checking second bearing",
            "Send OK for checking nut and plate washer",
            "Send OK for checking split pin and washer",
            "Send OK for for checking cap",
            "Send OK for checking bung"
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
                self.status_label.config(text=f"Send OK: {self.command_descriptions[self.current_command_index]}")
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
                self.status_label.config(text=f"Send OK: {self.command_descriptions[self.current_command_index]}")
            self.status_label.config(text="Click Send OK to start QR Code reading")

# Create and run the Tkinter app
if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.geometry("400x200")
    root.mainloop()