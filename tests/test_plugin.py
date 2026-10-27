"""scripts と hooks の動作確認。python3 -m unittest discover -s tests"""
import csv
import json
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

    def test_summarize(self):
        wp = {"area_id": "revenue", "area_name": "売上高", "status": "pass", "rounds": [{"round": 1, "score": 90}],
              "misstatements": [{"id": "M1", "amount": 2500000, "type": "事実", "corrected": False, "accounts": ["4110"]}]}
        (self.eng / "03_調書" / "revenue.json").write_text(json.dumps(wp, ensure_ascii=False), encoding="utf-8")
        c, out, err = run(S / "summarize.py", "--engagement", self.eng, "--today", TODAY, "--xlsx")
        self.assertEqual(c, 1, err)  # 手続上の重要性超え → 人の判断
        res = json.loads(out)
        self.assertTrue(res["judgements_needed"])
        self.assertTrue((self.eng / res["xlsx"]).exists())

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

    def test_audit_log(self):
        payload = {"tool_name": "Read", "tool_input": {"file_path": "x"}, "tool_response": "ok", "cwd": str(self.eng)}
        run(H / "audit_log.py", stdin=json.dumps(payload))
        self.assertTrue((self.eng / "06_ログ" / "tool_log.jsonl").read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    unittest.main()
