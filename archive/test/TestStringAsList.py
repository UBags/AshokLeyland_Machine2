import numpy as np
import json

ini_array = np.array([["1.1", "1.5", "2.7", "8.9"], ["1.3", "1.6", "2.9", "8.2"]])

print("initial array", str(ini_array))

# converting to array of floats
# using np.asarray
final_array = b = np.asarray(ini_array,
                             dtype=np.float64, order='C')

print("final array", str(final_array))
def convertStringToList(anInput : str):
    print(f"Calling cstl({anInput})")
    nChars = len(anInput)
    output = None
    aWord = ''
    wordCounter = 0
    while True:
        if wordCounter == nChars:
            if aWord != '':
                output.append(aWord)
            return output
        if anInput[wordCounter] == '[':
            if output is None:
                output = []
                print(f"Initiated output, which is {output}")
                wordCounter += 1
            else:
                output.append(convertStringToList(anInput[wordCounter:]))
                wordCounter += 1
        else:
            if output is None:
                wordCounter += 1
                continue
            elif anInput[wordCounter] == ',':
                print(f"    appending {aWord} to output")
                output.append(aWord)
                print(f"    output is now {output}")
                aWord = ''
                wordCounter += 1
            elif anInput[wordCounter] ==']':
                print(f"    appending {aWord} to output")
                output.append(aWord)
                print(f"    output is now {output}")
                wordCounter += 1
                return output
            else:
                aWord = f"{aWord}{anInput[wordCounter]}"
                wordCounter += 1
    return output

anarray = "[[121, 123], [127,345]]"
# aList = convertStringToList(anarray)
# print(aList)

aList = json.loads(anarray)
print(aList)
final_array = np.asarray(aList,
                             dtype=np.int32)
print(final_array)