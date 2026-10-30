import time
from time import sleep
import threading
from threading import Thread

class A():

    def __init__(self):
        self.aVar = 10
        self.launchARandomThreadForTestingVariableAccess()

    def launchARandomThreadForTestingVariableAccess(self):
        objRef = self
        sleepTime = 1.0
        someIntVar : int = 0
        def getSomeInput():
            nonlocal someIntVar
            while True:
                print(objRef.aVar)
                objRef.aVar += 5
                print(someIntVar)
                someIntVar += 10
                time.sleep(sleepTime)
        self.aThread = threading.Thread(
            name=f'Random Name',
            target=getSomeInput,
            args=(), daemon = True)
        self.aThread.start()

a = A()