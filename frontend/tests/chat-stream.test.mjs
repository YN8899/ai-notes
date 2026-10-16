import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readStream } from '../src/features/ai/chat-stream.ts'

test('SSE parsing preserves split UTF-8, split CRLF, comments and multiple events',async()=>{
  const original=globalThis.fetch, requests=[]
  const bytes=new TextEncoder().encode(': heartbeat\r\n\r\nevent: meta\r\ndata: {"answer":"首🙂"}\r\n\r\nevent: delta\r\ndata: {"offset":2,"text":"尾"}\r\n\r\nevent: done\r\ndata: {"status":"succeeded"}\r\n\r\n')
  globalThis.fetch=async(url,options)=>{
    requests.push({url,options})
    return new Response(new ReadableStream({start(controller){for(const byte of bytes)controller.enqueue(new Uint8Array([byte]));controller.close()}}))
  }
  try{
    const received=[]
    await readStream('existing-run',new AbortController().signal,(name,data)=>received.push({name,data}))
    assert.deepEqual(received,[{name:'meta',data:{answer:'首🙂'}},{name:'delta',data:{offset:2,text:'尾'}},{name:'done',data:{status:'succeeded'}}])
    assert.equal(requests.length,1)
    assert.equal(requests[0].url,'/api/v1/ai/runs/existing-run/events')
    assert.equal(requests[0].options.method,undefined) // Read-only GET; no generation on reconnect.
  }finally{globalThis.fetch=original}
})

test('unexpected EOF is explicit and does not silently retry or lose delivered text',async()=>{
  const original=globalThis.fetch, received=[];let calls=0
  globalThis.fetch=async()=>{calls++;return new Response('event: delta\ndata: {"offset":0,"text":"已保存"}\n\n')}
  try{
    await assert.rejects(()=>readStream('run',new AbortController().signal,(name,data)=>received.push(data)),/连接已断开/)
    assert.equal(calls,1);assert.equal(received[0].text,'已保存')
  }finally{globalThis.fetch=original}
})
