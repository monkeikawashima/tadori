// workflows/run-areas.js を、本物の state.py と、調書を書くだけの偽の executor / reviewer / fixer で動かす。
// 使い方: node workflow_harness.mjs <run-areas.js> <args の JSON> <領域ごとの点数の JSON>
// 結果（workflow の戻り値と、起動したエージェントの順番）を JSON で標準出力に出す。
import { execSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

const [script, argsJson, scoresJson] = process.argv.slice(2)
const args = JSON.parse(argsJson)
const scores = JSON.parse(scoresJson) // {"revenue": [70, 90], "cash": "blocked"}
const wpDir = path.join(args.engagement, '03_調書')
const calls = []

async function agent(prompt, opts) {
  calls.push(opts.label)
  if (prompt.includes('state.py"')) {
    const cmd = prompt.split('\n\n').pop()
    try {
      return { exit_code: 0, stdout: execSync(cmd, { encoding: 'utf8' }) }
    } catch (e) {
      return { exit_code: e.status, stdout: e.stdout }
    }
  }
  const area = opts.label.split(':')[0]
  const wpFile = path.join(wpDir, `${area}.json`)
  if (opts.agentType === 'tadori:executor') {
    const done = scores[area] !== 'blocked'
    const wp = {
      area_id: area, status: 'draft', rounds: [],
      procedures: [{ proc_id: 'P-01', status: done ? '完了' : '未了' }],
      blocked: done ? [] : [{ doc_type: '残高証明書', pbc: '' }],
    }
    fs.writeFileSync(wpFile, JSON.stringify(wp))
  } else if (opts.agentType === 'tadori:reviewer') {
    const round = Number(prompt.match(/(\d+)周目/)[1])
    const review = { area_id: area, round, score: scores[area][round - 1], hard_fails: [], issues: [], needs_human: [] }
    fs.writeFileSync(path.join(wpDir, `${area}.review.json`), JSON.stringify(review))
  } else if (opts.agentType === 'tadori:fixer') {
    const wp = JSON.parse(fs.readFileSync(wpFile, 'utf8'))
    wp.conclusion = `修正 ${calls.length}`
    fs.writeFileSync(wpFile, JSON.stringify(wp))
  }
  return 'ok'
}

async function pipeline(items, ...stages) {
  return Promise.all(items.map(async (item, i) => {
    let r = item
    for (const [k, stage] of stages.entries()) r = k === 0 ? await stage(item, item, i) : await stage(r, item, i)
    return r
  }))
}

const body = fs.readFileSync(script, 'utf8').replace(/^export const meta/m, 'const meta')
const run = new Function('args', 'agent', 'pipeline', 'parallel', 'phase', 'log', `return (async () => {\n${body}\n})()`)
const result = await run(args, agent, pipeline, null, () => {}, () => {})
console.log(JSON.stringify({ result, calls }))
