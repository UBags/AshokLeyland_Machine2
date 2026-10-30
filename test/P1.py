from threading import Thread
import time
from time import sleep

class P1:

    def __init__(self, a, b):
        self.p1_internal = P1_Internal(a, b)
        self.p1_internal.start()
        # self.p1_internal.join()

class P1_Internal(Thread):

    def __init__(self, a, b):
        Thread.__init__(self)
        self.a = a
        self.b = b

    def print(self):
        print(f"In P1, a is {self.a}")
        print(f"In P1, b is {self.b}")

    def run(self):
        while True:
            self.print()
            time.sleep(5)

# p1 = P1(10, 20)