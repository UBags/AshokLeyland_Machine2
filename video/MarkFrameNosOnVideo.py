from typing import Any
import cv2

video = cv2.VideoCapture("C:/CosTheta/GelTec/Videos/video_20240408_162618.mp4")

fps = video.get(cv2.CAP_PROP_FPS)
width = video.get(cv2.CAP_PROP_FRAME_WIDTH)
height = video.get(cv2.CAP_PROP_FRAME_HEIGHT)
print('frames per second = ',fps, 'width = ', width, 'height = ', height )

vidWrite: Any = cv2.VideoWriter('C:/CosTheta/GelTec/Videos/output_video1.mp4', cv2.VideoWriter_fourcc(*'MP4V'), fps, (1080, 1920))

font = cv2.FONT_HERSHEY_SIMPLEX
# coord
coord = (540, 960)
# fontScale
fontScale = 1
# Blue color in BGR
color = (255, 0, 0)
# Line thickness of 2 px
thickness = 2
# Using cv2.putText() method
ret = True
n = 0
while ret:
    ret, frame = video.read()
    n += 1
    image = cv2.putText(frame, f"{n}", coord, font,
                        fontScale, color, thickness, cv2.LINE_AA)
    vidWrite.write(image)

vidWrite.release()
cv2.destroyAllWindows()