param([Parameter(Mandatory=$true)][string]$Executable,[Parameter(Mandatory=$true)][int]$Port)
$ErrorActionPreference='Stop'
if($env:OS -ne 'Windows_NT'){throw 'WINDOWS_REQUIRED'}
$native=@'
using System; using System.Text; using System.Runtime.InteropServices; using System.ComponentModel;
public static class QboAppProbe {
 public static IntPtr completion=IntPtr.Zero; private static int memoryLimitHitCount=0;
 [StructLayout(LayoutKind.Sequential)] public struct Completion { public IntPtr key,port; }
 [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr CreateIoCompletionPort(IntPtr file,IntPtr existing,UIntPtr key,uint threads);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetQueuedCompletionStatus(IntPtr port,out uint message,out UIntPtr key,out IntPtr data,uint milliseconds);
 public static int MemoryLimitHits(){uint message;UIntPtr key;IntPtr data;while(completion!=IntPtr.Zero&&GetQueuedCompletionStatus(completion,out message,out key,out data,0)){if(message==10)memoryLimitHitCount++;}return memoryLimitHitCount;}
 public static System.Threading.Tasks.Task<string> StopSignal(){return System.Threading.Tasks.Task.Run<string>(()=>Console.In.ReadLine());}
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
 [DllImport("kernel32.dll")] public static extern bool GetExitCodeProcess(IntPtr process,out uint code);
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
   completion=CreateIoCompletionPort(new IntPtr(-1),IntPtr.Zero,UIntPtr.Zero,1);Check(completion!=IntPtr.Zero);
   var association=new Completion();association.key=h;association.port=completion;Set(h,7,association);
   Set(h,9,limits); // kill on close, 1 GiB committed memory ceiling, below-normal priority
   // CI differential test: retain private station, omit extra job UI limits.
   // Set(h,4,(uint)0xFF); // no external USER handles, desktop switching, clipboard or system changes
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

$expected=Join-Path $env:LOCALAPPDATA 'QuickBooksAdvanced\app-3.10.4\QuickBooks Online.exe'
if([IO.Path]::GetFullPath($Executable) -ne [IO.Path]::GetFullPath($expected)){throw 'UNVERIFIED_APP_PATH'}
if($Port -lt 1024 -or $Port -gt 65535){throw 'INVALID_PORT'}
if((Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash -ne '245fa8abebef8d769e526f18bd22e2bbe475224d118c072ceaaa433c943b0763'){throw 'APP_VERSION_CHANGED'}
# Never attach to or terminate a user's already-open app.
if(Get-Process -Name 'QuickBooks Online' -ErrorAction SilentlyContinue){throw 'APP_ALREADY_OPEN'}
$original=[QboAppProbe]::GetProcessWindowStation()
$station=[IntPtr]::Zero;$desktop=[IntPtr]::Zero;$job=[IntPtr]::Zero;$pi=$null
try{
 $station=[QboAppProbe]::Station()
 if(-not [QboAppProbe]::SetProcessWindowStation($station)){throw 'STATION_FAILED'}
 $name='QboAgent-'+[Guid]::NewGuid().ToString('N')
 $desktop=[QboAppProbe]::Desktop($name)
 if(-not [QboAppProbe]::SetProcessWindowStation($original)){throw 'STATION_FAILED'}
 $job=[QboAppProbe]::Job()
 # The private automation needs DOM forms, not hardware-accelerated graphics
 # or a warmed spare renderer. These switches affect only this owned launch.
 $pi=[QboAppProbe]::Start($job,$Executable,('--inspect=127.0.0.1:'+$Port+' --process-per-site --js-flags="--optimize-for-size" --disable-gpu --disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-features=CalculateNativeWinOcclusion,SpareRendererForSitePerProcess,ElectronUseSpareRenderer'),([QboAppProbe]::Name($station)+'\'+$name),(Split-Path -Parent $Executable))
 $isolated=$false
 for($i=0;$i -lt 100;$i++){
  try{$isolated=([QboAppProbe]::Name([QboAppProbe]::GetThreadDesktop($pi.tid)) -eq $name)}catch{}
  if($isolated){break}
  if([QboAppProbe]::WaitForSingleObject($pi.process,0) -eq 0){throw 'APP_EXITED'}
  Start-Sleep -Milliseconds 100
 }
 if(-not $isolated){throw 'ISOLATION_FAILED'}
 @{ready=$true;isolated=$true;pid=$pi.pid} | ConvertTo-Json -Compress | Write-Output
 # stdin EOF or CLOSE ends this owned app. Three minutes is a hard watchdog.
 $stop=[QboAppProbe]::StopSignal();$started=Get-Date
 $lastMetrics=Get-Date
 while(-not $stop.IsCompleted -and ((Get-Date)-$started).TotalSeconds -lt 180 -and [QboAppProbe]::Usage($job).active -gt 0){
  if(((Get-Date)-$lastMetrics).TotalSeconds -ge 3){
   $usage=[QboAppProbe]::Usage($job)
   @{metrics=$true;peakJobBytes=[QboAppProbe]::PeakBytes($job);activeProcesses=$usage.active;terminatedProcesses=$usage.terminated;elapsedSeconds=[int]((Get-Date)-$started).TotalSeconds;memoryLimitHits=[QboAppProbe]::MemoryLimitHits()} | ConvertTo-Json -Compress | Write-Output
   $lastMetrics=Get-Date
  }
  Start-Sleep -Milliseconds 200
 }
 $usage=[QboAppProbe]::Usage($job)
 [uint32]$rootExit=0;[void][QboAppProbe]::GetExitCodeProcess($pi.process,[ref]$rootExit)
 $reason=if($stop.IsCompleted){'stop_signal'}elseif($usage.active -eq 0){'app_exited'}else{'watchdog'}
 $signal=if($stop.IsCompleted){if($stop.IsFaulted){'fault'}elseif($null -eq $stop.Result){'eof'}elseif($stop.Result -eq 'CLOSE'){'close'}else{'other'}}else{'pending'}
 # Return only Windows' executable/module identifiers, never app logs or session data.
 $fault=$null
 if($reason -eq 'app_exited' -and $rootExit -ne 0){
  try{
   foreach($event in (Get-WinEvent -FilterHashtable @{LogName='Application';Id=1000;StartTime=$started.AddSeconds(-2)} -MaxEvents 8 -ErrorAction Stop)){
    $fields=@{};foreach($entry in ([xml]$event.ToXml()).Event.EventData.Data){$fields[$entry.Name]=[string]$entry.'#text'}
    if($fields.AppName -eq 'QuickBooks Online.exe'){$fault=@{module=$fields.ModuleName;exception=$fields.ExceptionCode;offset=$fields.FaultingOffset};break}
   }
  }catch{}
 }
 @{hostExit=$true;reason=$reason;signal=$signal;rootExitCode=$rootExit;activeProcesses=$usage.active;elapsedMs=[int]((Get-Date)-$started).TotalMilliseconds;peakJobBytes=[QboAppProbe]::PeakBytes($job);memoryLimitHits=[QboAppProbe]::MemoryLimitHits();fault=$fault} | ConvertTo-Json -Compress | Write-Output
}finally{
 [void][QboAppProbe]::SetProcessWindowStation($original)
 if($job -ne [IntPtr]::Zero){[void][QboAppProbe]::TerminateJobObject($job,0);[void][QboAppProbe]::CloseHandle($job)}
 if($null -ne $pi){[void][QboAppProbe]::CloseHandle($pi.thread);[void][QboAppProbe]::CloseHandle($pi.process)}
 if([QboAppProbe]::completion -ne [IntPtr]::Zero){[void][QboAppProbe]::CloseHandle([QboAppProbe]::completion)}
 if($desktop -ne [IntPtr]::Zero){[void][QboAppProbe]::CloseDesktop($desktop)}
 if($station -ne [IntPtr]::Zero){[void][QboAppProbe]::CloseWindowStation($station)}
}
