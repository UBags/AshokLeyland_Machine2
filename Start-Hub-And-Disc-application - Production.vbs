' Start-Hub-And-Disc-application - Production.vbs
'
' Put this file in the Startup folder (Win+R, shell:startup). It runs at every logon of this user.
' 1. If MainProgram.exe is already running, it does nothing.
' 2. Otherwise it waits until Redis (port 6379) and PostgreSQL (port 5432) accept connections,
'    up to WAIT_SECONDS for each, trying every POLL_MS (2 s; 3 s before CHANGES 174). After boot,
'    Redis in WSL can take longer than MainProgram's own 3 x 10 s retries.
' 3. Then it starts MainProgram.exe in a minimised console. The console stays open after
'    MainProgram exits (cmd /k).
' 4. It starts OtherServices.exe as well, but only if it is built and not already running.
' If Redis or PostgreSQL is not up in time, MainProgram is not started and a message box says so.
' Log: <APP_DIR>\StartupLauncher.log
Option Explicit

Const APP_DIR       = "D:\AshokLeyland"
Const MAIN_EXE      = "MainProgram.dist\MainProgram.exe"
Const OTHER_DIR     = "OtherServices.dist"
Const OTHER_EXE     = "OtherServices.exe"
Const REDIS_PORT    = 6379
Const POSTGRES_PORT = 5432
Const WAIT_SECONDS  = 240
Const POLL_MS       = 2000   ' gap between two tries; each try also takes ~0.3 - 1 s (a hidden PowerShell)
Const TITLE         = "Hub and Disc application"

Dim shell, fso, wmi, logPath, mainPath, otherDir, otherPath
Set shell = CreateObject("WScript.Shell")
Set fso   = CreateObject("Scripting.FileSystemObject")
Set wmi   = GetObject("winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2")
logPath   = fso.BuildPath(APP_DIR, "StartupLauncher.log")
mainPath  = fso.BuildPath(APP_DIR, MAIN_EXE)
otherDir  = fso.BuildPath(APP_DIR, OTHER_DIR)
otherPath = fso.BuildPath(otherDir, OTHER_EXE)

Function Two(n)
    Two = Right("0" & n, 2)
End Function

Sub WriteLog(msg)
    On Error Resume Next
    Dim f, t
    t = Now
    Set f = fso.OpenTextFile(logPath, 8, True)
    f.WriteLine Year(t) & "-" & Two(Month(t)) & "-" & Two(Day(t)) & " " & _
                Two(Hour(t)) & ":" & Two(Minute(t)) & ":" & Two(Second(t)) & "  " & msg
    f.Close
End Sub

Function IsRunning(imageName)
    IsRunning = (wmi.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE Name = '" & imageName & "'").Count > 0)
End Function

' True if something accepts TCP connections on 127.0.0.1:port (hidden PowerShell, exit code 0/1)
Function PortOpen(port)
    Dim cmd
    cmd = "powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -Command " & _
          """$c = New-Object Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1', " & port & "); exit 0 } " & _
          "catch { exit 1 } finally { $c.Close() }"""
    PortOpen = (shell.Run(cmd, 0, True) = 0)
End Function

Function WaitForPort(name, port)
    Dim start
    start = Now
    Do
        If PortOpen(port) Then
            WriteLog name & " accepts connections on port " & port & " (after " & DateDiff("s", start, Now) & " s)."
            WaitForPort = True
            Exit Function
        End If
        If DateDiff("s", start, Now) >= WAIT_SECONDS Then Exit Do
        WScript.Sleep POLL_MS
    Loop
    WriteLog "ERROR: " & name & " is not reachable on port " & port & " after " & WAIT_SECONDS & " s."
    WaitForPort = False
End Function

WriteLog "Launcher started."

If IsRunning("MainProgram.exe") Then
    WriteLog "MainProgram.exe is already running; not started again."
ElseIf Not fso.FileExists(mainPath) Then
    WriteLog "ERROR: not found: " & mainPath
    shell.Popup "MainProgram.exe was not found:" & vbCrLf & mainPath, 0, TITLE, 16
ElseIf Not WaitForPort("Redis", REDIS_PORT) Then
    shell.Popup "MainProgram was NOT started: Redis is not running (port " & REDIS_PORT & ")." & _
                vbCrLf & "See " & logPath, 0, TITLE, 16
ElseIf Not WaitForPort("PostgreSQL", POSTGRES_PORT) Then
    shell.Popup "MainProgram was NOT started: PostgreSQL is not running (port " & POSTGRES_PORT & ")." & _
                vbCrLf & "See " & logPath, 0, TITLE, 16
Else
    shell.CurrentDirectory = APP_DIR
    shell.Run "cmd.exe /k """ & mainPath & """", 2, False
    WriteLog "MainProgram.exe started: " & mainPath
End If

If fso.FileExists(otherPath) Then
    If IsRunning(OTHER_EXE) Then
        WriteLog OTHER_EXE & " is already running; not started again."
    Else
        shell.CurrentDirectory = otherDir
        shell.Run """" & otherPath & """", 0, False
        WriteLog OTHER_EXE & " started: " & otherPath
    End If
End If
