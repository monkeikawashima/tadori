#!/usr/bin/env python3
"""受領資料を 01_受領資料 に取り込み、SHA-256 と受領記録を受領台帳に残す。

01_受領資料 に原本を置く経路はこのスクリプトだけにする（hooks が他の書込を止める）。
同名ファイルがあっても上書きしない。中身が同じなら重複として記録だけ残す。

使い方:
  python3 hash_evidence.py --engagement <案件フォルダ> --sender "経理部 田中" \
      --received-at 2026-10-27T14:30 --mail-id <メールID> ファイル1 [ファイル2 ...]
  python3 hash_evidence.py --engagement <案件フォルダ> --verify   # 台帳と現物のハッシュ照合

台帳: 06_ログ/受領台帳.csv
"""
import argparse
import csv
import hashlib
import re
import shutil
import sys
from pathlib import Path

from _common import dump

LEDGER_COLS = ["evidence_id", "received_at", "sender", "mail_id", "original_name", "stored_path", "sha256", "bytes", "note"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s).strip("_") or "不明"


def load_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engagement", required=True, help="案件フォルダ")
    ap.add_argument("--sender", help="送付者（例: 経理部 田中）")
    ap.add_argument("--received-at", help="受領日時 YYYY-MM-DDTHH:MM")
    ap.add_argument("--mail-id", default="", help="メールのID（メール以外は空）")
    ap.add_argument("--note", default="")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args()

    root = Path(args.engagement)
    ledger_path = root / "06_ログ" / "受領台帳.csv"
    ledger = load_ledger(ledger_path)

    if args.verify:
        bad = []
        for row in ledger:
            p = root / row["stored_path"]
            if not p.exists():
                bad.append({"evidence_id": row["evidence_id"], "problem": "ファイルがない", "path": row["stored_path"]})
            elif sha256(p) != row["sha256"]:
                bad.append({"evidence_id": row["evidence_id"], "problem": "ハッシュ不一致（改変の可能性）", "path": row["stored_path"]})
        dump({"checked": len(ledger), "problems": bad})
        return 1 if bad else 0

    if not (args.sender and args.received_at and args.files):
        sys.exit("--sender、--received-at と取り込むファイルを指定してください")

    known = {r["sha256"]: r for r in ledger}
    day = re.sub(r"\D", "", args.received_at)[:8]
    dest_dir = root / "01_受領資料" / f"{day}_{safe(args.sender)}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    added, duplicates = [], []
    next_no = len(ledger) + 1
    for f in args.files:
        src = Path(f)
        if not src.is_file():
            sys.exit(f"ファイルがありません: {src}")
        digest = sha256(src)
        if digest in known:
            duplicates.append({"file": src.name, "same_as": known[digest]["evidence_id"]})
            continue
        dest = dest_dir / src.name
        n = 2
        while dest.exists():
            dest = dest_dir / f"{src.stem}_{n}{src.suffix}"
            n += 1
        shutil.copy2(src, dest)
        row = {
            "evidence_id": f"EV{next_no:04d}",
            "received_at": args.received_at,
            "sender": args.sender,
            "mail_id": args.mail_id,
            "original_name": src.name,
            "stored_path": str(dest.relative_to(root)),
            "sha256": digest,
            "bytes": dest.stat().st_size,
            "note": args.note,
        }
        next_no += 1
        known[digest] = row
        ledger.append(row)
        added.append(row)

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_COLS)
        w.writeheader()
        w.writerows(ledger)
    dump({"added": added, "duplicates": duplicates, "ledger": str(ledger_path.relative_to(root))})
    return 0


if __name__ == "__main__":
    sys.exit(main())
