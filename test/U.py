import psutil

# Iterate over all the keys in the dictionary
for interface in psutil.net_if_addrs():
    # Check if the interface has a valid MAC address
    if psutil.net_if_addrs()[interface][0].address:
        # Print the MAC address for the interface
        print(psutil.net_if_addrs()[interface][0].address)

import re, uuid

# joins elements of getnode() after each 2 digits.
# using regex expression
print("The MAC address in formatted and less complex way is : ", end="")
print(':'.join(re.findall('..', '%012x' % uuid.getnode())))

import subprocess
import sys

def run(cmd):
  try:
    return subprocess.run(cmd, shell=True, capture_output=True, check=True, encoding="utf-8") \
                     .stdout \
                     .strip()
  except:
    return None

def guid():
  if sys.platform == 'darwin':
    return run(
      "ioreg -d2 -c IOPlatformExpertDevice | awk -F\\\" '/IOPlatformUUID/{print $(NF-1)}'",
    )

  if sys.platform == 'win32' or sys.platform == 'cygwin' or sys.platform == 'msys':
    return run('wmic csproduct get uuid').split('\n')[2] \
                                         .strip()

  if sys.platform.startswith('linux'):
    return run('cat /var/lib/dbus/machine-id') or \
           run('cat /etc/machine-id')

  if sys.platform.startswith('openbsd') or sys.platform.startswith('freebsd'):
    return run('cat /etc/hostid') or \
           run('kenv -q smbios.system.uuid')

print(guid())