#!/usr/bin/env python3
"""勘定科目の増加（計上）と減少（入金・支払）を相手先ごとに突き合わせる。

売掛金なら請求（借方）と入金（貸方）、買掛金なら請求（貸方）と支払（借方）を、
相手先ごとに古い計上から順に消し込む（先入先出、一部消込あり）。
期末に残った計上が未決済の明細になり、計上より多い入金・支払は「対応する計上なし」になる
（期首残高の回収・支払か、計上漏れ・誤った相手先の可能性）。

使い方:
  # 売掛金。--tb を渡すと、未決済の合計＝期末残高、対応する計上なしの合計＝期首残高 かを確かめる
  python3 subledger_match.py --gl 02_正規化データ/仕訳帳.csv --account 1310 --tb 02_正規化データ/試算表.csv

  # 買掛金（増加は貸方）。支払の仕訳に相手先が入っていない会社は --pool で相手先を区別せずに消し込む
  python3 subledger_match.py --gl 02_正規化データ/仕訳帳.csv --account 2110 --increase-side credit --pool

行番号（line）は仕訳帳ファイルの行番号（見出しが1行目）。
終了コード: 0=残高と整合 / 1=整合しない、または対応する計上のない減少がある（--tb なしなら常に0）
"""
from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dump, read_table, require_columns, to_decimal  # noqa: E402
from je_filter import parse_date  # noqa: E402

NO_COUNTERPARTY = "（相手先なし）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gl", required=True)
    ap.add_argument("--account", required=True, help="勘定科目コード")
    ap.add_argument("--increase-side", choices=["debit", "credit"], default="debit",
                    help="残高が増える側。資産は debit（既定）、負債は credit")
    ap.add_argument("--pool", action="store_true", help="相手先を区別せず、勘定全体で古い順に消し込む")
    ap.add_argument("--tb", help="試算表。期首・期末残高と照合する")
    ap.add_argument("--limit", type=int, default=200, help="明細を出す件数の上限")
    args = ap.parse_args()

    gl = read_table(args.gl)
    require_columns(gl, ["date", "je_id", "account_code", "debit", "credit"], "仕訳帳")
    dec_side = "credit" if args.increase_side == "debit" else "debit"

    increases: dict[str, list[dict]] = defaultdict(list)
    decreases: dict[str, list[dict]] = defaultdict(list)
    for line, r in enumerate(gl, start=2):
        if str(r["account_code"]).strip() != args.account:
            continue
        cp = "（全体）" if args.pool else (str(r.get("counterparty", "")).strip() or NO_COUNTERPARTY)
        base = {"line": line, "je_id": str(r["je_id"]).strip(), "date": parse_date(r["date"]),
                "description": str(r.get("description", "")).strip()}
        inc, dec = to_decimal(r[args.increase_side]), to_decimal(r[dec_side])
        if inc:
            increases[cp].append(dict(base, amount=inc))
        if dec:
            decreases[cp].append(dict(base, amount=dec))

    by_cp, settle_days = [], []
    open_total = unmatched_total = inc_total = dec_total = Decimal(0)
    for cp in sorted(set(increases) | set(decreases)):
        incs = sorted(increases[cp], key=lambda x: (x["date"], x["line"]))
        decs = sorted(decreases[cp], key=lambda x: (x["date"], x["line"]))
        for x in incs:
            x["remaining"] = x["amount"]
        allocations, unmatched = [], []
        for d in decs:
            left = d["amount"]
            for x in incs:
                if left == 0:
                    break
                if x["remaining"] == 0 or x["date"] > d["date"]:
                    continue
                take = min(left, x["remaining"])
                x["remaining"] -= take
                left -= take
                allocations.append({"increase_line": x["line"], "decrease_line": d["line"], "amount": take,
                                    "exact": take == x["amount"] == d["amount"]})
                if x["remaining"] == 0:
                    days = (d["date"] - x["date"]).days
                    x["settled"] = {"line": d["line"], "date": d["date"], "days": days}
                    settle_days.append(days)
            if left:
                unmatched.append(dict(d, unmatched_amount=left))
        open_items = [x for x in incs if x["remaining"]]
        cp_open = sum((x["remaining"] for x in open_items), Decimal(0))
        cp_unmatched = sum((u["unmatched_amount"] for u in unmatched), Decimal(0))
        cp_inc = sum((x["amount"] for x in incs), Decimal(0))
        cp_dec = sum((d["amount"] for d in decs), Decimal(0))
        open_total += cp_open
        unmatched_total += cp_unmatched
        inc_total += cp_inc
        dec_total += cp_dec
        by_cp.append({
            "counterparty": cp, "increases": len(incs), "increase_total": cp_inc,
            "decreases": len(decs), "decrease_total": cp_dec,
            "exact_matches": sum(1 for a in allocations if a["exact"]),
            "open_total": cp_open,
            "open_items": [{k: x[k] for k in ("line", "je_id", "date", "amount", "remaining", "description")}
                           for x in open_items][: args.limit],
            "unmatched_decrease_total": cp_unmatched,
            "unmatched_decreases": [{k: u[k] for k in ("line", "je_id", "date", "amount", "unmatched_amount", "description")}
                                    for u in unmatched][: args.limit],
        })

    result = {
        "gl": args.gl, "account": args.account, "increase_side": args.increase_side, "pool": args.pool,
        "increase_total": inc_total, "decrease_total": dec_total,
        "open_total": open_total, "unmatched_decrease_total": unmatched_total,
        "settlement_days": ({"count": len(settle_days), "min": min(settle_days), "median": statistics.median(settle_days),
                             "max": max(settle_days)} if settle_days else {"count": 0}),
        "by_counterparty": by_cp,
    }
    code = 0
    if args.tb:
        tb = read_table(args.tb)
        require_columns(tb, ["account_code", "opening", "closing"], "試算表")
        row = next((r for r in tb if str(r["account_code"]).strip() == args.account), None)
        if row is None:
            sys.exit(f"試算表に勘定 {args.account} がありません")
        opening, closing = abs(to_decimal(row["opening"])), abs(to_decimal(row["closing"]))
        result["tb"] = {
            "opening": opening, "closing": closing,
            "open_total_equals_closing": open_total == closing,
            "unmatched_decrease_total_equals_opening": unmatched_total == opening,
        }
        if open_total != closing or unmatched_total != opening:
            code = 1
    dump(result)
    return code


if __name__ == "__main__":
    sys.exit(main())
