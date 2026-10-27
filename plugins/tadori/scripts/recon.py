#!/usr/bin/env python3
"""試算表と総勘定元帳（仕訳明細）の突合。

試算表の借方・貸方合計と、仕訳明細を勘定科目ごとに集計した額が一致するかを確かめる。
期首残高を渡すと、期首＋借方−貸方＝期末 の再計算もする。

使い方:
  python3 recon.py --tb 02_正規化データ/試算表.csv --gl 02_正規化データ/仕訳帳.csv

列の定義（02_正規化データ の標準形）:
  試算表: account_code, account_name, opening, debit, credit, closing
  仕訳帳: date, je_id, line_no, account_code, account_name, debit, credit, description,
          entered_by, entered_at, source, counterparty
終了コード: 0=一致 / 1=不一致あり（後続の手続は止める）
"""
import argparse
import sys
from collections import defaultdict
from decimal import Decimal

from _common import dump, read_table, require_columns, to_decimal


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tb", required=True, help="試算表 CSV/XLSX")
    ap.add_argument("--gl", required=True, help="仕訳帳 CSV/XLSX")
    ap.add_argument("--tolerance", default="0", help="許容差（円）。既定は0")
    args = ap.parse_args()
    tol = Decimal(args.tolerance)

    tb = read_table(args.tb)
    gl = read_table(args.gl)
    require_columns(tb, ["account_code", "debit", "credit"], "試算表")
    require_columns(gl, ["account_code", "debit", "credit", "je_id"], "仕訳帳")

    gl_sum: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    je_balance: dict[str, Decimal] = defaultdict(Decimal)
    for r in gl:
        code = str(r["account_code"]).strip()
        d, c = to_decimal(r["debit"]), to_decimal(r["credit"])
        gl_sum[code][0] += d
        gl_sum[code][1] += c
        je_balance[str(r["je_id"]).strip()] += d - c

    diffs, recalc = [], []
    tb_codes = set()
    for r in tb:
        code = str(r["account_code"]).strip()
        tb_codes.add(code)
        tb_d, tb_c = to_decimal(r["debit"]), to_decimal(r["credit"])
        g_d, g_c = gl_sum.get(code, [Decimal(0), Decimal(0)])
        if abs(tb_d - g_d) > tol or abs(tb_c - g_c) > tol:
            diffs.append({
                "account_code": code,
                "account_name": r.get("account_name", ""),
                "tb_debit": tb_d, "gl_debit": g_d, "debit_diff": tb_d - g_d,
                "tb_credit": tb_c, "gl_credit": g_c, "credit_diff": tb_c - g_c,
            })
        if "opening" in r and "closing" in r and str(r["closing"]).strip() != "":
            expected = to_decimal(r["opening"]) + tb_d - tb_c
            actual = to_decimal(r["closing"])
            # 貸方残の科目を正で持つ会計ソフトもあるため、符号反転でも一致すれば可とする
            if abs(expected - actual) > tol and abs(expected + actual) > tol:
                recalc.append({"account_code": code, "expected_closing": expected, "tb_closing": actual})

    gl_only = sorted(c for c in gl_sum if c not in tb_codes)
    unbalanced = [{"je_id": k, "diff": v} for k, v in je_balance.items() if abs(v) > tol]

    ok = not diffs and not recalc and not gl_only and not unbalanced
    dump({
        "result": "一致" if ok else "不一致",
        "accounts_checked": len(tb_codes),
        "gl_lines": len(gl),
        "account_differences": diffs,
        "closing_recalc_differences": recalc,
        "accounts_in_gl_only": gl_only,
        "unbalanced_journal_entries": unbalanced[:200],
        "unbalanced_count": len(unbalanced),
    })
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
