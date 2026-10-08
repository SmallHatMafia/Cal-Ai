$ErrorActionPreference='Stop'
$windowlessRoot=$PSScriptRoot
$windowlessLauncher=Join-Path $env:SystemRoot 'System32\conhost.exe'
Add-Type -TypeDefinition 'using System;using System.IO;using System.Text;using System.Runtime.InteropServices;public static class WindowlessLauncher{[DllImport("kernel32.dll",CharSet=CharSet.Unicode)]static extern uint GetLongPathName(string p,StringBuilder b,uint n);[DllImport("kernel32.dll")]static extern IntPtr GetConsoleWindow();[DllImport("user32.dll")]static extern bool IsWindowVisible(IntPtr h);public static bool ConsoleVisible(){return IsWindowVisible(GetConsoleWindow());}public static string Canonical(string p){var b=new StringBuilder(32768);var n=GetLongPathName(Path.GetFullPath(p),b,32768);if(n==0||n>=32768)throw new IOException("TASK_PATH_UNAVAILABLE");return b.ToString().TrimEnd((char)92);}}'
function Get-WindowlessArguments([string]$script){
  if($script -notin @('Run-Silent.ps1','Watch-Background.ps1')){throw 'INVALID_BACKGROUND_SCRIPT'}
  $ps=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
  return '--headless -- "'+$ps+'" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+(Join-Path $windowlessRoot $script)+'"'
}
function Same-TaskPath([string]$a,[string]$b){return [WindowlessLauncher]::Canonical($a) -eq [WindowlessLauncher]::Canonical($b)}
function Get-WindowlessMode([string]$script){
  if($script -eq 'Run-Silent.ps1'){return 'sync'}
  if($script -eq 'Watch-Background.ps1'){return 'watch'}
  throw 'INVALID_BACKGROUND_SCRIPT'
}
function Test-WindowlessAction($task,[string]$script){
  return @($task.Actions).Count -eq 1 -and (Same-TaskPath ([string]$task.Actions[0].Execute) $windowlessLauncher) -and [string]$task.Actions[0].Arguments -eq (Get-WindowlessArguments $script) -and (Same-TaskPath ([string]$task.Actions[0].WorkingDirectory) $windowlessRoot)
}
function Assert-OwnedBackgroundTask($task,[string]$script){
  if(Test-WindowlessAction $task $script){return}
  $legacy=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
  $match=[regex]::Match([string]$task.Actions[0].Arguments,'(?i)(?:^|\s)-File\s+"([^"\r\n]+)"(?:\s|$)')
  if(@($task.Actions).Count -eq 1 -and (Same-TaskPath ([string]$task.Actions[0].Execute) $legacy) -and $match.Success -and (Same-TaskPath $match.Groups[1].Value (Join-Path $windowlessRoot $script)) -and (Same-TaskPath ([string]$task.Actions[0].WorkingDirectory) $windowlessRoot)){return}
  if(@($task.Actions).Count -eq 1 -and (Same-TaskPath ([IO.Path]::GetDirectoryName([string]$task.Actions[0].Execute)) $windowlessRoot) -and [IO.Path]::GetFileName([string]$task.Actions[0].Execute) -match '^Background-Launcher-[a-f0-9]{16}\.exe$' -and [string]$task.Actions[0].Arguments -eq (Get-WindowlessArguments $script) -and (Same-TaskPath ([string]$task.Actions[0].WorkingDirectory) $windowlessRoot)){return}
  throw 'The registered background task belongs to a different installation.'
}
function New-WindowlessAction([string]$script){
  New-ScheduledTaskAction -Execute $windowlessLauncher -Argument (Get-WindowlessArguments $script) -WorkingDirectory $windowlessRoot
}
