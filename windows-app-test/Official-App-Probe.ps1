param([string]$Executable,[int]$Port,[switch]$CompileOnly)
if(-not $CompileOnly -and ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows')){throw 'DISPOSABLE_WINDOWS_CI_REQUIRED'}
$ErrorActionPreference='Stop'
# A bounded, signed diagnostic. Never used as the production sync launcher.
$native=@'
using System; using System.Text; using System.Runtime.InteropServices; using System.ComponentModel;
public static class QboAppProbe {
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] public struct SI { public int cb; public string reserved,desktop,title; public int x,y,cx,cy,xc,yc,fill,flags; public short show,reserved2; public IntPtr reservedPtr,input,output,error; }
 [StructLayout(LayoutKind.Sequential)] public struct PI { public IntPtr process,thread; public uint pid,tid; }
 [StructLayout(LayoutKind.Sequential)] public struct Limits { public long processTime,jobTime; public uint flags; public UIntPtr min,max; public uint active; public UIntPtr affinity; public uint priority,scheduling; }
 [StructLayout(LayoutKind.Sequential)] public struct IO { public ulong readOps,writeOps,otherOps,readBytes,writeBytes,otherBytes; }
 [StructLayout(LayoutKind.Sequential)] public struct Extended { public Limits basic; public IO io; public UIntPtr processMemory,jobMemory,peakProcess,peakJob; }
 [StructLayout(LayoutKind.Sequential)] public struct Accounting { public long user,kernel,periodUser,periodKernel; public uint faults,total,active,terminated; }
 [DllImport("user32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateWindowStation(string name,uint flags,uint access,IntPtr security);
 [DllImport("user32.dll")] public static extern IntPtr GetProcessWindowStation();
 [DllImport("user32.dll",SetLastError=true)] public static extern bool SetProcessWindowStation(IntPtr station);
 [DllImport("user32.dll")] public static extern bool CloseWindowStation(IntPtr station);
 [DllImport("user32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateDesktop(string name,IntPtr device,IntPtr mode,uint flags,uint access,IntPtr security);
 [DllImport("user32.dll")] public static extern bool CloseDesktop(IntPtr desktop);
 [DllImport("user32.dll",SetLastError=true)] public static extern IntPtr GetThreadDesktop(uint thread);
 [DllImport("user32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool GetUserObjectInformation(IntPtr obj,int index,StringBuilder text,int length,out int needed);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr security,string name);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job,int kind,IntPtr value,uint length);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool QueryInformationJobObject(IntPtr job,int kind,IntPtr value,uint length,IntPtr returned);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CreateProcess(string app,StringBuilder command,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string cwd,ref SI startup,out PI process);
 [DllImport("kernel32.dll",SetLastError=true)] static extern uint ResumeThread(IntPtr thread);
 [DllImport("kernel32.dll")] public static extern bool TerminateJobObject(IntPtr job,uint code);
 [DllImport("kernel32.dll")] public static extern bool TerminateProcess(IntPtr process,uint code);
 [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr handle);
 [DllImport("kernel32.dll")] public static extern uint WaitForSingleObject(IntPtr handle,uint ms);
 static void Check(bool ok){if(!ok)throw new Win32Exception(Marshal.GetLastWin32Error());}
 public static string Name(IntPtr obj){var s=new StringBuilder(512);int n;Check(GetUserObjectInformation(obj,2,s,1024,out n));return s.ToString();}
 public static IntPtr Station(){var h=CreateWindowStation(null,0,0x10000000,IntPtr.Zero);Check(h!=IntPtr.Zero);if(Name(h).Equals("WinSta0",StringComparison.OrdinalIgnoreCase)){CloseWindowStation(h);throw new Exception("INTERACTIVE_STATION");}return h;}
 public static IntPtr Desktop(string name){var h=CreateDesktop(name,IntPtr.Zero,IntPtr.Zero,0,0x10000000,IntPtr.Zero);Check(h!=IntPtr.Zero);return h;}
 static void Set(IntPtr job,int kind,object data){int size=Marshal.SizeOf(data);var p=Marshal.AllocHGlobal(size);try{Marshal.StructureToPtr(data,p,false);Check(SetInformationJobObject(job,kind,p,(uint)size));}finally{Marshal.FreeHGlobal(p);}}
 public static IntPtr Job(){
  var h=CreateJobObject(IntPtr.Zero,null);Check(h!=IntPtr.Zero);
  try{
   var limits=new Extended();limits.basic.flags=0x2000|0x200|0x20;limits.basic.priority=0x4000;limits.jobMemory=new UIntPtr(1073741824);
   Set(h,9,limits); // kill on close, 1 GiB committed memory ceiling, below-normal priority
   Set(h,4,(uint)0xFF); // no external USER handles, desktop switching, clipboard or system changes
   return h;
  }catch{CloseHandle(h);throw;}
 }
 public static Accounting Usage(IntPtr job){int size=Marshal.SizeOf(typeof(Accounting));var p=Marshal.AllocHGlobal(size);try{Check(QueryInformationJobObject(job,1,p,(uint)size,IntPtr.Zero));return (Accounting)Marshal.PtrToStructure(p,typeof(Accounting));}finally{Marshal.FreeHGlobal(p);}}
 public static ulong PeakBytes(IntPtr job){int size=Marshal.SizeOf(typeof(Extended));var p=Marshal.AllocHGlobal(size);try{Check(QueryInformationJobObject(job,9,p,(uint)size,IntPtr.Zero));return ((Extended)Marshal.PtrToStructure(p,typeof(Extended))).peakJob.ToUInt64();}finally{Marshal.FreeHGlobal(p);}}
 public static PI Start(IntPtr job,string exe,string args,string desktop,string cwd){
  var si=new SI();si.cb=Marshal.SizeOf(si);si.desktop=desktop;si.flags=1;si.show=0;PI p;
  Check(CreateProcess(exe,new StringBuilder("\""+exe+"\" "+args),IntPtr.Zero,IntPtr.Zero,false,0x08000004,IntPtr.Zero,cwd,ref si,out p));
  try{Check(AssignProcessToJobObject(job,p.process));Check(ResumeThread(p.thread)!=0xFFFFFFFF);return p;}
  catch{TerminateProcess(p.process,1);CloseHandle(p.thread);CloseHandle(p.process);throw;}
 }
}
'@
Add-Type -TypeDefinition $native
if($CompileOnly){Write-Output 'PROBE_COMPILED';return}
if($env:OS -ne 'Windows_NT'){throw 'WINDOWS_REQUIRED'}
$result=@{state='failed';code='PROBE_FAILED';isolated=$false;appStarted=$false;cdp=$false;intuitPage=$false;closed=$false}
$station=[IntPtr]::Zero;$desktop=[IntPtr]::Zero;$job=[IntPtr]::Zero;$processInfo=$null
$original=[QboAppProbe]::GetProcessWindowStation()
$protocol='HKCU:\Software\Classes\quickbooks'
$ownsProtocol=$false
$step='preflight'
try {
 if(-not (Test-Path -LiteralPath $Executable) -or $Port -lt 1024 -or $Port -gt 65535){throw 'INVALID_CONFIG'}
 # Never share or alter an existing Intuit app profile or installation.
 foreach($path in @((Join-Path $env:APPDATA 'QuickBooks Advanced'),(Join-Path $env:LOCALAPPDATA 'QuickBooksAdvanced'),(Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Desktop\QuickBooks Advanced.lnk'),(Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\SBSEG-PD MambaScrum\QuickBooks Advanced.lnk'))){if(Test-Path -LiteralPath $path){throw 'EXISTING_APP'}}
 if(Get-Process -Name 'QuickBooks Online','QuickBooks Advanced' -ErrorAction SilentlyContinue){throw 'EXISTING_APP'}
 if(Test-Path $protocol){throw 'EXISTING_APP'}
 # Intuit signs the outer installer, verified by stageInstaller before extract.
 # Its bundled Electron executable is unsigned: pin those exact extracted bytes.
 if((Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash -ne '245fa8abebef8d769e526f18bd22e2bbe475224d118c072ceaaa433c943b0763'){throw 'SIGNATURE_FAILED'}
 $step='isolation'
 $station=[QboAppProbe]::Station()
 if(-not [QboAppProbe]::SetProcessWindowStation($station)){throw 'STATION_FAILED'}
 $desktopName='QboProbe-'+[Guid]::NewGuid().ToString('N')
 $desktop=[QboAppProbe]::Desktop($desktopName)
 if(-not [QboAppProbe]::SetProcessWindowStation($original)){throw 'STATION_FAILED'}
 $job=[QboAppProbe]::Job()
 $desktopPath=[QboAppProbe]::Name($station)+'\'+$desktopName
 $step='launch'
 $args='--remote-debugging-address=127.0.0.1 --remote-debugging-port='+$Port+' --disable-background-networking --no-first-run'
 $processInfo=[QboAppProbe]::Start($job,$Executable,$args,$desktopPath,(Split-Path -Parent $Executable))
 $ownsProtocol=$true
 $started=Get-Date
 $step='desktop-check'
 for($attempt=0;$attempt -lt 50;$attempt++){
  try{$result.isolated=([QboAppProbe]::Name([QboAppProbe]::GetThreadDesktop($processInfo.tid)) -eq $desktopName)}catch{}
  if($result.isolated){break}
  if([QboAppProbe]::WaitForSingleObject($processInfo.process,0) -eq 0){$result.processExited=$true;break}
  Start-Sleep -Milliseconds 100
 }
 if(-not $result.isolated){throw 'ISOLATION_FAILED'}
 $step='observe'
 $cpuAtIdle=$null;$idleStarted=$null
 while(((Get-Date)-$started).TotalSeconds -lt 40){
  $usage=[QboAppProbe]::Usage($job)
  if($usage.active -eq 0){break}
  $result.appStarted=$true
  try {
   $targets=Invoke-RestMethod -Uri ('http://127.0.0.1:'+ $Port +'/json/list') -TimeoutSec 1
   $result.cdp=$true
   foreach($target in $targets){try{$u=[Uri]$target.url;if($u.Host -eq 'intuit.com' -or $u.Host.EndsWith('.intuit.com')){$result.intuitPage=$true}}catch{}}
  }catch{}
  if(((Get-Date)-$started).TotalSeconds -ge 25 -and $null -eq $cpuAtIdle){$cpuAtIdle=$usage.user+$usage.kernel;$idleStarted=Get-Date}
  Start-Sleep -Milliseconds 750
 }
 $usage=[QboAppProbe]::Usage($job)
 $result.peakCommitMiB=[Math]::Round([QboAppProbe]::PeakBytes($job)/1MB,1)
 $result.cpuSeconds=[Math]::Round(($usage.user+$usage.kernel)/10000000,2)
 $result.elapsedSeconds=[Math]::Round(((Get-Date)-$started).TotalSeconds,1)
 if($null -ne $cpuAtIdle){$result.idleCpuPercent=[Math]::Round((($usage.user+$usage.kernel-$cpuAtIdle)/10000000)/[Math]::Max(1,((Get-Date)-$idleStarted).TotalSeconds)/[Environment]::ProcessorCount*100,2)}
 $result.state='measured';$result.code=$null
}catch {
 $allowed=@('EXISTING_APP','SIGNATURE_FAILED','ISOLATION_FAILED','STATION_FAILED','INVALID_CONFIG')
 $errorDetail=$_.Exception
 while($null -ne $errorDetail.InnerException){$errorDetail=$errorDetail.InnerException}
 if($errorDetail -is [System.ComponentModel.Win32Exception]){$result.win32Error=$errorDetail.NativeErrorCode}
 $result.code=if($allowed -contains $_.Exception.Message){$_.Exception.Message}else{'PROBE_'+$step.ToUpper()+'_FAILED'}
}finally {
 [void][QboAppProbe]::SetProcessWindowStation($original)
 if($job -ne [IntPtr]::Zero){
  [void][QboAppProbe]::TerminateJobObject($job,0)
  for($i=0;$i -lt 20;$i++){if([QboAppProbe]::Usage($job).active -eq 0){$result.closed=$true;break};Start-Sleep -Milliseconds 100}
  [void][QboAppProbe]::CloseHandle($job)
 }
 if($null -ne $processInfo){[void][QboAppProbe]::CloseHandle($processInfo.thread);[void][QboAppProbe]::CloseHandle($processInfo.process)}
 if($desktop -ne [IntPtr]::Zero){[void][QboAppProbe]::CloseDesktop($desktop)}
 if($station -ne [IntPtr]::Zero){[void][QboAppProbe]::CloseWindowStation($station)}
 # The official app registers its protocol on startup. Remove only a newly
 # created registration that still points to this exact temporary executable.
 if($ownsProtocol -and $result.closed -and (Test-Path ($protocol+'\shell\open\command'))){
  $command=(Get-Item ($protocol+'\shell\open\command')).GetValue('')
  if($command -and $command.StartsWith(('"'+$Executable+'"'),[StringComparison]::OrdinalIgnoreCase)){Remove-Item $protocol -Recurse -Force}
 }
}
$result | ConvertTo-Json -Compress | Write-Output
