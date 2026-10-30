from datetime import datetime
from time import strftime

x = datetime.now().strftime('%d-%m-%Y %H:%M')
print(x)

y = [[i] for i in range(10)]
print(y)

# import FreeSimpleGUI as sg
#
# font = ('Courier New', 16)
# text = '\n'.join(chr(i)*50 for i in range(65, 91))
# column = [[sg.Text(text, font=font)]]
#
# layout = [
#     [sg.Column(column, size=(800, 300), scrollable=True, key = "Column")],
#     [sg.OK(), sg.Button('Up', key = "up"), sg.Button('Down', key = "down")],
# ]
#
# window = sg.Window('Demo', layout, finalize=True)
#
# while True:
#     event, values = window.read()
#     if event == sg.WIN_CLOSED:
#         break
#     elif event == "down":
#         window['Column'].Widget.canvas.yview_moveto(1.0)
#     elif event == "up":
#         window['Column'].Widget.canvas.yview_moveto(0.0)
#
# window.close()

# from dateutil.relativedelta import relativedelta
import datetime
from datetime import timedelta

date1 = datetime.datetime.strptime("2015-01-30", "%Y-%m-%d")
print(date1)

addDays = timedelta(days=2)
date1 = date1 + addDays
print(date1)
date1=datetime.datetime.strftime(date1, "%Y-%m-%d")
print(date1)

a = 0
for i in range(50):
    a += 1
    if a % 5 == 0:
        print(a)