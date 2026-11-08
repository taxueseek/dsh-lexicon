// The model-facing IME lexicon tools. Every tool shells out to ime_lexicon.py
// with validated arguments. The script's own discipline rules are preserved:
// imports only write a staging area, never a live IME; `apply` is deliberately
// not exposed as a tool.

import { defineTool } from '@deepseek-ai/dsh-tools'
import type { RunResult } from './index.ts'

export interface LexiconToolOptions {
  scriptTimeoutMs: number
  searchTimeoutMs: number
}

function renderJson(_args: unknown, value: unknown) {
  return [{ type: 'text' as const, text: JSON.stringify(value, null, 2) }]
}

/** Run the script and return { stdout, stderr } or throw with the tail of stderr. */
async function call(run: (args: string[], timeoutMs: number) => Promise<RunResult>, args: string[], timeoutMs: number): Promise<string> {
  const r = await run(args, timeoutMs)
  if (r.code !== 0) {
    const tail = (r.stderr.trim() || r.stdout.trim()).split('\n').slice(-8).join('\n')
    throw new Error(`ime_lexicon.py ${args[0]} failed (${r.code}): ${tail}`)
  }
  return r.stdout.trim()
}

function nonEmpty(value: unknown, label: string): string {
  if (typeof value !== 'string' || value.trim() === '') throw new Error(`${label} must be a non-empty string`)
  return value.trim()
}

function posInt(value: unknown, label: string, def: number, max = 1_000_000): number {
  if (value === undefined) return def
  const n = Math.trunc(Number(value))
  if (!Number.isInteger(n) || n < 1 || n > max) throw new Error(`${label} must be an integer in [1, ${max}]`)
  return n
}

export function defineLexiconTools(
  run: (args: string[], timeoutMs: number) => Promise<RunResult>,
  opts: LexiconToolOptions
) {
  return [
    defineTool({
      name: 'ime_detect',
      description: 'Detect the status of the Doubao and WeType (微信输入法) personal word libraries: which engines are running and how many slots are used.',
      parameters: {},
      output: { schema: { type: 'object', additionalProperties: true, properties: {} }, render: renderJson },
      isConcurrencySafe: () => true,
      async execute() {
        return { result: await call(run, ['detect'], opts.scriptTimeoutMs) }
      }
    }),

    defineTool({
      name: 'ime_export',
      description: 'Export IME word libraries to TSV files (intermediate table: word \\t pinyin \\t freq \\t source \\t syll \\t note). Nothing is modified — this is a read-only snapshot.',
      parameters: {
        engine: {
          type: 'string',
          enum: ['all', 'doubao', 'wechat', 'wetype'],
          description: 'Which engine(s) to export. Defaults to all.'
        }
      },
      output: { schema: { type: 'object', additionalProperties: true, properties: {} }, render: renderJson },
      isConcurrencySafe: () => true,
      async execute(args: { engine?: string }) {
        const argv = ['export']
        if (args.engine !== undefined && args.engine !== 'all') argv.push('--engine', args.engine)
        return { result: await call(run, argv, opts.scriptTimeoutMs) }
      }
    }),

    defineTool({
      name: 'ime_backup',
      description: 'Back up the IME word libraries to a timestamped snapshot. Returns the backup location.',
      parameters: {},
      output: { schema: { type: 'object', additionalProperties: true, properties: {} }, render: renderJson },
      isConcurrencySafe: () => true,
      async execute() {
        return { result: await call(run, ['backup'], opts.scriptTimeoutMs) }
      }
    }),

    defineTool({
      name: 'ime_search',
      description:
        'Search for industry/domain terminology to build a word list. WITHOUT --run it only prints a search plan (no network). ' +
        'WITH run=true it actually searches (argo first, generic fallback) and may take a couple of minutes.',
      parameters: {
        topic: { type: 'string', required: true, description: 'Topic or industry, e.g. 锂电池, 投资理财.' },
        run: { type: 'boolean', description: 'Actually execute the search (network). Defaults to false.' },
        out: { type: 'string', description: 'Optional output file for the raw term list.' },
        engine: { type: 'string', description: 'Optional engine hint (argo/generic).' }
      },
      output: { schema: { type: 'object', additionalProperties: true, properties: {} }, render: renderJson },
      isConcurrencySafe: () => true,
      async execute(args: { topic: string; run?: boolean; out?: string; engine?: string }) {
        const argv = ['search', '--topic', nonEmpty(args.topic, 'topic')]
        if (args.run === true) argv.push('--run')
        if (args.out !== undefined && args.out.trim() !== '') argv.push('--out', args.out.trim())
        if (args.engine !== undefined && args.engine.trim() !== '') argv.push('--engine', args.engine.trim())
        return { result: await call(run, argv, args.run === true ? opts.searchTimeoutMs : opts.scriptTimeoutMs) }
      }
    }),

    defineTool({
      name: 'ime_build',
      description: 'Build an industry word-list TSV from a raw term list. Terms without pinyin go to the general list (never applied).',
      parameters: {
        topic: { type: 'string', required: true, description: 'Topic label for the word list.' },
        from: { type: 'string', required: true, description: 'Path to the raw term list file (one term per line or TSV).' },
        term: { type: 'string', description: 'Optional key:value override, e.g. "材料:磷酸铁锂". Repeatable via comma.' }
      },
      output: { schema: { type: 'object', additionalProperties: true, properties: {} }, render: renderJson },
      isConcurrencySafe: () => true,
      async execute(args: { topic: string; from: string; term?: string }) {
        const argv = ['build', '--topic', nonEmpty(args.topic, 'topic'), '--from', nonEmpty(args.from, 'from')]
        if (args.term !== undefined && args.term.trim() !== '') {
          for (const t of args.term.split(',')) {
            if (t.trim() !== '') argv.push('--term', t.trim())
          }
        }
        return { result: await call(run, argv, opts.scriptTimeoutMs) }
      }
    }),

    defineTool({
      name: 'ime_optimize',
      description: 'Merge/dedupe/correct word lists: fix obvious pinyin/spelling issues, remove noise, dedupe across sources. Writes to --out, never in place.',
      parameters: {
        from: {
          type: 'string',
          required: true,
          description: 'Comma-separated input TSV paths (repeatable sources are merged).'
        },
        profile: {
          type: 'string',
          enum: ['personal', 'industry'],
          description: 'Optimization profile: personal (merges into your lexicon) or industry (keeps terms for sharing).'
        },
        budget: { type: 'number', description: 'Max entries to keep (default: engine-appropriate budget).' },
        out: { type: 'string', description: 'Output directory for the optimized TSV.' }
      },
      output: { schema: { type: 'object', additionalProperties: true, properties: {} }, render: renderJson },
      isConcurrencySafe: () => true,
      async execute(args: { from: string; profile?: string; budget?: number; out?: string }) {
        const argv = ['optimize']
        for (const f of nonEmpty(args.from, 'from').split(',')) {
          if (f.trim() !== '') argv.push('--from', f.trim())
        }
        if (args.profile !== undefined && args.profile.trim() !== '') argv.push('--profile', args.profile.trim())
        if (args.budget !== undefined) argv.push('--budget', String(posInt(args.budget, 'budget', 5000)))
        if (args.out !== undefined && args.out.trim() !== '') argv.push('--out', args.out.trim())
        return { result: await call(run, argv, opts.scriptTimeoutMs) }
      }
    }),

    defineTool({
      name: 'ime_import',
      description:
        'Stage an optimized TSV into the IME staging area WITHOUT touching the live library (import never applies; the input method must be exited before any apply, and apply stays a manual step). ' +
        'Set replace to rebuild the staged library from ONLY this TSV, dropping live words that were corrected/removed by optimize (e.g. typos like agnet, noise). ' +
        'Report the staged entry count back to the user and ask before anything further.',
      parameters: {
        engine: {
          type: 'string',
          enum: ['doubao', 'wechat', 'wetype'],
          required: true,
          description: 'Target engine. doubao budgets to ~7500 slots (Doubao 0.9.7+); wechat uses usr.dat.'
        },
        from: { type: 'string', required: true, description: 'Path to the optimized TSV.' },
        budget: { type: 'number', description: 'Entry budget for the staging write (doubao slot limit).' },
        replace: { type: 'boolean', description: '整表替换（消错）：用优化表重建 staging，不并入 live，去掉已被改写/删除的旧词。' }
      },
      output: { schema: { type: 'object', additionalProperties: true, properties: {} }, render: renderJson },
      isConcurrencySafe: () => true,
      async execute(args: { engine: string; from: string; budget?: number; replace?: boolean }) {
        const argv = ['import', '--engine', nonEmpty(args.engine, 'engine'), '--from', nonEmpty(args.from, 'from')]
        if (args.budget !== undefined) argv.push('--budget', String(posInt(args.budget, 'budget', 5000)))
        if (args.replace) argv.push('--replace')
        return { result: await call(run, argv, opts.scriptTimeoutMs) }
      }
    })
  ]
}
