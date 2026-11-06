from BaseUtils import *

class TestAttr():

    classAttr1 : int = 1
    classAttr2 : int = 2
    classDict = {}
    loggingName = None

    def __init__(self):
        TestAttr.loggingName = getFullyQualifiedName(__file__, __class__)

    @classmethod
    def populateDict(cls):
        for i in range(10):
            attr_name = f"classAttr{i}"
            if hasattr(cls, attr_name):
                TestAttr.classDict[attr_name] = i * 10

    @classmethod
    def getValue(cls, variable : int = 1):
        attr_name = f"classAttr{variable}"
        print(f"Value of {attr_name} is {getattr(cls, attr_name)}")
        return TestAttr.classDict[attr_name]

TestAttr.populateDict()
print(TestAttr.getValue(1))
print(TestAttr.getValue(2))
t = TestAttr()
print(t.loggingName)

