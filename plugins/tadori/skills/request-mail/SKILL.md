---
name: request-mail
description: 資料依頼キューを依頼先ごとに1通へ束ね、初回依頼・督促のメールを作る（下書き運用中は下書き、送信運用なら送信）。期限超過の依頼はエスカレーションする。「資料依頼を出して」「督促して」、または1日1回の定期実行で使う。
---

# 資料依頼メール

スクリプトの場所：`${CLAUDE_PLUGIN_ROOT}/scripts`（展開されていなければ `../../scripts`）。以下 `$S`。

## 1. 今日やることを出す
```bash
python3 $S/pbc.py due --engagement . --today <今日>
```
- `send_batches`：依頼先ごとの未送信の依頼
- `remind`：督促の時期に来た依頼
- `escalate`：督促しても返事のない依頼
- `past_evidence_deadline`：報告日から逆算した資料期限を過ぎた依頼

## 2. メールを作る（requester エージェント）
requester エージェントに `send_batches` と `remind` を渡す。依頼先1人につき1日1通にまとめる。テンプレートは `${CLAUDE_PLUGIN_ROOT}/templates/mail/`。

運用モードは engagement.json の `mail.mode`：
- `draft`（既定）：メールの下書きだけ作る。人が中身を見て自分で送る。テンプレートと束ね方が安定するまではこのまま
- `send`：requester が送信する。宛先・モード・計画承認は hooks が送信直前に確認する

## 3. エスカレーション
`escalate` と `past_evidence_deadline` の依頼は、メールを送らずに状態を `escalated` にする。
```bash
python3 $S/pbc.py mark --engagement . --id <PBC id> --status escalated --today <今日> --note "<理由>"
```
期限超過の領域は、最終確認で「資料未入手」として扱う。

## 4. 記録
```bash
python3 $S/pbc.py report --engagement .
```
報告は「下書き／送信した通数と宛先」「督促した件数」「エスカレーションした件数」だけ。
