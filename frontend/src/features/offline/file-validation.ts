import type {PDFDocumentLoadingTask} from 'pdfjs-dist'
const MAX_BYTES=50*1024*1024
export const MAX_PIXELS=40_000_000
function dimensions(width:number,height:number){
  if(!Number.isInteger(width)||!Number.isInteger(height)||width<=0||height<=0||width>32768||height>32768||width*height>MAX_PIXELS)throw new Error('图片尺寸无效或超过 4000 万像素限制')
}
export function imageDimensions(bytes:Uint8Array,mime:string){
  const d=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),ascii=(at:number,n:number)=>String.fromCharCode(...bytes.subarray(at,at+n))
  let width=0,height=0
  if(mime==='image/png'&&bytes.length>=33&&bytes[0]===137&&ascii(1,7)==='PNG\r\n\x1a\n'&&ascii(12,4)==='IHDR'&&d.getUint32(8)===13){width=d.getUint32(16);height=d.getUint32(20)}
  else if(mime==='image/jpeg'&&bytes[0]===255&&bytes[1]===216){
    let at=2
    while(at+4<=bytes.length){if(bytes[at++]!==255)break;while(bytes[at]===255)at++;const marker=bytes[at++];if(marker===217||marker===218)break;if(marker===1||marker>=208&&marker<=215)continue;const size=d.getUint16(at);if(size<2||at+size>bytes.length)break;if([192,193,194,195,197,198,199,201,202,203,205,206,207].includes(marker)&&size>=8){height=d.getUint16(at+3);width=d.getUint16(at+5);break}at+=size}
  }else if(mime==='image/webp'&&bytes.length>=30&&ascii(0,4)==='RIFF'&&ascii(8,4)==='WEBP'&&d.getUint32(4,true)+8===bytes.length){
    if(ascii(12,4)==='VP8X'){width=1+bytes[24]+(bytes[25]<<8)+(bytes[26]<<16);height=1+bytes[27]+(bytes[28]<<8)+(bytes[29]<<16)}
    else if(ascii(12,4)==='VP8 '&&bytes[23]===157&&bytes[24]===1&&bytes[25]===42){width=d.getUint16(26,true)&16383;height=d.getUint16(28,true)&16383}
    else if(ascii(12,4)==='VP8L'&&bytes[20]===47){const n=d.getUint32(21,true);width=(n&16383)+1;height=((n>>>14)&16383)+1}
  }
  dimensions(width,height);return {width,height}
}
export async function validateImage(file:Blob){
  if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size<=0||file.size>MAX_BYTES)throw new Error('请选择 50 MiB 以内的 PNG、JPEG 或 WebP 图片')
  const size=imageDimensions(new Uint8Array(await file.arrayBuffer()),file.type)
  if(typeof createImageBitmap!=='function')throw new Error('此浏览器不能安全解码图片，请使用支持 createImageBitmap 的浏览器')
  let expired=false,timer:ReturnType<typeof setTimeout>|undefined
  const decoding=createImageBitmap(file,{imageOrientation:'none'}).then(bitmap=>{if(expired){bitmap.close();throw new Error('图片校验超时')}return bitmap})
  try{
    const bitmap=await Promise.race([decoding,new Promise<never>((_,reject)=>{timer=setTimeout(()=>{expired=true;reject(new Error('图片校验超时，未保存任何页面'))},10000)})])
    try{dimensions(bitmap.width,bitmap.height);if(bitmap.width!==size.width||bitmap.height!==size.height)throw new Error('图片尺寸与文件头不一致')}finally{bitmap.close()}
  }catch(error){throw new Error('图片未通过真实解码校验：'+(error instanceof Error?error.message:'格式损坏'))}finally{clearTimeout(timer)}
}
export async function validatePdf(file:Blob){
  if(file.type!=='application/pdf'||file.size<=0||file.size>MAX_BYTES)throw new Error('请选择 50 MiB 以内的 PDF')
  const bytes=new Uint8Array(await file.arrayBuffer())
  if(!new TextDecoder('latin1').decode(bytes.subarray(0,8)).startsWith('%PDF-'))throw new Error('PDF 文件头无效')
  // PDF.js 6 removed dynamic code generation; do not use an older eval-capable build.
  const pdf=await import('pdfjs-dist/legacy/build/pdf.mjs')
  const port=typeof Worker==='undefined'?undefined:new Worker(new URL('pdfjs-dist/build/pdf.worker.min.mjs',import.meta.url),{type:'module'})
  const worker=port?pdf.PDFWorker.create({port}):undefined
  let task:PDFDocumentLoadingTask|undefined,timer:ReturnType<typeof setTimeout>|undefined
  try{
    task=pdf.getDocument({data:bytes,worker,enableXfa:false,useWorkerFetch:false,useWasm:false,disableFontFace:true,useSystemFonts:false,disableRange:true,disableStream:true,disableAutoFetch:true,stopAtErrors:true,maxImageSize:MAX_PIXELS,canvasMaxAreaInBytes:MAX_PIXELS*4,cMapUrl:undefined,standardFontDataUrl:undefined,wasmUrl:undefined})
    task.onPassword=()=>{void task?.destroy()}
    const parsing=(async()=>{
      const document=await task!.promise
      if(!Number.isInteger(document.numPages)||document.numPages<1||document.numPages>200)throw new Error('PDF 须为 1–200 页')
      for(let index=1;index<=document.numPages;index++){
        const page=await document.getPage(index),view=page.getViewport({scale:2})
        dimensions(Math.ceil(view.width),Math.ceil(view.height));page.cleanup()
      }
      return document.numPages
    })()
    return await Promise.race([parsing,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Error('PDF 解析超过 15 秒，未保存任何内容')),15000)})])
  }catch(error){throw new Error('PDF 未通过受限解析（不支持密码保护）：'+(error instanceof Error?error.message:'文件损坏'))}
  finally{clearTimeout(timer);port?.terminate();worker?.destroy();if(task)void task.destroy().catch(()=>{})}
}
