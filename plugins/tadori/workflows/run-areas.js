export const meta = {
  name: 'tadori-run-areas',
  description: 'tadori：承認済み計画の領域ごとに executor → reviewer → fixer のループを並列で回す（判断は state.py）',
  whenToUse: 'tadori の run スキルで、利用者が Workflow での実行を明示的に頼んだとき（Claude Code のみ）',
  phases: [
    { title: '実行', detail: '領域ごとに executor で手続を実施し、state.py after-execute で振り分け' },
    { title: 'レビュー', detail: 'reviewer → state.py review-step → fixer を、state.py が終わりと判断するまで' },
  ],
}

// 合格・打ち切り・修正に戻す、の判断と予算の数え方は、run スキルの手順と同じ scripts/state.py が決める。
// このスクリプトは呼ぶ順番だけを持つ。スクリプトからはファイルもコマンドも扱えないので、
// state.py は「コマンドを1回だけ実行して結果を返す」エージェントに実行させる。
const E = args && args.engagement
const S = args && args.scripts
const TODAY = args && args.today
const AREAS = args && args.areas
if (!E || !S || !TODAY || !Array.isArray(AREAS) || !AREAS.length) {
  throw new Error('args に engagement（案件フォルダの絶対パス）、scripts（scripts フォルダの絶対パス）、today、areas（配列）が必要です')
}
const MAX_LOOP = 20 // state.py の最大周回とは別の、暴走止め

const CMD = {
  type: 'object',
  properties: { exit_code: { type: 'integer' }, stdout: { type: 'string' } },
  required: ['exit_code', 'stdout'],
}

async function state(sub, area, extra, label, phase) {
  const cmd = `python3 "${S}/state.py" ${sub} --engagement "${E}" --today ${TODAY} --area ${area}${extra || ''}`
  const r = await agent(
    '次のコマンドを Bash でそのまま1回だけ実行し、終了コードと標準出力（そのままの文字列）を返してください。' +
    'コマンドを変えたり、別のコマンドを実行したり、結果を解釈したりしないでください。\n\n' + cmd,
    { label, phase, schema: CMD, effort: 'low' })
  if (!r) return { ok: false, out: { error: 'コマンドを実行するエージェントが止まった' } }
  let out
  try {
    out = JSON.parse(r.stdout)
  } catch (e) {
    out = { error: 'state.py の出力を JSON として読めない: ' + String(r.stdout).slice(0, 200) }
  }
  return { ok: r.exit_code === 0, out }
}

async function execute(area) {
  const c = await state('charge', area, ' --agent executor', `${area}: 予算`, '実行')
  if (!c.ok) return { area, status: 'escalate', reason: c.out.error }
  const ex = await agent(
    `領域 ${area} の監査手続を実行し、調書 03_調書/${area}.json を書いてください。` +
    `案件フォルダ：${E}（ここを作業ディレクトリにする）。今日：${TODAY}。` +
    `00_案件情報/plan.json の area_id "${area}" の部分と、engagement.json の重要性・期間を読んでください。`,
    { label: `${area}: executor`, phase: '実行', agentType: 'tadori:executor' })
  if (ex === null) return { area, status: 'draft', reason: 'executor が止まった' }
  const a = await state('after-execute', area, '', `${area}: 実行後の振り分け`, '実行')
  if (!a.ok) return { area, status: 'draft', reason: a.out.error }
  if (a.out.decision !== 'review') return { area, status: 'blocked', reason: '受領済みの資料では手続を実施できない' }
  return { area, review: true, round: a.out.next_round }
}

async function reviewLoop(prev, area) {
  if (!prev || !prev.review) return prev
  let round = prev.round
  for (let i = 0; i < MAX_LOOP; i++) {
    const c = await state('charge', area, ' --agent reviewer', `${area}: 予算`, 'レビュー')
    if (!c.ok) return { area, status: 'escalate', reason: c.out.error, round }
    const rv = await agent(
      `領域 ${area} の調書 03_調書/${area}.json を査閲し、03_調書/${area}.review.json を書いてください。` +
      `案件フォルダ：${E}（ここを作業ディレクトリにする）。${round}周目です（review.json の round に ${round} を書く）。` +
      '調書と証拠だけを見て、第三者として判断してください。',
      { label: `${area}: reviewer ${round}周目`, phase: 'レビュー', agentType: 'tadori:reviewer' })
    if (rv === null) return { area, status: 'draft', reason: 'reviewer が止まった', round }
    const s = await state('review-step', area, '', `${area}: ${round}周目の判定`, 'レビュー')
    if (!s.ok) return { area, status: 'draft', reason: s.out.error, round }
    log(`${area}：${round}周目 ${s.out.score}点（ハード${s.out.hard}件）→ ${s.out.decision}`)
    if (s.out.decision !== 'fix') {
      return { area, status: s.out.status, reason: s.out.reason, round, score: s.out.score }
    }
    const f = await state('charge', area, ' --agent fixer', `${area}: 予算`, 'レビュー')
    if (!f.ok) return { area, status: 'escalate', reason: f.out.error, round }
    const fx = await agent(
      `領域 ${area} の調書 03_調書/${area}.json を、03_調書/${area}.review.json の issues と hard_fails に沿って直してください。` +
      `案件フォルダ：${E}（ここを作業ディレクトリにする）。`,
      { label: `${area}: fixer ${round}周目`, phase: 'レビュー', agentType: 'tadori:fixer' })
    if (fx === null) return { area, status: 'draft', reason: 'fixer が止まった', round }
    round += 1
  }
  return { area, status: 'draft', reason: `ループが ${MAX_LOOP} 回を超えた。engagement.json の loop を確認する`, round }
}

const results = await pipeline(AREAS, execute, reviewLoop)
return {
  areas: results.map((r, i) => r || { area: AREAS[i], status: 'draft', reason: '途中で止まった' }),
}
