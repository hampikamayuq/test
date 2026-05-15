import json
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kommo_lead_analyzer import (
    analyze,
    extract_leads,
    extract_messages,
    infer_direction,
    parse_timestamp,
    render_markdown,
    write_csv,
)


class ParseTimestampTest(unittest.TestCase):
    def test_iso_string(self):
        self.assertEqual(parse_timestamp("1970-01-01T00:01:00Z"), 60)

    def test_milliseconds_converted(self):
        self.assertEqual(parse_timestamp(1_700_000_000_000), 1_700_000_000)

    def test_small_int_kept_as_seconds(self):
        self.assertEqual(parse_timestamp(60_000), 60_000)

    def test_none_returns_none(self):
        self.assertIsNone(parse_timestamp(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(parse_timestamp(""))


class InferDirectionTest(unittest.TestCase):
    def test_kommo_note_type_incoming_call(self):
        self.assertEqual(infer_direction({"note_type": 4}, ""), "incoming")

    def test_kommo_note_type_outgoing_call(self):
        self.assertEqual(infer_direction({"note_type": 10}, ""), "outgoing")

    def test_kommo_note_type_incoming_sms(self):
        self.assertEqual(infer_direction({"note_type": 25}, ""), "incoming")

    def test_kommo_note_type_outgoing_message(self):
        self.assertEqual(infer_direction({"note_type": 103}, ""), "outgoing")

    def test_direction_field_incoming(self):
        self.assertEqual(infer_direction({"direction": "incoming"}, ""), "incoming")

    def test_direction_field_outgoing(self):
        self.assertEqual(infer_direction({"direction": "outgoing"}, ""), "outgoing")

    def test_text_prefix_cliente(self):
        self.assertEqual(infer_direction({}, "Cliente: oi"), "incoming")

    def test_text_prefix_vendedor(self):
        self.assertEqual(infer_direction({}, "Vendedor: olá"), "outgoing")

    def test_unknown_when_no_signals(self):
        self.assertEqual(infer_direction({}, "mensagem genérica"), "unknown")


class AnalyzeTest(unittest.TestCase):
    def test_prioritizes_unanswered_buying_signal(self):
        leads = extract_leads([{"id": 1, "name": "Lead quente", "price": 100}])
        messages = extract_messages([
            {"lead_id": 1, "text": "quero comprar hoje, qual o preço?", "direction": "incoming", "created_at": 1_700_000_000}
        ])
        report = analyze(leads, messages, stale_hours=999999)
        self.assertEqual(report["summary"]["total_leads"], 1)
        self.assertEqual(report["summary"]["unanswered_leads"], 1)
        self.assertGreaterEqual(report["leads"][0]["priority_score"], 60)
        self.assertIn("prioridade", report["leads"][0]["recommendation"].lower())

    def test_stale_lead_sorted_first(self):
        recent_ts = int(time.time()) - 1800  # 30 minutes ago — not stale with stale_hours=1
        leads = extract_leads([
            {"id": 1, "name": "Fresco"},
            {"id": 2, "name": "Vencido"},
        ])
        messages = extract_messages([
            {"lead_id": 1, "text": "quero comprar", "direction": "incoming", "created_at": recent_ts},
            {"lead_id": 2, "text": "quero comprar", "direction": "incoming", "created_at": 1_000_000},
        ])
        report = analyze(leads, messages, stale_hours=1)
        first = report["leads"][0]
        self.assertTrue(first["stale_unanswered"])
        self.assertEqual(first["name"], "Vencido")

    def test_unanswered_hours_populated(self):
        leads = extract_leads([{"id": 1, "name": "X"}])
        messages = extract_messages([
            {"lead_id": 1, "text": "oi", "direction": "incoming", "created_at": 1_700_000_000}
        ])
        report = analyze(leads, messages, stale_hours=999999)
        self.assertIsNotNone(report["leads"][0]["unanswered_hours"])

    def test_report_has_generated_at(self):
        leads = extract_leads([{"id": 1}])
        messages = extract_messages([{"lead_id": 1, "text": "x", "direction": "incoming"}])
        report = analyze(leads, messages)
        self.assertIn("generated_at", report)
        self.assertRegex(report["generated_at"], r"\d{4}-\d{2}-\d{2}T")

    def test_response_time_computed(self):
        leads = extract_leads([{"id": 1}])
        messages = extract_messages([
            {"lead_id": 1, "text": "oi", "direction": "incoming", "created_at": 1_700_000_000},
            {"lead_id": 1, "text": "olá", "direction": "outgoing", "created_at": 1_700_000_120},
        ])
        report = analyze(leads, messages)
        self.assertEqual(report["summary"]["average_response_seconds"], 120.0)


class RenderMarkdownTest(unittest.TestCase):
    def _make_report(self):
        leads = extract_leads([{"id": 1, "name": "Test Lead", "pipeline_id": "pipe1"}])
        messages = extract_messages([
            {"lead_id": 1, "text": "quero comprar", "direction": "incoming", "created_at": 1_700_000_000}
        ])
        return analyze(leads, messages, stale_hours=999999)

    def test_contains_generated_at(self):
        report = self._make_report()
        md = render_markdown(report)
        self.assertIn("Gerado em", md)

    def test_pipeline_section_shown(self):
        report = self._make_report()
        md = render_markdown(report)
        self.assertIn("pipe1", md)

    def test_top_n_limits_rows(self):
        leads_data = [{"id": i} for i in range(30)]
        msgs_data = [{"lead_id": i, "text": "x", "direction": "incoming"} for i in range(30)]
        leads = extract_leads(leads_data)
        messages = extract_messages(msgs_data)
        report = analyze(leads, messages)
        md = render_markdown(report, top_n=5)
        # header + 5 data rows = 6 pipe chars in the table body
        table_rows = [line for line in md.splitlines() if line.startswith("| ") and not line.startswith("| Score") and not line.startswith("| ---")]
        self.assertEqual(len(table_rows), 5)


class WriteCsvTest(unittest.TestCase):
    def test_empty_leads_does_not_crash(self):
        report = {"generated_at": "", "summary": {}, "leads": []}
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        write_csv(report, path)
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("lead_id", content)


class CliTest(unittest.TestCase):
    def test_json_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            leads = tmp_path / "leads.json"
            messages = tmp_path / "messages.json"
            output = tmp_path / "report.json"
            leads.write_text(json.dumps([{"id": 1, "name": "Ana"}]), encoding="utf-8")
            messages.write_text(
                json.dumps([{"lead_id": 1, "text": "Cliente: quero orçamento", "direction": "incoming"}]),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable, str(ROOT / "kommo_lead_analyzer.py"),
                    "--leads-file", str(leads),
                    "--messages-file", str(messages),
                    "--format", "json",
                    "--output", str(output),
                ],
                check=True,
                text=True,
                capture_output=True,
            )
            self.assertIn("Relatório salvo", result.stdout)
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(data["summary"]["total_messages"], 1)

    def test_top_n_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            leads_data = [{"id": i, "name": f"Lead {i}"} for i in range(10)]
            msgs_data = [{"lead_id": i, "text": "oi", "direction": "incoming"} for i in range(10)]
            leads_f = tmp_path / "leads.json"
            msgs_f = tmp_path / "msgs.json"
            out_f = tmp_path / "out.md"
            leads_f.write_text(json.dumps(leads_data), encoding="utf-8")
            msgs_f.write_text(json.dumps(msgs_data), encoding="utf-8")
            subprocess.run(
                [
                    sys.executable, str(ROOT / "kommo_lead_analyzer.py"),
                    "--leads-file", str(leads_f),
                    "--messages-file", str(msgs_f),
                    "--top-n", "3",
                    "--output", str(out_f),
                ],
                check=True,
                capture_output=True,
            )
            content = out_f.read_text(encoding="utf-8")
            table_rows = [
                line for line in content.splitlines()
                if line.startswith("| ") and not line.startswith("| Score") and not line.startswith("| ---")
            ]
            self.assertEqual(len(table_rows), 3)

    def test_filter_from_requires_from_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            leads_f = tmp_path / "leads.json"
            msgs_f = tmp_path / "msgs.json"
            leads_f.write_text("[]", encoding="utf-8")
            msgs_f.write_text("[]", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable, str(ROOT / "kommo_lead_analyzer.py"),
                    "--leads-file", str(leads_f),
                    "--messages-file", str(msgs_f),
                    "--filter-from", "2026-01-01",
                    "--output", str(tmp_path / "out.md"),
                ],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--from-api", result.stderr)


if __name__ == "__main__":
    unittest.main()
