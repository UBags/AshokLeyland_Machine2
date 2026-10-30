import abc
from abc import ABC, abstractmethod

class C(ABC):

    @abstractmethod
    def m1(self, a : int, b: str):
        print(f"{a}.{b}")
