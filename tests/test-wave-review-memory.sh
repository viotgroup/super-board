#!/usr/bin/env bash
# Tests the review-memory wiring in workflows/super-board-wave.js. No gh calls:
# the script body runs under node with agent/pipeline/log stubbed.
#
# The wave script is a workflow body (top-level await + return), not a module,
# so plain `node --check` rejects it. This test is also its syntax check.
set -euo pipefail
cd "$(dirname "$0")"

node - ../workflows/super-board-wave.js <<'JS'
const fs = require('fs')
const src = fs.readFileSync(process.argv[2], 'utf8').replace(/^export const meta/m, 'const meta')
const AsyncFunction = (async () => {}).constructor
const run = (args, reply) => {
  const prompts = {}
  const agent = async (prompt, opts) => { prompts[opts.label] = prompt; return reply(opts.label) }
  const parallel = (thunks) => Promise.all(thunks.map((f) => f()))
  const pipeline = async (items, ...stages) => Promise.all(items.map(async (it) => {
    let v = it
    for (const [i, st] of stages.entries()) v = i === 0 ? await st(it) : await st(v, it)
    return v
  }))
  return new AsyncFunction('args', 'agent', 'pipeline', 'parallel', 'log', src)(args, agent, pipeline, parallel, () => {})
    .then((out) => ({ out, prompts }))
}
const fail = (m) => { console.error('FAIL: ' + m); process.exit(1) }
const base = { configPath: 'c.json' }

;(async () => {
  // 1 — the Review lane is told to load prior_report by its marker; other lanes are not.
  const ok = (label) => label.startsWith('preflight') ? { verdicts: [{ number: 7, verdict: 'proceed', detail: 'clean' }] } : ({ status: 'advanced', column: label.startsWith('review') ? 'Done' : 'next', detail: 'ok',
    ...(label.startsWith('classify') ? { kind: 'feature', complexity: 'low' } : {}) })
  const a = await run({ ...base, cards: [{ number: 7, status: 'Ready', title: 't' }] }, ok)
  const rp = a.prompts['review:#7'] || fail('review lane did not run')
  rp.includes('<!-- super-review:report -->') || fail('review prompt must name the report marker')
  rp.includes('prior_report') || fail('review prompt must ask for prior_report')
  ;/fixed \/ not fixed \/ no longer applies/.test(rp) || fail('review prompt must name the three round-1 verdicts')
  ;/before the builder's PR summary/.test(rp) || fail('review prompt must put the independent pass before the builder summary')
  ;/Gap \/ Bug \/ Verification miss \/ Scope drift \/ Over-engineering/.test(rp) || fail('review prompt must name the finding classes')
  rp.includes('ponytail:ponytail-review') || fail('review prompt must run ponytail-review')
  ;/never blocks merge alone/.test(rp) || fail('review prompt must say Over-engineering never blocks alone')
  ;/Truth-check already ran in this workflow as two independent sub-agents\. Do NOT run it inline/.test(rp) || fail('review prompt must hand over the workflow truth-check, not run it inline')
  ;['truth:Code-grounder:#7', 'truth:Historian:#7'].forEach((l) => {
    const tp = a.prompts[l] || fail(`${l} must run as its own workflow agent`)
    ;['in an adversarial truth-check of the PR', 'Over-engineering is ALWAYS Should fix and NEVER Blocker on its own',
      'Design taste and over-building do not count against it'].forEach((line) => tp.includes(line) || fail(`${l} must carry the fixed brief: ${line}`))
    ;/You write nothing/.test(tp) || fail(`${l} must stay read-only`)
  })
  ;/reclass any Over-engineering Blocker to Should fix/.test(rp) || fail('review prompt must re-apply the Over-engineering rule after the truth-check')
  ;['build:#7', 'qa:#7'].forEach((l) => a.prompts[l].includes('ponytail-review') && fail(`${l} must not get the review pass`))
  ;['build:#7', 'qa:#7'].forEach((l) => a.prompts[l].includes('super-review:report') && fail(`${l} must not get review memory`))
  // 1b — ui-refine-loop is standalone: no lane prompt mentions it.
  ;['build:#7', 'qa:#7', 'review:#7'].forEach((l) => /ui-refine-loop|qa-hook/.test(a.prompts[l]) && fail(`${l} must not trigger ui-refine-loop`))

  // 2 — a re-review bounce surfaces round-1 counts in the wave summary.
  const b = await run({ ...base, cards: [{ number: 9, status: 'Review', title: 't' }] },
    () => ({ status: 'bounced', column: 'Ready', detail: '1 prior finding not fixed',
             priorFindings: { fixed: 2, notFixed: 1, noLongerApplies: 0 } }))
  const s = b.out.cards[0]
  s.finalStatus === 'bounced' || fail('expected bounced')
  ;(s.priorFindings && s.priorFindings.notFixed === 1) || fail('summary must carry priorFindings')

  // 3 — a first review (no prior report) reports null, not a fabricated count.
  const c = await run({ ...base, cards: [{ number: 11, status: 'Review', title: 't' }] },
    () => ({ status: 'advanced', column: 'Done', detail: 'merged' }))
  c.out.cards[0].priorFindings === null || fail('first review must report priorFindings: null')
  ;['prior:#11'].forEach((l) => l in c.prompts && fail('a first review needs no prior-report pre-check'))

  // 4 — a card back from a bounce whose prior finding is still open skips the truth-check.
  const open = (label) => label.startsWith('prior') ? { priorReport: true, notFixed: ['R1 src/a.ts:4 still unguarded'], detail: '1 open' }
    : { status: 'bounced', column: 'Ready', detail: 'R1 not fixed' }
  const d = await run({ ...base, cards: [{ number: 13, status: 'Review', title: 't', labels: ['loop:rebuild-1'] }] }, open)
  d.prompts['prior:#13'] || fail('a loop:* card must get the prior-report pre-check')
  ;/You write nothing/.test(d.prompts['prior:#13']) || fail('the pre-check must stay read-only')
  Object.keys(d.prompts).some((l) => l.startsWith('truth:')) && fail('an open prior finding must skip the truth-check')
  ;/Truth-check was NOT run: .*R1 src\/a\.ts:4/.test(d.prompts['review:#13']) || fail('the Reviewer must be told why the truth-check was skipped')

  // 5 — the same card with every prior finding fixed runs the truth-check as usual.
  const fixed = (label) => label.startsWith('prior') ? { priorReport: true, notFixed: [], detail: 'all fixed' }
    : { status: 'advanced', column: 'Done', detail: 'merged' }
  const e = await run({ ...base, cards: [{ number: 15, status: 'Review', title: 't', labels: ['loop:rebuild-1'] }] }, fixed)
  ;['truth:Code-grounder:#15', 'truth:Historian:#15'].forEach((l) => e.prompts[l] || fail(`${l} must run when no prior finding is open`))
})().catch((e) => fail(e.stack))
JS
echo "PASS: test-wave-review-memory.sh (5 scenarios)"
