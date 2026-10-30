# import torch
#
# if torch.cuda.is_available():
#     print("GPU is available")
# else:
#     print("GPU is not available")
#
# from random import random
#
# from numba import njit, prange
# import numpy as np
# from random import random
#
#
# @njit(parallel=True)
# def parallel_sum(arr):
#     total = 0.0
#     for i in prange(arr.size):
#         total += arr[i]
#     return total
#
#
# arr = np.random.rand(1000000)
# print(parallel_sum(arr))
#
#
# def fast_monte_carlo_pi(nsamples):
#     acc = 0
#     for i in range(nsamples):
#         x = random()
#         y = random()
#         if (x ** 2 + y ** 2) < 1.0:cd \Py cd
#             acc += 1
#     return 4.0 * acc / nsamples
#
#
# print(fast_monte_carlo_pi(1000000))

import torch
print(torch.__version__)
print(torch.cuda.is_available())
print(torch.version.cuda)
print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "No GPU detected")