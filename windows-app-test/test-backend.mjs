import {chromium} from 'playwright-core';
import {openNativeApp} from './native-app.mjs';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
if(process.env.GITHUB_ACTIONS!=='true')throw Error('CI_ONLY');
const root=dirname(fileURLToPath(import.meta.url));
for(let i=0;i<2;i++){
 const app=await openNativeApp({root,chromium,executable:join(process.env.LOCALAPPDATA,'QuickBooksAdvanced','app-3.10.4','QuickBooks Online.exe')});
 try{
  const deadline=Date.now()+60000;let ready=false;
  while(Date.now()<deadline){
   for(const page of app.context.pages()){
    if(page.url().startsWith('https://accounts.intuit.com/app/sign-in')){
     const input=page.locator('input').first();
     if(await input.isVisible().catch(()=>false)){ready=true;break;}
    }
   }
   if(ready)break;
   await new Promise(r=>setTimeout(r,500));
  }
  if(!ready)throw Error('SIGNIN_CONTROLS_NOT_READY');
  console.log('NATIVE_BACKEND_SIGNIN_CONTROLS_PASS cycle='+i);
  for(const [name,fn] of [
   ['binding',()=>app.context.exposeBinding('__qboRememberLogin',()=>{})],
   ['init-script',()=>app.context.addInitScript(()=>{})],
   ['storage-state',()=>app.context.storageState()]
  ]){
   try { await fn();console.log('CAPTURE_STEP_PASS '+name); }
   catch(e){ console.log('CAPTURE_STEP_FAIL '+name+' '+String(e.message).replace(/https?:\\/\\/\\S+/g,'[URL]').slice(0,600)); throw Error('CAPTURE_REPRO_'+name); }
  }
 }finally{await app.close();}
}
