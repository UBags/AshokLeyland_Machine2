import types

from FunctionNamesForTestingCall import *

class NumberHolder():

    def __init__(self, n1: float, n2 : float, f : types.FunctionType):
        self.n1 = n1
        self.n2 = n2
        self.f = f

    def execute(self):
        if self.f is multiply:
            print(self.f(self.n1,self.n2))
        else:
            print(self.f(self.n1), self.f(self.n2))


numH1 = NumberHolder(6.3, 7.4, square)
numH1.execute()
numH2 = NumberHolder(3, 4, cube)
numH2.execute()
numH3 = NumberHolder(5,6, power4)
numH3.execute()
numH4 = NumberHolder(3,7, multiply)
numH4.execute()
