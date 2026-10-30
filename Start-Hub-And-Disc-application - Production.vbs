Set objShell = CreateObject("WScript.Shell")
Set objExec = objShell.Exec("tasklist /FI ""IMAGENAME eq MainProgram.exe""")
strOutput = objExec.StdOut.ReadAll
If InStr(1, strOutput, "MainProgram.exe", 1) = 0 Then
    ' MainProgram.exe is not running, start it in the background
    objShell.CurrentDirectory = "D:\AshokLeyland"
	objShell.Run "D:\AshokLeyland\Startup.bat", 2, False
End If


rem objShell.SendKeys "% {DOWN}{DOWN}{DOWN}{ENTER}"

' Check if OtherServices.exe is already running
Set objShell_1 = CreateObject("WScript.Shell")
Set objExec_1 = objShell_1.Exec("tasklist /FI ""IMAGENAME eq OtherServices.exe""")
strOutput_1 = objExec_1.StdOut.ReadAll
If InStr(1, strOutput_1, "OtherServices.exe", 1) = 0 Then
    ' OtherServices.exe is not running, start it in the background
    objShell_1.CurrentDirectory = "D:\AshokLeyland\OtherServices.dist"
    objShell_1.Run """D:\AshokLeyland\OtherServices.dist\OtherServices.exe""", 0, False
End If