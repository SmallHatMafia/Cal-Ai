// Disposable GitHub Windows CI only. No login, company data, or accounting writes.
import {createHash} from 'node:crypto';
import {createReadStream} from 'node:fs';
import {mkdir,mkdtemp,open,rename,rm,stat,statfs,writeFile,readFile} from 'node:fs/promises';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {createServer} from 'node:net';
import {createWriteStream} from 'node:fs';
import {pipeline} from 'node:stream/promises';

export const officialInstaller=Object.freeze({
  version:'3.10.4',
  url:'https://http-download.intuit.com/http.intuit/CMO/tango/static/latest/QuickBooks%20AdvancedSetup.exe',
  bytes:202772064,
  sha256:'0ee0b794369a49e5d1cbdfd09d1ba2dadb49674057563eae87c02abd42b8ece1',
  filename:'QuickBooks-Online-Setup-3.10.4.exe'
});
async function matches(path,spec){
  if(await stat(path).then(s=>s.size!==spec.bytes,()=>true))return false;
  const hash=createHash('sha256');for await(const chunk of createReadStream(path))hash.update(chunk);
  return hash.digest('hex')===spec.sha256;
}
export async function stageInstaller(root,spec=officialInstaller,fetcher=fetch,verifyPublisher=verifyIntuitPublisher){
  const dir=join(root,'downloads'),path=join(dir,spec.filename),partial=path+'.part.exe';
  await mkdir(dir,{recursive:true});
  try{
    if(!await matches(path,spec)){
      const response=await fetcher(spec.url,{redirect:'error',signal:AbortSignal.timeout(180000)});
      if(!response.ok||!response.body)throw Error('DOWNLOAD_FAILED');
      const file=await open(partial,'w');let count=0;const hash=createHash('sha256');
      try{
        for await(const chunk of response.body){
          count+=chunk.length;if(count>spec.bytes)throw Error('DOWNLOAD_SIZE');
          hash.update(chunk);
          let offset=0;
          while(offset<chunk.length){const result=await file.write(chunk,offset,chunk.length-offset);if(!result.bytesWritten)throw Error('DOWNLOAD_WRITE');offset+=result.bytesWritten;}
        }
      }finally{await file.close();}
      if(count!==spec.bytes||hash.digest('hex')!==spec.sha256)throw Error('DOWNLOAD_HASH');
      await verifyPublisher(partial);
      await rename(partial,path);
    }else await verifyPublisher(path);
    return {state:'ready',version:spec.version,bytes:spec.bytes};
  }finally{await rm(partial,{force:true}).catch(()=>{});}
}
async function verifyIntuitPublisher(path){
  if(process.platform!=='win32')throw Error('WINDOWS_REQUIRED');
  const script="$ErrorActionPreference='Stop';[Console]::InputEncoding=[Text.UTF8Encoding]::new($false);$path=[Console]::In.ReadToEnd();$s=Get-AuthenticodeSignature -LiteralPath $path;Write-Output ('SignatureStatus='+$s.Status+'; '+$s.StatusMessage);if($s.Status -ne 'Valid'){exit 1};$name=$s.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName,$false);Write-Output ('Publisher='+$name);if($name -notmatch '^Intuit(,)? Inc\\.?$'){exit 2};exit 0";
  await new Promise((resolve,reject)=>{
    const child=spawn(join(process.env.SystemRoot??'C:\\Windows','System32','WindowsPowerShell','v1.0','powershell.exe'),
      ['-NoLogo','-NoProfile','-NonInteractive','-WindowStyle','Hidden','-EncodedCommand',Buffer.from(script,'utf16le').toString('base64')],
      {windowsHide:true,stdio:['pipe','pipe','pipe'],timeout:30000});
    child.stdout.on('data',b=>console.log(b.toString()));child.stderr.on('data',b=>console.log(b.toString()));child.once('error',()=>reject(Error('SIGNATURE_CHECK_FAILED')));
    child.stdin.once('error',()=>reject(Error('SIGNATURE_CHECK_FAILED')));
    child.once('close',code=>code===0?resolve():reject(Error('SIGNATURE_CHECK_FAILED')));
    child.stdin.end(path);
  });
}


const root=dirname(fileURLToPath(import.meta.url));
async function ps(script,env={},timeout=90000){
  return new Promise((resolve,reject)=>{
    let output='';
    const child=spawn(join(process.env.SystemRoot??'C:\\Windows','System32','WindowsPowerShell','v1.0','powershell.exe'),
      ['-NoLogo','-NoProfile','-NonInteractive','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-EncodedCommand',Buffer.from(script,'utf16le').toString('base64')],
      {cwd:root,env:{...process.env,...env},windowsHide:true,stdio:['ignore','pipe','ignore'],timeout});
    child.stdout.on('data',b=>{output=(output+b.toString()).slice(-8192);});
    child.once('error',()=>reject(Error('HELPER_FAILED')));
    child.once('close',code=>code===0?resolve(output.trim()):reject(Error('HELPER_FAILED')));
  });
}
async function unusedPort(){
  return new Promise((resolve,reject)=>{const server=createServer();server.once('error',reject);server.listen(0,'127.0.0.1',()=>{const port=server.address().port;server.close(()=>resolve(port));});});
}
if(process.argv[2]==='--test'){
  if(process.env.GITHUB_ACTIONS!=='true'||process.env.RUNNER_OS!=='Windows')throw Error('DISPOSABLE_WINDOWS_CI_REQUIRED');
  const resultPath=join(root,'official-app-probe-v2.json');
  // An interrupted attempt also counts as attempted; never silently retry it.
  const marker=await open(resultPath,'wx').catch(()=>null);
  if(!marker)process.exit(0);
  await marker.writeFile(JSON.stringify({at:Date.now(),state:'preparing'}));await marker.close();
  const report=async value=>{await writeFile(resultPath+'.next',JSON.stringify({at:Date.now(),...value}));await rename(resultPath+'.next',resultPath);};
  let dir;
  let stage='preflight';
  try {
    const disk=await statfs(root);if(disk.bavail*disk.bsize<2*1024**3)throw Error('DISK_SPACE');
    dir=await mkdtemp(join(root,'official-app-test-'));
    stage='download';
    await stageInstaller(dir);
    const installer=join(dir,'downloads',officialInstaller.filename);
    const outer=join(dir,'bundle.zip');
    // Exact signed 3.10.4 embedded Squirrel ZIP boundaries; never execute its installer.
    await pipeline(createReadStream(installer,{start:169840,end:169840+202238933-1}),createWriteStream(outer));
    const unpack="$ErrorActionPreference='Stop';Add-Type -AssemblyName System.IO.Compression.FileSystem;$zip=[IO.Compression.ZipFile]::OpenRead($env:QBO_PROBE_ZIP);try{$entry=$zip.GetEntry('QuickBooksAdvanced-3.10.4-full.nupkg');if($null -eq $entry){throw 'PACKAGE'};[IO.Compression.ZipFileExtensions]::ExtractToFile($entry,($env:QBO_PROBE_DIR+'\\app.nupkg'))}finally{$zip.Dispose()};$app=[IO.Compression.ZipFile]::OpenRead(($env:QBO_PROBE_DIR+'\\app.nupkg'));try{foreach($entry in $app.Entries){if(-not $entry.FullName.StartsWith('lib/net45/')){continue};$name=$entry.FullName.Substring(10);if(-not $name -or $name.EndsWith('/')){continue};$base=[IO.Path]::GetFullPath(($env:QBO_PROBE_DIR+'\\app\\'));$dest=[IO.Path]::GetFullPath(($base+$name));if(-not $dest.StartsWith($base,[StringComparison]::OrdinalIgnoreCase)){throw 'PATH'};[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($dest))|Out-Null;[IO.Compression.ZipFileExtensions]::ExtractToFile($entry,$dest)}}finally{$app.Dispose()}";
    stage='extract';await ps(unpack,{QBO_PROBE_ZIP:outer,QBO_PROBE_DIR:dir},120000);
    const executable=join(dir,'app','QuickBooks Online.exe'),port=await unusedPort();
    await report({state:'running'});
    stage='launch';
    const raw=await ps("& (Join-Path $env:QBO_PROBE_ROOT 'Official-App-Probe.ps1') -Executable $env:QBO_PROBE_EXE -Port ([int]$env:QBO_PROBE_PORT)",{QBO_PROBE_ROOT:root,QBO_PROBE_EXE:executable,QBO_PROBE_PORT:String(port)},65000);
    const result=JSON.parse(raw.split(/\r?\n/).filter(Boolean).at(-1));
    await report(result);
    console.log(JSON.stringify(result));
    if(!result.isolated||!result.cdp||!result.closed)process.exitCode=1;
  }catch(error){
    const codes=new Set(['SIGN_IN_ACTIVE','DISK_SPACE','DOWNLOAD_FAILED','DOWNLOAD_SIZE','DOWNLOAD_HASH','SIGNATURE_CHECK_FAILED','HELPER_FAILED']);
    const result={state:'failed',code:codes.has(error.message)?error.message:'PROBE_FAILED',stage};await report(result);console.log(JSON.stringify(result));process.exitCode=1;
  }finally{
    if(dir)await rm(dir,{recursive:true,force:true,maxRetries:2,retryDelay:500}).catch(()=>{});
  }
}
