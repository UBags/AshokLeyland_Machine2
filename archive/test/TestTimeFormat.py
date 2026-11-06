from BaseUtils import *
import time
# x = time.gmtime(time.time())
# y=time.strftime("%Y-%m-%d-%H-%M-%S", x)
# s = time.strftime("%a, %d %b %Y %H:%M:%S + 1010", time.gmtime())
# s = time.strftime("%a, %Y-%m-%d-%H-%M-%S", time.time())
# print(y)
# a = time.time()
# print(a)
x = 1712726137.9633386
try:
    if isinstance(x, float):
        x = str(x)
    parts = x.strip().split(".")
    t = int(parts[0])
    print(1,'-',t)
    firstPart = time.localtime(t)
    print(2,'-',firstPart)
    firstPart = time.strftime("%Y-%m-%d-%H-%M-%S", firstPart)
    finalString = f"{firstPart}-{parts[1]}"
    print(finalString)
except:
    print(x)

print(x)