// Installed only inside the owned automation process before vendor JavaScript.
export function installInvisiblePolicy(electron){
 if(globalThis.__qboInvisiblePolicy)return globalThis.__qboInvisiblePolicy;
 const facts={active:true,blocked:0};
 const quiet=()=>{facts.blocked++;};
 const deny=()=>{facts.blocked++;return Promise.reject(Error('NATIVE_EXTERNAL_UI_BLOCKED'));};
 for(const name of ['BrowserWindow','BaseWindow']){
  const original=electron[name];if(!original)continue;
  const descriptor=Object.getOwnPropertyDescriptor(electron,name);
  if(!descriptor?.configurable)throw Error('NATIVE_HIDDEN_CONSTRUCTOR_UNAVAILABLE');
  for(const method of ['show','showInactive','focus','restore','minimize','maximize','moveTop','setAlwaysOnTop','setVisibleOnAllWorkspaces']){
   if(typeof original.prototype[method]==='function')Object.defineProperty(original.prototype,method,{value:quiet,configurable:true,writable:true});
  }
  if(typeof original.prototype.setSkipTaskbar==='function'){
   const set=original.prototype.setSkipTaskbar;
   Object.defineProperty(original.prototype,'setSkipTaskbar',{value:function(){return set.call(this,true);},configurable:true,writable:true});
  }
  const hidden=new Proxy(original,{construct(target,args,newTarget){return Reflect.construct(target,[{...(args[0]??{}),show:false,skipTaskbar:true},...args.slice(1)],newTarget);}});
  Object.defineProperty(electron,name,{get:()=>hidden,configurable:true});
 }
 for(const method of ['openExternal','openPath'])if(typeof electron.shell?.[method]==='function')electron.shell[method]=deny;
 if(typeof electron.shell?.showItemInFolder==='function')electron.shell.showItemInFolder=quiet;
 if(typeof electron.app?.relaunch==='function')electron.app.relaunch=quiet;
 if(typeof electron.Notification?.prototype.show==='function')electron.Notification.prototype.show=quiet;
 // New renderer windows must inherit hidden options, including vendor handlers.
 const guardContents=wc=>{
  const set=wc.setWindowOpenHandler;
  if(typeof set!=='function')return;
  wc.setWindowOpenHandler=function(handler){return set.call(this,details=>{
   const result=handler(details);
   if(result?.action!=='allow')return result;
   return {...result,overrideBrowserWindowOptions:{...result.overrideBrowserWindowOptions,show:false,skipTaskbar:true}};
  });};
  wc.setWindowOpenHandler(()=>({action:'deny'}));
 };
 electron.app.on('web-contents-created',(_,wc)=>guardContents(wc));
 for(const wc of electron.webContents.getAllWebContents())guardContents(wc);
 globalThis.__qboInvisiblePolicy=facts;return facts;
}
