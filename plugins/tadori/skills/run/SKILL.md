---
name: run
user-invocable: false
description: 承認済みの監査計画に従って、領域ごとに executor → reviewer → fixer のループを並列で回し、合格・エスカレーション・資料待ちに振り分ける。「監査を実行」「手続を回して」「〇〇の領域を再実行」で使う。
---

# 並列実行とレビューループ

スクリプトの場所：`${CLAUDE_PLUGIN_ROOT}/scripts`（展開されていなければ `../../scripts`）。以下 `$S`。

## 0. 始める前に
- 対象領域は `python3 $S/state.py next --engagement . --today <今日>` の `run`（未実行・実行途中）と `rerun`（資料が届いた）。指定があればそれだけ。`queue` だけなら 3 に進む
- `rerun` の領域は、先に `python3 $S/state.py set-status --engagement . --area <area_id> --to draft --today <今日>` で再実行の状態に戻す
- 計画が未承認なら `next` が C を返す。plan スキルに戻す

## ループの判断はスクリプトが決める
合格・打ち切り・修正に戻すの判断と、調書の状態の項目（`status`、`status_reason`、`status_history`、`rounds` など）の記録は `state.py` が行う。自分で調書の `status` や `rounds` を書かない（hooks が止める）。周回数・合格点・予算を自分で数えない。

| 呼ぶもの | いつ | 返す `decision` |
|---|---|---|
| `state.py charge --area <id> --agent <executor/reviewer/fixer>` | サブエージェントを起動する直前に毎回 | 終了コード 1 なら予算超過。起動せず、その領域は escalate になっている |
| `state.py after-execute --area <id>` | executor が終わったら | `review`：レビューへ／`blocked`：完了した手続が無いので 3 へ |
| `state.py review-step --area <id>` | reviewer が `<id>.review.json` を書いたら | `fix`：fixer へ／`pass`・`escalate`・`blocked`：その領域は終わり |

コマンドはどれも `--engagement . --today <今日>` を付ける。

## 1. 実行（領域ごと、並列）
領域ごとに `charge`（agent executor）→ executor → `after-execute`。独立した領域は同時に起動してよい（目安：同時に 4〜6 領域）。
渡すもの：area_id、plan.json の該当部分、重要性、データの場所。これ以外の会話の文脈は渡さない。

完了した手続が1つもない領域は `after-execute` が `blocked` にする（外部証拠がない状態でレビューしても、資料を求める指摘しか出ないため）。3 に進む。

## 2. レビューループ（領域ごと）
```
周 = after-execute の next_round
繰り返す:
    charge(reviewer) が失敗 → 終わり（escalate 済み）
    reviewer(area_id, 周)        ← 調書と証拠だけを渡す。executor/fixer の説明は渡さない。round に周を書かせる
    結果 = review-step(area_id)
    if 結果.decision != "fix":  終わり（pass / escalate / blocked）
    charge(fixer) が失敗 → 終わり
    fixer(area_id, review.json の issues と hard_fails)
    周 += 1
```
- 合格点・最大周回（`engagement.json` の `loop`）、「点が前の周以下なら打ち切り」、「ループ中に不足資料が増えたら blocked」、reviewer の `needs_human` の `escalations` への転記は、`review-step` が行う
- `status` を手で変える必要があるとき（skeptic や最終査閲の差戻し、人の判断で escalate）は `state.py set-status --area <id> --to <draft/escalate> --reason "…"`。遷移表にない移り方は止まる

### Claude Code で Workflow を使う場合
利用者が「workflow で回して」のように明示的に頼んだときだけ、1〜2 を Workflow ツールで実行してよい。
```
Workflow(scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/run-areas.js",
         args: {"engagement": "<案件フォルダの絶対パス>", "scripts": "<$S の絶対パス>", "today": "<今日>", "areas": ["<area_id>", ...]})
```
- 呼ぶ順番は上の 1〜2 と同じで、判断も同じ `state.py` が行う。違うのは、順番をスクリプトが持つことと、途中の結果が会話に入らないこと
- 止まったら、同じ scriptPath と args に `resumeFromRunId` を付けて再開できる。終わったエージェントの結果はそのまま使われる
- 戻り値の `areas`（領域ごとの status と理由）を見て 3 に進む
- Workflow が無い環境（Cowork など）や、利用者が頼んでいないときは、上の手順をそのまま行う

## 3. 資料不足の振り分け
status が `blocked` の領域は、調書の `blocked` のうち `pbc` が空のものを1件ずつ依頼キューに入れる。
```bash
python3 $S/pbc.py list --engagement . --status queued drafted sent reminded received rejected
python3 $S/pbc.py add --engagement . --today <今日> --json '{"area":"<area_id>","doc_type":"…","period":"…","accounts":[…],"procedure":"…","reason":"…","to":"<contactsのkey>"}'
```
- 先に `list` で未完了の依頼を見る。同じ資料が別の書き方で依頼済みなら、`"same_as": "<PBC ID>"` を付けて add し、既存の依頼に統合する。資料名・期間・勘定が一字一句同じときしか自動では統合されない
- 入れた依頼の PBC ID を、調書の `blocked` の該当行の `pbc` に書く
- `doc_type` と `reason` は、クライアントにそのまま見せてよい書き方にする。requester は資料名をそのままメールに入れる。不正の兆候に関わる取引先・伝票番号を名指ししない。「〇〇社分は別件」のように特定の取引先を特別扱いしていると分かる書き方もしない
- 依頼前に、受領台帳と 02_正規化データ に代わりになる資料がないかを executor が確認済みであること（`substitute_checked` が空なら executor に戻す）
- 宛先キーが contacts にないと止まる。その依頼は人間確認欄へ
- 確認状（銀行・取引先）、不正の兆候に関わる資料、人が行う手続の記録（経営者への質問、実査・立会の記録）は、キューに入れず `escalations` に書き、`blocked` の該当行の `pbc` に `ESC` と書く。`pbc` が空の行が残っていると、`state.py next` は依頼キューへの登録が済んでいないとみなし、run に戻し続ける

## 4. 状態の更新と報告
```bash
python3 $S/pbc.py report --engagement .
python3 $S/summarize.py --engagement . --today <今日>
python3 $S/engagement.py status --engagement . --status "実行中（pass x / 全 y）" --today <今日>
```
報告は「pass／escalate／blocked の領域数」「新しく依頼キューに入れた件数」「人の判断が要る事項」だけ。全領域が pass か escalate になり、blocked が0なら finalize スキルに進む（利用者には「/tadori:start で最終確認に進みます」と伝える）。

## 再実行
受領資料で依頼が充足したら、intake スキルから該当領域だけを指定してこのスキルを呼ぶ（`state.py next` の `rerun` に出る）。`set-status --to draft` で戻してから、executor は既存の調書を読み、未了の手続だけを実行する。周回数と「改善が止まった」の比較は、戻したところから数え直される。
