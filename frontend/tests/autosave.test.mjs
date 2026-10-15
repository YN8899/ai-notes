import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createAutosaveQueue } from '../src/features/notes/autosave.ts'

function fakeClock() {
  let now = 0, nextId = 0
  const tasks = new Map()
  return {
    set(callback, delay) { const id = ++nextId; tasks.set(id, { callback, at: now + delay }); return id },
    clear(id) { tasks.delete(id) },
    tick(ms) {
      const until = now + ms
      while (true) {
        const entry = [...tasks].filter(([, task]) => task.at <= until).sort((a, b) => a[1].at - b[1].at)[0]
        if (!entry) break
        now = entry[1].at; tasks.delete(entry[0]); entry[1].callback()
      }
      now = until
    },
    get pending() { return tasks.size },
  }
}
function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() }

function editor() {
  const clock = fakeClock(), calls = [], cacheWrites = [], draftWrites = []
  let value = 'initial', revision = 7, sequence = 0, status = 'saved', draft
  const queue = createAutosaveQueue(async isCurrent => {
    const submittedSequence = sequence, request = deferred()
    status = 'saving'
    calls.push({ value, revision, request })
    try {
      const result = await request.promise
      if (!isCurrent()) return 'saved'
      cacheWrites.push(result)
      revision = result.revision
      if (sequence === submittedSequence) { draft = undefined; status = 'saved' }
      else { draft = { value, revision }; draftWrites.push(draft); status = 'dirty' }
      return status
    } catch (error) {
      if (!isCurrent()) return 'saved'
      status = error.status === 409 ? 'conflict' : 'failed'
      return status
    }
  }, clock)
  return {
    clock, calls, cacheWrites, draftWrites, queue,
    edit(next) { value = next; sequence++; status = 'dirty'; draft = { value, revision }; queue.schedule() },
    get status() { return status }, get draft() { return draft },
    clearDraft() { draft = undefined },
  }
}

test('an edit whose debounce expires during a slow save is queued with the new revision', async () => {
  const e = editor()
  e.edit('first'); e.clock.tick(800)
  e.edit('latest'); e.clock.tick(800)
  assert.equal(e.calls.length, 1, 'PATCH requests must stay serialized')
  e.calls[0].request.resolve({ revision: 8 }); await flush()
  assert.deepEqual(e.draft, { value: 'latest', revision: 8 })
  e.clock.tick(799); assert.equal(e.calls.length, 1)
  e.clock.tick(1)
  assert.equal(e.calls.length, 2, 'latest draft must save without another edit or manual click')
  assert.equal(e.calls[1].value, 'latest')
  assert.equal(e.calls[1].revision, 8)
  e.calls[1].request.resolve({ revision: 9 }); await flush()
  assert.equal(e.status, 'saved'); assert.equal(e.draft, undefined)
  assert.equal(e.clock.pending, 0)
})
