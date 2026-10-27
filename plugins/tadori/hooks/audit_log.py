#!/usr/bin/env python3
"""PostToolUse: 案件フォルダ内で実行したツール呼出を 06_ログ/tool_log.jsonl に残す。

このログ自体が「何を見て、何を実行し、何を書いたか」の証跡になる。
失敗しても作業は止めない（終了コード 0）。
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from _common import find_engagement_root  # noqa: E402

MAX_FIELD = 2000


def clip(v):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= MAX_FIELD else s[:MAX_FIELD] + f"…（{len(s)}文字中{MAX_FIELD}文字）"


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    root = find_engagement_root(payload.get("cwd") or ".")
    if not root:
        return
    resp = payload.get("tool_response")
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session": payload.get("session_id"),
        "agent": payload.get("agent_type") or payload.get("agent_id") or "main",
        "tool": payload.get("tool_name"),
        "input": clip(payload.get("tool_input")),
        "response": clip(resp) if resp is not None else None,
    }
    try:
        log = root / "06_ログ" / "tool_log.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    main()
