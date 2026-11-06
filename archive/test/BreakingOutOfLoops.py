from time import sleep

count = 0
while True:
    print("In outer loop")
    while True:
        for i in range(3):
            print("In inner loop")
            sleep(1)
        break
    sleep(1)
    count += 1
    if count == 3:
        break