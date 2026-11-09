import { describe, it, expect, vi } from 'vitest'
import { shouldCoalesce, createCoalescer, COALESCE_PATHNAMES } from './requestCoalescing'

describe('shouldCoalesce', () => {
  it('matches GET requests to the known bootstrap endpoints', () => {
    for (const pathname of COALESCE_PATHNAMES) {
      expect(shouldCoalesce('GET', pathname)).toBe(true)
    }
  })

  it('never matches a mutating method, even for an otherwise-matching path', () => {
    expect(shouldCoalesce('POST', '/api/cards')).toBe(false)
    expect(shouldCoalesce('PUT', '/api/habits')).toBe(false)
    expect(shouldCoalesce('DELETE', '/api/tags')).toBe(false)
  })

  it('does not match an unrelated path', () => {
    expect(shouldCoalesce('GET', '/api/bridge/jobs')).toBe(false)
    expect(shouldCoalesce('GET', '/manifest.json')).toBe(false)
  })
})

function fakeResponse(body) {
  // A minimal stand-in for the Fetch API's Response, just enough for .clone() to work the
  // way createCoalescer uses it -- cloning returns an independent object carrying the same
  // body, same as the real Response.clone() contract.
  return { body, clone() { return { body: this.body } } }
}

describe('createCoalescer', () => {
  it('only calls fetchFn once for two requests sharing the same key while the first is pending', async () => {
    let resolveFetch
    const fetchFn = vi.fn(() => new Promise((resolve) => { resolveFetch = resolve }))
    const coalesce = createCoalescer(fetchFn)

    const p1 = coalesce('req1', '/api/cards')
    const p2 = coalesce('req2', '/api/cards')
    expect(fetchFn).toHaveBeenCalledTimes(1)

    resolveFetch(fakeResponse('cards-data'))
    const [r1, r2] = await Promise.all([p1, p2])
    expect(r1.body).toBe('cards-data')
    expect(r2.body).toBe('cards-data')
  })

  it('calls fetchFn again for a new request once the in-flight one has resolved', async () => {
    const fetchFn = vi.fn()
      .mockResolvedValueOnce(fakeResponse('first'))
      .mockResolvedValueOnce(fakeResponse('second'))
    const coalesce = createCoalescer(fetchFn)

    const r1 = await coalesce('req1', '/api/habits')
    const r2 = await coalesce('req2', '/api/habits')

    expect(fetchFn).toHaveBeenCalledTimes(2)
    expect(r1.body).toBe('first')
    expect(r2.body).toBe('second')
  })

  it('keeps different keys fully independent, even while both are pending', async () => {
    const fetchFn = vi.fn()
      .mockResolvedValueOnce(fakeResponse('cards'))
      .mockResolvedValueOnce(fakeResponse('habits'))
    const coalesce = createCoalescer(fetchFn)

    const [cards, habits] = await Promise.all([
      coalesce('req-cards', '/api/cards'),
      coalesce('req-habits', '/api/habits'),
    ])

    expect(fetchFn).toHaveBeenCalledTimes(2)
    expect(cards.body).toBe('cards')
    expect(habits.body).toBe('habits')
  })

  it('does not leave a failed request stuck in-flight -- a later call retries', async () => {
    const fetchFn = vi.fn()
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce(fakeResponse('recovered'))
    const coalesce = createCoalescer(fetchFn)

    await expect(coalesce('req1', '/api/tags')).rejects.toThrow('network down')
    const result = await coalesce('req2', '/api/tags')

    expect(fetchFn).toHaveBeenCalledTimes(2)
    expect(result.body).toBe('recovered')
  })
})
