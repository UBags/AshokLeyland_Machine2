import subprocess
import json
# import _winapi
from _winapi import *
import pathlib
import shlex

# Use public RTSP Stream for testing
in_stream = 'rtsp://wowzaec2demo.streamlock.net/vod/mp4:BigBuckBunny_115k.mp4'

probe_command = ['C:/Program Files (x86)/ffmpeg/bin/ffprobe.exe',
                 '-loglevel', 'error',
                 '-rtsp_transport', 'tcp',  # Force TCP (for testing)]
                 '-select_streams', 'v:0',  # Select only video stream 0.
                 '-show_entries', 'stream=width,height', # Select only width and height entries
                 '-of', 'json', # Get output in JSON format
                 in_stream]

# Read video width, height using FFprobe:
p0 = subprocess.Popen(probe_command, stdout=subprocess.PIPE)
probe_str = p0.communicate()[0] # Reading content of p0.stdout (output of FFprobe) as string
p0.wait()
probe_dct = json.loads(probe_str) # Convert string from JSON format to dictonary.

# Get width and height from the dictonary
width = probe_dct['streams'][0]['width']
height = probe_dct['streams'][0]['height']
print(width)
print(height)