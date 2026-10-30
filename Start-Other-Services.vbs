' Check if OtherServices.exe is already running
Set objShell_1 = CreateObject("WScript.Shell")
Set objExec_1 = objShell_1.Exec("tasklist /FI ""IMAGENAME eq OtherServices.exe""")
strOutput_1 = objExec_1.StdOut.ReadAll
If InStr(1, strOutput_1, "OtherServices.exe", 1) = 0 Then
    ' OtherServices.exe is not running, start it in the background. No need for any Command Prompt stuff here.
    objShell_1.CurrentDirectory = "D:\AshokLeyland\"
    objShell_1.Run """D:\AshokLeyland\OtherServices.dist\OtherServices.exe""", 0, False
End If