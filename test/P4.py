import random

def doActivity1():
    print("Doing activity 1")

def doActivity2():
    print("Doing activity 2")

def doActivity3():
    print("Doing activity 3")

def doActivity4():
    val = random.random()
    if val < 0.5:
        return False
    return True
