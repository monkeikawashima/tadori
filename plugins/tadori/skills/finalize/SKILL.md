---
name: finalize
user-invocable: false
description: 全領域の実行が終わった後の横断チェック（未修正虚偽表示の集計と重要性比較、科目間整合、skeptic による反証、不正シナリオの読み直し）を行い、人が最終査閲する一式を作る（ゲート2）。「最終確認」「まとめて」「査閲用の資料を作って」で使う。
---

# 横断チェックと最終パッケージ

スクリプトの場所：`${CLAUDE_PLUGIN_ROOT}/scripts`（展開されていなければ `../../scripts`）。以下 `$S`。

## 0. 前提
```bash
python3 $S/summarize.py --engagement . --today <今日>
```
`areas_not_pass` に `blocked` や `draft` が残っていれば止める。資料待ちなら request-mail へ、未実行なら run へ戻す。`escalate` は残っていてよい（人が判断する）。

## 1. 横断チェック
- 未修正虚偽表示：summarize.py の合計と重要性の比較をそのまま使う
- 科目間の整合：売上と売掛金・前受金、仕入と買掛金・棚卸資産、減価償却費と固定資産台帳、借入金と支払利息。数字はスクリプトか Python で出す
- 受領資料の改変チェック：`python3 $S/hash_evidence.py --engagement . --verify`
- skeptic エージェントを `loop.skeptic_count`（既定3）体、独立に起動する。特別なリスクの領域と、点数が合格点ぎりぎりの領域を優先する。うち1体には「不正シナリオ」と指示し、仕訳テストの結果を渡す
- skeptic の過半が「追加手続が必要」か「結論に反対」とした論点は、該当領域を run に戻すか、人間確認欄に上げる。戻すのは1回まで。戻すときは `python3 $S/state.py set-status --engagement . --area <area_id> --to draft --today <今日> --reason "skeptic の反証：<論点>"`

## 2. 最終パッケージを作る（`05_成果物/`）
`deliverables` スキルの形式とファイル名ルールに従う。

1. **査閲メモ**（Word）：人が最初に読む1本。順番は
   - 人の判断が要る事項（エスカレーション、確信度「低」の判断、重要性に近い差異、資料未入手の領域）
   - 未修正虚偽表示の合計と重要性の比較
   - 領域ごとの結論と点数の一覧
   - 再実施してほしい調書：pass した領域から無作為に3件（`python3 -c "import random; random.seed(<今日のYYYYMMDD>); ..."` で選び、seed を書く）
2. **未修正虚偽表示一覧**（Excel）：`python3 $S/summarize.py --engagement . --today <今日> --xlsx`
3. **調書一式**（Excel）：領域ごとに1シート。手続・証拠 ID・結果・結論。証拠 ID は 01_受領資料 の該当ファイルへのリンクにする
4. **依頼トラッカー**（Excel）：`pbc.py report` の最新版をコピー
5. **次期引継ぎ.md**：今期の論点、指摘事項、依頼先ごとの返信の速さ、来期に早めに依頼すべき資料

## 3. ゲート2
```bash
python3 $S/engagement.py status --engagement . --status "最終査閲待ち" --today <今日>
```
人に伝えるのは、査閲メモの場所と、判断が要る事項の件数だけ。意見の表明や結論の確定はしない。査閲の指摘を受けたら、該当領域を `state.py set-status --to draft --reason "最終査閲の指摘：<内容>"` で戻し、run で再実行する。
