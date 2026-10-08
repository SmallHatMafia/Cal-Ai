$ErrorActionPreference='Stop'
$root=Join-Path $env:TEMP ('headless-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $root | Out-Null
try {
$probe=@'
Add-Type 'using System;using System.Runtime.InteropServices;public class Probe{[DllImport("kernel32.dll")]public static extern IntPtr GetConsoleWindow();[DllImport("user32.dll")]public static extern bool IsWindowVisible(IntPtr hwnd);}'
$h=[Probe]::GetConsoleWindow()
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'result.json'),(@{window=$h.ToInt64();visible=[Probe]::IsWindowVisible($h)}|ConvertTo-Json -Compress))
[Console]::Out.WriteLine('READY');[Console]::Out.Flush()
[void][Console]::In.ReadLine()
exit 75
'@
$path=Join-Path $root 'probe.ps1'
Set-Content $path $probe
$p=New-Object Diagnostics.ProcessStartInfo
$p.FileName=Join-Path $env:SystemRoot 'System32\conhost.exe'
$p.Arguments='--headless -- "'+(Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')+'" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$path+'"'
$p.UseShellExecute=$false;$p.CreateNoWindow=$true;$p.RedirectStandardInput=$true;$p.RedirectStandardOutput=$true;$p.RedirectStandardError=$true
$c=[Diagnostics.Process]::Start($p)
$ready=$c.StandardOutput.ReadLineAsync()
if(-not $ready.Wait(20000)){ $c.Kill();throw 'No readiness'}
Write-Output ('READINESS='+$ready.Result)
$c.StandardInput.WriteLine('CLOSE');$c.StandardInput.Flush()
if(-not $c.WaitForExit(20000)){$c.Kill();throw 'No exit'}
Write-Output ('EXIT='+$c.ExitCode)
Get-Content (Join-Path $root 'result.json') | Write-Output
Write-Output ('STDERR='+$c.StandardError.ReadToEnd())
}finally{Remove-Item $root -Recurse -Force}