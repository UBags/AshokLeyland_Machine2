import cv2
import numpy as np
import subprocess

# Use public RTSP Stream for testing
in_stream = 'rtsp://freja.hiof.no:1935/rtplive/definst/hessdalen03.stream'

if True:
    # Read video width, height and framerate using OpenCV (use it if you don't know the size of the video frames).

    # Use public RTSP Streaming for testing:
    cap = cv2.VideoCapture(in_stream)

    framerate = cap.get(5)  # frame rate

    # Get resolution of input video
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Release VideoCapture - it was used just for getting video resolution
    cap.release()
else:
    # Set the size here, if video frame size is known
    width = 240
    height = 160

command = ['C:/ffmpeg/bin/ffmpeg.exe',
           # Using absolute path for example (in Linux replacing 'C:/ffmpeg/bin/ffmpeg.exe' with 'ffmpeg' supposes to work).
           # '-rtsp_flags', 'listen',   # The "listening" feature is not working (probably because the stream is from the web)
           '-rtsp_transport', 'tcp',  # Force TCP (for testing)
           '-max_delay', '30000000',  # 30 seconds (sometimes needed because the stream is from the web).
           '-i', in_stream,
           '-f', 'rawvideo',  # Video format is raw video
           '-pix_fmt', 'bgr24',  # bgr24 pixel format matches OpenCV default pixels format.
           '-an', 'pipe:']

# Open sub-process that gets in_stream as input and uses stdout as an output PIPE.
ffmpeg_process = subprocess.Popen(command, stdout=subprocess.PIPE)

while True:
    # Read width*height*3 bytes from stdout (1 frame)
    raw_frame = ffmpeg_process.stdout.read(width * height * 3)

    if len(raw_frame) != (width * height * 3):
        print('Error reading frame!!!')  # Break the loop in case of an error (too few bytes were read).
        break

    # Convert the bytes read into a NumPy array, and reshape it to video frame dimensions
    frame = np.frombuffer(raw_frame, np.uint8).reshape((height, width, 3))

    # Show the video frame
    cv2.imshow('startingImage', frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

ffmpeg_process.stdout.close()  # Closing stdout terminates FFmpeg sub-process.
ffmpeg_process.wait()  # Wait for FFmpeg sub-process to finish

cv2.destroyAllWindows()