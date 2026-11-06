from time import sleep
from random import random
from queue import Queue
from threading import Thread
from concurrent.futures import ThreadPoolExecutor
import time
from concurrent.futures import as_completed
import asyncio
import aiofiles

# create a queue for collecting message to write
q = Queue()
totalTime = 0

# task that blocks for a moment and write to a file
def task1(identifier, queue):
    # block for a moment
    global totalTime
    value = random()
    totalTime += value
    sleep(value)
    # push data into the queue
    q.put(f'Task {identifier} blocked for {value} of a second.\n')

# consume messages from the queue and write to the file
def file_writer(queue, file_handle):
    # loop forever
    while True:
        # retrieve a messages from the queue
        message = q.get()
        # write the message to file
        file_handle.write(message)
        # mark this ask as done
        q.task_done()

# write to a file from multiple threads
def method1():
    # open a file
    global totalTime
    totalTime = 0
    startTime = time.time()
    with open('results.txt', 'w', encoding='utf-8') as handle:
        # start a daemon thread to write queued messages
        writer = Thread(target=file_writer, args=(q, handle), daemon=True)
        writer.start()
        # create the thread pool
        with ThreadPoolExecutor(2000) as executor:
            # dispatch all tasks
            _ = [executor.submit(task1, i, q) for i in range(10000)]
        # after all threads are done, wait for all message to be consumed
        q.join()
    endTime = time.time()
    print(f'Method1 : Done in {endTime - startTime} sec, versus {totalTime = } sec')

#  ***********************************************

def task2(identifier):
    # block for a moment
    global totalTime
    value = random()
    totalTime += value
    sleep(value)
    # return data to write
    return f'Task {identifier} blocked for {value} of a second.\n'

# write to a file from multiple threads
def method2():
    # open a file
    global totalTime
    totalTime = 0
    startTime = time.time()
    with open('results.txt', 'w', encoding='utf-8') as handle:
        # create the thread pool
        with ThreadPoolExecutor(2000) as executor:
            # dispatch all tasks
            futureResults = [executor.submit(task2, i) for i in range(10000)]
            # process results as tasks are completed
            for aResult in as_completed(futureResults):
                # retrieve result to write
                result = aResult.result()
                # write to file
                handle.write(result)
    # wait for all tasks to complete
    endTime = time.time()
    print(f'Method2 : Done in {endTime - startTime} sec, versus {totalTime = } sec')

#  ***********************************************
# task that blocks for a moment and write to a file

# create a stateLock to protect the file
lock = asyncio.Lock()

async def task3(identifier, lock, file_handle):
    # block for a moment
    global totalTime
    value = random()
    totalTime += value
    await asyncio.sleep(value)
    # obtain the stateLock
    async with lock:
        # write to a file
        await file_handle.write(f'Task {identifier} blocked for {value} of a second.\n')

# write to a file from multiple threads
async def method3():
    # open a file
    global totalTime
    totalTime = 0
    startTime = time.time()
    async with aiofiles.open('results.txt', 'w', encoding='utf-8') as handle:
        # create coroutines
        tasks = [task3(i, lock, handle) for i in range(10000)]
        # execute tasks and wait for them to finish
        _ = await asyncio.gather(*tasks)
    endTime = time.time()
    print(f'Method3 : Done in {endTime - startTime} sec, versus {totalTime = } sec')

# entry point
if __name__ == '__main__':
    method1()
    method2()
    asyncio.run(method3())

