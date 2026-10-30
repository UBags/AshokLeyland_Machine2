import cv2
import os
from subprocess import PIPE, run

# camera_name = "Arducam OV9281 USB Camera"
command = ['ffmpeg','-f', 'dshow','-list_devices','true','-i','""']
result = run(command, stdout=PIPE, stderr=PIPE, universal_newlines=True)
print(result)
cam_id = 0

for item in result.stderr.splitlines():
#     if camera_name in item:
#         cam_id = int(item.split("[")[2].split(']')[0])
# print("cam id", cam_id)
    print(item)