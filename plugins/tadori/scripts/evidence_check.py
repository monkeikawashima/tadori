#!/usr/bin/env python3
"""受領資料（表形式）の期間と合計額を、試算表または指定額と照合する。

OCR の読み違いや、別期間・別勘定の資料が送られてきたケースをここで止める。
一致しない資料は調書の証拠に使わず、再依頼に回す。

使い方:
  # 得意先別残高表の合計 = 試算表の売掛金期末残高 か
  python3 evidence_check.py --file 02_正規化データ/得意先別残高表.csv --amount-col balance \
      --tb 02_正規化データ/試算表.csv --account 1130

  # 銀行明細の日付が期間内か、合計が指定額か
  python3 evidence_check.py --file x.csv --amount-col amount --date-col date \
      --period-start 2025-04-01 --period-end 2026-03-31 --expect-total 12345678

終了コード: 0=充足 / 1=不一致あり
"""
import argparse
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dump, read_table, require_columns, to_decimal  # noqa: E402
from je_filter import parse_date  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--amount-col", required=True)
    ap.add_argument("--date-col")
    ap.add_argument("--period-start")
    ap.add_argument("--period-end")
    ap.add_argument("--expect-total")
    ap.add_argument("--tb", help="試算表。--account と組で期末残高と照合")
    ap.add_argument("--account")
    ap.add_argument("--tolerance", default="0")
    args = ap.parse_args()
    tol = Decimal(args.tolerance)

    rows = read_table(args.file)
    cols = [args.amount_col] + ([args.date_col] if args.date_col else [])
    require_columns(rows, cols, "受領資料")

    total = sum((to_decimal(r[args.amount_col]) for r in rows), Decimal(0))
    problems = []

    expected = None
    if args.expect_total is not None:
        expected = to_decimal(args.expect_total)
    elif args.tb and args.account:
        tb = read_table(args.tb)
        require_columns(tb, ["account_code", "closing"], "試算表")
        match = [r for r in tb if str(r["account_code"]).strip() == args.account]
        if not match:
            problems.append(f"試算表に勘定 {args.account} がない")
        else:
            expected = to_decimal(match[0]["closing"])
    if expected is not None and abs(abs(total) - abs(expected)) > tol:
        problems.append(f"合計 {total} が照合先 {expected} と {abs(total) - abs(expected)} ずれている")

    out_of_period = []
    if args.date_col and (args.period_start or args.period_end):
        ps = parse_date(args.period_start) if args.period_start else None
        pe = parse_date(args.period_end) if args.period_end else None
        for i, r in enumerate(rows, start=2):
            d = parse_date(r[args.date_col])
            if d is None:
                out_of_period.append({"row": i, "value": str(r[args.date_col]), "problem": "日付として読めない"})
            elif (ps and d < ps) or (pe and d > pe):
                out_of_period.append({"row": i, "value": str(d), "problem": "期間外"})
        if out_of_period:
            problems.append(f"期間外または読めない日付が {len(out_of_period)} 行")

    dump({
        "result": "充足" if not problems else "不一致",
        "rows": len(rows),
        "total": total,
        "expected": expected,
        "problems": problems,
        "out_of_period_rows": out_of_period[:100],
    })
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
