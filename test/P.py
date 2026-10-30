from test.P4 import *
from P_Sub import *

activity1Done = False
activity2Done = False
activity3Done = False

if not activity1Done:
    doActivity1()
    activity1Done = True

if not activity2Done:
    doActivity2()
    activity2Done = True

if not activity3Done:
    doActivity3()
    activity3Done = True

main()