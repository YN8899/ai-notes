import {createHash} from 'node:crypto'
import {readFileSync} from 'node:fs'

/** Production-only full static shell, including lazy chunks and math fonts. */
export function pwaBuild(){return {name:'private-pwa-shell',apply:'build',enforce:'post',generateBundle(_options,bundle){
  const assets=['/index.html','/manifest.webmanifest','/favicon.svg','/pwa-icon.svg','/pwa-192.png','/pwa-512.png',...Object.keys(bundle).filter(path=>path.startsWith('assets/')).map(path=>'/'+path)]
  const hash=createHash('sha256')
  for(const [path,file] of Object.entries(bundle)){hash.update(path);hash.update(file.type==='asset'?file.source:file.code)}
  for(const path of assets.filter(path=>!path.startsWith('/assets/')&&path!=='/index.html'))hash.update(readFileSync(new URL('./public'+path,import.meta.url)))
  const version=hash.digest('hex').slice(0,20)
  const template=readFileSync(new URL('./src/features/pwa/sw-template.js',import.meta.url),'utf8')
  this.emitFile({type:'asset',fileName:'sw.js',source:template.replace('__SHELL_VERSION__',JSON.stringify(version)).replace('__SHELL_ASSETS__',JSON.stringify([...new Set(assets)]))})
}}}
