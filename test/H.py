from easygui import passwordbox

mouLimit = passwordbox('MOU')
try:
    mouLimit = float(mouLimit)
except:
    mouLimit = 2
mouLimit = round(mouLimit, 2)

print(mouLimit)