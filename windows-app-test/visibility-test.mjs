// Disposable Windows CI. No credentials, session or company data.
import {openNativeApp} from './native-app.mjs';
import {stageInstaller,officialInstaller} from './probe.mjs';
import {spawn} from 'node:child_process';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
if(process.env.GITHUB_ACTIONS!=='true'||process.env.RUNNER_OS!=='Windows')throw Error('CI_ONLY');
const root=dirname(fileURLToPath(import.meta.url));
const child=async(command,args)=>{
 const p=spawn(command,args,{windowsHide:true,stdio:'inherit'});
 const code=await new Promise((resolve,reject)=>{p.once('error',reject);p.once('exit',resolve);});
 if(code!==0)throw Error('CI_CHILD_FAILED');
};
if(process.argv[2]!=='--run'){
 if(process.env.QBO_CI_REUSE_INSTALL!=='1'){
  await stageInstaller(root);
  await child('powershell.exe',['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',join(root,'Hidden-Install.ps1'),'-Executable',join(root,'downloads',officialInstaller.filename)]);
 }
 await child('powershell.exe',['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',join(root,'visibility-test.ps1'),'-Node',process.execPath]);
}else{
 const executable=join(process.env.LOCALAPPDATA,'QuickBooksAdvanced','app-3.10.4','QuickBooks Online.exe');
 for(let cycle=1;cycle<=1;cycle++){
  let app;
  try{
   app=await openNativeApp({root,executable});
   await new Promise(resolve=>setTimeout(resolve,20000));
   const facts=await app.context.qboNativeDiagnostics();
   console.log(JSON.stringify({cycle,windows: facts.windows.length,contents:facts.contents.map(c=>({id:c.id,type:c.type})),resources:app.context.qboNativeMetrics()}));
   if(facts.windows.length===0)throw Error('NO_APP_WINDOWS_OBSERVED');
   for(const mode of ['show','popup','external']){
    const exercise=await app.context.qboVisibilityExercise(mode);
    console.log(JSON.stringify({exercise}));
    if(process.env.QBO_CI_HIDDEN_POLICY==='1'&&(exercise.visible||mode==='external'&&!exercise.blocked))throw Error('HIDDEN_POLICY_ASSERTION_FAILED');
    await new Promise(resolve=>setTimeout(resolve,5000));
   }
  }finally{if(app)await app.close();}
  console.log(JSON.stringify({cycle,closed:true}));
 }
}
