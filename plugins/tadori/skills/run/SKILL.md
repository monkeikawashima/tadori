---
name: run
description: 承認済みの監査計画に従って、領域ごとに executor → reviewer → fixer のループを並列で回し、合格・エスカレーション・資料待ちに振り分ける。「監査を実行」「手続を回して」「〇〇の領域を再実行」で使う。
---

# 並列実行とレビューループ

スクリプトの場所：`${CLAUDE_PLUGIN_ROOT}/scripts`（展開されていなければ `../../scripts`）。以下 `$S`。

## 0. 始める前に
- `00_案件情報/engagement.json` の `plan_status` が `approved` でなければ止めて、`/tadori:plan` に戻す
- 対象領域：指定があればそれだけ、無ければ plan.json の全領域から status が `pass` 以外のもの
- 予算：`engagement.json` の `budget`。領域ごとのサブエージェント呼出数と全体の呼出数を数え、超えたらその領域を `escalate`（理由「予算超過」）にして止める

## 1. 実行（領域ごと、並列）
領域ごとに executor エージェントを起動する。独立した領域は同時に起動してよい（目安：同時に 4〜6 領域）。
渡すもの：area_id、plan.json の該当部分、重要性、データの場所。これ以外の会話の文脈は渡さない。

## 2. レビューループ（領域ごと）
`engagement.json` の `loop`（既定：合格点 85、最大 4 周）に従う。

```
前回点 = なし
for 周 in 1..最大周:
    結果 = reviewer(area_id)          ← 調書と証拠だけを渡す。executor/fixer の説明は渡さない
    調書.rounds に {周, 点, ハード件数} を追記
    if ハード0件 かつ 点 >= 合格点:  status = pass; 終了
    if 前回点 があり 点 <= 前回点:   status = escalate（理由「改善が止まった」）; 終了
    前回点 = 点
    fixer(area_id, 結果.issues と hard_fails)
status = escalate（理由「最大周回」）
```
- reviewer の `needs_human` は調書の `escalations` に転記する
- ループ中に `blocked` が増えたら、その領域は `blocked` にしてループを抜ける

## 3. 資料不足の振り分け
status が `blocked` の領域は、調書の `blocked` を1件ずつ依頼キューに入れる。
```bash
python3 $S/pbc.py add --engagement . --today <今日> --json '{"area":"<area_id>","doc_type":"…","period":"…","accounts":[…],"procedure":"…","reason":"…","to":"<contactsのkey>"}'
```
- 同じ資料を別領域が求めていれば自動で1件に統合される
- 依頼前に、受領台帳と 02_正規化データ に代わりになる資料がないかを executor が確認済みであること（`substitute_checked` が空なら executor に戻す）
- 宛先キーが contacts にないと止まる。その依頼は人間確認欄へ
- 確認状（銀行・取引先）と不正の兆候に関わる資料は、キューに入れず `escalations` に書く

## 4. 状態の更新と報告
```bash
python3 $S/pbc.py report --engagement .
python3 $S/summarize.py --engagement . --today <今日>
python3 $S/engagement.py status --engagement . --status "実行中（pass x / 全 y）" --today <今日>
```
報告は「pass／escalate／blocked の領域数」「新しく依頼キューに入れた件数」「人の判断が要る事項」だけ。全領域が pass か escalate になり、blocked が0なら `/tadori:finalize` に進めると伝える。

## 再実行
受領資料で依頼が充足したら、intake スキルから該当領域だけを指定してこのスキルを呼ぶ。executor は既存の調書を読み、未了の手続だけを実行する。
