import time

from pycomm3 import LogixDriver
from pycomm3 import DINT, SHORT_STRING, Struct, STRING, REAL
from pycomm3.logger import configure_default_logger, LOG_VERBOSE

plcIP = '192.168.1.1'

def read_single(aTag : str):
    with LogixDriver(plcIP) as plc:
        return plc.read(aTag)


def read_multiple(listOfTags : list):
    with LogixDriver(plcIP) as plc:
        return plc.read(*listOfTags)

def read_array(arrayName : str, numberOfElements : int):
    with LogixDriver(plcIP) as plc:
        return plc.read(f"{arrayName}" + "{" + f"{numberOfElements}" + "}")


def read_array_slice(arrayName : str, index : int, numberOfElements : int):
    with LogixDriver(plcIP) as plc:
        return plc.read(f"{arrayName}[{index}]" + "{" + f"{numberOfElements}" + "}")

def read_strings(arrayName : str, index : int, numberOfElements : int):
    with LogixDriver(plcIP) as plc:
        return plc.read('STRING1', 'STRING_ARY1[2]{2}')

def write_single(tag : str, value:int):
    with LogixDriver(plcIP) as plc:
        val = plc.write((tag, value))
        print(f"Written {val}")
        return val

def write_multiple(tagValueDict : dict):
    with LogixDriver(plcIP) as plc:
        for key, value in tagValueDict:
            plc.write((key, value))

def write_structure(recipe_data : dict):
    with LogixDriver(plcIP) as plc:
        plc.write(('Example_Recipe', recipe_data))

def find_attributes():
    with LogixDriver(plcIP) as plc:
        pass
        ...  # do nothing, we're just letting the plc initialize the tag list

    for typ in plc.data_types:
        print(f'{typ} attributes: ', plc.data_types[typ]['attributes'])


def tag_list_equal():
    with LogixDriver(plcIP) as plc:
        tag_list = plc.get_tag_list()
        if {tag['tag_name']: tag for tag in tag_list} == plc.tags:
            print('They are the same!')

    with LogixDriver(plcIP, init_tags=False) as plc2:
        plc2.get_tag_list()

    if plc.tags == plc2.tags:
        print('Calling get_tag_list() does the same thing.')
    else:
        print('Calling get_tag_list() does NOT do the same.')

def find_pids():
    with LogixDriver(plcIP) as plc:

        # PIDs are structures, the data_type attribute will be a dict with data type definition.
        # For tag types of 'atomic' the data type will a string, we need to skip those first.
        # Then we can just look for tags whose data type name matches 'PID'
        pid_tags = [
            tag
            for tag, _def in plc.tags.items()
            if _def['data_type_name'] == 'PID'
        ]

        return pid_tags

configure_default_logger(level=LOG_VERBOSE, filename='pycomm3.log')

print(DINT.encode(112233))
# b'i\xb6\x01\x00'
print(DINT.decode(b'\x12\x34\x56\x78'))
# 2018915346
print(SHORT_STRING.encode('Hello there!'))
# b'\x0cHello there!'
print(SHORT_STRING.decode(b'\x0eGeneral Kenobi'))
# 'General Kenobi'


MyStruct = Struct(DINT('code'), STRING('name'), REAL('value'))
struct_values = {
    'code': 80,
    'name': 'my name',
    'value': 123.45
}
print(MyStruct.encode(struct_values))
# b'P\x00\x00\x00\x07\x00my namef\xe6\xf6B'
print(find_pids())
find_attributes()
tag_list_equal()
print(read_single('m_ControlOn'))
# print(read_multiple())
print(write_single('m_ControlOn', True))
print(write_single('Blinker', True))
for i in range(10):
    print(read_single('Blinker'))
    time.sleep(1)