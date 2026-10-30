# # rtsp://admin:abcd1234@192.168.1.100:554/Streaming/Channels/101?transportmode=unicast&profile=Profile_1
#
# import subprocess
# import cv2
# import numpy as np
#
# def extract_frames_and_display(rtsp_url):
#     # ffmpeg_cmd = [
#     #     "ffmpeg", "-i", rtsp_url, "-vf", "fps=25", "pipe:1"
#     # ]
#     # ffmpeg_cmd = f"ffmpeg -hwaccel auto -hwaccel_device opencl -i pipe:0 -pix_fmt bgr24 -s {FRAME_X}x{FRAME_Y}  -f rawvideo pipe:1'
#     ffmpeg_cmd = f"ffmpeg -i {rtsp_url} -f rawvideo pipe:1"
#     pipe = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, bufsize=10**8)
#
#     while True:
#         raw_image = pipe.stdout.read(10**6)
#         if not raw_image:
#             break
#
#         image = cv2.imdecode(np.frombuffer(raw_image, np.uint8), cv2.IMREAD_COLOR)
#
#         if image is None:
#             break
#
#         cv2.imshow("RTSP Stream", image)
#
#         if cv2.waitKey(1) == ord("q"):
#             break
#
#     pipe.stdout.close()
#
# if __name__ == "__main__":
#     rtsp_url = "rtsp://admin:abcd1234@192.168.1.100:554/Streaming/Channels/101?transportmode=unicast&profile=Profile_1"
#     extract_frames_and_display(rtsp_url)

import ffmpeg
import numpy as np
import cv2
import threading
from threading import Thread
import time

def showRTSP_In_cv2(rtspSource : str, frameName : str = "frame"):
    # args = {
    #     "rtsp_transport": "tcp",
    #     "fflags": "nobuffer",
    #     "flags": "low_delay"
    # }
    try:
        # args = {
        #     "rtsp_transport": "tcp",
        #     "fflags": "nobuffer",
        #     "fflags": "discardcorrupt",
        #     "fflags": "flush_packets",
        #     "flags": "low_delay",
        #     # "tune": "zerolatency",
        #     # "analyzeduration": "0",
        #     # "probesize": "32"
        #     # ,
        #     "thread_queue_size": "2"
        #     # "loglevel": "quiet",
        #     # "nostats": ""
        # }
        args = {
            "rtsp_transport": "tcp",
            "fflags": "nobuffer",
            "flags": "low_delay"
        }  # 添加参数
        probe = ffmpeg.probe(rtspSource)
        cap_info = next(x for x in probe['streams'] if x['codec_type'] == 'video')
        print("fps: {}".format(cap_info['r_frame_rate']))
        width = cap_info['width']
        height = cap_info['height']
        up, down = str(cap_info['r_frame_rate']).split('/')
        fps = eval(up) / eval(down)
        print("fps: {}".format(fps))
        process1 = (
            ffmpeg
            .input(rtspSource, **args)
            # .output('pipe:', format='rawvideo', preset='ultrafast', pix_fmt='rgb24')
            # .output('pipe:', format='rawvideo', preset='ultrafast', pix_fmt='bgr24')
            # .output('pipe:', format='rawvideo', pix_fmt='yuvj420p', vf='setpts=0', fflags='nobuffer') - hangs
            # .output('pipe:', format='rawvideo', pix_fmt='yuvj420p', fflags='nobuffer', analyzeduration='1')
            .output('pipe:', format='rawvideo', pix_fmt='bgr24', fflags='nobuffer', analyzeduration='1')
            .overwrite_output()
            .run_async(pipe_stdout=True)
        )
        while True:
            in_bytes = process1.stdout.read(width * height * 3)
            # in_bytes = process1.stdout.read((width * height * 3)//2) # for YUV format
            if not in_bytes:
                break
            # ndarray
            # in_frame = (
            frame = (
                np
                .frombuffer(in_bytes, np.uint8)
                .reshape([height, width, 3])
                # .reshape([(height * 3)//2, width]) # for YUV format
            )
            # frame = cv2.resize(in_frame, (1280, 720))
            # frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            # frame = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_I420)
            cv2.imshow(frameName, frame)
            if cv2.waitKey(1) == ord('q'):
                break
            # time.sleep(0.1)
        process1.kill()             # 关闭
    except Exception as e:
        print(e)


if __name__ == "__main__":
    # camera_ip = "192.168.20.221"
    # camera_login_user = "admin"
    # camera_login_pwd = "admin123"
    # camera_channel = 0      # 选择主码流，还是辅码流

    # alhua_rtsp = f"rtsp://{camera_login_user}:{camera_login_pwd}@{camera_ip}/cam/realmonitor?channel=1&subtype={camera_channel}"

    # For ONVIF
    # rtsp_url1 = "rtsp://admin:Leyland9753@192.168.1.64:554/Streaming/Channels/101?transportmode=unicast&profile=Profile_1"
    # rtsp_url2 = "rtsp://admin:Leyland9753@192.168.1.64:554/Streaming/Channels/101?transportmode=unicast&profile=Profile_1"

    # For direct RTSP
    rtsp_url1 = "rtsp://admin:Leyland9753@192.168.1.64/cam/realmonitor?channel=1&subtype=0"
    rtsp_url2 = "rtsp://admin:Leyland9753@192.168.1.64/cam/realmonitor?channel=1&subtype=0"

    thread1 = threading.Thread(name="1", target=showRTSP_In_cv2, args=(rtsp_url1, "frame1"), daemon=True)
    thread2 = threading.Thread(name="2", target=showRTSP_In_cv2, args=(rtsp_url2, "frame2"), daemon=True)
    # showRTSP_In_cv2(rtsp_url1, "frame1")
    # showRTSP_In_cv2(rtsp_url2, "frame2")
    thread1.start()
    time.sleep(10)
    thread2.start()
    thread1.join()
    thread2.join()