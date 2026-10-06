// Staged backend. Not enabled or distributed by the current release.
import {spawn} from 'node:child_process';
import {createServer} from 'node:net';
import {join} from 'node:path';
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
export async function openNativeApp({root,chromium,executable}) {
  if(process.platform!=='win32')throw Error('WINDOWS_REQUIRED');
  const port=await new Promise((resolve,reject)=>{const s=createServer();s.on('error',reject);s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});
  const child=spawn(join(process.env.SystemRoot,'System32','WindowsPowerShell','v1.0','powershell.exe'),['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',join(root,'Native-App-Host.ps1'),'-Executable',executable,'-Port',String(port)],{windowsHide:true,stdio:['pipe','pipe','pipe']});
  let browser,closing=false;
  child.stdin.on('error',()=>{});
  const exit=new Promise(resolve=>{child.once('exit',resolve);child.once('error',resolve);});
  async function close(){
    if(closing)return;closing=true;
    // Let Electron persist its own native authentication state before cleanup.
    if(browser){
      const session=await browser.newBrowserCDPSession().catch(()=>null);
      if(session)await Promise.race([session.send('Browser.close').catch(()=>{}),sleep(5000)]);
      await Promise.race([exit,sleep(5000)]);
      await browser.close().catch(()=>{});
    }
    child.stdin.end('CLOSE\n');
    await Promise.race([exit,sleep(3000)]);
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
    const deadline=Date.now()+30000;
    while(Date.now()<deadline){
      if(child.exitCode!==null)throw Error('APP_EXITED');
      try{browser=await chromium.connectOverCDP(`http://127.0.0.1:${port}`,{timeout:2000});break;}catch{await sleep(300);}
    }
    if(!browser)throw Error('APP_CONNECTION_TIMEOUT');
    const context=browser.contexts()[0];if(!context)throw Error('APP_CONTEXT_MISSING');
    return {context,close,browserMode:'native_app_private_desktop'};
  }catch(error){await close();throw error;}
}
