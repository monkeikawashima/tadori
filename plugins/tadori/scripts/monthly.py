#!/usr/bin/env python3
"""仕訳帳を勘定科目（と相手先）ごとに月別集計する。分析的手続の推移表に使う。

使い方:
  # 売上と外注費の月別推移
  python3 monthly.py --gl 02_正規化データ/仕訳帳.csv --accounts 4110 5110

  # 売掛金を得意先別・月別に。表を Excel で見るなら --csv で作業フォルダに書き出す
  python3 monthly.py --gl 02_正規化データ/仕訳帳.csv --accounts 1310 --by counterparty \
      --csv 03_調書/receivables_work/売掛金_月別.csv

net は借方−貸方（収益・負債はマイナスで出る）。count は仕訳行の数。
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dump, read_table, require_columns, to_decimal  # noqa: E402
from je_filter import parse_date  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gl", required=True)
    ap.add_argument("--accounts", nargs="*", default=[], help="勘定科目コード。省略すると全科目")
    ap.add_argument("--by", choices=["account", "counterparty"], default="account",
                    help="account=科目別、counterparty=科目×相手先別")
    ap.add_argument("--csv", help="縦持ちの表（month, account_code, counterparty, debit, credit, net, count）を書き出す先")
    args = ap.parse_args()

    gl = read_table(args.gl)
    require_columns(gl, ["date", "account_code", "debit", "credit"], "仕訳帳")
    wanted = set(args.accounts)

    cells = defaultdict(lambda: {"debit": Decimal(0), "credit": Decimal(0), "count": 0})
    names = {}
    for r in gl:
        code = str(r["account_code"]).strip()
        if wanted and code not in wanted:
            continue
        d = parse_date(r["date"])
        if d is None:
            sys.exit(f"日付として読めない値: {r['date']!r}（伝票 {r.get('je_id', '')}）")
        cp = str(r.get("counterparty", "")).strip() if args.by == "counterparty" else ""
        c = cells[(code, cp, f"{d:%Y-%m}")]
        c["debit"] += to_decimal(r["debit"])
        c["credit"] += to_decimal(r["credit"])
        c["count"] += 1
        names[code] = str(r.get("account_name", "")).strip()

    months = sorted({m for _, _, m in cells})
    series = defaultdict(dict)
    for (code, cp, m), c in cells.items():
        series[(code, cp)][m] = dict(c, net=c["debit"] - c["credit"])
    out = []
    for (code, cp) in sorted(series):
        by_month = {m: series[(code, cp)].get(m, {"debit": Decimal(0), "credit": Decimal(0), "count": 0, "net": Decimal(0)})
                    for m in months}
        item = {"account_code": code, "account_name": names.get(code, "")}
        if args.by == "counterparty":
            item["counterparty"] = cp
        item.update({
            "total": {k: sum((v[k] for v in by_month.values()), Decimal(0)) for k in ("debit", "credit", "net")},
            "months": by_month,
        })
        out.append(item)

    if args.csv:
        Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["month", "account_code", "account_name", "counterparty", "debit", "credit", "net", "count"])
            for item in out:
                for m, v in item["months"].items():
                    w.writerow([m, item["account_code"], item["account_name"], item.get("counterparty", ""),
                                v["debit"], v["credit"], v["net"], v["count"]])

    dump({"gl": args.gl, "by": args.by, "months": months, "series": out, **({"csv": args.csv} if args.csv else {})})
    return 0


if __name__ == "__main__":
    sys.exit(main())
