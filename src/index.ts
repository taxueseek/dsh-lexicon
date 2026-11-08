// dsh-lexicon — a DeepSeek Harness plugin bridging ime_lexicon.py (Doubao /
// WeType input-method word libraries) into model tools. The Python script
// stays the single source of truth for file-layer operations; this plugin
// only shells out with validated args and bounded timeouts.

import { spawn } from 'node:child_process'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import z from '@deepseek-ai/schemastery'
import { defineLexiconTools } from './tools.ts'

/** Cordis plugin name — must match the row id in cordis.patch.yml. */
export const name = 'dsh-lexicon'

/** Services required by this plugin. */
export const inject = ['tools', 'systemPrompt']

/** Bundled ime_lexicon.py engine shipped under vendor/ime-lexicon (lib/index.js → ../vendor). */
export const BUNDLED_SCRIPT = join(dirname(fileURLToPath(import.meta.url)), '..', 'vendor', 'ime-lexicon', 'ime_lexicon.py')

export interface LexiconConfig {
  /** Path to ime_lexicon.py. '~' is expanded. Defaults to the bundled copy. */
  scriptPath: string
  /** Default command timeout in ms (detect/export/backup/import/build/optimize). */
  scriptTimeoutMs: number
  /** Longer timeout for search --run (argo web search). */
  searchTimeoutMs: number
}

export const Config = z.object({
  scriptPath: z.string().default(BUNDLED_SCRIPT),
  scriptTimeoutMs: z.number().default(120_000),
  searchTimeoutMs: z.number().default(300_000)
})

export type RunResult = { code: number; stdout: string; stderr: string }

export function runScript(
  scriptPath: string,
  args: string[],
  timeoutMs: number,
  cwd = process.cwd()
): Promise<RunResult> {
  return new Promise((resolve) => {
    const child = spawn('python3', [scriptPath, ...args], { cwd })
    let stdout = ''
    let stderr = ''
    const timer = setTimeout(() => {
      child.kill('SIGKILL')
      resolve({ code: -1, stdout, stderr: `ime_lexicon.py timed out after ${timeoutMs}ms (SIGKILL)` })
    }, timeoutMs)
    child.stdout.on('data', (d: Buffer) => (stdout += d.toString('utf8')))
    child.stderr.on('data', (d: Buffer) => (stderr += d.toString('utf8')))
    child.on('error', (err) => {
      clearTimeout(timer)
      resolve({ code: -1, stdout: '', stderr: String(err.message) })
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      resolve({ code: code ?? -1, stdout, stderr })
    })
  })
}

export function expandHome(p: string): string {
  return p === '~' ? homedir() : p.startsWith('~/') ? join(homedir(), p.slice(2)) : p
}

export function apply(ctx: any, config: LexiconConfig): void {
  const script = expandHome(config.scriptPath)

  ctx.systemPrompt.section({
    name: 'tool:ime-lexicon',
    order: 110,
    text:
      'The dsh-lexicon plugin manages Doubao / WeType (微信输入法) personal word libraries through the ime_lexicon.py CLI. ' +
      'Use ime_detect to check IME state, ime_export/ime_backup for safe snapshots, ime_search/ime_build/ime_optimize to craft industry word lists, ' +
      'and ime_import to stage them. NEVER apply staged changes to a live IME without the user explicitly confirming — apply is a manual step outside this plugin.'
  })

  const exec = (args: string[], timeoutMs: number) => runScript(script, args, timeoutMs)

  for (const tool of defineLexiconTools(exec, {
    scriptTimeoutMs: config.scriptTimeoutMs,
    searchTimeoutMs: config.searchTimeoutMs
  })) {
    ctx.tools.register(tool)
  }
}
