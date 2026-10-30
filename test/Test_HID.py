
import ctypes
ctypes.CDLL('C:\\HID\\x64\\hidapi.dll')

import hid
import os

print(hid.enumerate())

for item in hid.enumerate():
    print(f'{item["manufacturer_string"] = } ; {item["vendor_id"] = } ; {item["product_id"] = }')