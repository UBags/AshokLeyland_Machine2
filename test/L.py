import types
from types import *
import random
import numpy as np
import time

def f():
    return

print(type(f))
print(callable(f))
print(isinstance(f, types.FunctionType))

randomList = []
for i in range(100):
    randomList.append(random.random())

startTime = time.time()
for i in range(100):
    nparray = np.array(randomList)
    npdiff = np.diff(nparray)
    npmean = npdiff.mean()
endTime = time.time()
print(f"{npmean =} calculated in {(endTime - startTime)*1000/10} ms")