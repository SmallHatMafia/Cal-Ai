// Direct control of the owned Electron main process. No public command endpoint.
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const allowed=url=>{try{return /^https:\/\/(?:c[0-9]+\.)?qbo\.intuit\.com\/app\//.test(url);}catch{return false;}};
export async function connectInspector(port){
 const response=await fetch(`http://127.0.0.1:${port}/json/list`,{signal:AbortSignal.timeout(2000)});
 const targets=await response.json();const target=targets.find(t=>t.type==='node');
 if(!target)throw Error('NATIVE_INSPECTOR_MISSING');
 const url=new URL(target.webSocketDebuggerUrl);
 if(url.protocol!=='ws:'||!['127.0.0.1','localhost'].includes(url.hostname)||url.port!==String(port))throw Error('INVALID_INSPECTOR');
 const ws=new WebSocket(url);await new Promise((resolve,reject)=>{const timer=setTimeout(()=>{ws.close();reject(Error('INSPECTOR_TIMEOUT'));},3000);ws.addEventListener('open',()=>{clearTimeout(timer);resolve();},{once:true});ws.addEventListener('error',()=>{clearTimeout(timer);reject(Error('INSPECTOR_CONNECTION_FAILED'));},{once:true});});
 let sequence=0,contextId=null;const pending=new Map();
 ws.addEventListener('message',event=>{let message;try{message=JSON.parse(event.data);}catch{return;}if(message.method==='Runtime.executionContextCreated'&&message.params.context.auxData?.isDefault)contextId=message.params.context.id;if(message.method==='Runtime.executionContextDestroyed'&&message.params.executionContextId===contextId)contextId=null;const item=pending.get(message.id);if(!item)return;pending.delete(message.id);clearTimeout(item.timer);if(message.error){const error=Error(/context/i.test(message.error.message)?'INSPECTOR_CONTEXT_NOT_READY':/parameter/i.test(message.error.message)?'INSPECTOR_INVALID_PARAMETERS':'INSPECTOR_COMMAND_FAILED');const known=['Object couldn\'t be returned by value','Promise was collected','Execution was terminated','Internal error','Cannot find context with specified id','Inspected target navigated or closed'];error.protocol={method:item.method,code:Number(message.error.code),reason:known.find(t=>message.error.message===t)??'unclassified'};item.reject(error);}else item.resolve(message.result);});
 ws.addEventListener('close',()=>{for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('INSPECTOR_CLOSED'));}pending.clear();});
 const send=(method,params={},timeout=6000)=>new Promise((resolve,reject)=>{const id=++sequence;const timer=setTimeout(()=>{pending.delete(id);reject(Error('NATIVE_COMMAND_TIMEOUT'));},timeout);pending.set(id,{resolve,reject,timer,method});try{ws.send(JSON.stringify({id,method,params}));}catch{clearTimeout(timer);pending.delete(id);reject(Error('INSPECTOR_CLOSED'));}});
 // Keep asynchronous command results in the main process. Some Electron builds
 // collect inspector-awaited Promises, even for synchronous bootstrap code.
 // Poll reads only; never re-evaluate a command that might have clicked Save.
 const raw=async expression=>{const result=await send('Runtime.evaluate',{expression,contextId,includeCommandLineAPI:true,returnByValue:true});if(result.exceptionDetails){const error=Error('NATIVE_MAIN_EVALUATION_FAILED');error.nativeDetail=String(result.exceptionDetails.exception?.description??result.exceptionDetails.text??'').split('\n')[0].replace(/https?:\/\/\S+/g,'[url]').replace(/[A-Z]:\\Users\\[^\\\s]+/gi,'[user]').slice(0,220);throw error;}return result.result?.value;};
 let commandId=0;
 const evaluate=async expression=>{
  const key=`qbo-${Date.now()}-${++commandId}`;
  const slot=`globalThis.__targetQboCommands[${JSON.stringify(key)}]`;
  try{
   await raw(`(()=>{globalThis.__targetQboCommands??=Object.create(null);const task=${slot}={done:false};try{const value=(${expression});if(!value||typeof value.then!=='function'){task.value=value;task.done=true;return true;}task.promise=Promise.resolve(value).then(value=>{task.value=value;task.done=true;},error=>{task.error=/^[A-Z_]{1,60}$/.test(error?.message??'')?error.message:'NATIVE_MAIN_EVALUATION_FAILED';task.detail=String(error?.message??'').slice(0,160);task.done=true;});}catch(error){task.error=/^[A-Z_]{1,60}$/.test(error?.message??'')?error.message:'NATIVE_MAIN_EVALUATION_FAILED';task.detail=String(error?.message??'').slice(0,160);task.done=true;}return true;})()`);
   const deadline=Date.now()+6000;
   do{const state=await raw(`(()=>{const task=${slot};return task?.done?{done:true,value:task.value,error:task.error,detail:task.detail}:{done:false};})()`);if(state?.done){if(state.error){const error=Error(state.error);error.nativeDetail=String(state.detail??'').replace(/https?:\/\/\S+/g,'[url]').replace(/[A-Z]:\\Users\\[^\\\s]+/gi,'[user]').slice(0,180);throw error;}return state.value;}await sleep(50);}while(Date.now()<deadline);
   throw Error('NATIVE_COMMAND_TIMEOUT');
  }finally{await raw(`delete ${slot}`).catch(()=>{});}
 };
 await send('Runtime.enable');
 const readyDeadline=Date.now()+10000;while(contextId===null&&Date.now()<readyDeadline)await sleep(50);
 if(contextId===null){ws.close();throw Error('INSPECTOR_CONTEXT_NOT_READY');}
 return {evaluate,close:()=>ws.close()};
}
const bootstrap=`(()=>{
 const electron=require('electron');
 if(globalThis.__targetQboController)return true;
 const events=[];const watched=new WeakSet();
 const record=(id,event,reason=null)=>{events.push({id,event,reason,at:Date.now()});if(events.length>32)events.shift();};
 const watch=wc=>{if(watched.has(wc))return;watched.add(wc);
  for(const event of ['did-start-navigation','did-navigate','did-navigate-in-page','dom-ready','destroyed','unresponsive','responsive'])wc.on(event,()=>record(wc.id,event));
  wc.on('render-process-gone',(_,details)=>record(wc.id,'render-process-gone',['clean-exit','abnormal-exit','killed','crashed','oom','launch-failed','integrity-failure'].includes(details.reason)?details.reason:'other'));
 };
 for(const wc of electron.webContents.getAllWebContents())watch(wc);
 electron.app.on('web-contents-created',(_,wc)=>watch(wc));
 globalThis.__targetQboController={electron,events};return true;
})()`;
export async function createElectronContext(connection){
 try{await connection.evaluate(bootstrap);}catch(error){error.nativeStage='bootstrap';throw error;}
 const pages=new Map();
 const run=expression=>connection.evaluate(expression);
 const wcExpr=id=>`(()=>{const w=globalThis.__targetQboController.electron.webContents.fromId(${Number(id)});if(!w||w.isDestroyed()||!/^https:\\/\\/(?:c[0-9]+\\.)?qbo\\.intuit\\.com\\/app\\//.test(w.getURL()))throw Error('QBO_DOCUMENT_UNAVAILABLE');return w;})()`;
 const context={
  async qboVisibilityExercise(mode){
   if(process.env.GITHUB_ACTIONS!=='true'||process.env.RUNNER_OS!=='Windows')throw Error('CI_ONLY');
   if(!['show','popup','external'].includes(mode))throw Error('CI_EXERCISE_INVALID');
   return run(`(async()=>{const e=globalThis.__targetQboController.electron;const mode=${JSON.stringify(mode)};if(mode==='external'){await e.shell.openExternal('https://example.com');return {mode};}if(mode==='popup'){const w=new e.BrowserWindow({width:400,height:300,show:true,webPreferences:{sandbox:true}});await w.loadURL('data:text/html,<title>Disposable visibility fixture</title><h1>Fixture</h1>');w.show();w.focus();setTimeout(()=>{if(!w.isDestroyed())w.close();},3000);return {mode};}for(const w of e.BrowserWindow.getAllWindows()){w.show();w.showInactive();w.minimize();w.restore();w.focus();}return {mode};})()`);
  },
  qboBrowserFacts:{browserMode:'native_app_private_desktop',desktopIsolationVerified:true},
  pages:()=>[...pages.values()],
  async refresh(){
   const list=await run(`globalThis.__targetQboController.electron.webContents.getAllWebContents().filter(w=>!w.isDestroyed()).map(w=>({id:w.id,url:w.getURL()}))`);
   const live=new Set();for(const item of list){if(!allowed(item.url))continue;live.add(item.id);if(!pages.has(item.id))pages.set(item.id,makePage(item));else pages.get(item.id)._url=item.url;}
   for(const id of pages.keys())if(!live.has(id))pages.delete(id);
  },
  async qboNativeDiagnostics(){return run(`({events:globalThis.__targetQboController.events,processes:globalThis.__targetQboController.electron.app.getAppMetrics().map(p=>({pid:p.pid,type:p.type,cpu:p.cpu.percentCPUUsage,workingSetKiB:p.memory.workingSetSize,peakWorkingSetKiB:p.memory.peakWorkingSetSize})),contents:globalThis.__targetQboController.electron.webContents.getAllWebContents().filter(w=>!w.isDestroyed()).map(w=>({id:w.id,pid:w.getOSProcessId(),type:w.getType(),path:(()=>{try{const u=new URL(w.getURL());return u.protocol==='https:'?u.origin+u.pathname:u.protocol;}catch{return 'other';}})(),owner:w.getOwnerBrowserWindow()?.id??null})),windows:globalThis.__targetQboController.electron.BrowserWindow.getAllWindows().map(w=>({id:w.id,content:w.webContents.id,views:w.getBrowserViews?.().map(v=>v.webContents.id)??[]}))})`);},
  async cookies(){return run(`(async()=>{const w=globalThis.__targetQboController.electron.webContents.getAllWebContents().find(w=>!w.isDestroyed()&&/^https:\\/\\/(?:c[0-9]+\\.)?qbo\\.intuit\\.com\\/app\\//.test(w.getURL()));if(!w)return [];return (await w.session.cookies.get({})).map(c=>({domain:c.domain,expires:c.expirationDate??-1}));})()`);},
  async newCDPSession(page){return {send:(method,params)=>page._qboNativeInput(method,params),detach:async()=>{}};},
  async quit(){await run(`(()=>{setTimeout(()=>globalThis.__targetQboController.electron.app.quit(),0);return true;})()`).catch(()=>{});connection.close();}
 };
 function makePage(item){
  const page={_url:item.url,_id:item.id,_qboDirectEvaluate:true,_frameIsMain:true,
   url(){return this._url;},context:()=>context,setDefaultTimeout(){},waitForTimeout:sleep,
   async evaluate(fn,arg){
    const code=`(${fn.toString()})(${JSON.stringify(arg)??'undefined'})`;
    // Read the current main document through the same owned debugger as input;
    // an unresolved Electron frame IPC promise must not stall rendered controls.
    return run(`(async()=>{const w=${wcExpr(item.id)};if(!w.debugger.isAttached())w.debugger.attach('1.3');const r=await w.debugger.sendCommand('Runtime.evaluate',{expression:${JSON.stringify(code)},returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error('NATIVE_PAGE_EVALUATION_FAILED');return r.result?.value;})()`);
   },
   async _qboNativeInput(method,params={}){
    if(method.startsWith('Input.')&&!this._frameIsMain)throw Error('NATIVE_FRAME_INPUT_UNVERIFIED');
    // Only the private app's document gets input; never Windows input/focus APIs.
    if(!['Input.dispatchMouseEvent','Input.insertText','Input.dispatchKeyEvent','Emulation.setFocusEmulationEnabled','Page.navigate','Runtime.getHeapUsage'].includes(method))throw Error('NATIVE_COMMAND_NOT_ALLOWED');
    return run(`(async()=>{const w=${wcExpr(item.id)};if(!w.debugger.isAttached())w.debugger.attach('1.3');return w.debugger.sendCommand(${JSON.stringify(method)},${JSON.stringify(params)});})()`);
   },
   async goto(url){if(!allowed(url))throw Error('QBO_URL_REQUIRED');await run(`(()=>{const w=${wcExpr(item.id)};w.loadURL(${JSON.stringify(url)}).catch(()=>{});return true;})()`);const deadline=Date.now()+25000;do{await context.refresh();if(!pages.has(item.id))throw Error('NATIVE_DOCUMENT_REPLACED');if(new URL(this._url).pathname===new URL(url).pathname&&await this.evaluate(()=>document.readyState!=='loading').catch(()=>false))return;await sleep(200);}while(Date.now()<deadline);throw Error('NATIVE_NAVIGATION_TIMEOUT');},
   _qboProtocolDiagnostic:()=>({transport:'electron-main',webContentsId:item.id,clickPhase:page._qboClickPhase??null}),
  };return page;
 }
 try{await context.refresh();}catch(error){error.nativeStage='enumerate';throw error;}return context;
}
