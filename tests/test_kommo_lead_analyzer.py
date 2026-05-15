import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kommo_lead_analyzer import analyze, extract_leads, extract_messages, parse_timestamp


class KommoLeadAnalyzerTest(unittest.TestCase):
    def test_parse_timestamp_accepts_iso_and_milliseconds(self):
        self.assertEqual(parse_timestamp("1970-01-01T00:01:00Z"), 60)
        self.assertEqual(parse_timestamp(60_000), 60000)
        self.assertEqual(parse_timestamp(1_700_000_000_000), 1_700_000_000)

    def test_analyze_prioritizes_unanswered_buying_signal(self):
        leads = extract_leads([{"id": 1, "name": "Lead quente", "price": 100}])
        messages = extract_messages([
            {"lead_id": 1, "text": "quero comprar hoje, qual o preço?", "direction": "incoming", "created_at": 1_700_000_000}
        ])
        report = analyze(leads, messages, stale_hours=999999)
        self.assertEqual(report["summary"]["total_leads"], 1)
        self.assertEqual(report["summary"]["unanswered_leads"], 1)
        self.assertGreaterEqual(report["leads"][0]["priority_score"], 60)
        self.assertIn("prioridade", report["leads"][0]["recommendation"].lower())

    def test_cli_writes_json_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            leads = tmp_path / "leads.json"
            messages = tmp_path / "messages.json"
            output = tmp_path / "report.json"
            leads.write_text(json.dumps([{"id": 1, "name": "Ana"}]), encoding="utf-8")
            messages.write_text(json.dumps([{"lead_id": 1, "text": "Cliente: quero orçamento", "direction": "incoming"}]), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "kommo_lead_analyzer.py"), "--leads-file", str(leads), "--messages-file", str(messages), "--format", "json", "--output", str(output)],
                check=True,
                text=True,
                capture_output=True,
            )
            self.assertIn("Relatório salvo", result.stdout)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["summary"]["total_messages"], 1)


if __name__ == "__main__":
    unittest.main()
