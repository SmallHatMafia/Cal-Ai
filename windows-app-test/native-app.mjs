// Official app backend, restricted to an owned private Windows desktop.
import {spawn} from 'node:child_process';
import {createServer} from 'node:net';
import {join} from 'node:path';
import {connectInspector,createElectronContext} from './electron-controller.mjs';
// Startup requests can precede Playwright's Frame object in Electron. Never
// dereference request.frame() here; a route-handler rejection kills the runner.
export async function routeProjectStart(route,onFailure=()=>{}){
  try{
    const request=route.request();
    if(!request.isNavigationRequest()||request.headers()['sec-fetch-dest']!=='document')return await route.continue();
    const destination=new URL(request.url());
    if(destination.origin!=='https://qbo.intuit.com'||destination.pathname!=='/app/homepage')return await route.continue();
    destination.pathname='/app/projects-overview';destination.searchParams.set('jobId','projects');
    await route.fulfill({status:302,headers:{location:destination.href,'cache-control':'no-store'},body:''});
  }catch(error){onFailure(/closed/i.test(String(error.message))?'TARGET_CLOSED':'STARTUP_ROUTE_FAILED');await route.abort().catch(()=>{});}
}
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function bounded(promise,ms,fallback){let timer;try{return await Promise.race([promise,new Promise(resolve=>{timer=setTimeout(()=>resolve(fallback),ms);})]);}finally{clearTimeout(timer);}}
export async function openNativeApp({root,chromium,executable}) {
  if(process.platform!=='win32')throw Error('WINDOWS_REQUIRED');
  const port=await new Promise((resolve,reject)=>{const s=createServer();s.on('error',reject);s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});
  const child=spawn(join(process.env.SystemRoot,'System32','WindowsPowerShell','v1.0','powershell.exe'),['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',join(root,'Native-App-Host.ps1'),'-Executable',executable,'-Port',String(port)],{windowsHide:true,stdio:['pipe','pipe','pipe']});
  let browser,context,closing=false,metrics=null,metricsBuffer='',hostError='',hostExit=null,stage='launch';
  const stages=[];
  const mark=value=>{stage=value;stages.push({stage:value,at:Date.now()});};
  const hostStatus=()=>({exitCode:child.exitCode,signal:child.signalCode,hasError:!!hostError,detail:hostError.replace(/[A-Z]:\\Users\\[^\\\s]+/gi,'[user]').replace(/https?:\/\/\S+/gi,'[url]').replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi,'[email]').slice(-1800),exit:hostExit,stages});
  child.stderr.on('data',chunk=>{hostError=(hostError+chunk.toString()).slice(-4096);});
  child.stdout.on('data',chunk=>{metricsBuffer=(metricsBuffer+chunk.toString()).slice(-4096);let end;while((end=metricsBuffer.indexOf('\n'))>=0){const line=metricsBuffer.slice(0,end);metricsBuffer=metricsBuffer.slice(end+1);try{const v=JSON.parse(line);if(v.hostExit===true)hostExit=v;if(v.metrics===true)metrics={peakJobBytes:Number(v.peakJobBytes),activeProcesses:Number(v.activeProcesses),terminatedProcesses:Number(v.terminatedProcesses),elapsedSeconds:Number(v.elapsedSeconds),memoryLimitHits:Number(v.memoryLimitHits??0)};}catch{}}});
  child.stdin.on('error',()=>{});
  const exit=new Promise(resolve=>{child.once('exit',resolve);child.once('error',resolve);});
  async function close(){
    if(closing)return;closing=true;
    // Let Electron persist its own native authentication state before cleanup.
    if(context)await bounded(context.quit(),5000,null);
    else browser?.close();
    // QuickBooks' will-quit handler waits 10s, then flushes state for up to 30s.
    // Killing it after 3s interrupted that documented-in-package shutdown path.
    await bounded(exit,context?45000:3000,null);
    child.stdin.end('CLOSE\n');
    await bounded(exit,3000,null);
    if(child.exitCode===null)child.kill(); // Owned host's kill-on-close job contains only its app.
  }
  try{
    await new Promise((resolve,reject)=>{
      let out='';const timer=setTimeout(()=>done(Error('APP_START_TIMEOUT')),20000);
      const onError=()=>done(Error('APP_HOST_FAILED'));
      const onExit=()=>done(Error('APP_HOST_EXITED'));
      const onData=b=>{out=(out+b.toString()).slice(-8192);for(const line of out.split(/\r?\n/)){try{const p=JSON.parse(line);if(p.ready&&p.isolated&&p.pid>0)return done();}catch{}}};
      function done(error){clearTimeout(timer);child.off('error',onError);child.off('exit',onExit);child.stdout.off('data',onData);error?reject(error):resolve();}
      child.on('error',onError);child.on('exit',onExit);child.stdout.on('data',onData);
    });
    // Establish that the vendor app can start before attaching any controller.
    mark('unattached_startup');
    await bounded(exit,50,null);
    if(child.exitCode!==null)throw Error('APP_EXITED_WITHOUT_CONTROLLER');
    mark('inspector_connect');
    const deadline=Date.now()+30000;
    while(Date.now()<deadline){
      if(child.exitCode!==null)throw Error('APP_EXITED');
      try{browser=await connectInspector(port,{startupPaused:process.env.QBO_CI_HIDDEN_POLICY==='1'});break;}catch{await sleep(300);}
    }
    if(!browser)throw Error('APP_CONNECTION_TIMEOUT');
    mark('controller_initialize');
    context=await createElectronContext(browser);
    mark('controller_ready');
    context.qboNativeMetrics=()=>metrics;
    context.qboHostStatus=hostStatus;
    return {context,close,browserMode:'native_app_private_desktop'};
  }catch(error){
    const known=['APP_ALREADY_OPEN','APP_VERSION_CHANGED','UNVERIFIED_APP_PATH','ISOLATION_FAILED','STATION_FAILED'];
    error.nativeFailure={code:known.find(code=>hostError.includes(code))??(/^[A-Z_]{1,60}$/.test(error.message)?error.message:'NATIVE_START_FAILED'),resources:metrics,stage:error.nativeStage??stage,protocol:error.protocol??null,detail:error.nativeDetail??null,host:hostStatus()};
    await close();throw error;
  }
}
