// Disposable Windows CI only; never uses a real QuickBooks account.
import {stageInstaller,officialInstaller} from './probe.mjs';
import {join} from 'node:path';
import {spawn} from 'node:child_process';
if(process.env.GITHUB_ACTIONS!=='true'||process.env.RUNNER_OS!=='Windows')throw Error('CI_ONLY');
const root=process.cwd();
await stageInstaller(root);
const script=String.raw`
$ErrorActionPreference='Stop'
$p=Start-Process -FilePath $env:TEST_INSTALLER -ArgumentList '--silent' -PassThru
if(-not $p.WaitForExit(120000)){throw 'INSTALL_TIMEOUT'}
Write-Output ('InstallerExit='+$p.ExitCode)
$exe=Get-Item (Join-Path $env:LOCALAPPDATA 'QuickBooksAdvanced\app-3.10.4\QuickBooks Online.exe')
if(-not $exe){throw 'INSTALLED_EXE_MISSING'}
Get-Process -Name 'QuickBooks Online' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
$app=Start-Process -FilePath $exe.FullName -ArgumentList '--remote-debugging-address=127.0.0.1','--remote-debugging-port=19223','--enable-logging' -PassThru
try {
 Start-Sleep -Seconds 45
 $targets=Invoke-RestMethod http://127.0.0.1:19223/json/list -TimeoutSec 3
 $targets | Select-Object type,title,url | ConvertTo-Json -Compress
 Get-Process | Where-Object {$_.ProcessName -match 'QuickBooks|msedgewebview'} | Select-Object ProcessName,MainWindowTitle | ConvertTo-Json -Compress
 $log=Join-Path $env:APPDATA 'QuickBooks Advanced\logs\main.log'
 if(Test-Path $log){Get-Content $log -Tail 30}
} finally {Get-Process -Name 'QuickBooks Online' -ErrorAction SilentlyContinue | Stop-Process -Force}
Start-Sleep -Seconds 2
$result=& (Join-Path $PWD 'windows-app-test/Installed-Private-Probe.ps1') -Executable $exe.FullName -Port 19224
$result | Write-Output
$proof=$result | Select-Object -Last 1 | ConvertFrom-Json
if(-not ($proof.isolated -and $proof.cdp -and $proof.intuitPage -and $proof.closed)){throw 'PRIVATE_SIGNIN_NOT_PROVEN'}
`;
const child=spawn('powershell.exe',['-NoProfile','-NonInteractive','-EncodedCommand',Buffer.from(script,'utf16le').toString('base64')],{env:{...process.env,TEST_INSTALLER:join(root,'downloads',officialInstaller.filename)},stdio:'inherit',timeout:200000});
child.on('exit',code=>{process.exitCode=code??1});
