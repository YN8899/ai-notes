/* Built with the exact production bundle. No user data or API caching. */
const VERSION=__SHELL_VERSION__
const ASSETS=__SHELL_ASSETS__
const PREFIX='ai-notes-shell-'
const CACHE=PREFIX+VERSION
const STATIC=new Set(ASSETS)
self.addEventListener('install',event=>event.waitUntil((async()=>{
  const cache=await caches.open(CACHE)
  await cache.addAll(ASSETS)
  // An update waits for an explicit user action; never reload an editor.
})()))
self.addEventListener('message',event=>{
  if(event.data?.type==='ACTIVATE_UPDATE')void self.skipWaiting()
})
self.addEventListener('activate',event=>event.waitUntil((async()=>{
  // Keep the previous shell available for open windows loading old lazy chunks.
  const keys=(await caches.keys()).filter(key=>key.startsWith(PREFIX))
  for(const key of keys.slice(0,-2))await caches.delete(key)
  await self.clients.claim()
})()))
self.addEventListener('fetch',event=>{
  const request=event.request,url=new URL(request.url)
  if(request.method!=='GET'||url.origin!==self.location.origin||url.pathname.startsWith('/api/')||url.search)return
  if(request.mode==='navigate'&&(url.pathname==='/'||url.pathname==='/index.html')){
    event.respondWith((async()=>{
      try{return await fetch(request,{cache:'no-store'})}catch{return (await caches.open(CACHE)).match('/index.html')}
    })())
  }else if(STATIC.has(url.pathname)){
    event.respondWith((async()=>{return (await caches.open(CACHE)).match(url.pathname)||fetch(request)})())
  }else if(/^\/assets\/[A-Za-z0-9_./-]+-[A-Za-z0-9_-]+\.(?:js|css|woff2?|ttf)$/.test(url.pathname)){
    // Read only previously pre-cached static files; never populate from requests.
    event.respondWith((async()=>{return (await caches.match(url.pathname))||fetch(request)})())
  }
})
self.addEventListener('push',event=>{
  // Ignore payload: upstream data cannot put private text or URLs on lockscreen.
  event.waitUntil(self.registration.showNotification('知页',{body:'知页有新的站内提醒',tag:'notes-notification',icon:'/pwa-192.png',badge:'/pwa-192.png',data:{}}))
})
self.addEventListener('notificationclick',event=>{
  event.notification.close()
  event.waitUntil(self.clients.openWindow(new URL('/',self.location.origin).href))
})
