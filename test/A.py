from test.B import *

p_root = get_project_root( ).replace("\\","/") +"/"
# print(p_root)

def getCallerDetails():
    prev_frame = inspect.currentframe().f_back
    (
        filename,
        line_number,
        function_name,
        lines,
        index,
    ) = inspect.getframeinfo(prev_frame)


def x(y : int = 3):
    # print(p_root)
    # frame = inspect.stack( )
    # print(type(frame)," : ", frame)
    # print(type(frame[1])," : ", frame[1])
    prev_frame = inspect.currentframe().f_back
    (
        filename,
        line_number,
        function_name,
        lines,
        index,
    ) = inspect.getframeinfo(prev_frame)
    filename = filename.replace("\\","/")
    filename = filename[filename.find(p_root) + len(p_root):].replace("/",".")
    filename = filename[:filename.find(".py")]
    # function_name = function_name[0].replace("()","").replace("\n","")
    print(filename, "\n", line_number, "\n", function_name, "\n", lines, "\n", index)

    # prev_frame = inspect.currentframe().f_back
    # print(prev_frame.f_locals)
    # # print(prev_frame.f_locals["self"])
    # the_class = prev_frame.f_locals['self'].__class__
    # # the_file = prev_frame.f_locals["self"].__file__
    # if the_class is not None:
    #     className = the_class.__name__
    # the_method = prev_frame.f_code.co_name
    # line = prev_frame.f_lineno
    # the_caller = f"{className if the_class else ''}{'.' if the_class else ''}{the_method} : {line}"
    # print(f"{the_caller} gave result {y}")

class X():

    @staticmethod
    def x(y: int = 3):
        Y.y(y)
        # frame = inspect.stack()
        # # print(type(frame))
        # # print(type(frame), " : ", frame)
        # frame = str(frame[0])
        # frame = frame[frame.find("lineno=") + len("lineno="):]
        # frame = int(frame[:frame.find(",")])
        # print(frame)
        #
        # # prev_frame = inspect.currentframe().f_back
        # prev_frame = inspect.currentframe().f_back
        # (
        #     filename,
        #     line_number,
        #     function_name,
        #     lines,
        #     index,
        # ) = inspect.getframeinfo(prev_frame)
        # filename = filename.replace("\\", "/")
        # filename = filename[filename.find(p_root) + len(p_root):].replace("/", ".")
        # filename = filename[:filename.find(".py")]
        # lines = re.sub(r'\([^()]*\)', '', lines[0])
        # print(filename, "\n", line_number, "\n", function_name, "\n", lines, "\n", "index =", index)
        # prev_frame = inspect.currentframe().f_back
        # print(prev_frame)
        # print(prev_frame.f_locals)
        # # print(prev_frame.f_locals["self"])
        # the_class = prev_frame.f_locals['self'].__class__
        # # the_file = prev_frame.f_locals["self"].__file__
        # if the_class is not None:
        #     className = the_class.__name__
        # the_method = prev_frame.f_code.co_name
        # line = prev_frame.f_lineno
        # the_caller = f"{className if the_class else ''}{'.' if the_class else ''}{the_method} : {line}"
        # print(f"{the_caller} gave result {y}")

# x(4)
X().x(4)