---
name: audit-procedures
description: 監査手続のライブラリ（手続ID、目的、アサーション、必要データ、使うスクリプト）。計画で領域に手続を割り当てるとき、executor が手続を実行するときに読む。対象：リスク評価、仕訳テスト、現金預金、売上・売掛金・期間帰属、棚卸資産、固定資産・減損、会計上の見積り、継続企業・後発事象。
---

# 手続ライブラリ

領域に合うリファレンスだけを読む。

| ファイル | 手続ID | 内容 |
|---|---|---|
| references/risk-assessment.md | RA- | 分析的手続、重要性の案、リスクの識別 |
| references/je-testing.md | JE- | 仕訳テスト（経営者による内部統制の無効化への対応） |
| references/cash.md | CASH- | 現金預金 |
| references/revenue.md | REV- / AR- / CUT- | 売上、売掛金、期間帰属 |
| references/inventory.md | INV- | 棚卸資産 |
| references/fixed-assets.md | FA- | 有形固定資産、減価償却、減損 |
| references/estimates.md | EST- | 引当金、繰延税金資産など会計上の見積り |
| references/going-concern-subsequent.md | GC- / SE- | 継続企業、後発事象 |

## 共通ルール
- アサーションは次の語で書く：実在性、網羅性、権利と義務、評価と期間配分、期間帰属、正確性、分類、表示
- 計算・抽出は `${CLAUDE_PLUGIN_ROOT}/scripts/` で行う
  - `recon.py`：試算表と仕訳帳の突合、期末残高の再計算
  - `sample_mus.py`：金額単位サンプリング（seed を調書に残す）
  - `je_filter.py`：仕訳テストの抽出
  - `evidence_check.py`：受領資料の期間・合計の照合
- このプラグインで実施できない手続がある。実査、棚卸立会、確認状の発送・回収、経営者への質問は人が行い、その結果（立会記録、確認回答、質問の記録）を受領資料として取り込んでから調書で使う
- 手続の件数や抽出条件は、領域のリスクの高さで変える。特別なリスクには、その論点に向けた個別の手続（実証手続の詳細テスト）を必ず入れる
