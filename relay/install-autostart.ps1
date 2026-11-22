$ErrorActionPreference = "Stop"
$RelayRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RelayPython = Join-Path $RelayRoot ".venv\Scripts\pythonw.exe"
$RelayPython = if (Test-Path -LiteralPath $RelayPython) { $RelayPython } else { Join-Path $RelayRoot ".venv312\Scripts\pythonw.exe" }
$RelayScript = Join-Path $RelayRoot "service.py"
if (-not (Test-Path -LiteralPath $RelayPython)) { throw "Create .venv or .venv312 before installing autostart." }
if (-not (Test-Path -LiteralPath (Join-Path $RelayRoot ".env"))) { throw "Configure .env first." }
$RelayUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$RelayAction = New-ScheduledTaskAction -Execute $RelayPython -Argument ('-u "' + $RelayScript + '"') -WorkingDirectory $RelayRoot
$RelayTrigger = New-ScheduledTaskTrigger -AtLogOn -User $RelayUser
$RelayPrincipal = New-ScheduledTaskPrincipal -UserId $RelayUser -LogonType Interactive -RunLevel Limited
$RelaySettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "Agent Relay - MyTerminal" -Action $RelayAction -Trigger $RelayTrigger -Principal $RelayPrincipal -Settings $RelaySettings -Description "Telegram MyTerminal, nine agents and subagent coordinator" -Force | Out-Null
Write-Output "Autostart installed: Agent Relay - MyTerminal (at user logon, restarts after failure)."

