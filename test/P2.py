from threading import Thread
import time
from time import sleep

class P2:

    def __init__(self, a, b):
        self.p2_internal = P2_Internal(a, b)
        self.p2_internal.start()
        # self.p2_internal.join()

class P2_Internal(Thread):

    def __init__(self, a, b):
        Thread.__init__(self)
        self.a = a
        self.b = b

    def print(self):
        print(f"In P2, a is {self.a}")
        print(f"In P2, b is {self.b}")

    def run(self):
        while True:
            self.print()
            time.sleep(5)

# p2 = P2(15, 30)