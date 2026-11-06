import enum
import time
from typing import List
from abc import ABC, abstractmethod

from pymodbus.client import ModbusTcpClient



_client = ModbusTcpClient(host='192.168.1.50', port=502)
print(_client.params.host, _client.params.port, _client.params.source_address, _client.socket, _client.use_sync)
_connected = _client.connect()
print(_connected, _client.socket)
if _connected:
    _client.close()
print(_client.socket)