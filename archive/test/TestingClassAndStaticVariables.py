class CSStudent:
    stream = 'cse'  # Class Variable
    aDict = {} # Class Variable

    def __init__(self, name, roll, key, value):
        self.name = name  # Instance Variable
        self.roll = roll  # Instance Variable
        CSStudent.aDict[key] = value

# Objects of CSStudent class
a = CSStudent('Geek', 1, "a", 1)
b = CSStudent('Nerd', 2, "b", 2)

print(a.stream)  # prints "cse"
print(b.stream)  # prints "cse"
print(a.name)  # prints "Geek"
print(b.name)  # prints "Nerd"
print(a.roll)  # prints "1"
print(b.roll)  # prints "2"
print(a.aDict)
print(b.aDict)

# Class variables can be accessed using class
# name also
print(CSStudent.stream)  # prints "cse"
print(CSStudent.aDict)

# Now if we change the stream for just a it won't be changed for b
a.stream = 'ece'
a.aDict["c"] = 3
print(a.stream)  # prints 'ece'
print(b.stream)  # prints 'cse'
print(a.aDict)
print(b.aDict)
print(CSStudent.aDict)

# To change the stream for all instances of the class we can change it
# directly from the class
CSStudent.stream = 'mech'
CSStudent.aDict["d"] = 4

print(a.stream)  # prints 'ece'
print(b.stream)  # prints 'mech'
print(a.aDict)
print(b.aDict)
print(CSStudent.aDict)

b.aDict["a"] = 10
print(a.aDict)
print(b.aDict)
print(CSStudent.aDict)