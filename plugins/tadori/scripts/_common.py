"""tadori スクリプト共通処理。標準ライブラリのみで動く。"""
from __future__ import annotations

import csv
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ENGAGEMENT_FILE = Path("00_案件情報") / "engagement.json"

# 案件フォルダの標準構成
FOLDERS = [
    "00_案件情報",
    "01_受領資料",
    "02_正規化データ",
    "03_調書",
    "04_依頼管理",
    "05_成果物",
    "06_ログ",
]


def find_engagement_root(start: str | Path) -> Path | None:
    """start から親をたどり、00_案件情報/engagement.json を持つフォルダを返す。"""
    p = Path(start).resolve()
    if p.is_file():
        p = p.parent
    for cand in [p, *p.parents]:
        if (cand / ENGAGEMENT_FILE).is_file():
            return cand
    return None


def load_engagement(root: Path) -> dict:
    with open(root / ENGAGEMENT_FILE, encoding="utf-8") as f:
        return json.load(f)


def to_decimal(value) -> Decimal:
    """'1,234'、'(500)'、'△500'、'▲500'、空欄を Decimal にする。"""
    if value is None:
        return Decimal(0)
    s = str(value).strip().replace(",", "").replace("¥", "").replace("円", "")
    if s in ("", "-"):
        return Decimal(0)
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if s[:1] in ("△", "▲", "-"):
        neg, s = True, s[1:]
    try:
        d = Decimal(s)
    except InvalidOperation as e:
        raise ValueError(f"金額として読めない値: {value!r}") from e
    return -d if neg else d


def read_table(path: str | Path) -> list[dict]:
    """CSV（UTF-8/UTF-8 BOM/CP932）か XLSX（先頭シート）を辞書のリストで読む。"""
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        try:
            import openpyxl  # noqa: PLC0415
        except ImportError:
            sys.exit("xlsx を読むには openpyxl が必要です。CSV に書き出してから渡してください。")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
        header = [str(h).strip() if h is not None else "" for h in rows[0]]
        return [
            {header[i]: ("" if v is None else v) for i, v in enumerate(r) if i < len(header)}
            for r in rows[1:]
            if any(v is not None for v in r)
        ]
    for enc in ("utf-8-sig", "cp932"):
        try:
            with open(path, encoding=enc, newline="") as f:
                return [{k.strip(): v for k, v in row.items() if k} for row in csv.DictReader(f)]
        except UnicodeDecodeError:
            continue
    sys.exit(f"文字コードを判別できません: {path}")


def require_columns(rows: list[dict], cols: list[str], label: str) -> None:
    if not rows:
        sys.exit(f"{label} が空です")
    missing = [c for c in cols if c not in rows[0]]
    if missing:
        sys.exit(f"{label} に必要な列がありません: {', '.join(missing)}（現在の列: {', '.join(rows[0].keys())}）")


def dump(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
