
class CallerClass:
    def __init__(self, section: int):
        self.section = section

    def execute(self, input: int):
        # Get the classmethod object
        fnToBeExecuted = Processor.methodMapping[self.section]
        # Access the underlying function using __func__ and call it with the class
        result = fnToBeExecuted.__func__(Processor, input)
        print(f"The result of executing {fnToBeExecuted.__name__} is {result}")

class Processor:
    @classmethod
    def add1(cls, input: int):
        return input + 1

    @classmethod
    def add2(cls, input: int):
        return input + 2

    @classmethod
    def add3(cls, input: int):
        return input + 3

    methodMapping = {1: add1, 2: add2, 3: add3}

# Test the code
cc1 = CallerClass(1)
cc1.execute(6)  # Output: The result of executing add1 is 7

cc2 = CallerClass(2)
cc2.execute(6)  # Output: The result of executing add2 is 8

cc3 = CallerClass(3)
cc3.execute(6)  # Output: The result of executing add3 is 9

from typing import List, Union, Optional, Tuple
import numpy as np

# Define INVALID_RESULT as a Union of str and bool
INVALID_RESULT: Union[str, bool] = "abcd"  # Example value

# Define the type for each inner tuple
ResultPair = Tuple[Union[str, bool], Optional[np.ndarray]]

# Define the outer list type
resultsFromPictureAssessment: List[ResultPair] = [
    (INVALID_RESULT, None),
    (INVALID_RESULT, None),
    (INVALID_RESULT, None)
]

# Example usage
resultsFromPictureAssessment[0] = ("Error: Invalid Image", None)  # Valid
resultsFromPictureAssessment[1] = (True, None)  # Valid
resultsFromPictureAssessment[2] = (False, np.array([[1, 2], [3, 4]]))  # Valid

from utils.CosThetaFileUtils import *

getFileNameForSaving(useNanoSec=True)
time.sleep(0.005)
getFileNameForSaving(useNanoSec=True)