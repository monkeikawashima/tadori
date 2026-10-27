#!/usr/bin/env python3
"""PreToolUse: メール送信ガード（Gmail / Outlook などのコネクタ）。

案件フォルダを開いているセッションでだけ働く。
  - 計画承認前は送信も下書きも止める
  - 宛先は engagement.json の contacts と auditor_domains に載っているものだけ
  - mail.mode が "draft" の間は送信・返信を止め、下書き作成だけ通す
  - 転送は常に止める（受領資料の外部流出を防ぐ）
  - 宛先を明示しない返信は止める（返信先が誰か事前に確かめられないため）
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from _common import find_engagement_root, load_engagement  # noqa: E402

MAIL_HINT = re.compile(r"gmail|outlook|mail|email", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
RECIPIENT_KEYS = re.compile(r"^(to|cc|bcc|recipients?|to_?recipients|cc_?recipients|bcc_?recipients|address(es)?)$", re.I)


def deny(msg: str, root: Path, payload: dict) -> None:
    try:
        with open(root / "06_ログ" / "guard_denials.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"tool": payload.get("tool_name"), "reason": msg}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    print(f"[tadori] {msg}", file=sys.stderr)
    sys.exit(2)


def collect_recipients(obj, found: list[str], under_key: bool = False) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            collect_recipients(v, found, under_key or bool(RECIPIENT_KEYS.match(str(k))))
    elif isinstance(obj, list):
        for v in obj:
            collect_recipients(v, found, under_key)
    elif isinstance(obj, str) and under_key:
        found.extend(m.lower() for m in EMAIL.findall(obj))


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    tool = payload.get("tool_name", "")
    name = tool.lower()
    if not tool.startswith("mcp__") or not MAIL_HINT.search(tool):
        return
    is_forward = "forward" in name
    is_draft = "draft" in name and "send" not in name
    is_send = any(w in name for w in ("send", "reply")) and not is_draft
    if not (is_forward or is_draft or is_send):
        return  # 検索・既読化などは対象外

    root = find_engagement_root(payload.get("cwd") or ".")
    if not root:
        return
    eng = load_engagement(root)

    if is_forward:
        deny("案件フォルダを開いている間はメールの転送はできません。", root, payload)
    if eng.get("plan_status") != "approved":
        deny("計画が未承認のため、資料依頼メールはまだ作れません。先に計画承認（ゲート1）を受けてください。", root, payload)
    if is_send and eng.get("mail", {}).get("mode", "draft") != "send":
        deny("この案件は下書き運用です（engagement.json の mail.mode が draft）。送信ではなく下書きを作ってください。", root, payload)

    allowed = {c["email"].strip().lower() for c in eng.get("contacts", []) if c.get("email")}
    domains = {d.strip().lower().lstrip("@") for d in eng.get("auditor_domains", [])}
    found: list[str] = []
    collect_recipients(payload.get("tool_input", {}), found)
    if not found:
        if "reply" in name:
            deny("宛先を明示しない返信は送れません。登録済みの担当者宛てに宛先を書いて作成してください。", root, payload)
        if is_send:
            deny("宛先メールアドレスを確認できないため送信を止めました。", root, payload)
        return
    bad = [a for a in found if a not in allowed and a.split("@")[-1] not in domains]
    if bad:
        deny(f"未登録の宛先が含まれています: {', '.join(sorted(set(bad)))}。依頼先は計画承認時に engagement.json の contacts に登録した担当者だけです。", root, payload)


if __name__ == "__main__":
    main()
