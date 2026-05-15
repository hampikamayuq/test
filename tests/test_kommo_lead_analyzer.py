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
    compute_first_response_seconds,
    detect_bot_stage,
    detect_city,
    detect_payment_type,
    detect_return_patient,
    detect_source,
    detect_specialty,
    extract_leads,
    extract_messages,
    format_seconds,
    infer_direction,
    load_delta_snapshot,
    parse_timestamp,
    render_html,
    render_markdown,
    save_delta_snapshot,
    write_csv,
    Message,
)


# ---------------------------------------------------------------------------
# parse_timestamp
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# infer_direction
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Clinic-specific detection
# ---------------------------------------------------------------------------

def _make_messages(texts_and_directions):
    msgs = []
    for text, direction in texts_and_directions:
        msgs.append(Message(lead_id="1", text=text, direction=direction))
    return msgs


class DetectSourceTest(unittest.TestCase):
    def test_instagram(self):
        msgs = _make_messages([("Ola, vim do instagram e gostaria de agendar", "incoming")])
        self.assertEqual(detect_source(msgs), "instagram")

    def test_anuncio(self):
        msgs = _make_messages([("vim através do anúncio e gostaria de agendar", "incoming")])
        self.assertEqual(detect_source(msgs), "anuncio")

    def test_pagina(self):
        msgs = _make_messages([("Olá conheci o trabalho da Clinica pela página", "incoming")])
        self.assertEqual(detect_source(msgs), "pagina")

    def test_direto_when_no_match(self):
        msgs = _make_messages([("Gostaria de agendar com Dr. Miguel", "incoming")])
        self.assertEqual(detect_source(msgs), "direto")

    def test_no_messages_returns_direto(self):
        self.assertEqual(detect_source([]), "direto")


class DetectSpecialtyTest(unittest.TestCase):
    def test_unhas(self):
        msgs = _make_messages([("Gostaria de agendar com Dr. Ceccarelli", "incoming")])
        self.assertEqual(detect_specialty(msgs), "unhas")

    def test_psoriase(self):
        msgs = _make_messages([("consulta de psoríase com a Dra. Manuela", "incoming")])
        self.assertEqual(detect_specialty(msgs), "psoriase")

    def test_cabelo(self):
        msgs = _make_messages([("queda de cabelo e alopecia", "incoming")])
        self.assertEqual(detect_specialty(msgs), "cabelo")

    def test_hidradenite(self):
        msgs = _make_messages([("tenho hidradenite supurativa", "incoming")])
        self.assertEqual(detect_specialty(msgs), "hidradenite")

    def test_cirurgia(self):
        msgs = _make_messages([("consulta com Dr. Galvez para cirurgia dermatológica", "incoming")])
        self.assertEqual(detect_specialty(msgs), "cirurgia_derm")

    def test_none_when_no_match(self):
        msgs = _make_messages([("boa tarde", "incoming")])
        self.assertIsNone(detect_specialty(msgs))


class DetectCityTest(unittest.TestCase):
    def test_copacabana(self):
        msgs = _make_messages([("gostaria de atendimento em copacabana", "incoming")])
        self.assertEqual(detect_city(msgs), "Copacabana")

    def test_sao_paulo(self):
        msgs = _make_messages([("prefiro são paulo", "incoming")])
        self.assertEqual(detect_city(msgs), "São Paulo")

    def test_none_when_no_match(self):
        msgs = _make_messages([("boa tarde", "incoming")])
        self.assertIsNone(detect_city(msgs))


class DetectBotStageTest(unittest.TestCase):
    def test_stage_0_no_messages(self):
        self.assertEqual(detect_bot_stage([]), 0)

    def test_stage_1_boas_vindas(self):
        msgs = _make_messages([("Seja bem-vindo à Clinica QARA!", "outgoing")])
        self.assertEqual(detect_bot_stage(msgs), 1)

    def test_stage_2_viu_medico(self):
        msgs = _make_messages([
            ("Seja bem-vindo à Clinica QARA!", "outgoing"),
            ("Dr. Miguel Ceccarelli especialista em doenças de unhas", "outgoing"),
        ])
        self.assertEqual(detect_bot_stage(msgs), 2)

    def test_stage_4_agendamento(self):
        msgs = _make_messages([
            ("Seja bem-vindo à Clinica QARA!", "outgoing"),
            ("Estamos prontos para agendar sua visita. Qual seria o melhor horário para você?", "outgoing"),
        ])
        self.assertEqual(detect_bot_stage(msgs), 4)

    def test_stage_5_fallback(self):
        msgs = _make_messages([
            ("Nossa equipe entrará em contato com você em breve", "outgoing"),
        ])
        self.assertEqual(detect_bot_stage(msgs), 5)


class DetectPaymentTypeTest(unittest.TestCase):
    def test_convenio(self):
        msgs = _make_messages([("aceito convênio Unimed?", "incoming")])
        self.assertEqual(detect_payment_type(msgs), "convenio")

    def test_particular(self):
        msgs = _make_messages([("consulta particular, como pago?", "incoming")])
        self.assertEqual(detect_payment_type(msgs), "particular")

    def test_none_when_not_mentioned(self):
        msgs = _make_messages([("gostaria de agendar", "incoming")])
        self.assertIsNone(detect_payment_type(msgs))

    def test_only_checks_incoming(self):
        msgs = _make_messages([
            ("consulta particular disponível", "outgoing"),
            ("quero agendar", "incoming"),
        ])
        self.assertIsNone(detect_payment_type(msgs))


class DetectReturnPatientTest(unittest.TestCase):
    def test_return_patient(self):
        msgs = _make_messages([("já sou paciente e gostaria de remarcar", "incoming")])
        self.assertTrue(detect_return_patient(msgs))

    def test_retorno_keyword(self):
        msgs = _make_messages([("quero agendar retorno com Dr. Ceccarelli", "incoming")])
        self.assertTrue(detect_return_patient(msgs))

    def test_new_patient(self):
        msgs = _make_messages([("nunca fui a clinica, gostaria de agendar", "incoming")])
        self.assertFalse(detect_return_patient(msgs))

    def test_only_checks_incoming(self):
        msgs = _make_messages([
            ("paciente antiga voltou ao sistema", "outgoing"),
            ("gostaria de agendar", "incoming"),
        ])
        self.assertFalse(detect_return_patient(msgs))


class ComputeFirstResponseTest(unittest.TestCase):
    def test_basic_first_response(self):
        msgs = _make_messages([
            ("oi", "incoming"),
            ("olá, como posso ajudar?", "outgoing"),
        ])
        msgs[0].created_at = 1_700_000_000
        msgs[1].created_at = 1_700_000_120
        self.assertEqual(compute_first_response_seconds(msgs), 120)

    def test_no_outgoing_returns_none(self):
        msgs = _make_messages([("oi", "incoming")])
        msgs[0].created_at = 1_700_000_000
        self.assertIsNone(compute_first_response_seconds(msgs))

    def test_no_incoming_returns_none(self):
        msgs = _make_messages([("olá", "outgoing")])
        msgs[0].created_at = 1_700_000_000
        self.assertIsNone(compute_first_response_seconds(msgs))

    def test_ignores_second_incoming_pair(self):
        msgs = [
            Message(lead_id="1", text="oi", direction="incoming", created_at=1_700_000_000),
            Message(lead_id="1", text="olá", direction="outgoing", created_at=1_700_000_060),
            Message(lead_id="1", text="outra dúvida", direction="incoming", created_at=1_700_000_200),
            Message(lead_id="1", text="claro", direction="outgoing", created_at=1_700_000_500),
        ]
        self.assertEqual(compute_first_response_seconds(msgs), 60)


# ---------------------------------------------------------------------------
# analyze()
# ---------------------------------------------------------------------------

class AnalyzeTest(unittest.TestCase):
    def test_prioritizes_unanswered_buying_signal(self):
        leads = extract_leads([{"id": 1, "name": "Lead quente", "price": 100}])
        messages = extract_messages([
            {"lead_id": 1, "text": "quero agendar hoje, qual o preço?", "direction": "incoming", "created_at": 1_700_000_000}
        ])
        report = analyze(leads, messages, stale_hours=999999)
        self.assertEqual(report["summary"]["total_leads"], 1)
        self.assertEqual(report["summary"]["unanswered_leads"], 1)
        self.assertGreaterEqual(report["leads"][0]["priority_score"], 60)
        self.assertIn("prioridade", report["leads"][0]["recommendation"].lower())

    def test_stale_lead_sorted_first(self):
        recent_ts = int(time.time()) - 1800  # 30 min ago — not stale with stale_hours=1
        leads = extract_leads([{"id": 1, "name": "Fresco"}, {"id": 2, "name": "Vencido"}])
        messages = extract_messages([
            {"lead_id": 1, "text": "quero agendar", "direction": "incoming", "created_at": recent_ts},
            {"lead_id": 2, "text": "quero agendar", "direction": "incoming", "created_at": 1_000_000},
        ])
        report = analyze(leads, messages, stale_hours=1)
        self.assertTrue(report["leads"][0]["stale_unanswered"])
        self.assertEqual(report["leads"][0]["name"], "Vencido")

    def test_report_has_generated_at(self):
        leads = extract_leads([{"id": 1}])
        messages = extract_messages([{"lead_id": 1, "text": "x", "direction": "incoming"}])
        report = analyze(leads, messages)
        self.assertRegex(report["generated_at"], r"\d{4}-\d{2}-\d{2}T")

    def test_response_time_computed(self):
        leads = extract_leads([{"id": 1}])
        messages = extract_messages([
            {"lead_id": 1, "text": "oi", "direction": "incoming", "created_at": 1_700_000_000},
            {"lead_id": 1, "text": "olá", "direction": "outgoing", "created_at": 1_700_000_120},
        ])
        report = analyze(leads, messages)
        self.assertEqual(report["summary"]["average_response_seconds"], 120.0)

    def test_specialty_and_source_in_lead_report(self):
        leads = extract_leads([{"id": 1, "name": "Ana"}])
        messages = extract_messages([
            {"lead_id": 1, "text": "vim do instagram e gostaria de agendar com Dr. Ceccarelli sobre doenças de unha", "direction": "incoming"},
        ])
        report = analyze(leads, messages)
        lead = report["leads"][0]
        self.assertEqual(lead["source"], "instagram")
        self.assertEqual(lead["specialty"], "unhas")

    def test_bot_stage_in_summary(self):
        leads = extract_leads([{"id": 1}])
        messages = extract_messages([
            {"lead_id": 1, "text": "Seja bem-vindo à Clinica QARA!", "direction": "outgoing"},
            {"lead_id": 1, "text": "Estamos prontos para agendar sua visita. Qual seria o melhor horário para você?", "direction": "outgoing"},
        ])
        report = analyze(leads, messages)
        self.assertIn("bot_stages", report["summary"])
        self.assertEqual(report["leads"][0]["bot_stage"], 4)

    def test_summary_has_sources_and_specialties(self):
        leads = extract_leads([{"id": 1}, {"id": 2}])
        messages = extract_messages([
            {"lead_id": 1, "text": "vim do instagram, quero agendar com Dr. Ceccarelli", "direction": "incoming"},
            {"lead_id": 2, "text": "quero consulta de psoríase", "direction": "incoming"},
        ])
        report = analyze(leads, messages)
        self.assertIn("sources", report["summary"])
        self.assertIn("specialties", report["summary"])
        self.assertIn("instagram", report["summary"]["sources"])

    def test_payment_type_in_lead_report(self):
        leads = extract_leads([{"id": 1}])
        messages = extract_messages([
            {"lead_id": 1, "text": "aceito plano Unimed?", "direction": "incoming"},
        ])
        report = analyze(leads, messages)
        self.assertEqual(report["leads"][0]["payment_type"], "convenio")

    def test_return_patient_in_lead_report(self):
        leads = extract_leads([{"id": 1}])
        messages = extract_messages([
            {"lead_id": 1, "text": "já sou paciente e gostaria de remarcar consulta", "direction": "incoming"},
        ])
        report = analyze(leads, messages)
        self.assertTrue(report["leads"][0]["return_patient"])
        self.assertGreater(report["summary"]["return_patients"], 0)

    def test_first_response_in_summary(self):
        leads = extract_leads([{"id": 1}])
        messages = extract_messages([
            {"lead_id": 1, "text": "oi", "direction": "incoming", "created_at": 1_700_000_000},
            {"lead_id": 1, "text": "olá", "direction": "outgoing", "created_at": 1_700_000_300},
        ])
        report = analyze(leads, messages)
        self.assertEqual(report["summary"]["average_first_response_seconds"], 300.0)

    def test_overdue_task_leads_sorted_first(self):
        recent_ts = int(time.time()) - 1800
        leads = extract_leads([{"id": "A"}, {"id": "B"}])
        messages = extract_messages([
            {"lead_id": "A", "text": "oi", "direction": "outgoing", "created_at": recent_ts},
            {"lead_id": "B", "text": "oi", "direction": "outgoing", "created_at": recent_ts},
        ])
        report = analyze(leads, messages, overdue_task_leads={"A"})
        self.assertTrue(report["leads"][0]["has_overdue_task"])
        self.assertEqual(report["leads"][0]["lead_id"], "A")

    def test_name_map_applied(self):
        leads = extract_leads([{"id": 1, "status_id": 42, "pipeline_id": 7}])
        messages = extract_messages([{"lead_id": 1, "text": "oi", "direction": "incoming"}])
        report = analyze(leads, messages, name_map={"42": "Novo", "7": "Principal"})
        lead = report["leads"][0]
        self.assertEqual(lead["status_name"], "Novo")
        self.assertEqual(lead["pipeline_name"], "Principal")

    def test_delta_bot_stage(self):
        leads = extract_leads([{"id": "1"}])
        messages = extract_messages([
            {"lead_id": "1", "text": "Seja bem-vindo à Clinica QARA!", "direction": "outgoing"},
        ])
        prev_snapshot = {"1": {"bot_stage": 0, "unanswered": False, "priority_score": 20}}
        report = analyze(leads, messages, prev_snapshot=prev_snapshot)
        self.assertEqual(report["leads"][0]["delta_bot_stage"], 1)

    def test_leads_by_hour_and_weekday(self):
        leads = extract_leads([{"id": 1, "created_at": "2026-05-14T10:00:00Z"}])
        messages = extract_messages([{"lead_id": 1, "text": "oi", "direction": "incoming"}])
        report = analyze(leads, messages)
        self.assertIn("leads_by_hour", report["summary"])
        self.assertIn("leads_by_weekday", report["summary"])
        self.assertIn("10", report["summary"]["leads_by_hour"])


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------

class RenderMarkdownTest(unittest.TestCase):
    def _make_report(self):
        leads = extract_leads([{"id": 123456, "name": "Maria Silva", "pipeline_id": "pipe1"}])
        messages = extract_messages([
            {"lead_id": 123456, "text": "vim do instagram e gostaria de agendar com Dr. Ceccarelli", "direction": "incoming", "created_at": 1_700_000_000},
        ])
        return analyze(leads, messages, stale_hours=999999)

    def test_contains_generated_at(self):
        md = render_markdown(self._make_report())
        self.assertIn("Gerado em", md)

    def test_contains_lead_id(self):
        md = render_markdown(self._make_report())
        self.assertIn("123456", md)

    def test_contains_source_section(self):
        md = render_markdown(self._make_report())
        self.assertIn("Canal de Captação", md)

    def test_contains_specialty_section(self):
        md = render_markdown(self._make_report())
        self.assertIn("Especialidade", md)

    def test_contains_bot_funnel_section(self):
        md = render_markdown(self._make_report())
        self.assertIn("Funil do Bot", md)

    def test_top_n_limits_rows(self):
        leads_data = [{"id": i} for i in range(30)]
        msgs_data = [{"lead_id": i, "text": "x", "direction": "incoming"} for i in range(30)]
        report = analyze(extract_leads(leads_data), extract_messages(msgs_data))
        md = render_markdown(report, top_n=5)
        table_rows = [line for line in md.splitlines() if line.startswith("| `")]
        self.assertLessEqual(len(table_rows), 5 * 4)

    def test_delta_column_shown(self):
        leads = extract_leads([{"id": "1"}])
        messages = extract_messages([
            {"lead_id": "1", "text": "Seja bem-vindo à Clinica QARA!", "direction": "outgoing"},
        ])
        prev = {"1": {"bot_stage": 0, "unanswered": False, "priority_score": 20}}
        report = analyze(leads, messages, prev_snapshot=prev)
        md = render_markdown(report)
        self.assertIn("+1", md)

    def test_payment_type_column(self):
        leads = extract_leads([{"id": "1"}])
        messages = extract_messages([
            {"lead_id": "1", "text": "aceito Unimed?", "direction": "incoming"},
        ])
        report = analyze(leads, messages)
        md = render_markdown(report)
        self.assertIn("Convênio", md)

    def test_overdue_task_section(self):
        recent_ts = int(time.time()) - 60
        leads = extract_leads([{"id": "99"}])
        messages = extract_messages([
            {"lead_id": "99", "text": "oi", "direction": "outgoing", "created_at": recent_ts},
        ])
        report = analyze(leads, messages, overdue_task_leads={"99"})
        md = render_markdown(report)
        self.assertIn("Tarefa Vencida", md)


# ---------------------------------------------------------------------------
# render_html
# ---------------------------------------------------------------------------

class RenderHtmlTest(unittest.TestCase):
    def _make_report(self):
        leads = extract_leads([{"id": 1, "name": "João", "created_at": "2026-05-14T10:00:00Z"}])
        messages = extract_messages([
            {"lead_id": 1, "text": "vim do instagram, quero agendar com Dr. Ceccarelli", "direction": "incoming", "created_at": 1_700_000_000},
            {"lead_id": 1, "text": "Seja bem-vindo à Clinica QARA!", "direction": "outgoing", "created_at": 1_700_000_060},
        ])
        return analyze(leads, messages, stale_hours=999999)

    def test_is_valid_html(self):
        html = render_html(self._make_report())
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("</html>", html)

    def test_contains_lead_id(self):
        html = render_html(self._make_report())
        self.assertIn("1", html)

    def test_contains_bar_chart_elements(self):
        html = render_html(self._make_report())
        self.assertIn("class='bar'", html)

    def test_contains_summary_cards(self):
        html = render_html(self._make_report())
        self.assertIn("class='card'", html)

    def test_contains_clinic_name(self):
        html = render_html(self._make_report())
        self.assertIn("Clínica QARA", html)


# ---------------------------------------------------------------------------
# Delta snapshot
# ---------------------------------------------------------------------------

class DeltaSnapshotTest(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        leads = extract_leads([{"id": "1"}, {"id": "2"}])
        messages = extract_messages([
            {"lead_id": "1", "text": "oi", "direction": "incoming"},
            {"lead_id": "2", "text": "Seja bem-vindo à Clinica QARA!", "direction": "outgoing"},
        ])
        report = analyze(leads, messages)
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
            out_path = f.name
        save_delta_snapshot(report, out_path)
        loaded = load_delta_snapshot(out_path)
        self.assertIsNotNone(loaded)
        self.assertIn("1", loaded)
        self.assertIn("bot_stage", loaded["1"])

    def test_load_missing_returns_none(self):
        self.assertIsNone(load_delta_snapshot("/tmp/nonexistent_snapshot_xyz.md"))


# ---------------------------------------------------------------------------
# format_seconds
# ---------------------------------------------------------------------------

class FormatSecondsTest(unittest.TestCase):
    def test_none(self):
        self.assertEqual(format_seconds(None), "n/d")

    def test_minutes_only(self):
        self.assertEqual(format_seconds(90), "1min")

    def test_hours_and_minutes(self):
        self.assertEqual(format_seconds(3660), "1h 1min")

    def test_zero(self):
        self.assertEqual(format_seconds(0), "0min")


# ---------------------------------------------------------------------------
# write_csv
# ---------------------------------------------------------------------------

class WriteCsvTest(unittest.TestCase):
    def test_empty_leads_does_not_crash(self):
        report = {"generated_at": "", "summary": {}, "leads": []}
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        write_csv(report, path)
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("lead_id", content)

    def test_csv_includes_new_fields(self):
        leads = extract_leads([{"id": 1, "name": "Test"}])
        messages = extract_messages([{"lead_id": 1, "text": "vim do instagram, Ceccarelli", "direction": "incoming"}])
        report = analyze(leads, messages)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            path = f.name
        write_csv(report, path)
        with open(path, encoding="utf-8") as fh:
            header = fh.readline()
        self.assertIn("source", header)
        self.assertIn("specialty", header)
        self.assertIn("bot_stage", header)
        self.assertIn("payment_type", header)
        self.assertIn("return_patient", header)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class CliTest(unittest.TestCase):
    def test_json_report_includes_new_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            leads_f = tmp_path / "leads.json"
            msgs_f = tmp_path / "messages.json"
            out_f = tmp_path / "report.json"
            leads_f.write_text(json.dumps([{"id": 1, "name": "Ana"}]), encoding="utf-8")
            msgs_f.write_text(
                json.dumps([{"lead_id": 1, "text": "vim do instagram e gostaria de agendar com Dr. Ceccarelli", "direction": "incoming"}]),
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(ROOT / "kommo_lead_analyzer.py"),
                 "--leads-file", str(leads_f), "--messages-file", str(msgs_f),
                 "--format", "json", "--output", str(out_f), "--no-delta"],
                check=True, text=True, capture_output=True,
            )
            self.assertIn("Relatório salvo", result.stdout)
            data = json.loads(out_f.read_text(encoding="utf-8"))
            self.assertIn("sources", data["summary"])
            self.assertIn("specialties", data["summary"])
            self.assertIn("bot_stages", data["summary"])
            self.assertIn("payment_types", data["summary"])
            lead = data["leads"][0]
            self.assertEqual(lead["source"], "instagram")
            self.assertEqual(lead["specialty"], "unhas")
            self.assertIn("payment_type", lead)
            self.assertIn("return_patient", lead)
            self.assertIn("first_response_seconds", lead)

    def test_html_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            leads_f = tmp_path / "leads.json"
            msgs_f = tmp_path / "msgs.json"
            out_f = tmp_path / "report.html"
            leads_f.write_text(json.dumps([{"id": 1}]), encoding="utf-8")
            msgs_f.write_text(json.dumps([{"lead_id": 1, "text": "oi", "direction": "incoming"}]), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(ROOT / "kommo_lead_analyzer.py"),
                 "--leads-file", str(leads_f), "--messages-file", str(msgs_f),
                 "--format", "html", "--output", str(out_f), "--no-delta"],
                check=True, capture_output=True,
            )
            content = out_f.read_text(encoding="utf-8")
            self.assertIn("<!DOCTYPE html>", content)
            self.assertIn("Clínica QARA", content)

    def test_top_n_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            leads_data = [{"id": i, "name": "Lead " + str(i)} for i in range(10)]
            msgs_data = [{"lead_id": i, "text": "oi", "direction": "incoming"} for i in range(10)]
            leads_f = tmp_path / "leads.json"
            msgs_f = tmp_path / "msgs.json"
            out_f = tmp_path / "out.md"
            leads_f.write_text(json.dumps(leads_data), encoding="utf-8")
            msgs_f.write_text(json.dumps(msgs_data), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(ROOT / "kommo_lead_analyzer.py"),
                 "--leads-file", str(leads_f), "--messages-file", str(msgs_f),
                 "--top-n", "3", "--output", str(out_f), "--no-delta"],
                check=True, capture_output=True,
            )
            content = out_f.read_text(encoding="utf-8")
            priority_rows = [l for l in content.splitlines() if l.startswith("| `")]
            self.assertLessEqual(len(priority_rows), 12)  # at most 4 tables × 3 rows

    def test_filter_from_requires_from_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            leads_f = tmp_path / "leads.json"
            msgs_f = tmp_path / "msgs.json"
            leads_f.write_text("[]", encoding="utf-8")
            msgs_f.write_text("[]", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "kommo_lead_analyzer.py"),
                 "--leads-file", str(leads_f), "--messages-file", str(msgs_f),
                 "--filter-from", "2026-01-01", "--output", str(tmp_path / "out.md")],
                text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--from-api", result.stderr)

    def test_delta_snapshot_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            leads_f = tmp_path / "leads.json"
            msgs_f = tmp_path / "msgs.json"
            out_f = tmp_path / "out.md"
            leads_f.write_text(json.dumps([{"id": 1}]), encoding="utf-8")
            msgs_f.write_text(json.dumps([{"lead_id": 1, "text": "oi", "direction": "incoming"}]), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(ROOT / "kommo_lead_analyzer.py"),
                 "--leads-file", str(leads_f), "--messages-file", str(msgs_f),
                 "--output", str(out_f)],
                check=True, capture_output=True,
            )
            snapshot_path = tmp_path / "out.md.snapshot.json"
            self.assertTrue(snapshot_path.exists())
            data = json.loads(snapshot_path.read_text(encoding="utf-8"))
            self.assertIn("1", data)

    def test_no_delta_skips_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            leads_f = tmp_path / "leads.json"
            msgs_f = tmp_path / "msgs.json"
            out_f = tmp_path / "out.md"
            leads_f.write_text(json.dumps([{"id": 1}]), encoding="utf-8")
            msgs_f.write_text(json.dumps([{"lead_id": 1, "text": "oi", "direction": "incoming"}]), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(ROOT / "kommo_lead_analyzer.py"),
                 "--leads-file", str(leads_f), "--messages-file", str(msgs_f),
                 "--output", str(out_f), "--no-delta"],
                check=True, capture_output=True,
            )
            snapshot_path = tmp_path / "out.md.snapshot.json"
            self.assertFalse(snapshot_path.exists())


if __name__ == "__main__":
    unittest.main()
