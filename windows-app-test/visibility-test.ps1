param([Parameter(Mandatory=$true)][string]$Node)
$ErrorActionPreference='Stop'
if($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows'){throw 'CI_ONLY'}
# Observe the actual input desktop, not the private app's launch thread.
Add-Type -TypeDefinition @'
using System; using System.Collections.Generic; using System.Runtime.InteropServices;
public static class VisibleQboWindows {
 public delegate bool Callback(IntPtr window,IntPtr arg);
 [DllImport("user32.dll",SetLastError=true)] public static extern IntPtr OpenInputDesktop(uint flags,bool inherit,uint access);
 [DllImport("user32.dll")] public static extern bool CloseDesktop(IntPtr desktop);
 [DllImport("user32.dll",SetLastError=true)] static extern bool EnumDesktopWindows(IntPtr desktop,Callback callback,IntPtr arg);
 [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
 [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window,out uint pid);
 public static uint[] Pids(IntPtr desktop){var pids=new HashSet<uint>();Callback read=(window,arg)=>{uint pid;if(IsWindowVisible(window)){GetWindowThreadProcessId(window,out pid);pids.Add(pid);}return true;};if(!EnumDesktopWindows(desktop,read,IntPtr.Zero))throw new Exception("INPUT_DESKTOP_ENUMERATION_FAILED");return new List<uint>(pids).ToArray();}
}
'@
$desktop=[VisibleQboWindows]::OpenInputDesktop(0,$false,0x41)
if($desktop -eq [IntPtr]::Zero){throw 'INPUT_DESKTOP_UNAVAILABLE'}
$fixture=$null;$runner=$null;$seen=@{}
try {
 # Positive control: a visible harmless window must be detected by this observer.
 $fixtureFile=Join-Path $env:RUNNER_TEMP 'qbo-visibility-positive.ps1'
 @'
Add-Type -AssemblyName System.Windows.Forms
$form=New-Object System.Windows.Forms.Form
$form.Text='Visibility positive control'
$form.ShowInTaskbar=$true
$timer=New-Object System.Windows.Forms.Timer
$timer.Interval=5000
$timer.add_Tick({$form.Close()})
$timer.Start()
[void]$form.ShowDialog()
'@ | Set-Content -LiteralPath $fixtureFile
 $fixture=Start-Process powershell.exe -ArgumentList @('-NoProfile','-File',$fixtureFile) -PassThru -WindowStyle Hidden
 $positive=$false;$deadline=(Get-Date).AddSeconds(8)
 while((Get-Date) -lt $deadline -and -not $fixture.HasExited){
  if([VisibleQboWindows]::Pids($desktop) -contains [uint32]$fixture.Id){$positive=$true;break}
  Start-Sleep -Milliseconds 20
 }
 if(-not $positive){throw 'OBSERVER_DID_NOT_DETECT_VISIBLE_CONTROL'}
 $fixture.WaitForExit()
 Write-Output '{"positiveControlDetected":true}'
 $stdout=Join-Path $env:RUNNER_TEMP 'qbo-visibility-out.txt'
 $stderr=Join-Path $env:RUNNER_TEMP 'qbo-visibility-err.txt'
 $runner=Start-Process -FilePath $Node -ArgumentList @('windows-app-test/visibility-test.mjs','--run') -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
 $deadline=(Get-Date).AddMinutes(4)
 $samples=0
 while(-not $runner.HasExited -and (Get-Date) -lt $deadline){
  $visible=[VisibleQboWindows]::Pids($desktop);$samples++
  foreach($process in @(Get-Process -Name 'QuickBooks Online' -ErrorAction SilentlyContinue)){
   if($visible -contains [uint32]$process.Id){$seen[$process.Id]=$true}
  }
  Start-Sleep -Milliseconds 20
 }
 if(-not $runner.HasExited){Stop-Process -Id $runner.Id;throw 'VISIBILITY_TEST_TIMEOUT'}
 Get-Content -LiteralPath $stdout
 Get-Content -LiteralPath $stderr
 @{inputDesktopSamples=$samples;visibleQuickBooksProcesses=$seen.Count;exitCode=$runner.ExitCode} | ConvertTo-Json -Compress | Write-Output
 if($runner.ExitCode -ne 0){throw 'APP_TEST_FAILED'}
 if($seen.Count -ne 0){throw 'QUICKBOOKS_VISIBLE_ON_INPUT_DESKTOP'}
} finally {
 if($fixture -and -not $fixture.HasExited){Stop-Process -Id $fixture.Id}
 if($runner -and -not $runner.HasExited){Stop-Process -Id $runner.Id}
 [void][VisibleQboWindows]::CloseDesktop($desktop)
}
