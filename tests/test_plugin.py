"""scripts と hooks の動作確認。python3 -m unittest discover -s tests"""
import csv
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "tadori"
S = PLUGIN / "scripts"
H = PLUGIN / "hooks"
TODAY = "2026-10-27"


def run(script: Path, *args, stdin=None, cwd=None):
    p = subprocess.run([sys.executable, str(script), *map(str, args)], input=stdin,
                       capture_output=True, text=True, cwd=cwd)
    return p.returncode, p.stdout, p.stderr


def write_csv(path: Path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


GL_HEADER = ["date", "je_id", "line_no", "account_code", "account_name", "debit", "credit",
             "description", "entered_by", "entered_at", "source", "counterparty"]
GL_ROWS = [
    ["2026-01-15", "J1", 1, "1130", "売掛金", "1,100,000", "", "1月売上 A社", "tanaka", "2026-01-15", "auto", "A社"],
    ["2026-01-15", "J1", 2, "4110", "売上高", "", "1,100,000", "1月売上 A社", "tanaka", "2026-01-15", "auto", "A社"],
    ["2026-03-28", "J2", 1, "1130", "売掛金", "5,000,000", "", "売上 調整", "sato", "2026-04-25", "manual", "B社"],
    ["2026-03-28", "J2", 2, "4110", "売上高", "", "5,000,000", "売上 調整", "sato", "2026-04-25", "manual", "B社"],
    ["2026-02-10", "J3", 1, "1110", "普通預金", "300,000", "", "A社入金", "tanaka", "2026-02-10", "auto", "A社"],
    ["2026-02-10", "J3", 2, "1130", "売掛金", "", "300,000", "A社入金", "tanaka", "2026-02-10", "auto", "A社"],
]
TB_HEADER = ["account_code", "account_name", "opening", "debit", "credit", "closing"]
TB_ROWS = [
    ["1110", "普通預金", "1,000,000", "300,000", "0", "1,300,000"],
    ["1130", "売掛金", "0", "6,100,000", "300,000", "5,800,000"],
    ["4110", "売上高", "0", "0", "6,100,000", "-6,100,000"],
]


class PluginTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        code, out, err = run(S / "engagement.py", "new", "--root", self.tmp, "--client", "テスト商事",
                             "--period-end", "2026-03-31", "--engagement-type", "任意監査", "--today", TODAY)
        self.assertEqual(code, 0, err)
        self.eng = self.tmp / "テスト商事_2026-03期"
        nd = self.eng / "02_正規化データ"
        write_csv(nd / "仕訳帳.csv", GL_HEADER, GL_ROWS)
        write_csv(nd / "試算表.csv", TB_HEADER, TB_ROWS)
        cfg = json.loads((self.eng / "00_案件情報" / "engagement.json").read_text(encoding="utf-8"))
        cfg["contacts"] = [{"key": "keiri", "role": "経理部", "name": "田中", "email": "tanaka@client.example"}]
        cfg["auditor_domains"] = ["audit.example"]
        cfg["materiality"].update(overall=3000000, performance=2000000, trivial=150000)
        cfg["evidence_deadline"] = "2026-11-20"
        cfg["mail"]["signature"] = "監査チーム 川島"
        (self.eng / "00_案件情報" / "engagement.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        (self.eng / "00_案件情報" / "plan.json").write_text('{"version":1,"areas":[]}', encoding="utf-8")

    def approve(self):
        code, _, err = run(S / "engagement.py", "approve", "--engagement", self.eng, "--by", "川島", "--today", TODAY)
        self.assertEqual(code, 0, err)

    # ---- scripts ----
    def test_engagement_structure_and_ledger(self):
        for d in ["00_案件情報", "01_受領資料", "02_正規化データ", "03_調書", "04_依頼管理", "05_成果物", "06_ログ"]:
            self.assertTrue((self.eng / d).is_dir(), d)
        self.assertTrue((self.tmp / "案件台帳.xlsx").exists())
        code, _, _ = run(S / "engagement.py", "new", "--root", self.tmp, "--client", "テスト商事",
                         "--period-end", "2026-03-31", "--today", TODAY)
        self.assertNotEqual(code, 0, "同名フォルダは作らない")

    def test_recon_match_and_mismatch(self):
        nd = self.eng / "02_正規化データ"
        code, out, err = run(S / "recon.py", "--tb", nd / "試算表.csv", "--gl", nd / "仕訳帳.csv")
        self.assertEqual(code, 0, out + err)
        rows = [r[:] for r in TB_ROWS]
        rows[1][3] = "6,000,000"
        write_csv(nd / "試算表_誤.csv", TB_HEADER, rows)
        code, out, _ = run(S / "recon.py", "--tb", nd / "試算表_誤.csv", "--gl", nd / "仕訳帳.csv")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["account_differences"][0]["account_code"], "1130")

    def test_je_filter(self):
        code, out, err = run(S / "je_filter.py", "--gl", self.eng / "02_正規化データ" / "仕訳帳.csv",
                             "--period-end", "2026-03-31", "--close-date", "2026-04-20")
        self.assertEqual(code, 0, err)
        res = json.loads(out)
        top = res["hits"][0]
        self.assertEqual(top["je_id"], "J2")
        for r in ["weekend", "period_end", "post_close", "round_amount", "manual"]:
            self.assertIn(r, top["reasons"])

    def test_je_filter_entered_at_without_seconds(self):
        for entered_at in ["2026-04-25T10:05", "2026/04/25 10:05", "2026-04-25 10:05"]:
            rows = [r[:] for r in GL_ROWS]
            for r in rows:
                if r[1] == "J2":
                    r[9] = entered_at
            gl = self.eng / "02_正規化データ" / "仕訳帳_秒なし.csv"
            write_csv(gl, GL_HEADER, rows)
            code, out, err = run(S / "je_filter.py", "--gl", gl, "--period-end", "2026-03-31", "--close-date", "2026-04-20")
            self.assertEqual(code, 0, err)
            top = json.loads(out)["hits"][0]
            self.assertIn("post_close", top["reasons"], entered_at)

    def test_mus_deterministic(self):
        pop = self.tmp / "pop.csv"
        write_csv(pop, ["id", "balance"], [[f"C{i:03d}", str(10000 * (i % 37 + 1))] for i in range(200)] + [["BIG", "9,000,000"]])
        args = ["--population", pop, "--amount-col", "balance", "--id-col", "id", "--tolerable", "3000000", "--seed", "42"]
        a = json.loads(run(S / "sample_mus.py", *args)[1])
        b = json.loads(run(S / "sample_mus.py", *args)[1])
        self.assertEqual(a["sampled_items"], b["sampled_items"])
        self.assertEqual(a["key_items"][0]["id"], "BIG")
        self.assertGreater(len(a["sampled_items"]), 0)

    def test_hash_evidence_import_duplicate_verify(self):
        f = self.tmp / "残高表.csv"
        write_csv(f, ["customer_code", "balance"], [["A", "800,000"], ["B", "5,000,000"]])
        code, out, err = run(S / "hash_evidence.py", "--engagement", self.eng, "--sender", "経理部 田中",
                             "--received-at", "2026-10-27T14:30", f)
        self.assertEqual(code, 0, err)
        added = json.loads(out)["added"][0]
        self.assertEqual(added["evidence_id"], "EV0001")
        code, out, _ = run(S / "hash_evidence.py", "--engagement", self.eng, "--sender", "経理部 田中",
                           "--received-at", "2026-10-28T09:00", f)
        self.assertEqual(json.loads(out)["duplicates"][0]["same_as"], "EV0001")
        self.assertEqual(run(S / "hash_evidence.py", "--engagement", self.eng, "--verify")[0], 0)
        stored = self.eng / added["stored_path"]
        stored.write_text("tampered", encoding="utf-8")
        self.assertEqual(run(S / "hash_evidence.py", "--engagement", self.eng, "--verify")[0], 1)

    def test_evidence_check(self):
        f = self.tmp / "残高表.csv"
        write_csv(f, ["customer_code", "balance"], [["A", "800,000"], ["B", "5,000,000"]])
        tb = self.eng / "02_正規化データ" / "試算表.csv"
        code, out, _ = run(S / "evidence_check.py", "--file", f, "--amount-col", "balance", "--tb", tb, "--account", "1130")
        self.assertEqual(code, 0, out)
        write_csv(f, ["customer_code", "balance"], [["A", "800,000"], ["B", "8,000,000"]])
        code, out, _ = run(S / "evidence_check.py", "--file", f, "--amount-col", "balance", "--tb", tb, "--account", "1130")
        self.assertEqual(code, 1)

    def test_subledger_match(self):
        nd = self.eng / "02_正規化データ"
        code, out, err = run(S / "subledger_match.py", "--gl", nd / "仕訳帳.csv", "--account", "1130", "--tb", nd / "試算表.csv")
        self.assertEqual(code, 0, err)
        res = json.loads(out)
        self.assertEqual(res["open_total"], "5800000")  # = 試算表の期末残高
        self.assertTrue(res["tb"]["open_total_equals_closing"])
        a = next(c for c in res["by_counterparty"] if c["counterparty"] == "A社")
        self.assertEqual(a["open_items"][0]["remaining"], "800000")  # 1,100,000 のうち 300,000 を消込
        # 計上より前の入金は消し込まず「対応する計上なし」にする
        rows = [r[:] for r in GL_ROWS]
        rows[4][0] = rows[5][0] = "2026-01-10"
        write_csv(nd / "仕訳帳_先入金.csv", GL_HEADER, rows)
        code, out, _ = run(S / "subledger_match.py", "--gl", nd / "仕訳帳_先入金.csv", "--account", "1130", "--tb", nd / "試算表.csv")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["unmatched_decrease_total"], "300000")

    def test_je_sequence(self):
        code, out, err = run(S / "je_sequence.py", "--gl", self.eng / "02_正規化データ" / "仕訳帳.csv", "--lag-days", "7")
        self.assertEqual(code, 0, err)
        res = json.loads(out)
        self.assertEqual(res["journal_entries"], 3)
        self.assertEqual(res["gaps"]["count"], 0)
        self.assertEqual([x["je_id"] for x in res["date_reversals"]["items"]], ["J3"])  # J2 3/28 → J3 2/10
        self.assertEqual([x["je_id"] for x in res["entered_reversals"]["items"]], ["J3"])
        self.assertEqual([x["je_id"] for x in res["entry_lag"]["late"]], ["J2"])  # 3/28 計上、4/25 入力
        rows = [r for r in GL_ROWS if r[1] != "J2"]
        rows[1][9] = "2026-01-20"
        write_csv(self.tmp / "gl_gap.csv", GL_HEADER, rows)
        res = json.loads(run(S / "je_sequence.py", "--gl", self.tmp / "gl_gap.csv")[1])
        self.assertEqual(res["gaps"]["ids"], ["J2"])
        self.assertEqual(res["mixed_entered_at"]["items"][0]["je_id"], "J1")

    def test_monthly(self):
        csv_out = self.eng / "03_調書" / "revenue_work" / "月別.csv"
        code, out, err = run(S / "monthly.py", "--gl", self.eng / "02_正規化データ" / "仕訳帳.csv", "--accounts", "4110",
                             "--csv", csv_out)
        self.assertEqual(code, 0, err)
        res = json.loads(out)
        self.assertEqual(res["months"], ["2026-01", "2026-03"])
        months = res["series"][0]["months"]
        self.assertEqual((months["2026-01"]["credit"], months["2026-03"]["credit"]), ("1100000", "5000000"))
        self.assertEqual(res["series"][0]["total"]["net"], "-6100000")
        self.assertTrue(csv_out.exists())

    def test_pbc_merge_due_report(self):
        req = {"area": "売上", "doc_type": "得意先別売掛金残高表", "period": "2026-03-31時点",
               "accounts": ["1130"], "procedure": "REV-02", "reason": "母集団", "to": "keiri"}
        c, out, err = run(S / "pbc.py", "add", "--engagement", self.eng, "--today", TODAY, "--json", json.dumps(req, ensure_ascii=False))
        self.assertEqual(json.loads(out)["action"], "created", err)
        req2 = dict(req, area="売掛金", doc_type="得意先別 売掛金残高表")
        c, out, _ = run(S / "pbc.py", "add", "--engagement", self.eng, "--today", TODAY, "--json", json.dumps(req2, ensure_ascii=False))
        self.assertEqual(json.loads(out)["action"], "merged")
        bad = dict(req, to="unknown", doc_type="別資料")
        self.assertNotEqual(run(S / "pbc.py", "add", "--engagement", self.eng, "--today", TODAY, "--json", json.dumps(bad))[0], 0)
        # 書き方が違う同じ資料は same_as で既存の依頼に寄せる
        req3 = dict(req, area="仕訳テスト", doc_type="得意先ごとの売掛金の残高一覧", period="期末日", same_as="PBC0001")
        c, out, err = run(S / "pbc.py", "add", "--engagement", self.eng, "--today", TODAY, "--json", json.dumps(req3, ensure_ascii=False))
        self.assertEqual(json.loads(out)["areas"], ["仕訳テスト", "売上", "売掛金"], err)
        self.assertNotEqual(run(S / "pbc.py", "add", "--engagement", self.eng, "--today", TODAY,
                                "--json", json.dumps(dict(req3, same_as="PBC0099"), ensure_ascii=False))[0], 0)
        self.assertEqual(len(json.loads(run(S / "pbc.py", "list", "--engagement", self.eng)[1])), 1)
        run(S / "pbc.py", "mark", "--engagement", self.eng, "--id", "PBC0001", "--status", "sent", "--today", "2026-10-27")
        due = json.loads(run(S / "pbc.py", "due", "--engagement", self.eng, "--today", "2026-10-30")[1])
        self.assertEqual(due["remind"][0]["id"], "PBC0001")  # 3営業日経過
        run(S / "pbc.py", "mark", "--engagement", self.eng, "--id", "PBC0001", "--status", "reminded", "--today", "2026-10-30")
        due = json.loads(run(S / "pbc.py", "due", "--engagement", self.eng, "--today", "2026-11-04")[1])
        self.assertEqual(due["escalate"][0]["id"], "PBC0001")
        c, out, err = run(S / "pbc.py", "report", "--engagement", self.eng)
        self.assertEqual(c, 0, err)
        self.assertTrue((self.eng / "04_依頼管理" / "依頼トラッカー.xlsx").exists())

    def test_approve_requires_materiality(self):
        cfg_p = self.eng / "00_案件情報" / "engagement.json"
        cfg = json.loads(cfg_p.read_text(encoding="utf-8"))
        cfg["materiality"]["overall"] = None
        cfg_p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        self.assertNotEqual(run(S / "engagement.py", "approve", "--engagement", self.eng, "--by", "x", "--today", TODAY)[0], 0)

    def test_approve_requires_deadline_and_signature(self):
        # 依頼メールの期限と署名が空のまま承認すると、承認後は書き換えられず「期限未設定」のメールになる
        cfg_p = self.eng / "00_案件情報" / "engagement.json"
        base = json.loads(cfg_p.read_text(encoding="utf-8"))
        for key, value in [("evidence_deadline", ""), ("evidence_deadline", "11月末"), ("signature", "  ")]:
            cfg = json.loads(json.dumps(base))
            if key == "signature":
                cfg["mail"]["signature"] = value
            else:
                cfg[key] = value
            cfg_p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
            code, _, err = run(S / "engagement.py", "approve", "--engagement", self.eng, "--by", "x", "--today", TODAY)
            self.assertNotEqual(code, 0, f"{key}={value!r}")
        cfg_p.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
        self.approve()

    def ledger_rows(self, folder: Path) -> list[list]:
        try:
            import openpyxl  # noqa: PLC0415
        except ImportError:  # openpyxl が無い環境では台帳は CSV になる
            with open(folder / "案件台帳.csv", encoding="utf-8-sig", newline="") as f:
                return [[v or None for v in row] for row in list(csv.reader(f))[1:]]
        return [[c.value for c in r] for r in openpyxl.load_workbook(folder / "案件台帳.xlsx").active.iter_rows(min_row=2)]

    def test_ledger_with_relative_engagement_path(self):
        # 案件フォルダの中から「.」やサブフォルダを渡しても、親フォルダの台帳の該当行を更新する
        code, _, err = run(S / "engagement.py", "approve", "--engagement", ".", "--by", "川島", "--today", TODAY, cwd=self.eng)
        self.assertEqual(code, 0, err)
        code, _, err = run(S / "engagement.py", "status", "--engagement", "03_調書", "--status", "実行中（pass 0 / 全 9）",
                           "--today", TODAY, cwd=self.eng)
        self.assertEqual(code, 0, err)
        for name in ("案件台帳.xlsx", "案件台帳.csv"):
            self.assertFalse((self.eng / name).exists(), "案件フォルダの中に台帳を作らない")
        rows = self.ledger_rows(self.tmp)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "テスト商事_2026-03期")
        self.assertEqual(rows[0][4], "実行中（pass 0 / 全 9）")
        self.assertEqual(rows[0][5], f"{TODAY} 川島")
        self.assertNotEqual(run(S / "engagement.py", "status", "--engagement", self.tmp, "--status", "x", "--today", TODAY)[0], 0,
                            "案件フォルダでない場所には書かない")

    def test_scripts_run_in_isolated_mode(self):
        # python3 -I（スクリプトのフォルダを import 先に足さない）でも共通モジュールを読める
        for script in sorted(S.glob("*.py")):
            p = subprocess.run([sys.executable, "-I", str(script), "--help"], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, f"{script.name}: {p.stderr}")

    def test_summarize(self):
        wp = {"area_id": "revenue", "area_name": "売上高", "status": "pass", "rounds": [{"round": 1, "score": 90}],
              "misstatements": [{"id": "M1", "amount": 2500000, "type": "事実", "corrected": False, "accounts": ["4110"]}]}
        (self.eng / "03_調書" / "revenue.json").write_text(json.dumps(wp, ensure_ascii=False), encoding="utf-8")
        c, out, err = run(S / "summarize.py", "--engagement", self.eng, "--today", TODAY, "--xlsx")
        self.assertEqual(c, 1, err)  # 手続上の重要性超え → 人の判断
        res = json.loads(out)
        self.assertTrue(res["judgements_needed"])
        self.assertTrue((self.eng / res["xlsx"]).exists())

    # ---- state ----
    def state(self, *args, today=TODAY):
        c, out, err = run(S / "state.py", *args, "--engagement", self.eng, "--today", today)
        return c, (json.loads(out) if out.strip() else {}), err

    def set_plan(self, *areas):
        plan = {"version": 1, "areas": [{"area_id": a, "area_name": a, "procedures": []} for a in areas]}
        (self.eng / "00_案件情報" / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    def write_wp(self, area, done=True, blocked=None):
        """executor が書く想定の調書。状態の項目は残したまま、手続と不足資料だけを書き換える。"""
        p = self.eng / "03_調書" / f"{area}.json"
        wp = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"area_id": area, "status": "draft", "rounds": []}
        wp["procedures"] = [{"proc_id": "P-01", "status": "完了" if done else "未了"}]
        wp["blocked"] = blocked or []
        p.write_text(json.dumps(wp, ensure_ascii=False), encoding="utf-8")

    def read_wp(self, area):
        return json.loads((self.eng / "03_調書" / f"{area}.json").read_text(encoding="utf-8"))

    def write_review(self, area, rnd, score, hard=0, needs_human=()):
        rv = {"area_id": area, "round": rnd, "score": score, "needs_human": list(needs_human),
              "hard_fails": [{"check": "H1", "where": "P-01", "detail": "x"}] * hard, "issues": []}
        (self.eng / "03_調書" / f"{area}.review.json").write_text(json.dumps(rv, ensure_ascii=False), encoding="utf-8")

    def test_state_next_walks_the_engagement(self):
        self.assertEqual(run(S / "state.py", "next", "--engagement", self.tmp, "--today", TODAY)[1].count('"A"'), 1)
        tb = self.eng / "02_正規化データ" / "試算表.csv"
        tb.rename(tb.with_suffix(".bak"))
        self.assertEqual(self.state("next")[1]["state"], "B")
        tb.with_suffix(".bak").rename(tb)
        self.set_plan("revenue", "cash")
        self.assertEqual(self.state("next")[1]["state"], "C")
        self.approve()
        res = self.state("next")[1]
        self.assertEqual((res["state"], res["run"]), ("D", ["revenue", "cash"]))
        # revenue は1周で合格、cash は資料が無く手続を実施できない
        self.write_wp("revenue")
        self.assertEqual(self.state("after-execute", "--area", "revenue")[1]["decision"], "review")
        self.write_review("revenue", 1, 90)
        self.assertEqual(self.state("review-step", "--area", "revenue")[1]["decision"], "pass")
        self.write_wp("cash", done=False, blocked=[{"doc_type": "残高証明書", "pbc": ""}])
        self.assertEqual(self.state("after-execute", "--area", "cash")[1]["decision"], "blocked")
        self.assertEqual(self.state("next")[1]["queue"], ["cash"], "依頼キューに入れていない不足資料")
        req = {"area": "cash", "doc_type": "残高証明書", "period": "2026-03-31", "reason": "残高の照合", "to": "keiri"}
        run(S / "pbc.py", "add", "--engagement", self.eng, "--today", TODAY, "--json", json.dumps(req, ensure_ascii=False))
        wp = self.read_wp("cash")
        wp["blocked"][0]["pbc"] = "PBC0001"
        (self.eng / "03_調書" / "cash.json").write_text(json.dumps(wp, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.state("next")[1]["state"], "E")
        run(S / "pbc.py", "mark", "--engagement", self.eng, "--id", "PBC0001", "--status", "sent", "--today", TODAY)
        res = self.state("next")[1]
        self.assertEqual((res["state"], res["keep_watching"]), ("F", True))
        run(S / "pbc.py", "mark", "--engagement", self.eng, "--id", "PBC0001", "--status", "accepted", "--today", TODAY)
        self.assertEqual(self.state("next")[1]["rerun"], ["cash"])
        c, res, err = self.state("set-status", "--area", "cash", "--to", "draft")
        self.assertEqual(c, 0, res)
        self.assertEqual(self.read_wp("cash")["rerun_pbc"], ["PBC0001"])
        self.write_wp("cash")
        self.state("after-execute", "--area", "cash")
        self.write_review("cash", 1, 88)
        self.assertEqual(self.state("review-step", "--area", "cash")[1]["decision"], "pass")
        res = self.state("next")[1]
        self.assertEqual((res["state"], res["keep_watching"]), ("G", False))
        run(S / "engagement.py", "status", "--engagement", self.eng, "--status", "最終査閲待ち", "--today", TODAY)
        self.assertEqual(self.state("next")[1]["state"], "H")

    def test_state_next_not_requested_documents(self):
        self.set_plan("cash")
        self.approve()
        self.write_wp("cash", done=False, blocked=[{"doc_type": "銀行確認状の回答", "pbc": "ESC"}])
        self.state("after-execute", "--area", "cash")
        res = self.state("next")[1]
        self.assertEqual((res["state"], res["keep_watching"]), ("F", False), "人が扱う資料だけを待つなら見張らない")

    def test_state_review_loop_decisions(self):
        self.write_wp("revenue")
        self.state("after-execute", "--area", "revenue")
        self.write_review("revenue", 1, 70, hard=1, needs_human=[{"issue": "期間帰属の判断", "why": "x"}])
        self.assertEqual(self.state("review-step", "--area", "revenue")[1]["decision"], "fix")
        again = self.state("review-step", "--area", "revenue")[1]
        self.assertTrue(again["already_recorded"], "同じ査閲結果は二重に数えない")
        self.assertEqual(len(self.read_wp("revenue")["rounds"]), 1)
        self.assertEqual(self.read_wp("revenue")["escalations"][0]["issue"], "期間帰属の判断")
        self.write_review("revenue", 2, 70)
        res = self.state("review-step", "--area", "revenue")[1]
        self.assertEqual((res["decision"], res["reason"]), ("escalate", "改善が止まった"))
        # 最大周回（2周に絞る）と、ループ中に不足資料が増えた場合
        cfg = json.loads((self.eng / "00_案件情報" / "engagement.json").read_text(encoding="utf-8"))
        cfg["loop"]["max_rounds"] = 2
        (self.eng / "00_案件情報" / "engagement.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        self.write_wp("cash")
        self.state("after-execute", "--area", "cash")
        for rnd, score in [(1, 60), (2, 75)]:
            self.write_review("cash", rnd, score)
            res = self.state("review-step", "--area", "cash")[1]
        self.assertEqual((res["decision"], res["reason"]), ("escalate", "最大周回"))
        self.write_wp("ar")
        self.state("after-execute", "--area", "ar")
        self.write_wp("ar", blocked=[{"doc_type": "残高確認の回答", "pbc": ""}])
        self.write_review("ar", 1, 95)
        self.assertEqual(self.state("review-step", "--area", "ar")[1]["decision"], "blocked")

    def test_state_set_status_rules(self):
        self.write_wp("revenue")
        self.state("after-execute", "--area", "revenue")
        self.assertEqual(self.state("set-status", "--area", "revenue", "--to", "pass")[0], 1, "査閲の記録が無い")
        self.assertEqual(self.state("set-status", "--area", "revenue", "--to", "escalate", "--reason", "人の判断")[0], 1)
        self.assertEqual(self.state("set-status", "--area", "revenue", "--to", "escalate", "--reason", "なんとなく")[0], 1)
        self.write_review("revenue", 1, 92)
        self.state("review-step", "--area", "revenue")
        self.assertEqual(self.state("set-status", "--area", "revenue", "--to", "blocked")[0], 1, "pass から blocked へは移れない")
        self.assertEqual(self.state("set-status", "--area", "revenue", "--to", "draft")[0], 1, "理由なしでは戻せない")
        c, res, _ = self.state("set-status", "--area", "revenue", "--to", "draft", "--reason", "skeptic の反証で再実行")
        self.assertEqual(c, 0, res)
        wp = self.read_wp("revenue")
        self.assertEqual((wp["status"], wp["loop_start"]), ("draft", 1))
        self.assertEqual([h["to"] for h in wp["status_history"]], ["pass", "draft"])

    def test_state_charge_budget(self):
        cfg = json.loads((self.eng / "00_案件情報" / "engagement.json").read_text(encoding="utf-8"))
        cfg["budget"].update(max_agent_calls_per_area=2, max_agent_calls_total=2)
        (self.eng / "00_案件情報" / "engagement.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        self.set_plan("revenue", "cash")
        self.approve()
        self.write_wp("revenue")
        for _ in range(2):
            self.assertEqual(self.state("charge", "--area", "revenue", "--agent", "reviewer")[0], 0)
        c, res, _ = self.state("charge", "--area", "revenue", "--agent", "fixer")
        self.assertEqual(c, 1)
        self.assertIn("予算超過", res["error"])
        self.assertEqual((self.read_wp("revenue")["status"], self.read_wp("revenue")["status_reason"]), ("escalate", "予算超過"))
        res = self.state("next")[1]  # cash は未実行だが、全体の予算も使い切った
        self.assertEqual((res["action"], res["keep_watching"]), ("ask-human", False))

    def test_workflow_run_areas_uses_state_py(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node が無い")
        wf_args = {"engagement": str(self.eng), "scripts": str(S), "today": TODAY, "areas": ["revenue", "cash", "ar"]}
        scores = {"revenue": [70, 90], "cash": "blocked", "ar": [60, 55]}
        p = subprocess.run([node, str(Path(__file__).parent / "workflow_harness.mjs"), str(PLUGIN / "workflows" / "run-areas.js"),
                            json.dumps(wf_args, ensure_ascii=False), json.dumps(scores)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        res = json.loads(p.stdout)
        got = {a["area"]: (a["status"], a.get("reason")) for a in res["result"]["areas"]}
        self.assertEqual(got, {"revenue": ("pass", "2周目で合格"), "cash": ("blocked", "受領済みの資料では手続を実施できない"),
                               "ar": ("escalate", "改善が止まった")})
        self.assertEqual([r["score"] for r in self.read_wp("revenue")["rounds"]], [70, 90])
        self.assertEqual(self.read_wp("revenue")["conclusion"][:2], "修正", "fixer が1回入った")
        budget = json.loads((self.eng / "03_調書" / "_budget.json").read_text(encoding="utf-8"))
        self.assertEqual(budget["areas"], {"revenue": 4, "cash": 1, "ar": 4})  # executor + reviewer×2 + fixer×1

    # ---- hooks ----
    def hook(self, script, tool, tool_input, cwd=None):
        payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd or self.eng)}
        return run(H / script, stdin=json.dumps(payload, ensure_ascii=False))

    def test_guard_raw_documents(self):
        code, _, err = self.hook("guard.py", "Write", {"file_path": str(self.eng / "01_受領資料" / "x.csv")})
        self.assertEqual(code, 2)
        self.assertIn("01_受領資料", err)
        self.assertEqual(self.hook("guard.py", "Bash", {"command": "rm -rf 01_受領資料/20261027_x"})[0], 2)
        self.assertEqual(self.hook("guard.py", "Bash", {"command": "ls 01_受領資料"})[0], 0)
        self.assertEqual(self.hook("guard.py", "Write", {"file_path": str(self.eng / "03_調書" / "revenue.json")})[0], 0)

    def test_guard_workpaper_state_only_via_state_py(self):
        p = self.eng / "03_調書" / "revenue.json"
        fresh = {"area_id": "revenue", "procedures": [], "status": "draft", "rounds": []}
        self.assertEqual(self.hook("guard.py", "Write", {"file_path": str(p), "content": json.dumps(fresh)})[0], 0)
        code, _, err = self.hook("guard.py", "Write", {"file_path": str(p), "content": json.dumps(dict(fresh, status="pass"))})
        self.assertEqual(code, 2, "初めて書く調書を pass にはできない")
        self.assertIn("state.py", err)
        p.write_text(json.dumps(fresh, ensure_ascii=False, indent=2), encoding="utf-8")
        self.state("after-execute", "--area", "revenue")  # 手続が無いので止まる（状態は変わらない）
        self.write_wp("revenue")
        self.state("after-execute", "--area", "revenue")
        self.write_review("revenue", 1, 60)
        self.state("review-step", "--area", "revenue")
        cur = p.read_text(encoding="utf-8")
        wp = json.loads(cur)
        # 状態の項目を残したまま、手続や結論を直すのはよい
        ok = dict(wp, conclusion="修正後の結論")
        self.assertEqual(self.hook("guard.py", "Write", {"file_path": str(p), "content": json.dumps(ok, ensure_ascii=False)})[0], 0)
        # rounds を消す・status を書き換える・点数を書き換える
        for bad in (dict(wp, rounds=[]), dict(wp, status="pass"),
                    dict(wp, rounds=[dict(wp["rounds"][0], score=95)])):
            self.assertEqual(self.hook("guard.py", "Write", {"file_path": str(p), "content": json.dumps(bad, ensure_ascii=False)})[0], 2)
        self.assertEqual(self.hook("guard.py", "Edit", {"file_path": str(p), "old_string": '"status": "draft"',
                                                        "new_string": '"status": "pass"'})[0], 2)
        self.assertEqual(self.hook("guard.py", "Edit", {"file_path": str(p), "old_string": '"area_id": "revenue"',
                                                        "new_string": '"area_id": "revenue", "confidence": "中"'})[0], 0)
        self.assertEqual(self.hook("guard.py", "Write", {"file_path": str(self.eng / "03_調書" / "_budget.json"),
                                                         "content": "{}"})[0], 2)
        # レビュー結果と作業フォルダは対象外
        self.assertEqual(self.hook("guard.py", "Write", {"file_path": str(self.eng / "03_調書" / "revenue.review.json"),
                                                         "content": '{"status": "pass"}'})[0], 0)
        self.assertEqual(self.hook("guard.py", "Write", {"file_path": str(self.eng / "03_調書" / "revenue_work" / "a.json"),
                                                         "content": '{"status": "pass"}'})[0], 0)
        py = "python3 -c \"import json,pathlib;pathlib.Path('03_調書/revenue.json').write_text('{}')\""
        self.assertEqual(self.hook("guard.py", "Bash", {"command": py})[0], 2)
        self.assertEqual(self.hook("guard.py", "Bash", {"command": "jq . 03_調書/revenue.json"})[0], 0)
        self.assertEqual(self.hook("guard.py", "Bash", {"command": "python3 $S/state.py review-step --area revenue"})[0], 0)

    def test_guard_plan_locked_after_approval(self):
        plan = str(self.eng / "00_案件情報" / "plan.json")
        self.assertEqual(self.hook("guard.py", "Edit", {"file_path": plan})[0], 0)
        self.approve()
        self.assertEqual(self.hook("guard.py", "Edit", {"file_path": plan})[0], 2)
        self.assertEqual(self.hook("guard.py", "Bash", {"command": "sed -i s/a/b/ 00_案件情報/plan.json"})[0], 2)

    def test_guard_cross_engagement(self):
        run(S / "engagement.py", "new", "--root", self.tmp, "--client", "別会社", "--period-end", "2026-03-31", "--today", TODAY)
        other = self.tmp / "別会社_2026-03期" / "03_調書" / "x.json"
        code, _, err = self.hook("guard.py", "Write", {"file_path": str(other)})
        self.assertEqual(code, 2)
        self.assertIn("別の案件フォルダ", err)

    def test_mail_guard(self):
        send = "mcp__Gmail__send_message"
        draft = "mcp__Gmail__create_draft"
        ok_to = {"to": ["tanaka@client.example"], "subject": "s", "body": "b"}
        self.assertEqual(self.hook("mail_guard.py", draft, ok_to)[0], 2, "計画未承認")
        self.approve()
        self.assertEqual(self.hook("mail_guard.py", draft, ok_to)[0], 0)
        self.assertEqual(self.hook("mail_guard.py", send, ok_to)[0], 2, "下書き運用中")
        self.assertEqual(self.hook("mail_guard.py", draft, {"to": "evil@other.example"})[0], 2)
        self.assertEqual(self.hook("mail_guard.py", draft, {"to": "me@audit.example"})[0], 0)
        self.assertEqual(self.hook("mail_guard.py", "mcp__Gmail__forward", {"to": "tanaka@client.example"})[0], 2)
        self.assertEqual(self.hook("mail_guard.py", "mcp__Gmail__search_threads", {"q": "x"})[0], 0)
        self.assertEqual(self.hook("mail_guard.py", "mcp__Slack__slack_send_message", {"channel": "x"})[0], 0)
        self.assertEqual(self.hook("mail_guard.py", send, ok_to, cwd=self.tmp)[0], 0, "案件外では働かない")

    def test_stop_check_nudges_once_when_run_is_unfinished(self):
        def stop(active=False, cwd=None):
            payload = {"cwd": str(cwd or self.eng), "stop_hook_active": active}
            code, out, err = run(H / "stop_check.py", stdin=json.dumps(payload))
            self.assertEqual(code, 0, err)
            return json.loads(out) if out.strip() else None

        self.write_wp("revenue")
        self.assertIsNone(stop(), "計画承認前は働かない")
        self.approve()
        res = stop()
        self.assertEqual(res["decision"], "block")
        self.assertIn("revenue：status が draft", res["reason"])
        self.assertIsNone(stop(active=True), "促すのは1回だけ")
        self.assertIsNone(stop(cwd=self.tmp), "案件外では働かない")
        self.state("after-execute", "--area", "revenue")
        self.write_review("revenue", 1, 90)
        self.assertIn("review-step", stop()["reason"])
        self.state("review-step", "--area", "revenue")
        self.assertIsNone(stop(), "pass になれば止めない")
        self.write_wp("cash", done=False, blocked=[{"doc_type": "残高証明書", "pbc": ""}])
        self.state("after-execute", "--area", "cash")
        self.assertIsNone(stop(), "資料待ち（blocked）では止めない")

    def test_audit_log(self):
        payload = {"tool_name": "Read", "tool_input": {"file_path": "x"}, "tool_response": "ok", "cwd": str(self.eng)}
        run(H / "audit_log.py", stdin=json.dumps(payload))
        self.assertTrue((self.eng / "06_ログ" / "tool_log.jsonl").read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    unittest.main()
