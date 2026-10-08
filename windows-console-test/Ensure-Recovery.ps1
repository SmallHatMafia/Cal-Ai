$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'Windowless-Tasks.ps1')
$taskName='Target Roofing QuickBooks Sync'
$recoveryId='TargetRoofingAutomaticRecovery'
$task=Get-ScheduledTask -TaskName $taskName -TaskPath '\'
$launcher=Join-Path $PSScriptRoot 'Run-Silent.ps1'
# Only modify this installation's existing task. Never touch another app/task.
Assert-OwnedBackgroundTask $task 'Run-Silent.ps1'
if(-not (Test-WindowlessAction $task 'Run-Silent.ps1')){
  Set-ScheduledTask -TaskName $taskName -TaskPath '\' -Action (New-WindowlessAction 'Run-Silent.ps1') | Out-Null
  $task=Get-ScheduledTask -TaskName $taskName -TaskPath '\'
}
function Recovery-IsConfigured($candidate) {
  $timer=@($candidate.Triggers | Where-Object { $_.Id -eq $recoveryId -and $_.Enabled -ne $false -and $_.Repetition.Interval -eq 'PT5M' -and -not $_.Repetition.Duration -and -not $_.EndBoundary })
  $logon=@($candidate.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' -and $_.Enabled -ne $false })
  return $timer.Count -eq 1 -and $logon.Count -gt 0 -and
    [string]$candidate.Settings.MultipleInstances -in @('IgnoreNew','2') -and
    $candidate.Settings.StartWhenAvailable -and
    -not $candidate.Settings.DisallowStartIfOnBatteries -and
    -not $candidate.Settings.StopIfGoingOnBatteries -and
    -not $candidate.Settings.WakeToRun
}
if(-not (Recovery-IsConfigured $task)) {
  $triggers=@($task.Triggers | Where-Object { $_.Id -ne $recoveryId })
  if(-not @($triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' -and $_.Enabled -ne $false }).Count) {
    if(-not $task.Principal.UserId){throw 'The registered Windows user is missing.'}
    $triggers+=New-ScheduledTaskTrigger -AtLogOn -User $task.Principal.UserId
  }
  $timer=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 5)
  $timer.Id=$recoveryId
  # Omitting duration/end boundary repeats indefinitely. IgnoreNew leaves a
  # healthy running instance alone; Windows starts it only when it is stopped.
  $triggers+=$timer
  $settings=New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
  Set-ScheduledTask -TaskName $taskName -TaskPath '\' -Trigger $triggers -Settings $settings | Out-Null
  $task=Get-ScheduledTask -TaskName $taskName -TaskPath '\'
}
if(-not (Recovery-IsConfigured $task)){throw 'Windows did not retain the recovery settings.'}
# This second task is independent of Run-Silent/supervisor. A hung browser
# cannot prevent it from running. It uses the same limited Windows user.
$watchName='Target Roofing QuickBooks Recovery'
$watchScript=Join-Path $PSScriptRoot 'Watch-Background.ps1'
if(-not (Test-Path $watchScript)){throw 'Recovery helper missing'}
$watch=Get-ScheduledTask -TaskName $watchName -TaskPath '\' -ErrorAction SilentlyContinue
$arguments='-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "'+$watchScript+'"'
if($watch){
  Assert-OwnedBackgroundTask $watch 'Watch-Background.ps1'
  if($watch.Principal.UserId -ne $task.Principal.UserId){throw 'Recovery task belongs to a different Windows user.'}
  if(-not (Test-WindowlessAction $watch 'Watch-Background.ps1')){
    Set-ScheduledTask -TaskName $watchName -TaskPath '\' -Action (New-WindowlessAction 'Watch-Background.ps1') | Out-Null
    $watch=Get-ScheduledTask -TaskName $watchName -TaskPath '\'
  }
}
if(-not $watch){
  $action=New-WindowlessAction 'Watch-Background.ps1'
  $timer=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 5)
  $principal=New-ScheduledTaskPrincipal -UserId $task.Principal.UserId -LogonType Interactive -RunLevel Limited
  $settings=New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 1)
  Register-ScheduledTask -TaskName $watchName -TaskPath '\' -Action $action -Trigger $timer -Principal $principal -Settings $settings | Out-Null
  $watch=Get-ScheduledTask -TaskName $watchName -TaskPath '\'
}
if(-not (Test-WindowlessAction $task 'Run-Silent.ps1') -or -not (Test-WindowlessAction $watch 'Watch-Background.ps1')){throw 'Windowless task registration failed'}
@{at=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds();syncConsoleFree=$true;recoveryConsoleFree=$true} | ConvertTo-Json -Compress | Set-Content (Join-Path $PSScriptRoot 'windowless-tasks.json') -Encoding UTF8
Write-Output 'RECOVERY_ENABLED'
