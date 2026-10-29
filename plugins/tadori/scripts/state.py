#!/usr/bin/env python3
"""案件の「次にやること」と、調書の状態の移り変わりを決める。

start スキルの状態判定（A〜H）と、run スキルのレビューループの判断（合格・打ち切り・修正に戻す）を、
Claude が文章を読んで決めるのではなく、このスクリプトが決める。調書の状態の項目
（_common.STATE_FIELDS：status、rounds など）を書けるのはこのスクリプトだけで、
他の方法で書き換えると hooks/guard.py が止める。

サブコマンド:
  next           次にやることを A〜H で返す
  after-execute  executor の後に呼ぶ。完了した手続が無ければ blocked、あればレビューへ
  review-step    reviewer の後に呼ぶ。査閲結果を rounds に記録し、pass / escalate / blocked / fix を決める
  set-status     status を移す。遷移表にない移り方と、条件を満たさない移り方は止める
  charge         サブエージェントの呼出を1回数える。予算を超えていたら数えずに止める

例:
  python3 state.py next --engagement . --today 2026-11-02
  python3 state.py charge --engagement . --area revenue --agent reviewer --today 2026-11-02
  python3 state.py review-step --engagement . --area revenue --today 2026-11-02
  python3 state.py set-status --engagement . --area revenue --to draft --today 2026-11-10 --reason "最終査閲の指摘で再実行"
終了コード: 0=成功 / 1=止めた（理由は JSON の error）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pbc  # noqa: E402
from _common import dump, find_engagement_root, load_engagement  # noqa: E402

DONE = {"pass", "escalate"}
# 移ってよい先。draft に戻すのは再実行（資料の受領、skeptic・最終査閲による差戻し）
TRANSITIONS = {
    "draft": {"blocked", "pass", "escalate"},
    "blocked": {"draft", "escalate"},
    "pass": {"draft"},
    "escalate": {"draft"},
}
ESCALATE_REASONS = ["改善が止まった", "最大周回", "予算超過", "人の判断"]


class Stop(Exception):
    pass


# ---- 調書と記録 ----
def wp_path(root: Path, area: str) -> Path:
    return root / "03_調書" / f"{area}.json"


def load_wp(root: Path, area: str) -> dict | None:
    p = wp_path(root, area)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def require_wp(root: Path, area: str) -> dict:
    wp = load_wp(root, area)
    if wp is None:
        raise Stop(f"調書がありません: 03_調書/{area}.json")
    return wp


def save_wp(root: Path, area: str, wp: dict) -> None:
    wp_path(root, area).write_text(json.dumps(wp, ensure_ascii=False, indent=2), encoding="utf-8")


def review_sha(root: Path, area: str) -> str | None:
    p = root / "03_調書" / f"{area}.review.json"
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else None


def loop_conf(eng: dict) -> tuple[int, int]:
    loop = eng.get("loop", {})
    return int(loop.get("pass_score", 85)), int(loop.get("max_rounds", 4))


def cycle_rounds(wp: dict) -> list[dict]:
    """今回の実行（最後に draft に戻してから）の rounds。"""
    return wp.get("rounds", [])[wp.get("loop_start", 0):]


def completed(wp: dict) -> int:
    return sum(1 for p in wp.get("procedures", []) if p.get("status") == "完了")


def accepted_pbc(root: Path, wp: dict) -> list[str]:
    """blocked が待っている依頼のうち、充足済みでまだ再実行に使っていないもの。"""
    reqs = pbc.replay(root)
    used = set(wp.get("rerun_pbc", []))
    ids = [b.get("pbc") for b in wp.get("blocked", []) if b.get("pbc")]
    return [i for i in dict.fromkeys(ids) if reqs.get(i, {}).get("status") == "accepted" and i not in used]


def last_engagement_status(root: Path) -> str:
    p = root / "06_ログ" / "承認記録.jsonl"
    last = ""
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                if e.get("action") == "status":
                    last = e.get("status", "")
    return last


# ---- 予算 ----
def budget_path(root: Path) -> Path:
    return root / "03_調書" / "_budget.json"


def load_budget(root: Path) -> dict:
    p = budget_path(root)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"total": 0, "areas": {}, "log": []}


def budget_limits(eng: dict) -> tuple[int, int]:
    b = eng.get("budget", {})
    return int(b.get("max_agent_calls_per_area", 12)), int(b.get("max_agent_calls_total", 300))


def over_budget(root: Path, eng: dict, area: str | None) -> str:
    per, total = budget_limits(eng)
    used = load_budget(root)
    if used["total"] >= total:
        return f"全体の呼出が上限 {total} 回に達した"
    if area and used["areas"].get(area, 0) >= per:
        return f"この領域の呼出が上限 {per} 回に達した"
    return ""


# ---- 遷移 ----
def move(wp: dict, to: str, reason: str, today: str) -> None:
    frm = wp.get("status") or "draft"
    if to != frm and to not in TRANSITIONS.get(frm, set()):
        raise Stop(f"{frm} から {to} には移れません（移れる先：{', '.join(sorted(TRANSITIONS.get(frm, set())))}）")
    if to == "draft" and frm != "draft":
        # 再実行。周回数と「改善が止まった」の比較は、ここから数え直す
        wp["loop_start"] = len(wp.get("rounds", []))
        wp.pop("blocked_at_loop_start", None)
        wp.pop("loop_decision", None)
    wp["status"] = to
    wp["status_reason"] = reason
    wp.setdefault("status_history", []).append({"ts": today, "from": frm, "to": to, "reason": reason})


def check(root: Path, eng: dict, area: str, wp: dict, to: str, reason: str) -> None:
    """set-status で手で移すときの条件。review-step と after-execute は自分で条件を確かめてから move する。"""
    rs = cycle_rounds(wp)
    frm = wp.get("status") or "draft"
    if to == "pass":
        pass_score, _ = loop_conf(eng)
        if not rs:
            raise Stop("今回の査閲の記録（rounds）が無いので pass にできません。review-step を使ってください")
        last = rs[-1]
        if last["hard"] or last["score"] < pass_score:
            raise Stop(f"最後の査閲が合格の条件を満たしていません（{last['score']}点、ハード{last['hard']}件、合格点 {pass_score}）")
        if last.get("review_sha") != review_sha(root, area):
            raise Stop("最新の査閲結果が rounds に記録されていません。先に review-step を実行してください")
        if wp.get("blocked"):
            raise Stop("資料待ちの手続（blocked）が残っているので pass にできません")
    elif to == "blocked":
        if not wp.get("blocked"):
            raise Stop("不足資料（blocked）が空なので blocked にできません")
    elif to == "escalate":
        _, max_rounds = loop_conf(eng)
        if reason not in ESCALATE_REASONS:
            raise Stop(f"escalate の理由は {', '.join(ESCALATE_REASONS)} のいずれか")
        if reason == "改善が止まった" and not (len(rs) >= 2 and rs[-1]["score"] <= rs[-2]["score"]):
            raise Stop("直前の周より点が下がっていない（または周回が2回未満）ので「改善が止まった」にできません")
        if reason == "最大周回" and len(rs) < max_rounds:
            raise Stop(f"周回が {len(rs)} 回で、最大 {max_rounds} 回に達していません")
        if reason == "予算超過" and not over_budget(root, eng, area):
            raise Stop("予算はまだ残っています")
        if reason == "人の判断" and not wp.get("escalations"):
            raise Stop("escalations が空です。人に判断してほしい事項を先に調書の escalations に書いてください")
    elif to == "draft" and frm != "draft":
        if frm == "blocked" and not reason and not accepted_pbc(root, wp):
            raise Stop("待っている依頼がまだ充足していません。充足前に再実行するなら --reason を付けてください")
        if frm in DONE and not reason:
            raise Stop(f"{frm} から戻すには理由（--reason）が必要です")


# ---- サブコマンド ----
def cmd_next(root: Path | None, args) -> dict:
    def out(state, action, reason, **kw):
        # /loop で見張るときの止まる条件。人の判断が要るところ（C・H・予算超過）と、やることが無くなったら止める
        return {"state": state, "action": action, "reason": reason, **kw,
                "keep_watching": state in ("D", "E", "F") and action != "ask-human"}

    if root is None:
        return out("A", "new-engagement", "00_案件情報/engagement.json がない")
    eng = load_engagement(root)
    missing = [n for n in ("試算表.csv", "仕訳帳.csv") if not (root / "02_正規化データ" / n).exists()]
    if missing:
        return out("B", "intake", f"02_正規化データ に {'、'.join(missing)} がない")
    if eng.get("plan_status") != "approved":
        return out("C", "plan", "計画が未承認")
    plan_file = root / "00_案件情報" / "plan.json"
    areas = json.loads(plan_file.read_text(encoding="utf-8")).get("areas", []) if plan_file.exists() else []
    if not areas:
        return out("C", "plan", "plan.json に領域が無い。差戻し（engagement.py reopen）して計画を直す")

    rows, to_run, to_queue, to_rerun, escalations = [], [], [], [], 0
    for a in areas:
        aid = a["area_id"]
        wp = load_wp(root, aid)
        st = (wp.get("status") or "draft") if wp else "未実行"
        rows.append({"area_id": aid, "area_name": a.get("area_name", ""), "status": st,
                     "reason": (wp or {}).get("status_reason", "")})
        if wp:
            escalations += len(wp.get("escalations", []))
        if st in ("未実行", "draft"):
            to_run.append(aid)
        elif st == "blocked":
            if any(not b.get("pbc") for b in wp.get("blocked", [])):
                to_queue.append(aid)
            if accepted_pbc(root, wp):
                to_rerun.append(aid)
    statuses = [r["status"] for r in rows]
    summary = {"total": len(rows), **{s: statuses.count(s) for s in ("pass", "escalate", "blocked", "draft", "未実行")}}
    open_reqs = [r for r in pbc.replay(root).values() if r["status"] in pbc.OPEN]
    common = {"areas": rows, "summary": summary, "decisions_needed": escalations, "open_requests": len(open_reqs)}

    if last_engagement_status(root) == "最終査閲待ち" and all(s in DONE for s in statuses):
        return out("H", "final-review", "最終査閲待ち", **common)
    if to_run or to_queue or to_rerun:
        over = over_budget(root, eng, None)
        if over and (to_run or to_rerun):
            return out("D", "ask-human", f"予算超過（{over}）。続けるかは人が決める", **common)
        return out("D", "run", "未実行・実行途中・資料が届いた・依頼キュー未登録の領域がある",
                   run=to_run, rerun=to_rerun, queue=to_queue, **common)
    d = pbc.due(root, args.today)
    if d["send_batches"] or d["remind"] or d["escalate"]:
        return out("E", "request-mail", "送るべき依頼・督促がある", due=d, **common)
    if "blocked" in statuses:
        return out("F", "wait", "資料待ちの領域がある", past_evidence_deadline=d["past_evidence_deadline"], **common)
    return out("G", "finalize", "全領域が pass か escalate", **common)


def cmd_after_execute(root: Path, eng: dict, args) -> dict:
    wp = require_wp(root, args.area)
    if (wp.get("status") or "draft") != "draft":
        raise Stop(f"status が {wp.get('status')} です。再実行するなら先に set-status --to draft")
    n, blocked = completed(wp), len(wp.get("blocked", []))
    if n == 0:
        if not blocked:
            raise Stop("完了した手続も不足資料（blocked）もありません。executor に戻してください")
        move(wp, "blocked", "受領済みの資料では手続を実施できない", args.today)
        decision = "blocked"
    else:
        wp["status"] = "draft"
        wp["blocked_at_loop_start"] = blocked
        decision = "review"
    save_wp(root, args.area, wp)
    return {"area_id": args.area, "decision": decision, "completed": n, "blocked": blocked,
            "next_round": len(cycle_rounds(wp)) + 1}


def cmd_review_step(root: Path, eng: dict, args) -> dict:
    wp = require_wp(root, args.area)
    sha = review_sha(root, args.area)
    if sha is None:
        raise Stop(f"査閲結果がありません: 03_調書/{args.area}.review.json")
    for r in wp.get("rounds", []):
        if r.get("review_sha") == sha:  # 同じ査閲結果を二重に数えない
            return {"area_id": args.area, "round": r["round"], "decision": wp.get("loop_decision", ""),
                    "status": wp.get("status"), "already_recorded": True}
    if (wp.get("status") or "draft") != "draft":
        raise Stop(f"status が {wp.get('status')} の調書は査閲ループに入れません")
    rv = json.loads((root / "03_調書" / f"{args.area}.review.json").read_text(encoding="utf-8"))
    hard, score = len(rv.get("hard_fails", [])), int(rv.get("score", 0))
    prev = cycle_rounds(wp)[-1]["score"] if cycle_rounds(wp) else None
    rounds = wp.setdefault("rounds", [])
    rounds.append({"round": len(rounds) + 1, "score": score, "hard": hard, "review_sha": sha, "ts": args.today})
    esc = wp.setdefault("escalations", [])
    known = {e.get("issue") for e in esc}
    for nh in rv.get("needs_human", []):
        if nh.get("issue") not in known:
            esc.append({**nh, "from": f"reviewer {len(rounds)}周目"})

    pass_score, max_rounds = loop_conf(eng)
    n = len(cycle_rounds(wp))
    blocked = len(wp.get("blocked", []))
    if blocked > wp.get("blocked_at_loop_start", blocked):
        decision, reason = "blocked", "ループ中に不足資料が増えた"
    elif hard == 0 and score >= pass_score:
        decision, reason = ("blocked", "合格点だが資料待ちの手続がある") if blocked else ("pass", f"{n}周目で合格")
    elif prev is not None and score <= prev:
        decision, reason = "escalate", "改善が止まった"
    elif n >= max_rounds:
        decision, reason = "escalate", "最大周回"
    else:
        decision, reason = "fix", ""
    if decision != "fix":
        move(wp, decision, reason, args.today)
    wp["loop_decision"] = decision
    save_wp(root, args.area, wp)
    return {"area_id": args.area, "round": n, "score": score, "hard": hard, "pass_score": pass_score,
            "decision": decision, "status": wp["status"], "reason": reason}


def cmd_set_status(root: Path, eng: dict, args) -> dict:
    wp = require_wp(root, args.area)
    reason = (args.reason or "").strip()
    check(root, eng, args.area, wp, args.to, reason)
    frm = wp.get("status") or "draft"
    if args.to == "draft" and frm == "blocked":
        used = accepted_pbc(root, wp)
        wp["rerun_pbc"] = wp.get("rerun_pbc", []) + used
        reason = reason or f"依頼 {'、'.join(used)} が充足したので再実行"
    move(wp, args.to, reason, args.today)
    save_wp(root, args.area, wp)
    return {"area_id": args.area, "from": frm, "to": args.to, "reason": reason}


def cmd_charge(root: Path, eng: dict, args) -> dict:
    over = over_budget(root, eng, args.area)
    if over:
        wp = load_wp(root, args.area)
        if wp and (wp.get("status") or "draft") not in DONE:
            move(wp, "escalate", "予算超過", args.today)
            save_wp(root, args.area, wp)
        raise Stop(f"予算超過：{over}。{args.agent} は呼ばずに、この領域を escalate にした")
    used = load_budget(root)
    used["total"] += 1
    used["areas"][args.area] = used["areas"].get(args.area, 0) + 1
    used["log"].append({"ts": args.today, "area": args.area, "agent": args.agent})
    budget_path(root).write_text(json.dumps(used, ensure_ascii=False, indent=2), encoding="utf-8")
    per, total = budget_limits(eng)
    return {"charged": True, "area_calls": used["areas"][args.area], "area_limit": per,
            "total_calls": used["total"], "total_limit": total}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["next", "after-execute", "review-step", "set-status", "charge"])
    ap.add_argument("--engagement", required=True)
    ap.add_argument("--today", required=True)
    ap.add_argument("--area")
    ap.add_argument("--to", choices=sorted(TRANSITIONS))
    ap.add_argument("--reason")
    ap.add_argument("--agent", choices=["executor", "reviewer", "fixer", "skeptic", "intake", "requester"])
    args = ap.parse_args()
    root = find_engagement_root(Path(args.engagement).expanduser())
    try:
        if args.command == "next":
            dump(cmd_next(root, args))
            return 0
        if root is None:
            raise Stop(f"案件フォルダが見つかりません: {args.engagement}")
        if not args.area:
            raise Stop("--area が必要です")
        if args.command == "set-status" and not args.to:
            raise Stop("set-status には --to が必要です")
        if args.command == "charge" and not args.agent:
            raise Stop("charge には --agent が必要です")
        eng = load_engagement(root)
        fn = {"after-execute": cmd_after_execute, "review-step": cmd_review_step,
              "set-status": cmd_set_status, "charge": cmd_charge}[args.command]
        dump(fn(root, eng, args))
        return 0
    except Stop as e:
        dump({"error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
