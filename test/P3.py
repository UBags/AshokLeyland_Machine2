from threading import Thread
import time
from time import sleep

class P3:

    def __init__(self, a, b):
        self.p3_internal = P3_Internal(a, b)
        self.p3_internal.start()
        # self.p3_internal.join()

class P3_Internal(Thread):

    def __init__(self, a, b):
        Thread.__init__(self)
        self.a = a
        self.b = b

    def print(self):
        print(f"In P3, a is {self.a}")
        print(f"In P3, b is {self.b}")

    def run(self):
        while True:
            self.print()
            time.sleep(5)

# p3 = P3(20, 40)