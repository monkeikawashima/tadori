#!/usr/bin/env python3
"""PreToolUse: 案件フォルダの書込ガード。

止めるもの:
  - 01_受領資料 への書込・削除・移動（原本は scripts/hash_evidence.py 経由でのみ追加）
  - 計画承認後の 00_案件情報/plan.json と engagement.json の書換え
  - 開いている案件とは別の案件フォルダへの書込（クライアント取り違え防止）
止めたら終了コード 2 で理由を返す。Claude はその理由を読んで別の方法を取る。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from _common import find_engagement_root, load_engagement  # noqa: E402

LOCKED_AFTER_APPROVAL = {"plan.json", "engagement.json"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
BASH_MUTATING = re.compile(r"(\brm\b|\bmv\b|\bcp\b|>|\btee\b|\btruncate\b|\bsed\s+-i|\bshred\b|\bchmod\b|\bunlink\b|\brsync\b|\bdd\b)")


def deny(msg: str, root: Path | None, payload: dict) -> None:
    if root:
        try:
            with open(root / "06_ログ" / "guard_denials.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"tool": payload.get("tool_name"), "input": payload.get("tool_input"), "reason": msg},
                                   ensure_ascii=False)[:4000] + "\n")
        except OSError:
            pass
    print(f"[tadori] {msg}", file=sys.stderr)
    sys.exit(2)


def approved(root: Path) -> bool:
    try:
        return load_engagement(root).get("plan_status") == "approved"
    except (OSError, ValueError):
        return False


def check_path(target: Path, cwd_root: Path | None, payload: dict) -> None:
    t_root = find_engagement_root(target.parent if not target.exists() else target)
    if cwd_root and t_root and t_root != cwd_root:
        deny(f"別の案件フォルダ（{t_root.name}）への書込は禁止です。いま開いている案件は {cwd_root.name} です。", cwd_root, payload)
    root = t_root or cwd_root
    if not root:
        return
    try:
        rel = target.resolve().relative_to(root)
    except ValueError:
        return
    parts = rel.parts
    if parts and parts[0] == "01_受領資料":
        deny("01_受領資料 は原本置き場なので書込・編集できません。取込は scripts/hash_evidence.py を使い、加工したデータは 02_正規化データ に置いてください。", root, payload)
    if len(parts) == 2 and parts[0] == "00_案件情報" and parts[1] in LOCKED_AFTER_APPROVAL and approved(root):
        deny(f"計画は承認済みのため {parts[1]} は変更できません。変更が要る場合は人に差戻し（engagement.py reopen）を依頼してください。", root, payload)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input", {}) or {}
    cwd = payload.get("cwd") or "."
    cwd_root = find_engagement_root(cwd)

    if tool in WRITE_TOOLS:
        fp = ti.get("file_path") or ti.get("notebook_path")
        if fp:
            p = Path(fp)
            if not p.is_absolute():
                p = Path(cwd) / p
            check_path(p, cwd_root, payload)
        return

    if tool == "Bash":
        cmd = ti.get("command", "")
        root = cwd_root
        if "hash_evidence.py" in cmd and not re.search(r"\brm\b|\bmv\b", cmd):
            return
        if "01_受領資料" in cmd and BASH_MUTATING.search(cmd):
            deny("01_受領資料 を変更するコマンドは実行できません（読取は可）。取込は scripts/hash_evidence.py を使ってください。", root, payload)
        if root and approved(root) and re.search(r"(plan|engagement)\.json", cmd) and BASH_MUTATING.search(cmd):
            deny("計画承認後は plan.json / engagement.json をコマンドで変更できません。", root, payload)


if __name__ == "__main__":
    main()
