' DataToolWatchdog.vbs - start the data-converter-tool watchdog at user logon.
'
' WHAT IT DOES
'   Launches scripts\watchdog_server.py with pythonw.exe, hidden window, no console.
'   The watchdog is a LONG-RUNNING daemon, not a one-shot check: it polls
'   127.0.0.1:51978 every 10 seconds (GET /api/ping) and restarts server.py
'   whenever the service is gone, forever, until stopped.
'   It holds a Windows named mutex so at most one watchdog instance exists:
'   running this script twice is harmless (the second copy exits immediately).
'
' HOW TO STOP IT
'   1) create an empty file  <project>\data\watchdog.stop  -> the daemon exits on
'      its next loop, or
'   2) Task Manager -> end the pythonw.exe process that runs watchdog_server.py.
'
' HOW TO INSTALL
'   Copy this file into
'     %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
'   and keep PROJECT_ROOT below in sync with where the tool actually lives.
Option Explicit

Dim PROJECT_ROOT
PROJECT_ROOT = "C:\Users\Administrator\WorkBuddy\2026-08-27-16-17-35\data-converter-tool"

Dim WshShell
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = PROJECT_ROOT

Dim cmd
cmd = """" & PROJECT_ROOT & "\.venv\Scripts\pythonw.exe"" """ & PROJECT_ROOT & "scripts\watchdog_server.py"" --interval 10"

' 0 = hidden window, False = return immediately and let the daemon keep running
WshShell.Run cmd, 0, False
