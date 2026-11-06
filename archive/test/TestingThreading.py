import threading
import time
import logging

class Trial():

    logging.basicConfig(level=logging.DEBUG,
                        format='(%(threadName)-9s) %(message)s', )

    # def wait_for_event(e):
    #     logutils.debug('wait_for_event starting')
    #     event_is_set = e.wait()
    #     logutils.debug('event set: %s', event_is_set)


    # def wait_for_event_timeout(e, t):
    #     while not e.is_set():
    #         logutils.debug('wait_for_event_timeout starting')
    #         event_is_set = e.wait(t)
    #         logutils.debug('event set: %s', event_is_set)
    #         if event_is_set:
    #             logutils.debug('processing event')
    #         else:
    #             logutils.debug('doing other things')

    def __init__(self):
        self.value1 = 10
        self.value2 = 20

    def changeAndPrintValue(self, name: str, timeout = 3):
        for i in range(5):
            logging.debug(f'In {name} - Before : value1 = {self.value1};  value2 = {self.value2}')
            self.value1 += 10
            self.value2 += 10
            logging.debug(f'In {name} - After : value1 = {self.value1};  value2 = {self.value2}')
            time.sleep(timeout)

    def doWork(self):
        t1 = threading.Thread(name='Thread 1',
                              target=self.changeAndPrintValue,
                              args=("Thread 1", 3))
        t2 = threading.Thread(name='Thread 2',
                              target=self.changeAndPrintValue,
                              args=("Thread 2", 4))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

if __name__ == '__main__':
    # e = threading.Event()
    # t1 = threading.Thread(name='blocking',
    #                       target=wait_for_event,
    #                       args=(e,))
    # t1.start()
    #
    # t2 = threading.Thread(name='non-blocking',
    #                       target=wait_for_event_timeout,
    #                       args=(e, 2))
    # t2.start()
    logging.debug('Starting')
    trial = Trial()
    trial.doWork()
    logging.debug('Done')