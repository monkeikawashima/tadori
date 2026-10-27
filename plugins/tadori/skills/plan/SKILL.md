---
name: plan
description: 監査計画（リスク評価、重要性、領域ごとの手続割当、資料依頼先とメール運用）のドラフトを作り、人の承認（ゲート1）を受けて固定する。「計画を作って」「監査計画」「リスク評価」で使う。承認後の実行は run スキル。
---

# 監査計画とゲート1

スクリプトの場所：`${CLAUDE_PLUGIN_ROOT}/scripts`（展開されていなければ `../../scripts`）。以下 `$S`。

人が決めること：重要性、特別な検討を要するリスク、依頼先、メールを自動送信にするか。AI はドラフトと根拠を出すだけで、これらを自分で確定しない。

## 1. 前提の確認
- `00_案件情報/engagement.json` の期間・種別が埋まっている
- 試算表と仕訳帳が `02_正規化データ/` にあり、突合が一致している：
  ```bash
  python3 $S/recon.py --tb 02_正規化データ/試算表.csv --gl 02_正規化データ/仕訳帳.csv
  ```
  不一致なら計画に進まない。差異の一覧を見せて、正しいデータを依頼する
- `00_案件情報/前期引継ぎ.md` があれば読む

## 2. ドラフトを作る
`audit-procedures` スキルの「リスク評価」と各領域のリファレンスを使う。

- 分析的手続：前期比・月次推移・比率を計算し、増減の大きい科目を挙げる（計算はスクリプトか Python で）
- 重要性の案：ベンチマーク（税引前利益、売上高、総資産、純資産）と率を2〜3案出し、根拠を書く。値は engagement.json に入れず案として示す
- 領域の切り方：重要な勘定科目 × アサーション。1領域は1人の担当者が1日で終わる程度に
- 各領域に手続 ID を割り当てる（手続ライブラリの ID を使う）
- 仕訳テストの条件（期末日、決算確定日、抽出条件）
- 不正リスク：収益認識と経営者による内部統制の無効化は常に含める
- 依頼先（contacts）と、最初に依頼する資料の一覧

出力：`00_案件情報/plan.json`
```json
{
  "version": 1,
  "risk_assessment": [{"account": "", "risk": "", "level": "特別 | 高 | 中 | 低", "basis": ""}],
  "materiality_options": [{"benchmark": "", "rate": 0, "amount": 0, "rationale": ""}],
  "je_test": {"period_end": "", "close_date": "", "round_unit": 1000000, "keywords": []},
  "areas": [{
    "area_id": "revenue", "area_name": "売上高", "accounts": ["4110"],
    "assertions": ["実在性", "期間帰属"], "risk_level": "特別",
    "procedures": ["REV-01", "REV-03", "CUT-01"],
    "data_needed": ["仕訳帳", "得意先別売上明細", "期末前後の出荷記録"]
  }],
  "initial_requests": [{"doc_type": "", "period": "", "accounts": [], "to": "keiri", "reason": ""}]
}
```

## 3. 承認用の資料を作る
`deliverables` スキルの形式ルールで「監査計画書」（Word）を `05_成果物/` に作る。人が読むのは次の順：
1. 決めてほしいこと（重要性の案から選ぶ、特別なリスクの確認、依頼先とアドレス、メールを下書き運用で始めるか）
2. リスク評価の表と根拠
3. 領域と手続の割当
4. 最初の資料依頼リスト

## 4. ゲート1：承認を受ける
会話で上の「決めてほしいこと」に答えてもらう。答えを engagement.json（materiality、contacts、mail.mode）に書き込み、承認者の名前を確認してから実行する。
```bash
python3 $S/engagement.py approve --engagement . --by "<承認者名>" --today <今日>
```
- 重要性か依頼先のアドレスが空だとスクリプトが止まる。埋まるまで承認しない
- 承認後、plan.json と engagement.json は hooks で書込禁止になる。変更は人の指示で `engagement.py reopen --reason` を実行してから

承認できたら、初回依頼を `pbc.py add` でキューに入れ、`/tadori:run` に進めると伝える。
