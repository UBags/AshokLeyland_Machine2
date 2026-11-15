from random import random

from numba import njit, prange
import numpy as np

@njit(parallel=True)
def parallel_sum(arr):
    total = 0.0
    for i in prange(arr.size):
        total += arr[i]
    return total

arr = np.random.rand(1000000)
print(parallel_sum(arr))

def fast_monte_carlo_pi(nsamples):
    acc = 0
    for i in range(nsamples):
        x = random()
        y = random()
        if (x ** 2 + y ** 2) < 1.0:
            acc += 1
    return 4.0 * acc / nsamples

print(fast_monte_carlo_pi(1000000))