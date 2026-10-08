$ErrorActionPreference='Stop'
$windowlessRoot=$PSScriptRoot
$launcherSource=Join-Path $windowlessRoot 'Windowless-Launcher.cs'
$launcherHash=(Get-FileHash -LiteralPath $launcherSource -Algorithm SHA256).Hash.Substring(0,16).ToLowerInvariant()
$windowlessLauncher=Join-Path $windowlessRoot ('Background-Launcher-'+$launcherHash+'.exe')
if(-not (Test-Path -LiteralPath $windowlessLauncher)){
  $pending=$windowlessLauncher+'.next.exe'
  try{
    Remove-Item -LiteralPath $pending -Force -ErrorAction SilentlyContinue
    Add-Type -TypeDefinition (Get-Content -LiteralPath $launcherSource -Raw) -OutputAssembly $pending -OutputType WindowsApplication
    Move-Item -LiteralPath $pending -Destination $windowlessLauncher -Force
  } finally {Remove-Item -LiteralPath $pending -Force -ErrorAction SilentlyContinue}
}
# Read back the PE subsystem; a console binary must never become a task action.
$pe=[IO.File]::ReadAllBytes($windowlessLauncher)
$offset=[BitConverter]::ToInt32($pe,0x3c)
if($offset -lt 0 -or $offset+94 -gt $pe.Length -or [BitConverter]::ToUInt32($pe,$offset) -ne 0x4550 -or [BitConverter]::ToUInt16($pe,$offset+92) -ne 2){throw 'WINDOWLESS_LAUNCHER_INVALID'}
if(-not ('WindowlessPath' -as [type])){
  Add-Type -TypeDefinition 'using System;using System.Text;using System.Runtime.InteropServices;public static class WindowlessPath{[DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]static extern uint GetLongPathName(string path,StringBuilder output,uint size);public static string Canonical(string path){var output=new StringBuilder(32768);uint count=GetLongPathName(System.IO.Path.GetFullPath(path),output,32768);if(count==0||count>=32768)throw new Exception("TASK_PATH_UNAVAILABLE");return output.ToString().TrimEnd((char)92);}}'
}
function Same-TaskPath([string]$a,[string]$b){return [WindowlessPath]::Canonical($a) -eq [WindowlessPath]::Canonical($b)}
function Get-WindowlessMode([string]$script){
  if($script -eq 'Run-Silent.ps1'){return 'sync'}
  if($script -eq 'Watch-Background.ps1'){return 'watch'}
  throw 'INVALID_BACKGROUND_SCRIPT'
}
function Test-WindowlessAction($task,[string]$script){
  return @($task.Actions).Count -eq 1 -and (Same-TaskPath ([string]$task.Actions[0].Execute) $windowlessLauncher) -and [string]$task.Actions[0].Arguments -eq (Get-WindowlessMode $script) -and (Same-TaskPath ([string]$task.Actions[0].WorkingDirectory) $windowlessRoot)
}
function Assert-OwnedBackgroundTask($task,[string]$script){
  if(Test-WindowlessAction $task $script){return}
  $legacy=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
  $match=[regex]::Match([string]$task.Actions[0].Arguments,'(?i)(?:^|\s)-File\s+"([^"\r\n]+)"(?:\s|$)')
  if(@($task.Actions).Count -eq 1 -and (Same-TaskPath ([string]$task.Actions[0].Execute) $legacy) -and $match.Success -and (Same-TaskPath $match.Groups[1].Value (Join-Path $windowlessRoot $script)) -and (Same-TaskPath ([string]$task.Actions[0].WorkingDirectory) $windowlessRoot)){return}
  # An older signed launcher belongs here only when its executable is in this root.
  if(@($task.Actions).Count -eq 1 -and (Same-TaskPath ([IO.Path]::GetDirectoryName([string]$task.Actions[0].Execute)) $windowlessRoot) -and [IO.Path]::GetFileName([string]$task.Actions[0].Execute) -match '^Background-Launcher-[a-f0-9]{16}\.exe$' -and [string]$task.Actions[0].Arguments -eq (Get-WindowlessMode $script) -and (Same-TaskPath ([string]$task.Actions[0].WorkingDirectory) $windowlessRoot)){return}
  throw 'The registered background task belongs to a different installation.'
}
function New-WindowlessAction([string]$script){
  New-ScheduledTaskAction -Execute $windowlessLauncher -Argument (Get-WindowlessMode $script) -WorkingDirectory $windowlessRoot
}
