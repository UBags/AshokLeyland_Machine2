

class Z():

    def __init__(self, name):
        self.name = name

    def outer(self):
        objRef = self
        def inner():
            print(objRef.name)
        inner()

z = Z("A Name")
z.outer()