#!/usr/bin/env python3
"""Stop: run の途中のまま応答を終えようとしたら、1回だけ止めて続きを促す。

止める条件（案件フォルダで、計画承認後だけ）:
  - 03_調書/<area_id>.review.json があるのに、その査閲結果が rounds に記録されていない（review-step の呼び忘れ）
  - status が draft の調書がある（after-execute・review-step の判定まで回していない）
バックグラウンドのエージェントを待っている場合など、続けても進めないこともあるので、促すのは1回だけ
（stop_hook_active が true なら止めない）。人の判断が要るところで止まるのは正しい動きなので、
blocked・pass・escalate の領域があることでは止めない。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from _common import find_engagement_root, load_engagement  # noqa: E402
from state import is_unrecorded_review  # noqa: E402


def unfinished(root: Path) -> list[str]:
    found = []
    for p in sorted((root / "03_調書").glob("*.json")):
        if p.name.startswith("_") or p.name.endswith(".review.json"):
            continue
        try:
            wp = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        area = wp.get("area_id", p.stem)
        if (wp.get("status") or "draft") != "draft":
            continue  # ループの外。古い版の rounds には査閲結果の記録が無いので、ここでは比べない
        if is_unrecorded_review(root, area, wp):
            found.append(f"{area}：査閲結果が rounds に記録されていない（state.py review-step を実行する）")
        else:
            found.append(f"{area}：status が draft のまま（after-execute か review-step の判定まで進める）")
    return found


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    if payload.get("stop_hook_active"):
        return
    root = find_engagement_root(payload.get("cwd") or ".")
    if not root:
        return
    try:
        if load_engagement(root).get("plan_status") != "approved":
            return
    except (OSError, ValueError):
        return
    found = unfinished(root)
    if found:
        reason = ("[tadori] run が途中のままです。次の領域を run スキルの手順で最後まで進めてください。\n- "
                  + "\n- ".join(found)
                  + "\nバックグラウンドのエージェントを待っているなど、いま進められない場合は、その旨を伝えて終えてかまいません。")
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


if __name__ == "__main__":
    main()
