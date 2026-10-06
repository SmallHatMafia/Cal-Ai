// Explicit setup only. The updater must never invoke this automatically.
import {stageInstaller,officialInstaller} from './probe.mjs';
import {openNativeApp} from './native-app.mjs';
import {chromium} from 'playwright-core';
import {readFile,writeFile,rename} from 'node:fs/promises';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
if(process.platform!=='win32'||process.argv[2]!=='--explicit-setup')throw Error('EXPLICIT_WINDOWS_SETUP_REQUIRED');
const root=dirname(fileURLToPath(import.meta.url));
await stageInstaller(root);
await new Promise((resolve,reject)=>{
 const child=spawn(join(process.env.SystemRoot,'System32','WindowsPowerShell','v1.0','powershell.exe'),['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',join(root,'Install-Native-App.ps1'),'-Executable',join(root,'downloads',officialInstaller.filename)],{windowsHide:true,stdio:'ignore',timeout:160000});
 child.once('error',reject);child.once('exit',code=>code===0?resolve():reject(Error('INSTALL_NOT_VERIFIED')));
});
const executable=join(process.env.LOCALAPPDATA,'QuickBooksAdvanced','app-3.10.4','QuickBooks Online.exe');
const app=await openNativeApp({root,chromium,executable});
try{
 const deadline=Date.now()+60000;let ready=false;
 while(Date.now()<deadline){
  ready=app.context.pages().some(p=>p.url().startsWith('https://accounts.intuit.com/app/sign-in')||/^https:\/\/(c[0-9]+\.)?qbo\.intuit\.com\/app\//.test(p.url()));
  if(ready)break;await new Promise(r=>setTimeout(r,500));
 }
 if(!ready)throw Error('APP_PAGE_NOT_READY');
}finally{await app.close();}
const path=join(root,'config.json');
const config=JSON.parse((await readFile(path,'utf8')).replace(/^\uFEFF/,''));
await writeFile(path+'.native-next',JSON.stringify({...config,browserMode:'native_app',browserExecutable:executable},null,2));
await rename(path+'.native-next',path);
await writeFile(join(root,'native-setup-result.json'),JSON.stringify({at:Date.now(),installed:true,backgroundStartupVerified:true,authenticated:false}));
