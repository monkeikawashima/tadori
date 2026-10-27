#!/usr/bin/env python3
"""金額単位サンプリング（MUS、系統抽出）。

サンプリング間隔 = 許容虚偽表示額 ÷ 信頼係数。間隔以上の項目は全件抽出し、
残りは乱数の開始点から間隔ごとに抽出する。乱数は --seed で固定するので、
同じ入力と seed なら誰が何度回しても同じサンプルになる（調書に seed を残すこと）。

使い方:
  python3 sample_mus.py --population 02_正規化データ/売掛金残高.csv \
      --amount-col balance --id-col customer_code \
      --tolerable 5000000 --confidence-factor 3.0 --seed 20261027

信頼係数の目安（予想誤謬ゼロ）: 信頼度95%=3.0、90%=2.3、80%=1.6
"""
import argparse
import random
import sys
from decimal import ROUND_DOWN, Decimal

from _common import dump, read_table, require_columns, to_decimal


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--population", required=True)
    ap.add_argument("--amount-col", required=True)
    ap.add_argument("--id-col", required=True)
    ap.add_argument("--tolerable", required=True, help="許容虚偽表示額（円）")
    ap.add_argument("--confidence-factor", default="3.0")
    ap.add_argument("--interval", help="間隔を直接指定する場合（円）。指定時は tolerable/factor より優先")
    ap.add_argument("--seed", required=True, type=int, help="乱数の種。調書に記録する")
    args = ap.parse_args()

    rows = read_table(args.population)
    require_columns(rows, [args.amount_col, args.id_col], "母集団")

    items = [(str(r[args.id_col]), abs(to_decimal(r[args.amount_col])), r) for r in rows]
    items = [i for i in items if i[1] > 0]
    total = sum((i[1] for i in items), Decimal(0))
    if args.interval:
        interval = Decimal(args.interval)
    else:
        interval = (Decimal(args.tolerable) / Decimal(args.confidence_factor)).quantize(Decimal(1), ROUND_DOWN)
    if interval <= 0:
        sys.exit("間隔が0以下です")

    key_items = sorted((i for i in items if i[1] >= interval), key=lambda i: -i[1])
    rest = [i for i in items if i[1] < interval]

    rng = random.Random(args.seed)
    start = Decimal(rng.randint(1, int(interval)))
    selected, cum, next_hit = [], Decimal(0), start
    for item_id, amt, _ in rest:
        cum += amt
        if next_hit <= cum:
            selected.append({"id": item_id, "amount": amt, "hit_at": next_hit})
            while next_hit <= cum:
                next_hit += interval

    dump({
        "method": "MUS（系統抽出）",
        "seed": args.seed,
        "population_count": len(items),
        "population_amount": total,
        "interval": interval,
        "random_start": start,
        "key_items": [{"id": i[0], "amount": i[1]} for i in key_items],
        "key_items_amount": sum((i[1] for i in key_items), Decimal(0)),
        "sampled_items": selected,
        "sample_size": len(key_items) + len(selected),
        "coverage_ratio": float(
            (sum((i[1] for i in key_items), Decimal(0)) + sum((s["amount"] for s in selected), Decimal(0))) / total
        ) if total else 0.0,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
