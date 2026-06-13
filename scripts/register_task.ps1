# register_task.ps1 - UAC self-elevation, registriert MickBot-Watchdog Scheduled Task

if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

$ProjectDir     = "E:\TRADING\Market Cipher"
$TaskName       = "MickBot-Watchdog"
$WatchdogScript = "$ProjectDir\scripts\watchdog.py"

$Python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $Python) {
    Write-Host "FEHLER: Python nicht im PATH." -ForegroundColor Red
    Read-Host "Enter"
    exit 1
}

if (-not (Test-Path $WatchdogScript)) {
    Write-Host "FEHLER: watchdog.py nicht gefunden." -ForegroundColor Red
    Read-Host "Enter"
    exit 1
}

Write-Host ""
Write-Host "MickBot-Watchdog Task Setup" -ForegroundColor Cyan
Write-Host "Python : $Python"
Write-Host "Script : $WatchdogScript"
Write-Host ""

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Entferne alten Task..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$Action   = New-ScheduledTaskAction -Execute $Python -Argument "`"$WatchdogScript`"" -WorkingDirectory $ProjectDir
$Trigger  = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 2) -ExecutionTimeLimit (New-TimeSpan -Hours 0) -MultipleInstances IgnoreNew -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -RunLevel Highest -Force | Out-Null

Write-Host "Task registriert!" -ForegroundColor Green
Write-Host "Starte sofort..."

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3

$state = (Get-ScheduledTask -TaskName $TaskName).State
if ($state -eq "Running") {
    Write-Host "Laeuft! Status: $state" -ForegroundColor Green
} else {
    Write-Host "Status: $state" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Ab jetzt startet der Bot automatisch bei Windows-Anmeldung." -ForegroundColor Green
Write-Host ""
Write-Host "Stoppen : Stop-ScheduledTask -TaskName MickBot-Watchdog"
Write-Host "Status  : Get-ScheduledTask -TaskName MickBot-Watchdog"
Write-Host ""
Read-Host "Enter zum Schliessen"
