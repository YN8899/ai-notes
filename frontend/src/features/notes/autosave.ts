export type SaveOutcome = 'saved' | 'dirty' | 'failed' | 'conflict'
export interface AutosaveTimers {
  set: (callback: () => void, delay: number) => unknown
  clear: (handle: unknown) => void
}
const timers: AutosaveTimers = {
  set: (callback, delay) => setTimeout(callback, delay),
  clear: handle => clearTimeout(handle as ReturnType<typeof setTimeout>),
}

export function createAutosaveQueue(
  save: (isCurrent: () => boolean) => Promise<SaveOutcome>,
  clock: AutosaveTimers = timers,
  delay = 800,
) {
  let active = true, generation = 0, running = false, blocked = false
  let timer: unknown
  function cancelTimer() {
    if (timer !== undefined) clock.clear(timer)
    timer = undefined
  }
  function schedule() {
    if (!active || blocked) return
    cancelTimer()
    timer = clock.set(() => { timer = undefined; void saveNow() }, delay)
  }
  async function saveNow() {
    if (!active || blocked || running) return
    cancelTimer()
    running = true
    const requestGeneration = generation
    const isCurrent = () => active && generation === requestGeneration
    let outcome: SaveOutcome = 'failed'
    try {
      outcome = await save(isCurrent)
    } finally {
      if (isCurrent()) {
        running = false
        cancelTimer()
        blocked = outcome === 'conflict'
        // A timer can expire while the PATCH is still running. Its latest draft
        // must be scheduled explicitly when that request releases the queue.
        if (outcome === 'dirty') schedule()
      }
    }
  }
  return {
    schedule, saveNow,
    reset() { blocked = false; cancelTimer() },
    activate() { active = true },
    dispose() { active = false; generation++; running = false; cancelTimer() },
  }
}
