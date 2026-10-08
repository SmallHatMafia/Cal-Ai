$ErrorActionPreference='Stop'
$root=Join-Path $env:TEMP ('windowless task '+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$tasks=@('Target Roofing QuickBooks Sync','Target Roofing QuickBooks Recovery')
try{
  foreach($name in @('Windowless-Launcher.cs','Windowless-Tasks.ps1','Ensure-Recovery.ps1')){Copy-Item (Join-Path $PSScriptRoot $name) $root}
  $fixture=@'
Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public static class Probe{[DllImport("kernel32.dll")]public static extern IntPtr GetConsoleWindow();}'
$mode=if([IO.Path]::GetFileName($PSCommandPath) -eq 'Run-Silent.ps1'){'sync'}else{'watch'}
@{console=([Probe]::GetConsoleWindow().ToInt64());pid=$PID} | ConvertTo-Json -Compress | Set-Content (Join-Path $PSScriptRoot ($mode+'.json'))
Start-Sleep -Seconds 1
exit 37
'@
  foreach($script in @('Run-Silent.ps1','Watch-Background.ps1')){Set-Content (Join-Path $root $script) $fixture}
  $ps=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
  # Positive control: the direct console-host path really has a console.
  $old=New-Object Diagnostics.ProcessStartInfo
  $old.FileName=$ps;$old.Arguments='-NoProfile -File "'+(Join-Path $root 'Run-Silent.ps1')+'"';$old.UseShellExecute=$false
  $process=[Diagnostics.Process]::Start($old);$process.WaitForExit();$process.Dispose()
  $before=Get-Content (Join-Path $root 'sync.json') -Raw | ConvertFrom-Json
  if($before.console -eq 0){throw 'Positive control did not allocate a console'}
  Write-Output 'POSITIVE_CONTROL_CONSOLE_PRESENT'
  $user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
  $principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
  $settings=New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
  $trigger=New-ScheduledTaskTrigger -AtLogOn -User $user
  for($i=0;$i -lt 2;$i++){
    $script=@('Run-Silent.ps1','Watch-Background.ps1')[$i]
    $action=New-ScheduledTaskAction -Execute $ps -Argument ('-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "'+(Join-Path $root $script)+'"') -WorkingDirectory $root
    Register-ScheduledTask -TaskName $tasks[$i] -TaskPath '\' -Action $action -Principal $principal -Trigger $trigger -Settings $settings | Out-Null
  }
  & (Join-Path $root 'Ensure-Recovery.ps1')
  . (Join-Path $root 'Windowless-Tasks.ps1')
  foreach($mode in @('sync','watch')){
    $script=if($mode -eq 'sync'){'Run-Silent.ps1'}else{'Watch-Background.ps1'}
    $task=Get-ScheduledTask -TaskName $tasks[@('sync','watch').IndexOf($mode)]
    if(-not (Test-WindowlessAction $task $script)){throw 'Task still directly starts PowerShell'}
    Remove-Item (Join-Path $root ($mode+'.json')) -Force -ErrorAction SilentlyContinue
    $start=New-Object Diagnostics.ProcessStartInfo
    $start.FileName=$windowlessLauncher;$start.Arguments=$mode;$start.UseShellExecute=$false
    $process=[Diagnostics.Process]::Start($start);$process.WaitForExit()
    if($process.ExitCode -ne 37){throw ('Child exit code lost: '+$process.ExitCode)}
    $process.Dispose()
    $after=Get-Content (Join-Path $root ($mode+'.json')) -Raw | ConvertFrom-Json
    if($after.console -ne 0){throw 'A console was allocated by the new launcher'}
  }
  & (Join-Path $root 'Ensure-Recovery.ps1') # Migration is idempotent.
  Write-Output 'WINDOWLESS_TASK_ACTIONS_AND_BOTH_CHILDREN_VERIFIED'
} finally {
  foreach($task in $tasks){Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue}
  Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
}
