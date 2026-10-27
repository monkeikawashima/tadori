#!/usr/bin/env python3
"""仕訳テストの抽出条件を当てて、該当仕訳と理由を返す。

判断はしない。「条件に当たった仕訳」と「当たった条件」を出すだけで、
不正の兆候かどうかは executor と skeptic が仕訳の中身を見て判断する。

使い方:
  python3 je_filter.py --gl 02_正規化データ/仕訳帳.csv --period-end 2026-03-31 \
      --close-date 2026-04-20 --round-unit 1000000

条件:
  weekend        土日の計上日
  holiday        engagement.json の holidays に入っている日（--holidays で渡す）
  period_end     期末日の前後 --window 日以内
  post_close     入力日時が --close-date より後（決算確定後の入力）
  round_amount   --round-unit の倍数かつ --round-min 以上
  manual         source 列が manual/手入力/手動
  keyword        摘要に --keywords のいずれかを含む
  rare_pair      借方科目×貸方科目の組合せが期中 --rare-max 回以下
  unusual_user   入力者が期中 --rare-user-max 件以下しか入力していない
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from _common import dump, read_table, require_columns, to_decimal

DEFAULT_KEYWORDS = ["調整", "修正", "訂正", "仮", "振替", "取消", "その他", "雑", "社長", "立替"]


def parse_date(s) -> date | None:
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    s = str(s).strip()
    # 入力日時は秒なし（2027-01-20T22:14、2027/01/20 22:14）で出す会計ソフトもある
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s[:19], fmt).date()
        except ValueError:
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gl", required=True)
    ap.add_argument("--period-end", required=True, help="期末日 YYYY-MM-DD")
    ap.add_argument("--close-date", help="決算確定日 YYYY-MM-DD")
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--round-unit", default="1000000")
    ap.add_argument("--round-min", default="1000000")
    ap.add_argument("--keywords", nargs="*", default=DEFAULT_KEYWORDS)
    ap.add_argument("--holidays", nargs="*", default=[])
    ap.add_argument("--rare-max", type=int, default=2)
    ap.add_argument("--rare-user-max", type=int, default=5)
    ap.add_argument("--limit", type=int, default=500, help="出力する仕訳の上限")
    args = ap.parse_args()

    gl = read_table(args.gl)
    require_columns(gl, ["date", "je_id", "account_code", "debit", "credit"], "仕訳帳")
    pe = parse_date(args.period_end)
    close = parse_date(args.close_date) if args.close_date else None
    holidays = {parse_date(h) for h in args.holidays}
    unit, rmin = to_decimal(args.round_unit), to_decimal(args.round_min)

    entries: dict[str, list[dict]] = defaultdict(list)
    for r in gl:
        entries[str(r["je_id"]).strip()].append(r)

    def pair_of(lines):
        dr = sorted({str(l["account_code"]) for l in lines if to_decimal(l["debit"]) != 0})
        cr = sorted({str(l["account_code"]) for l in lines if to_decimal(l["credit"]) != 0})
        return ("+".join(dr), "+".join(cr))

    pair_count = Counter(pair_of(ls) for ls in entries.values())
    user_count = Counter(str(ls[0].get("entered_by", "")).strip() for ls in entries.values())

    hits = []
    reason_count: Counter = Counter()
    for je_id, lines in entries.items():
        head = lines[0]
        reasons = []
        d = parse_date(head["date"])
        amount = sum((to_decimal(l["debit"]) for l in lines), to_decimal(0))
        if d:
            if d.weekday() >= 5:
                reasons.append("weekend")
            if d in holidays:
                reasons.append("holiday")
            if pe and abs((d - pe).days) <= args.window:
                reasons.append("period_end")
        if close and head.get("entered_at"):
            ea = parse_date(head["entered_at"])
            if ea and ea > close:
                reasons.append("post_close")
        if unit > 0 and amount >= rmin and amount % unit == 0:
            reasons.append("round_amount")
        if str(head.get("source", "")).strip().lower() in ("manual", "手入力", "手動", "振替伝票"):
            reasons.append("manual")
        desc = " ".join(str(l.get("description", "")) for l in lines)
        kw = [k for k in args.keywords if k and k in desc]
        if kw:
            reasons.append("keyword:" + "/".join(kw))
        if pair_count[pair_of(lines)] <= args.rare_max:
            reasons.append("rare_pair")
        user = str(head.get("entered_by", "")).strip()
        if user and user_count[user] <= args.rare_user_max:
            reasons.append("unusual_user")
        if reasons:
            for rs in reasons:
                reason_count[rs.split(":")[0]] += 1
            hits.append({
                "je_id": je_id, "date": str(head["date"]), "amount": amount,
                "entered_by": user, "description": desc[:200],
                "accounts": pair_of(lines), "reasons": reasons, "reason_count": len(reasons),
            })

    hits.sort(key=lambda h: (-h["reason_count"], -h["amount"]))
    dump({
        "journal_entries": len(entries),
        "hit_count": len(hits),
        "hits_by_reason": dict(reason_count),
        "params": vars(args),
        "hits": hits[: args.limit],
        "truncated": len(hits) > args.limit,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
