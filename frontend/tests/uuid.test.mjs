import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import { test } from 'node:test'
import { createUuid } from '../src/uuid.ts'

function useCrypto(t,source) {
  const original=Object.getOwnPropertyDescriptor(globalThis,'crypto')
  Object.defineProperty(globalThis,'crypto',{configurable:true,value:source})
  t.after(()=>{if(original)Object.defineProperty(globalThis,'crypto',original);else Reflect.deleteProperty(globalThis,'crypto')})
}

test('uses native randomUUID with its Crypto receiver when available',t=>{
  const expected='ad3a1e9f-9847-4c31-8677-c39ec134f152'
  const source={randomUUID(){assert.equal(this,source);return expected},getRandomValues(){assert.fail('Native UUID must be preferred')}}
  useCrypto(t,source)
  assert.equal(createUuid(),expected)
})

for(const [byte,expected] of [[0,'00000000-0000-4000-8000-000000000000'],[255,'ffffffff-ffff-4fff-bfff-ffffffffffff']]) {
  test(`HTTP fallback sets the UUID version and variant for random byte ${byte}`,t=>{
    const source={getRandomValues(bytes){assert.equal(this,source);assert.ok(bytes instanceof Uint8Array);assert.equal(bytes.length,16);return bytes.fill(byte)}}
    useCrypto(t,source)
    assert.equal(createUuid(),expected)
  })
}

test('HTTP fallback produces distinct UUID v4 request keys without randomUUID',t=>{
  useCrypto(t,{getRandomValues:bytes=>webcrypto.getRandomValues(bytes)})
  const keys=Array.from({length:100},()=>createUuid())
  assert.equal(new Set(keys).size,100)
  for(const key of keys)assert.match(key,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
})

test('reports missing secure randomness instead of using Math.random',t=>{
  useCrypto(t,undefined)
  assert.throws(()=>createUuid(),/不支持安全随机数/)
})
