from logutils.SlaveLoggers import *
from logutils.Logger import *
from Configuration import *

CosThetaConfigurator.getInstance()

class Y():

    # project_root = get_project_root( ).replace("\\","/") +"/"
    # fullyQualifiedName = None
    logSource = getFullyQualifiedName(__file__)

    def __init__(self):
        Y.logSource = getFullyQualifiedName(__file__, __class__)

    # @classmethod
    # def getSource(cls):
    #     if not hasattr(cls, "fullyQualifiedName"):
    #         setattr(cls, "fullyQualifiedName", getFullyQualifiedName(__file__, cls.__class__.__name__))
    #     # print(getattr(cls, "fullyQualifiedName"))
    #     return getattr(cls, "fullyQualifiedName")

    @classmethod
    def y(cls, z : int = 2):
        # t = inspect.get
        # print(f"inspect.trace() returns {t}")
        # frame = inspect.stack()
        # print(f"inspect.stack() returns {frame}")
        # current_frame = inspect.currentframe()
        # print(f"currentframe() is {current_frame}")
        # (
        #     filename,
        #     line_number,
        #     function_name,
        #     lines,
        #     index,
        # ) = inspect.getframeinfo(current_frame)
        # print(filename, "\n", line_number, "\n", function_name, "\n", lines, "\n", "index =", index)
        # print("***************")
        # prev_frame1 = current_frame.f_back
        # print(f"previous frame is {prev_frame1}")
        # (
        #     filename,
        #     line_number,
        #     function_name,
        #     lines,
        #     index,
        # ) = inspect.getframeinfo(prev_frame1)
        # print(filename, "\n", line_number, "\n", function_name, "\n", lines, "\n", "index =", index)
        # print("***************")
        # prev_frame2 = prev_frame1.f_back
        # print(f"previous frame is {prev_frame2}")
        # (
        #     filename,
        #     line_number,
        #     function_name,
        #     lines,
        #     index,
        # ) = inspect.getframeinfo(prev_frame2)
        # print(filename, "\n", line_number, "\n", function_name, "\n", lines, "\n", "index =", index)
        # print("***************")
        # filename = filename.replace("\\", "/")
        # filename = filename[filename.find(Y.project_root) + len(Y.project_root):].replace("/", ".")
        # filename = filename[:filename.find(".py")]
        # print("Calling filename :", filename)
        # lines = re.sub(r'\([^()]*\)', '', lines[0])
        # print("Lines calling are ", lines)
        # # print(filename, "\n", line_number, "\n", function_name, "\n", lines, "\n", "index =", index)
        # frame = inspect.stack()
        # print(frame)
        # # print(type(frame), " : ", frame)
        # frame = str(frame[1])
        # line = frame[frame.find("lineno=") + len("lineno="):]
        # line = int(line[:line.find(",")])
        # print("Line number is", line)
        # # print(frame)



        # currentFrame = inspect.currentframe()
        # currClass = currentFrame.f_locals['cls'].__name__
        # co = currentFrame.f_code
        # func_name = co.co_name
        # lno = co.co_firstlineno
        # if func_name == 'write':
        #     # Ignore write() calls from print statements
        #     return
        # func_line_no = currentFrame.f_locals
        # func_filename = co.co_filename
        # print(f"{func_filename}.{currClass}.{func_name} : {lno}")
        # # print(currentFrame.f_trace_lines) - Rubbish
        # # print(currentFrame.f_lasti) - Rubbish
        # callerFrame = currentFrame.f_back
        # print(callerFrame)
        # try:
        #     callClass = callerFrame.f_locals['cls'].__name__
        # except:
        #     callClass = None
        # co1 = callerFrame.f_code
        # func_name1 = co1.co_name
        # # lno1 = co1.co_firstlineno
        # lno1 = callerFrame.f_lineno
        # if func_name1 == 'write':
        #     # Ignore write() calls from print statements
        #     return
        # func_line_no1 = callerFrame.f_locals
        # func_filename1 = co1.co_filename
        # print(f"{func_filename1}{'.' if callClass is not None else ''}{callClass if callClass is not None else ''}.{func_name1} : {lno1}")
        # print(callerFrame.f_lasti)
        # caller_line_no = caller.f_lineno
        # caller_filename = caller.f_code.co_filename
        # print(f'Call to {func_name} on line {func_line_no} of {func_filename} from line {caller_line_no} of {caller_filename}')
        # SlaveConsoleLogger.getInstance().start()
        # printLight(Y.fullyQualifiedName)
        # printBlue(SlaveConsoleLogger.getInstance())
        '''
        for i in range(10):
            SlaveConsoleLogger.getInstance().logConsiderAction(Y.logSource, f"This is an error {i}")
            time.sleep(2)
        SlaveConsoleLogger.getInstance().stop()
        '''
        for i in range(20):
            SlaveFileLogger.getInstance().logConsiderAction(Y.logSource, f"This is an error {i}")
            time.sleep(2)
        SlaveFileLogger.getInstance().stop()
        # print(
        #     f'Call to {func_name} on line {func_line_no} of {func_filename} from line {caller_line_no} of {caller_filename}')

# z = Y()
# z.y()