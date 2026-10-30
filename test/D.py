import abc
from abc import ABC, abstractmethod
from C import C

class D(C):

    def m1(self, a: int, b: str):
        print(f"Subclass - {a}.{b}")
        super().m1(a, b)


D().m1(3, "xyz")