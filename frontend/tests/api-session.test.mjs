import assert from 'node:assert/strict'
import { beforeEach, test } from 'node:test'
import { api, ApiError, changePassword, downloadBackup, getToken, setToken } from '../src/api.ts'

const storage = new Map()
Object.defineProperty(globalThis, 'localStorage', {
  value: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  },
})

beforeEach(() => setToken('old-session'))

const unauthorized = () => Response.json({ detail: 'Unauthorized' }, { status: 401 })
const newToken = () => Response.json({ access_token: 'new-session' })

test('a late 401 from an old token cannot sign out the current session', async (t) => {
  const response = Promise.withResolvers()
  t.mock.method(globalThis, 'fetch', () => response.promise)

  const request = api('/api/nodes')
  setToken('new-session')
  response.resolve(unauthorized())

  await assert.rejects(request, { name: 'AbortError' })
})

test('a backup download cannot finish after signing out', async (t) => {
  const response = Promise.withResolvers()
  t.mock.method(globalThis, 'fetch', () => response.promise)

  const request = downloadBackup('backup.tar')
  setToken(null)
  response.resolve(new Response(new Blob(['backup contents'])))

  await assert.rejects(request, { name: 'AbortError' })
})

test('a 401 received during password rotation waits for the new token', async (t) => {
  const response = Promise.withResolvers()
  t.mock.method(globalThis, 'fetch', async (path) =>
    path === '/api/auth/password' ? response.promise : unauthorized(),
  )

  const change = changePassword('old-password', 'new-password')
  const request = assert.rejects(api('/api/nodes'), { name: 'AbortError' })
  await new Promise((resolve) => setImmediate(resolve))
  response.resolve(newToken())

  await change
  await request
  assert.equal(getToken(), 'new-session')
})

test('a current 401 still propagates if password rotation fails', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => unauthorized())

  const change = changePassword('old-password', 'new-password')
  const isUnauthorized = (err) => err instanceof ApiError && err.status === 401
  await Promise.all([
    assert.rejects(change, isUnauthorized),
    assert.rejects(api('/api/nodes'), isUnauthorized),
  ])
  assert.equal(getToken(), 'old-session')
})

test('a late password response cannot restore a signed-out session', async (t) => {
  const response = Promise.withResolvers()
  t.mock.method(globalThis, 'fetch', () => response.promise)

  const change = changePassword('old-password', 'new-password')
  setToken(null)
  response.resolve(newToken())

  await assert.rejects(change, { name: 'AbortError' })
  assert.equal(getToken(), null)
})
