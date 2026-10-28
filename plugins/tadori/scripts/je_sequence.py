#!/usr/bin/env python3
"""伝票番号・計上日・入力日時の並びを母集団全件で調べる。

後から挿入された仕訳や、日付をさかのぼって入力された仕訳を探す材料にする。
判断はしない。逆転や遅れの件数と該当伝票を出すだけで、意味づけは executor と skeptic が行う。

使い方:
  python3 je_sequence.py --gl 02_正規化データ/仕訳帳.csv --lag-days 7

出すもの:
  gaps               伝票番号の欠番（番号が「接頭辞＋数字」のとき）
  date_reversals     伝票番号順で、計上日が直前の伝票より前に戻る箇所
  entered_reversals  伝票番号順で、入力日時（伝票内で最も早いもの）が直前の伝票より前に戻る箇所
  mixed_entered_at   同じ伝票の中で行ごとに入力日時が違う伝票
  entry_lag          計上日から入力日（伝票内で最も早いもの）までの日数の分布と、--lag-days を超える伝票
行番号（lines）は仕訳帳ファイルの行番号（見出しが1行目）。
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dump, read_table, require_columns  # noqa: E402
from je_filter import parse_date  # noqa: E402

LAG_BINS = [("<0", None, -1), ("0", 0, 0), ("1-3", 1, 3), ("4-7", 4, 7), ("8-30", 8, 30), (">30", 31, None)]


def parse_datetime(s) -> datetime | None:
    if isinstance(s, datetime):
        return s
    s = str(s).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    d = parse_date(s)
    return datetime(d.year, d.month, d.day) if d else None


def id_key(je_id: str):
    """「00012」「J12」「2026-0012」のような番号を、数字部分で並べられるようにする。"""
    m = re.match(r"^(.*?)(\d+)$", je_id)
    return (m.group(1), int(m.group(2)), je_id) if m else (je_id, -1, je_id)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gl", required=True)
    ap.add_argument("--lag-days", type=int, default=7, help="入力の遅れとして一覧に出す日数（これを超えるもの）")
    ap.add_argument("--limit", type=int, default=200, help="一覧に出す件数の上限")
    args = ap.parse_args()

    gl = read_table(args.gl)
    require_columns(gl, ["date", "je_id"], "仕訳帳")
    has_entered = "entered_at" in gl[0]

    entries: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for line, r in enumerate(gl, start=2):
        entries[str(r["je_id"]).strip()].append((line, r))

    jes = []
    for je_id, rows in entries.items():
        dates = [parse_date(r["date"]) for _, r in rows]
        entered = [parse_datetime(r["entered_at"]) for _, r in rows if has_entered and str(r["entered_at"]).strip()]
        jes.append({"je_id": je_id, "lines": [ln for ln, _ in rows], "date": min(d for d in dates if d),
                    "entered_min": min(entered) if entered else None, "entered_max": max(entered) if entered else None,
                    "entered_by": str(rows[0][1].get("entered_by", "")).strip(),
                    "description": str(rows[0][1].get("description", "")).strip()})
    jes.sort(key=lambda j: id_key(j["je_id"]))

    def brief(j):
        return {"je_id": j["je_id"], "date": j["date"], "entered_at": j["entered_min"], "entered_by": j["entered_by"],
                "description": j["description"], "lines": j["lines"]}

    gaps = []
    by_prefix = defaultdict(list)
    for j in jes:
        prefix, num, _ = id_key(j["je_id"])
        if num >= 0:
            by_prefix[prefix].append(num)
    for prefix, nums in by_prefix.items():
        present = set(nums)
        width = max(len(j["je_id"]) - len(prefix) for j in jes if id_key(j["je_id"])[0] == prefix)
        gaps += [f"{prefix}{n:0{width}d}" for n in range(min(nums), max(nums) + 1) if n not in present]

    date_rev, entered_rev = [], []
    for prev, cur in zip(jes, jes[1:]):
        if cur["date"] < prev["date"]:
            date_rev.append({"previous": prev["je_id"], "previous_date": prev["date"], **brief(cur)})
        if cur["entered_min"] and prev["entered_min"] and cur["entered_min"] < prev["entered_min"]:
            entered_rev.append({"previous": prev["je_id"], "previous_entered_at": prev["entered_min"], **brief(cur)})

    mixed = [dict(brief(j), entered_max=j["entered_max"],
                  spread_minutes=int((j["entered_max"] - j["entered_min"]).total_seconds() // 60))
             for j in jes if j["entered_min"] and j["entered_max"] != j["entered_min"]]
    mixed.sort(key=lambda x: -x["spread_minutes"])

    lag_dist, late = Counter(), []
    for j in jes:
        if not j["entered_min"]:
            continue
        lag = (j["entered_min"].date() - j["date"]).days
        for label, lo, hi in LAG_BINS:
            if (lo is None or lag >= lo) and (hi is None or lag <= hi):
                lag_dist[label] += 1
                break
        if lag > args.lag_days or lag < 0:
            late.append(dict(brief(j), lag_days=lag))
    late.sort(key=lambda x: -abs(x["lag_days"]))

    def by_month(items):
        return dict(sorted(Counter(str(i["date"])[:7] for i in items).items()))

    dump({
        "gl": args.gl, "journal_entries": len(jes), "has_entered_at": has_entered,
        "gaps": {"count": len(gaps), "ids": gaps[: args.limit]},
        "date_reversals": {"count": len(date_rev), "by_month": by_month(date_rev), "items": date_rev[: args.limit]},
        "entered_reversals": {"count": len(entered_rev), "by_month": by_month(entered_rev), "items": entered_rev[: args.limit]},
        "mixed_entered_at": {"count": len(mixed), "items": mixed[: args.limit]},
        "entry_lag": {"lag_days_threshold": args.lag_days,
                      "distribution": {label: lag_dist.get(label, 0) for label, _, _ in LAG_BINS},
                      "late_count": len(late), "late": late[: args.limit]},
        "truncated": any(n > args.limit for n in (len(gaps), len(date_rev), len(entered_rev), len(mixed), len(late))),
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
