import { test } from 'node:test'
import assert from 'node:assert/strict'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { runScript, expandHome, BUNDLED_SCRIPT } from '../src/index.ts'

/** The engine copy this repo ships (same file the plugin defaults to). */
const VENDOR_SCRIPT = BUNDLED_SCRIPT
const VENDOR_EXISTS = join(dirname(fileURLToPath(import.meta.url)), '..', 'vendor', 'ime-lexicon', 'ime_lexicon.py')

test('expandHome expands ~ and ~/ paths', () => {
  const home = process.env.HOME ?? ''
  assert.equal(expandHome('~'), home)
  assert.equal(expandHome('~/x/y.py'), `${home}/x/y.py`)
  assert.equal(expandHome('/abs/path'), '/abs/path')
})

test('BUNDLED_SCRIPT points at the vendored engine', () => {
  assert.equal(VENDOR_SCRIPT, VENDOR_EXISTS)
})

test('runScript resolves the vendored ime_lexicon.py and its CLI', async () => {
  const r = await runScript(VENDOR_SCRIPT, ['--help'], 15_000)
  assert.equal(r.code, 0)
  assert.match(r.stdout, /detect/)
  assert.match(r.stdout, /export/)
})

test('runScript surfaces a non-zero exit with stderr', async () => {
  const r = await runScript(VENDOR_SCRIPT, ['no-such-subcommand'], 15_000)
  assert.notEqual(r.code, 0)
  assert.ok(r.stderr.length > 0 || r.stdout.length > 0)
})

test('runScript errors cleanly for a missing script', async () => {
  const r = await runScript('/nonexistent/ime_lexicon.py', ['detect'], 5_000)
  assert.notEqual(r.code, 0)
})
