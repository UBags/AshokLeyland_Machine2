import time
from time import sleep
from multiprocessing import Process, freeze_support
import os
import sys

from test.P1 import P1
from test.P2 import P2
from test.P3 import P3
from test.P4 import *

# p1 = None
# p2 = None
# p3 = None
#
# def startP1(a, b):
#     global p1
#     p1 = P1(a,b)
#
# def startP2(a, b):
#     global p2
#     p2 = P2(a,b)
#
# def startP3(a, b):
#     global p3
#     p3 = P3(a,b)

def main():

    activity1Done = False
    activity2Done = False
    activity3Done = False

    # if not activity1Done:
    doActivity1()
        # activity1Done = True

    # if not activity2Done:
    doActivity2()
        # activity2Done = True

    # if not activity3Done:
    doActivity3()
        # activity3Done = True

    val = doActivity4()
    print(val)

    if val:

        PR1 = Process(target=P1, args=(10,20))
        PR2 = Process(target=P2, args=(15,30))
        PR3 = Process(target=P3, args=(20,40))

        PR1.start()
        time.sleep(3)
        PR2.start()
        time.sleep(3)
        PR3.start()

        PR1.join()

    sys.exit(0)

if __name__ == '__main__':
    main()