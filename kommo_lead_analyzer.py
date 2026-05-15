#!/usr/bin/env python3
"""Analyze Kommo leads and message/notes exports or live API data.

This CLI is intentionally dependency-free so it can run in small automation
containers, cron jobs, and no-code runners that can execute Python scripts.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

BUYING_KEYWORDS = {
    "pt": ["comprar", "preço", "valor", "orçamento", "contratar", "pagamento", "pix", "cartão", "boleto", "quero", "fechar"],
    "es": ["comprar", "precio", "valor", "presupuesto", "contratar", "pago", "tarjeta", "quiero", "cerrar"],
    "en": ["buy", "price", "quote", "budget", "hire", "payment", "card", "want", "purchase"],
}
OBJECTION_KEYWORDS = {
    "pt": ["caro", "dúvida", "duvida", "não posso", "nao posso", "depois", "concorrente", "desconto", "problema"],
    "es": ["caro", "duda", "no puedo", "después", "despues", "competidor", "descuento", "problema"],
    "en": ["expensive", "question", "later", "competitor", "discount", "problem", "can't"],
}
URGENCY_KEYWORDS = {
    "pt": ["hoje", "urgente", "agora", "imediato", "rápido", "rapido"],
    "es": ["hoy", "urgente", "ahora", "inmediato", "rápido", "rapido"],
    "en": ["today", "urgent", "now", "immediate", "fast", "quick"],
}
NEGATIVE_KEYWORDS = {
    "pt": ["não gostei", "nao gostei", "cancelar", "reclamação", "reclamacao", "ruim", "péssimo", "pessimo"],
    "es": ["no me gustó", "cancelar", "queja", "malo", "pésimo", "pesimo"],
    "en": ["dislike", "cancel", "complaint", "bad", "terrible"],
}

TEXT_KEYS = ("text", "message", "body", "comment", "note", "content", "value")
TIME_KEYS = ("created_at", "updated_at", "date", "timestamp", "createdAt", "time")
LEAD_ID_KEYS = ("lead_id", "entity_id", "parent_id", "id_lead", "leadId")

# Kommo note_type integers → direction
# 4 = incoming call, 10 = outgoing call, 25 = incoming SMS, 26 = outgoing SMS,
# 102 = incoming message, 103 = outgoing message
_NOTE_TYPE_INCOMING = {4, 25, 102}
_NOTE_TYPE_OUTGOING = {10, 26, 103}


@dataclass
class Message:
    lead_id: str
    text: str
    created_at: int | None = None
    direction: str = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Lead:
    id: str
    name: str = ""
    status_id: str = ""
    pipeline_id: str = ""
    created_at: int | None = None
    price: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


def parse_timestamp(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        # Kommo timestamps are seconds. Accept milliseconds too.
        return int(value / 1000) if value > 10_000_000_000 else int(value)
    text = str(value).strip()
    if text.isdigit():
        return parse_timestamp(int(text))
    try:
        return int(dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, dict):
        for key in TEXT_KEYS:
            if key in value:
                return normalize_text(value[key])
    return re.sub(r"\s+", " ", str(value)).strip()


def first_present(data: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def read_json_or_csv(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8-sig") as handle:
        if path.lower().endswith(".csv"):
            return list(csv.DictReader(handle))
        data = json.load(handle)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        # Kommo API responses usually place entities under _embedded.
        embedded = data.get("_embedded")
        if isinstance(embedded, dict):
            for value in embedded.values():
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        for key in ("leads", "notes", "messages", "data", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise ValueError(f"Unsupported data shape in {path}")


def extract_leads(rows: Iterable[dict[str, Any]]) -> dict[str, Lead]:
    leads: dict[str, Lead] = {}
    for row in rows:
        lead_id = str(first_present(row, ("id", "lead_id", "entity_id")) or "").strip()
        if not lead_id:
            continue
        price_raw = first_present(row, ("price", "budget", "sale")) or 0
        try:
            price = float(str(price_raw).replace(",", "."))
        except ValueError:
            price = 0.0
        leads[lead_id] = Lead(
            id=lead_id,
            name=str(first_present(row, ("name", "lead_name", "title")) or ""),
            status_id=str(first_present(row, ("status_id", "status", "stage")) or ""),
            pipeline_id=str(first_present(row, ("pipeline_id", "pipeline")) or ""),
            created_at=parse_timestamp(first_present(row, TIME_KEYS)),
            price=price,
            raw=row,
        )
    return leads


def extract_messages(rows: Iterable[dict[str, Any]]) -> list[Message]:
    messages: list[Message] = []
    for row in rows:
        lead_id = str(first_present(row, LEAD_ID_KEYS) or "").strip()
        if not lead_id:
            # Kommo notes may store the entity id in nested metadata.
            params = row.get("params") if isinstance(row.get("params"), dict) else {}
            lead_id = str(first_present(params, LEAD_ID_KEYS) or "").strip()
        if not lead_id:
            continue
        params = row.get("params") if isinstance(row.get("params"), dict) else {}
        text = normalize_text(first_present(row, TEXT_KEYS) or first_present(params, TEXT_KEYS) or params)
        direction = infer_direction(row, text)
        messages.append(
            Message(
                lead_id=lead_id,
                text=text,
                created_at=parse_timestamp(first_present(row, TIME_KEYS)),
                direction=direction,
                raw=row,
            )
        )
    return messages


def infer_direction(row: dict[str, Any], text: str) -> str:
    # Kommo note_type integer takes priority when available.
    params = row.get("params") if isinstance(row.get("params"), dict) else {}
    note_type_raw = row.get("note_type") or params.get("note_type")
    if note_type_raw is not None:
        try:
            note_type = int(note_type_raw)
            if note_type in _NOTE_TYPE_INCOMING:
                return "incoming"
            if note_type in _NOTE_TYPE_OUTGOING:
                return "outgoing"
        except (ValueError, TypeError):
            pass

    direction = str(first_present(row, ("direction", "sender_type", "type")) or "").lower()
    if any(token in direction for token in ("incoming", "inbound", "client", "customer", "received")):
        return "incoming"
    if any(token in direction for token in ("outgoing", "outbound", "manager", "user", "sent")):
        return "outgoing"
    lowered = text.lower()
    if lowered.startswith(("cliente:", "lead:", "customer:")):
        return "incoming"
    if lowered.startswith(("atendente:", "vendedor:", "manager:", "user:")):
        return "outgoing"
    return "unknown"


def count_keywords(text: str, catalog: dict[str, list[str]]) -> int:
    lowered = text.lower()
    return sum(1 for words in catalog.values() for word in words if word in lowered)


def analyze(leads: dict[str, Lead], messages: list[Message], stale_hours: int = 24) -> dict[str, Any]:
    by_lead: dict[str, list[Message]] = defaultdict(list)
    for message in messages:
        by_lead[message.lead_id].append(message)
        if message.lead_id not in leads:
            leads[message.lead_id] = Lead(id=message.lead_id, name=f"Lead {message.lead_id}")

    now = int(time.time())
    lead_reports = []
    response_times: list[int] = []
    pipeline_counter: Counter[str] = Counter()
    status_counter: Counter[str] = Counter()

    for lead in leads.values():
        pipeline_counter[lead.pipeline_id or "unknown"] += 1
        status_counter[lead.status_id or "unknown"] += 1
        lead_messages = sorted(by_lead.get(lead.id, []), key=lambda item: item.created_at or 0)
        full_text = " ".join(message.text for message in lead_messages)
        buying = count_keywords(full_text, BUYING_KEYWORDS)
        objections = count_keywords(full_text, OBJECTION_KEYWORDS)
        urgency = count_keywords(full_text, URGENCY_KEYWORDS)
        negative = count_keywords(full_text, NEGATIVE_KEYWORDS)
        last_message = lead_messages[-1] if lead_messages else None
        unanswered = bool(last_message and last_message.direction == "incoming")
        stale = bool(unanswered and last_message.created_at and now - last_message.created_at > stale_hours * 3600)
        score = max(0, min(100, 20 + buying * 18 + urgency * 15 - objections * 8 - negative * 18 + (10 if lead.price else 0) + (-15 if stale else 0)))

        pending_incoming: Message | None = None
        for message in lead_messages:
            if message.direction == "incoming":
                pending_incoming = message
            elif message.direction == "outgoing" and pending_incoming and pending_incoming.created_at and message.created_at:
                if message.created_at >= pending_incoming.created_at:
                    response_times.append(message.created_at - pending_incoming.created_at)
                    pending_incoming = None

        last_ts = last_message.created_at if last_message else None
        unanswered_hours: float | None = round((now - last_ts) / 3600, 1) if unanswered and last_ts else None

        lead_reports.append(
            {
                "lead_id": lead.id,
                "name": lead.name,
                "pipeline_id": lead.pipeline_id or None,
                "status_id": lead.status_id or None,
                "price": lead.price,
                "messages": len(lead_messages),
                "buying_signals": buying,
                "objections": objections,
                "urgency_signals": urgency,
                "negative_signals": negative,
                "last_direction": last_message.direction if last_message else None,
                "unanswered": unanswered,
                "unanswered_hours": unanswered_hours,
                "stale_unanswered": stale,
                "priority_score": score,
                "recommendation": recommend(score, unanswered, stale, objections),
            }
        )

    # Sort: stale first, then unanswered, then by score descending.
    lead_reports.sort(
        key=lambda item: (item["stale_unanswered"], item["unanswered"], item["priority_score"], item["messages"]),
        reverse=True,
    )
    avg_response = sum(response_times) / len(response_times) if response_times else None
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": {
            "total_leads": len(leads),
            "total_messages": len(messages),
            "unanswered_leads": sum(1 for item in lead_reports if item["unanswered"]),
            "stale_unanswered_leads": sum(1 for item in lead_reports if item["stale_unanswered"]),
            "average_response_seconds": round(avg_response, 2) if avg_response is not None else None,
            "pipelines": dict(pipeline_counter),
            "statuses": dict(status_counter),
        },
        "leads": lead_reports,
    }


def recommend(score: int, unanswered: bool, stale: bool, objections: int) -> str:
    if stale:
        return "Responder agora: lead sem retorno há muito tempo."
    if unanswered and score >= 60:
        return "Alta prioridade: responder com proposta ou próximo passo."
    if unanswered:
        return "Responder e qualificar necessidade, prazo e orçamento."
    if objections:
        return "Trabalhar objeções e enviar prova social/caso de uso."
    if score >= 70:
        return "Lead quente: tentar fechamento ou agendar reunião."
    return "Nutrir com follow-up e conteúdo relevante."


def render_markdown(report: dict[str, Any], top_n: int = 20) -> str:
    summary = report["summary"]
    generated = report.get("generated_at", "")
    lines = ["# Análise de Leads e Mensagens Kommo", ""]
    if generated:
        lines += [f"_Gerado em {generated}_", ""]
    lines += [
        f"- Leads analisados: **{summary['total_leads']}**",
        f"- Mensagens analisadas: **{summary['total_messages']}**",
        f"- Leads sem resposta: **{summary['unanswered_leads']}**",
        f"- Leads sem resposta vencidos: **{summary['stale_unanswered_leads']}**",
        f"- Tempo médio de resposta: **{format_seconds(summary['average_response_seconds'])}**",
        "",
    ]

    pipelines = summary.get("pipelines", {})
    if pipelines and not (len(pipelines) == 1 and "unknown" in pipelines):
        lines += ["## Leads por pipeline", ""]
        lines += [f"- `{pid}`: {count}" for pid, count in sorted(pipelines.items(), key=lambda x: -x[1])]
        lines += [""]

    lines += [
        "## Top prioridades",
        "",
        "| Score | Lead | Msgs | Sem resposta | Recomendação |",
        "| ---: | --- | ---: | --- | --- |",
    ]
    for lead in report["leads"][:top_n]:
        if lead["stale_unanswered"] and lead["unanswered_hours"]:
            unanswered_label = f"sim ({lead['unanswered_hours']}h) ⚠️"
        elif lead["unanswered"] and lead["unanswered_hours"]:
            unanswered_label = f"sim ({lead['unanswered_hours']}h)"
        elif lead["unanswered"]:
            unanswered_label = "sim"
        else:
            unanswered_label = "não"
        lines.append(
            f"| {lead['priority_score']} | {lead['name'] or lead['lead_id']} | {lead['messages']} | "
            f"{unanswered_label} | {lead['recommendation']} |"
        )
    return "\n".join(lines) + "\n"


def format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "n/d"
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}min"
    return f"{minutes}min"


def write_csv(report: dict[str, Any], path: str) -> None:
    rows = report["leads"]
    if not rows:
        fieldnames = ["lead_id", "name", "priority_score", "recommendation"]
    else:
        fieldnames = list(rows[0].keys())
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _api_request(url: str, token: str, timeout: int = 30, max_retries: int = 4) -> bytes:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    request = urllib.request.Request(url, headers=headers)
    last_exc: Exception = RuntimeError("unknown error")
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code == 429 or exc.code >= 500:
                if attempt < max_retries:
                    time.sleep(2 ** attempt)
                    continue
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Kommo API returned HTTP {exc.code}: {details}") from exc
        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Network error reaching Kommo API: {exc.reason}") from exc
    raise RuntimeError(f"Max retries exceeded: {last_exc}") from last_exc


def fetch_kommo_collection(
    subdomain: str,
    token: str,
    path: str,
    limit: int = 250,
    filter_from: int | None = None,
    filter_to: int | None = None,
) -> list[dict[str, Any]]:
    base = f"https://{subdomain}.kommo.com/api/v4/{path.lstrip('/')}"
    params: dict[str, str] = {"limit": str(limit)}
    if filter_from is not None:
        params["filter[created_at][from]"] = str(filter_from)
    if filter_to is not None:
        params["filter[created_at][to]"] = str(filter_to)
    url = f"{base}?{urllib.parse.urlencode(params)}"
    rows: list[dict[str, Any]] = []
    while url:
        payload = json.loads(_api_request(url, token).decode("utf-8"))
        embedded = payload.get("_embedded", {}) if isinstance(payload, dict) else {}
        for value in embedded.values():
            if isinstance(value, list):
                rows.extend(item for item in value if isinstance(item, dict))
                break
        next_href = payload.get("_links", {}).get("next", {}).get("href") if isinstance(payload, dict) else None
        url = str(urllib.parse.urljoin(url, next_href)) if next_href else ""
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analisa mensagens/notas e leads do Kommo para priorizar follow-up comercial."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--leads-file", help="Arquivo JSON/CSV com leads exportados do Kommo.")
    source.add_argument(
        "--from-api",
        action="store_true",
        help="Busca leads e notas pela API usando KOMMO_SUBDOMAIN e KOMMO_ACCESS_TOKEN.",
    )
    parser.add_argument(
        "--messages-file",
        help="Arquivo JSON/CSV com mensagens ou notas. Obrigatório com --leads-file.",
    )
    parser.add_argument("--output", default="kommo_analysis.md", help="Caminho do relatório de saída.")
    parser.add_argument(
        "--format",
        choices=("markdown", "json", "csv"),
        default="markdown",
        help="Formato do relatório.",
    )
    parser.add_argument(
        "--stale-hours",
        type=int,
        default=24,
        help="Horas para considerar um lead sem resposta como vencido (padrão: 24).",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="Quantidade de leads exibidos no relatório Markdown (padrão: 20).",
    )
    parser.add_argument(
        "--filter-from",
        help="Filtrar leads criados a partir desta data ISO 8601 (ex: 2026-01-01). Apenas com --from-api.",
    )
    parser.add_argument(
        "--filter-to",
        help="Filtrar leads criados até esta data ISO 8601 (ex: 2026-12-31). Apenas com --from-api.",
    )
    return parser


def _parse_date_to_timestamp(value: str) -> int:
    try:
        return int(dt.datetime.fromisoformat(value).timestamp())
    except ValueError:
        raise ValueError(f"Data inválida: {value!r}. Use formato ISO 8601 (ex: 2026-01-01).")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    filter_from: int | None = None
    filter_to: int | None = None
    if args.filter_from or args.filter_to:
        if not args.from_api:
            print("--filter-from/--filter-to são suportados apenas com --from-api.", file=sys.stderr)
            return 2
        try:
            if args.filter_from:
                filter_from = _parse_date_to_timestamp(args.filter_from)
            if args.filter_to:
                filter_to = _parse_date_to_timestamp(args.filter_to)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    if args.from_api:
        subdomain = os.getenv("KOMMO_SUBDOMAIN", "").strip().removesuffix(".kommo.com")
        token = os.getenv("KOMMO_ACCESS_TOKEN", "").strip()
        if not subdomain or not token:
            print("Defina KOMMO_SUBDOMAIN e KOMMO_ACCESS_TOKEN para usar --from-api.", file=sys.stderr)
            return 2
        leads = extract_leads(
            fetch_kommo_collection(subdomain, token, "leads", filter_from=filter_from, filter_to=filter_to)
        )
        messages = extract_messages(
            fetch_kommo_collection(subdomain, token, "leads/notes", filter_from=filter_from, filter_to=filter_to)
        )
    else:
        if not args.messages_file:
            print("--messages-file é obrigatório quando --leads-file é usado.", file=sys.stderr)
            return 2
        leads = extract_leads(read_json_or_csv(args.leads_file))
        messages = extract_messages(read_json_or_csv(args.messages_file))

    report = analyze(leads, messages, stale_hours=args.stale_hours)
    if args.format == "json":
        content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(content)
    elif args.format == "csv":
        write_csv(report, args.output)
    else:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(report, top_n=args.top_n))
    print(f"Relatório salvo em {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
