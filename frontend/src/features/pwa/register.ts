type InstallPrompt=Event&{prompt:()=>Promise<void>;userChoice:Promise<{outcome:string}>}
export type PwaState={registration:ServiceWorkerRegistration|null;waiting:boolean;installed:boolean;installAvailable:boolean;activated:boolean;error:string}
const state:PwaState={registration:null,waiting:false,installed:false,installAvailable:false,activated:false,error:''}
const listeners=new Set<()=>void>()
let prompt:InstallPrompt|null=null,started=false
const changed=()=>listeners.forEach(listener=>listener())
export const pwaSnapshot=()=>state
export function subscribePwa(listener:()=>void){listeners.add(listener);return()=>{listeners.delete(listener)}}
export async function installApp(){if(!prompt)return;await prompt.prompt();await prompt.userChoice;prompt=null;state.installAvailable=false;changed()}
export function activateUpdate(){state.registration?.waiting?.postMessage({type:'ACTIVATE_UPDATE'})}
export function registerPwa(){
  if(started)return
  started=true
  state.installed=window.matchMedia('(display-mode: standalone)').matches
  window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();prompt=event as InstallPrompt;state.installAvailable=true;changed()})
  window.addEventListener('appinstalled',()=>{prompt=null;state.installed=true;state.installAvailable=false;changed()})
  if(!import.meta.env.PROD||!('serviceWorker' in navigator)||!window.isSecureContext)return
  const hadController=Boolean(navigator.serviceWorker.controller)
  navigator.serviceWorker.addEventListener('controllerchange',()=>{state.activated=hadController||state.waiting;state.waiting=false;changed()})
  void navigator.serviceWorker.register('/sw.js',{scope:'/',updateViaCache:'none'}).then(registration=>{
    state.registration=registration
    const update=()=>{state.waiting=Boolean(registration.waiting);changed()}
    update()
    registration.addEventListener('updatefound',()=>registration.installing?.addEventListener('statechange',update))
    // Detect an update when a long-lived standalone window regains focus.
    window.addEventListener('focus',()=>{void registration.update().catch(()=>{})})
  }).catch(()=>{state.error='离线应用壳暂未就绪，请联网刷新后重试。';changed()})
}
