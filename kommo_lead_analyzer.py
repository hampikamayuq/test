#!/usr/bin/env python3
"""Analyze Kommo leads and message/notes exports or live API data.

This CLI is intentionally dependency-free so it can run in small automation
containers, cron jobs, and no-code runners that can execute Python scripts.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import email.utils
import hashlib
import hmac
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

CHAT_HISTORY_ERROR_LOG_LIMIT = 3

# ---------------------------------------------------------------------------
# Buying intent / objection / urgency / negative sentiment
# ---------------------------------------------------------------------------

BUYING_KEYWORDS = {
    "pt": ["comprar", "preço", "valor", "orçamento", "contratar", "pagamento", "pix", "cartão", "boleto", "quero", "fechar", "agendar", "marcar"],
    "es": ["comprar", "precio", "valor", "presupuesto", "contratar", "pago", "tarjeta", "quiero", "cerrar"],
    "en": ["buy", "price", "quote", "budget", "hire", "payment", "card", "want", "purchase"],
}
OBJECTION_KEYWORDS = {
    "pt": ["caro", "dúvida", "duvida", "não posso", "nao posso", "depois", "concorrente", "desconto", "problema", "convênio", "plano de saúde"],
    "es": ["caro", "duda", "no puedo", "después", "despues", "competidor", "descuento", "problema"],
    "en": ["expensive", "question", "later", "competitor", "discount", "problem", "can't"],
}
URGENCY_KEYWORDS = {
    "pt": ["hoje", "urgente", "agora", "imediato", "rápido", "rapido", "essa semana", "quanto antes"],
    "es": ["hoy", "urgente", "ahora", "inmediato", "rápido", "rapido"],
    "en": ["today", "urgent", "now", "immediate", "fast", "quick"],
}
NEGATIVE_KEYWORDS = {
    "pt": ["não gostei", "nao gostei", "cancelar", "reclamação", "reclamacao", "ruim", "péssimo", "pessimo", "desistir"],
    "es": ["no me gustó", "cancelar", "queja", "malo", "pésimo", "pesimo"],
    "en": ["dislike", "cancel", "complaint", "bad", "terrible"],
}

# ---------------------------------------------------------------------------
# Clinic-specific: specialty, source, city, bot funnel
# ---------------------------------------------------------------------------

SPECIALTY_KEYWORDS: dict[str, list[str]] = {
    "unhas": [
        "ceccarelli", "dr. miguel", "dr miguel", "doutor miguel",
        "doença de unha", "doenças de unha", "onicomicose", "micose de unha", "micose nas unhas",
        "fungo na unha", "fungo nas unhas", "encravada", "unha encravada", "unhas encravadas",
        "paroniquia", "onicólise", "onicolise", "unha amarela", "unha quebrando", "unha fraca",
    ],
    "cabelo": [
        "stohmann", "dra. diana", "dra diana", "doutora diana",
        "tricologia", "transplante capilar", "queda de cabelo", "queda capilar",
        "alopecia", "calvície", "calvicie", "fio quebrando", "cabelo fraco",
        "alopecia areata", "effluvium", "eflúvio", "efflúvio", "dermatite seborreica no couro",
        "caspa intensa", "couro cabeludo", "perda capilar",
    ],
    "cirurgia_derm": [
        "galvez", "dr. diego", "dr diego", "doutor diego",
        "cirurgia dermatológica", "cirurgia dermatologica",
        "nevo", "nevo melanocítico", "melanoma", "carcinoma", "carcinoma basocelular",
        "carcinoma espinocelular", "biópsia", "biopsia", "excisão", "excisao",
        "ceratose", "pinta suspeita", "mancha suspeita", "lesão na pele", "lesao na pele",
        "caroço na pele", "caroco na pele", "tumor de pele", "cirurgia de pele",
    ],
    "psoriase": [
        "psoríase", "psoriase", "psoriasis",
        "placa vermelha descamando", "placas vermelhas", "pele descamando em placas",
        "descamação na pele", "psoríase em placas",
    ],
    "dermatite_atopica": [
        "dermatite atópica", "dermatite atopica", "eczema atópico", "eczema atopico",
        "dermatite", "eczema", "atopia", "pele com coceira intensa", "coceira intensa",
        "alergia na pele", "prurido", "pele ressecada com coceira",
    ],
    "hidradenite": [
        "hidradenite", "hidrosadenite", "hidradenite supurativa",
        "nódulo na axila", "nodulo na axila", "nódulos na axila", "abscesso na axila",
        "abscesso recorrente", "furúnculo recorrente", "furunculo recorrente",
        "nódulos na virilha", "nodulos na virilha",
    ],
    "auto_inflamatoria": [
        "manuela", "manuela pedretti", "dra. manuela", "dra manuela", "doutora manuela",
        "doenças autoinflamatórias", "doença autoinflamatória", "autoinflamatória",
        "doenças autoinflamatorias", "doenca autoinflamatoria", "autoinflamatoria",
        "doença inflamatória sistêmica", "inflamação sistêmica",
    ],
    "dermatopediatria": [
        "dermatopediatria", "dermatologia infantil", "pediatria",
        "criança com", "filho com", "filha com", "bebê com", "bebe com",
        "dermatite no bebê", "manchinha no bebê", "alergia no bebê", "dermatologista infantil",
        "criança de", "filho de", "recém-nascido",
    ],
    "estetica": [
        "botox", "toxina botulínica", "toxina botulinica",
        "peeling", "laser estético", "laser estetico", "laser",
        "harmonização", "harmonizacao", "preenchimento",
        "ácido hialurônico", "acido hialuronico", "bioestimulador", "skinbooster",
        "microagulhamento", "fio de sustentação", "fio de sustentacao",
        "rejuvenescimento", "lifting", "manchas por laser",
    ],
    "pele_geral": [
        "acne", "espinha", "cravos", "mancha na pele", "manchas", "melasma",
        "dermatologia geral", "dermatologista", "consulta de pele", "consulta dermatológica",
        "quero consulta", "preciso de dermatologista", "rosacea", "rosácea",
        "urticária", "urticaria", "vitiligo", "herpes", "coceira", "pele oleosa",
        "pele sensível", "alergia", "vermelhidão", "vermelhidao",
    ],
}

SPECIALTY_LABELS: dict[str, str] = {
    "unhas":             "Unhas (Dr. Miguel)",
    "cabelo":            "Cabelo (Dra. Diana)",
    "cirurgia_derm":     "Cirurgia (Dr. Diego)",
    "psoriase":          "Psoríase (Dra. Manuela)",
    "dermatite_atopica": "Dermatite Atópica (Dra. Manuela)",
    "hidradenite":       "Hidradenite (Dra. Manuela)",
    "auto_inflamatoria": "Auto-inflamatória (Dra. Manuela)",
    "dermatopediatria":  "Dermatopediatria",
    "estetica":          "Estética",
    "pele_geral":        "Pele Geral",
}

SOURCE_KEYWORDS: dict[str, list[str]] = {
    "instagram": [
        "instagram", "insta", "pelo insta", "pelo instagram",
        "vi no instagram", "stories", "reels", "feed do instagram",
        "post do instagram", "instagram da clínica", "instagram da clinica",
    ],
    "anuncio": [
        "anúncio", "anuncio", "através do anúncio", "atraves do anuncio",
        "vi um anúncio", "vi um anuncio", "propaganda", "publicidade", "patrocinado",
        "sponsored", "anúncio no facebook", "anuncio no facebook",
        "anúncio no google", "anuncio no google", "google ads", "trafego pago",
        "tráfego pago", "utm_source", "utm_medium=cpc", "utm_medium=paid",
        "link patrocinado",
    ],
    "pagina": [
        "pela página", "pela pagina", "pelo site", "pela page", "website",
        "pela internet", "no google", "busquei no google", "pesquisei no google",
        "encontrei no google", "site de vocês", "site de voces", "busca",
    ],
}
SOURCE_LABELS: dict[str, str] = {
    "instagram": "Instagram",
    "anuncio":   "Anúncio",
    "pagina":    "Site/Página",
    "direto":    "Direto/Indicação",
}

CITY_KEYWORDS: dict[str, list[str]] = {
    "Copacabana": [
        "copacabana", "santa clara", "rua santa clara", "posto 6", "posto seis",
        "unidade copacabana", "copa", "perto de copacabana",
    ],
    "Barra da Tijuca": [
        "barra da tijuca", "barra tijuca", "na barra", "unidade barra",
        "downtown", "américas", "americas", "barra shopping",
    ],
    "São Paulo": [
        "são paulo", "sao paulo", "itaim", "itaim bibi", "joaquim floriano",
        "faria lima", "paulista", "jardins", "pinheiros", "em sp", "em são paulo",
        "unidade sp", "unidade são paulo",
    ],
}

PAYMENT_KEYWORDS: dict[str, list[str]] = {
    "convenio": [
        "convênio", "convenio", "plano de saúde", "plano de saude",
        "unimed", "bradesco saúde", "bradesco saude", "amil", "sulamerica",
        "sul américa", "notre dame", "hapvida", "assim saúde", "assim saude",
        "golden cross", "porto seguro saúde", "porto seguro saude",
    ],
    "particular": ["particular", "sem convênio", "sem convenio"],
}

RETURN_PATIENT_KEYWORDS = [
    "já sou paciente", "ja sou paciente", "já consultei", "ja consultei",
    "retorno", "remarcar", "já fui", "ja fui", "já fiz tratamento",
    "acompanhamento", "já realizei", "ja realizei", "paciente antiga",
    "paciente antigo", "voltei", "voltar a consultar",
]

WEEKDAY_NAMES = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

# Bot funnel stages ordered from highest to lowest
_BOT_FUNNEL: list[tuple[int, list[str]]] = [
    (5, ["entrará em contato", "entrara em contato", "oferecer toda a ajuda"]),
    (4, ["prontos para agendar", "melhor horário para você", "melhor horario para voce",
         "dia e horário seriam ideais", "dia e horario seriam ideais"]),
    (3, ["qual cidade", "em qual cidade"]),
    (2, ["ceccarelli", "galvez", "stohmann", "manuela pedretti", "sobre a consulta",
         "consulta particular", "dr. diego", "dr diego", "dra. diana", "dra diana",
         "dra. manuela", "dra manuela", "dr. miguel", "dr miguel"]),
    (1, ["seja bem-vindo", "seja bem vindo", "clínica qara", "clinica qara"]),
]
BOT_STAGE_LABELS: dict[int, str] = {
    0: "Sem interação",
    1: "Boas-vindas recebidas",
    2: "Viu perfil do médico",
    3: "Selecionou cidade",
    4: "Chegou ao agendamento",
    5: "Fallback (equipe vai contatar)",
}

# ---------------------------------------------------------------------------
# Field key aliases
# ---------------------------------------------------------------------------

TEXT_KEYS = ("text", "message", "body", "comment", "note", "content", "value")
TIME_KEYS = ("created_at", "updated_at", "date", "timestamp", "createdAt", "time")
LEAD_ID_KEYS = ("lead_id", "entity_id", "parent_id", "id_lead", "leadId")

# Kommo note_type integers → direction
# 4 = incoming call, 10 = outgoing call, 25 = incoming SMS, 26 = outgoing SMS,
# 102 = incoming message, 103 = outgoing message
_NOTE_TYPE_INCOMING = {4, 25, 102}
_NOTE_TYPE_OUTGOING = {10, 26, 103}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_timestamp(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
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


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

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


def extract_custom_field_messages(leads_raw: Iterable[dict[str, Any]]) -> list[Message]:
    """Treat each lead's non-empty custom field values as a single incoming message.

    Kommo SalesBot and many WhatsApp integrations store chatbot answers
    (city, specialty, payment type, etc.) in custom fields rather than notes.
    Concatenating those values lets the keyword detectors find the right data.
    """
    messages: list[Message] = []
    _SKIP_VALUES = {"none", "null", "false", "0", "-", "n/a", "não informado", "nao informado"}
    for lead in leads_raw:
        lead_id = str(lead.get("id", "")).strip()
        if not lead_id:
            continue
        fields = lead.get("custom_fields_values") or []
        if not isinstance(fields, list):
            continue
        parts: list[str] = []
        for field in fields:
            if not isinstance(field, dict):
                continue
            field_name = str(field.get("field_name") or "").strip()
            for v in (field.get("values") or []):
                if isinstance(v, dict):
                    raw_val = v.get("value")
                    # enum fields: prefer the enum label when present
                    enum_val = v.get("enum_value") or (v.get("value") if isinstance(v.get("value"), str) else None)
                    val = str(enum_val or raw_val or "").strip()
                else:
                    val = str(v or "").strip()
                if val and val.lower() not in _SKIP_VALUES:
                    parts.append(f"{field_name}: {val}" if field_name else val)
        if parts:
            messages.append(Message(
                lead_id=lead_id,
                text=" | ".join(parts),
                created_at=parse_timestamp(lead.get("created_at")),
                direction="incoming",
                raw={"_source": "custom_fields"},
            ))
    return messages


def infer_direction(row: dict[str, Any], text: str) -> str:
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
            nt_str = str(note_type_raw).lower()
            if nt_str in ("chat_in", "sms_in", "call_in"):
                return "incoming"
            if nt_str in ("chat_out", "sms_out", "call_out"):
                return "outgoing"

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


# ---------------------------------------------------------------------------
# Clinic-specific detection
# ---------------------------------------------------------------------------

def _match_keywords(text_lower: str, catalog: dict[str, list[str]]) -> str | None:
    for key, words in catalog.items():
        if any(w in text_lower for w in words):
            return key
    return None


def detect_source(messages: list[Message]) -> str:
    # Scan all incoming messages so UTM data stored in custom fields is found.
    for msg in messages:
        if msg.direction == "incoming":
            match = _match_keywords(msg.text.lower(), SOURCE_KEYWORDS)
            if match:
                return match
    return "direto"


def detect_specialty(messages: list[Message]) -> str | None:
    full_text = " ".join(m.text.lower() for m in messages)
    return _match_keywords(full_text, SPECIALTY_KEYWORDS)


def detect_city(messages: list[Message]) -> str | None:
    full_text = " ".join(m.text.lower() for m in messages)
    for city, words in CITY_KEYWORDS.items():
        if any(w in full_text for w in words):
            return city
    return None


def detect_bot_stage(messages: list[Message]) -> int:
    outgoing_text = " ".join(m.text.lower() for m in messages if m.direction in ("outgoing", "unknown"))
    if not outgoing_text:
        return 0
    for stage, phrases in _BOT_FUNNEL:
        if any(p in outgoing_text for p in phrases):
            return stage
    return 0


def detect_payment_type(messages: list[Message]) -> str | None:
    """Return 'convenio', 'particular', or None if not mentioned."""
    incoming_text = " ".join(m.text.lower() for m in messages if m.direction == "incoming")
    return _match_keywords(incoming_text, PAYMENT_KEYWORDS)


def detect_return_patient(messages: list[Message]) -> bool:
    """Return True if the patient indicates they have been to the clinic before."""
    incoming_text = " ".join(m.text.lower() for m in messages if m.direction == "incoming")
    return any(kw in incoming_text for kw in RETURN_PATIENT_KEYWORDS)


def compute_first_response_seconds(messages: list[Message]) -> int | None:
    """Return seconds from the first incoming message to the first outgoing reply."""
    sorted_msgs = sorted(messages, key=lambda m: m.created_at or 0)
    first_in: Message | None = None
    for msg in sorted_msgs:
        if msg.direction == "incoming" and first_in is None:
            first_in = msg
        elif msg.direction == "outgoing" and first_in is not None:
            if first_in.created_at and msg.created_at and msg.created_at >= first_in.created_at:
                return msg.created_at - first_in.created_at
    return None


# ---------------------------------------------------------------------------
# Keyword scoring
# ---------------------------------------------------------------------------

def count_keywords(text: str, catalog: dict[str, list[str]]) -> int:
    lowered = text.lower()
    return sum(1 for words in catalog.values() for word in words if word in lowered)


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def analyze(
    leads: dict[str, Lead],
    messages: list[Message],
    stale_hours: int = 24,
    name_map: dict[str, str] | None = None,
    overdue_task_leads: set[str] | None = None,
    prev_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    by_lead: dict[str, list[Message]] = defaultdict(list)
    for message in messages:
        by_lead[message.lead_id].append(message)
        if message.lead_id not in leads:
            leads[message.lead_id] = Lead(id=message.lead_id, name="Lead " + message.lead_id)

    now = int(time.time())
    lead_reports: list[dict[str, Any]] = []
    response_times: list[int] = []
    pipeline_counter: Counter[str] = Counter()
    status_counter: Counter[str] = Counter()
    specialty_counter: Counter[str] = Counter()
    source_counter: Counter[str] = Counter()
    city_counter: Counter[str] = Counter()
    bot_stage_counter: Counter[int] = Counter()
    payment_counter: Counter[str] = Counter()
    return_patient_count = 0
    leads_by_hour: Counter[int] = Counter()
    leads_by_weekday: Counter[int] = Counter()

    for lead in leads.values():
        pipeline_name = (name_map or {}).get(lead.pipeline_id, lead.pipeline_id) if lead.pipeline_id else ""
        status_name = (name_map or {}).get(lead.status_id, lead.status_id) if lead.status_id else ""
        pipeline_counter[pipeline_name or "unknown"] += 1
        status_counter[status_name or "unknown"] += 1

        if lead.created_at:
            ts = dt.datetime.fromtimestamp(lead.created_at, tz=dt.timezone.utc)
            leads_by_hour[ts.hour] += 1
            leads_by_weekday[ts.weekday()] += 1

        lead_messages = sorted(by_lead.get(lead.id, []), key=lambda m: m.created_at or 0)
        full_text = " ".join(m.text for m in lead_messages)

        buying = count_keywords(full_text, BUYING_KEYWORDS)
        objections = count_keywords(full_text, OBJECTION_KEYWORDS)
        urgency = count_keywords(full_text, URGENCY_KEYWORDS)
        negative = count_keywords(full_text, NEGATIVE_KEYWORDS)

        last_message = lead_messages[-1] if lead_messages else None
        unanswered = bool(last_message and last_message.direction == "incoming")
        stale = bool(
            unanswered
            and last_message.created_at
            and now - last_message.created_at > stale_hours * 3600
        )
        has_overdue_task = lead.id in (overdue_task_leads or set())
        score = max(
            0,
            min(
                100,
                20
                + buying * 18
                + urgency * 15
                - objections * 8
                - negative * 18
                + (10 if lead.price else 0)
                + (-15 if stale else 0)
                + (10 if has_overdue_task else 0),
            ),
        )

        pending_incoming: Message | None = None
        for msg in lead_messages:
            if msg.direction == "incoming":
                pending_incoming = msg
            elif msg.direction == "outgoing" and pending_incoming and pending_incoming.created_at and msg.created_at:
                if msg.created_at >= pending_incoming.created_at:
                    response_times.append(msg.created_at - pending_incoming.created_at)
                    pending_incoming = None

        last_ts = last_message.created_at if last_message else None
        unanswered_hours: float | None = round((now - last_ts) / 3600, 1) if unanswered and last_ts else None

        source = detect_source(lead_messages)
        specialty = detect_specialty(lead_messages)
        city = detect_city(lead_messages)
        bot_stage = detect_bot_stage(lead_messages)
        payment_type = detect_payment_type(lead_messages)
        return_patient = detect_return_patient(lead_messages)
        first_response = compute_first_response_seconds(lead_messages)

        source_counter[source] += 1
        specialty_counter[specialty or "desconhecida"] += 1
        city_counter[city or "não identificada"] += 1
        bot_stage_counter[bot_stage] += 1
        if payment_type:
            payment_counter[payment_type] += 1
        if return_patient:
            return_patient_count += 1

        prev_lead = (prev_snapshot or {}).get(lead.id, {})
        prev_bot_stage: int | None = prev_lead.get("bot_stage") if prev_lead else None
        delta_bot_stage: int | None = (bot_stage - prev_bot_stage) if prev_bot_stage is not None else None

        lead_reports.append(
            {
                "lead_id": lead.id,
                "name": lead.name,
                "pipeline_id": lead.pipeline_id or None,
                "pipeline_name": pipeline_name or None,
                "status_id": lead.status_id or None,
                "status_name": status_name or None,
                "price": lead.price,
                "messages": len(lead_messages),
                "source": source,
                "specialty": specialty,
                "specialty_label": SPECIALTY_LABELS.get(specialty, specialty) if specialty else None,
                "city": city,
                "bot_stage": bot_stage,
                "bot_stage_label": BOT_STAGE_LABELS.get(bot_stage, str(bot_stage)),
                "payment_type": payment_type,
                "return_patient": return_patient,
                "first_response_seconds": first_response,
                "has_overdue_task": has_overdue_task,
                "buying_signals": buying,
                "objections": objections,
                "urgency_signals": urgency,
                "negative_signals": negative,
                "last_direction": last_message.direction if last_message else None,
                "unanswered": unanswered,
                "unanswered_hours": unanswered_hours,
                "stale_unanswered": stale,
                "priority_score": score,
                "prev_bot_stage": prev_bot_stage,
                "delta_bot_stage": delta_bot_stage,
                "recommendation": recommend(score, unanswered, stale, objections, has_overdue_task),
            }
        )

    lead_reports.sort(
        key=lambda r: (r["has_overdue_task"], r["stale_unanswered"], r["unanswered"], r["priority_score"], r["messages"]),
        reverse=True,
    )

    avg_response = sum(response_times) / len(response_times) if response_times else None

    specialty_booked: Counter[str] = Counter()
    for r in lead_reports:
        if r["bot_stage"] >= 4:
            specialty_booked[r["specialty"] or "desconhecida"] += 1

    first_response_list = [r["first_response_seconds"] for r in lead_reports if r["first_response_seconds"] is not None]
    avg_first_response = sum(first_response_list) / len(first_response_list) if first_response_list else None

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": {
            "total_leads": len(leads),
            "total_messages": len(messages),
            "unanswered_leads": sum(1 for r in lead_reports if r["unanswered"]),
            "stale_unanswered_leads": sum(1 for r in lead_reports if r["stale_unanswered"]),
            "overdue_task_leads": sum(1 for r in lead_reports if r["has_overdue_task"]),
            "return_patients": return_patient_count,
            "average_response_seconds": round(avg_response, 2) if avg_response is not None else None,
            "average_first_response_seconds": round(avg_first_response, 2) if avg_first_response is not None else None,
            "pipelines": dict(pipeline_counter),
            "statuses": dict(status_counter),
            "sources": dict(source_counter),
            "specialties": dict(specialty_counter),
            "cities": dict(city_counter),
            "bot_stages": {BOT_STAGE_LABELS.get(k, str(k)): v for k, v in bot_stage_counter.items()},
            "specialty_booked": dict(specialty_booked),
            "payment_types": dict(payment_counter),
            "leads_by_hour": {str(h): c for h, c in sorted(leads_by_hour.items())},
            "leads_by_weekday": {WEEKDAY_NAMES[d]: c for d, c in sorted(leads_by_weekday.items())},
        },
        "leads": lead_reports,
    }


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------

def recommend(
    score: int,
    unanswered: bool,
    stale: bool,
    objections: int,
    has_overdue_task: bool = False,
) -> str:
    if has_overdue_task:
        return "Tarefa vencida: resolver pendência e retomar contato."
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


# ---------------------------------------------------------------------------
# Report rendering — Markdown
# ---------------------------------------------------------------------------

def render_markdown(report: dict[str, Any], top_n: int = 30) -> str:
    summary = report["summary"]
    generated = report.get("generated_at", "")
    all_leads = report["leads"]

    lines: list[str] = ["# Análise de Leads — Clínica QARA", ""]
    if generated:
        lines += [f"_Gerado em {generated}_", ""]

    # --- Overview ---
    avg_resp = format_seconds(summary["average_response_seconds"])
    avg_first = format_seconds(summary.get("average_first_response_seconds"))
    lines += [
        "## Visão Geral",
        "",
        "| Métrica | Valor |",
        "| --- | --- |",
        "| Total de leads | **" + str(summary["total_leads"]) + "** |",
        "| Total de mensagens | **" + str(summary["total_messages"]) + "** |",
        "| Leads sem resposta | **" + str(summary["unanswered_leads"]) + "** |",
        "| Leads vencidos sem resposta | **" + str(summary["stale_unanswered_leads"]) + "** |",
        "| Leads com tarefa vencida | **" + str(summary.get("overdue_task_leads", 0)) + "** |",
        "| Pacientes de retorno | **" + str(summary.get("return_patients", 0)) + "** |",
        "| Tempo médio de resposta | **" + avg_resp + "** |",
        "| Tempo médio 1ª resposta | **" + avg_first + "** |",
        "",
    ]

    # --- By source ---
    sources = summary.get("sources", {})
    if sources:
        total = sum(sources.values()) or 1
        lines += ["## Canal de Captação", ""]
        lines += ["| Canal | Leads | % |", "| --- | ---: | ---: |"]
        for key, cnt in sorted(sources.items(), key=lambda x: -x[1]):
            label = SOURCE_LABELS.get(key, key)
            lines.append("| " + label + " | " + str(cnt) + " | " + str(int(cnt / total * 100)) + "% |")
        lines.append("")

    # --- By specialty ---
    specialties = summary.get("specialties", {})
    booked = summary.get("specialty_booked", {})
    if specialties:
        lines += ["## Por Especialidade", ""]
        lines += ["| Especialidade | Leads | Agendou | Conv. % |", "| --- | ---: | ---: | ---: |"]
        for key, cnt in sorted(specialties.items(), key=lambda x: -x[1]):
            label = SPECIALTY_LABELS.get(key, key)
            bk = booked.get(key, 0)
            conv = str(int(bk / cnt * 100)) + "%" if cnt else "—"
            lines.append("| " + label + " | " + str(cnt) + " | " + str(bk) + " | " + conv + " |")
        lines.append("")

    # --- By city ---
    cities = summary.get("cities", {})
    if cities:
        lines += ["## Por Unidade", ""]
        lines += ["| Unidade | Leads |", "| --- | ---: |"]
        for city, cnt in sorted(cities.items(), key=lambda x: -x[1]):
            lines.append("| " + city + " | " + str(cnt) + " |")
        lines.append("")

    # --- Bot funnel ---
    bot_stages = summary.get("bot_stages", {})
    if bot_stages:
        total_b = sum(bot_stages.values()) or 1
        lines += ["## Funil do Bot", ""]
        lines += ["| Etapa | Leads | % |", "| --- | ---: | ---: |"]
        for label, cnt in sorted(bot_stages.items(), key=lambda x: -x[1]):
            lines.append("| " + label + " | " + str(cnt) + " | " + str(int(cnt / total_b * 100)) + "% |")
        lines.append("")

    # --- Payment types ---
    payment_types = summary.get("payment_types", {})
    if payment_types:
        lines += ["## Tipo de Pagamento Mencionado", ""]
        lines += ["| Tipo | Leads |", "| --- | ---: |"]
        labels = {"convenio": "Convênio", "particular": "Particular"}
        for key, cnt in sorted(payment_types.items(), key=lambda x: -x[1]):
            lines.append("| " + labels.get(key, key) + " | " + str(cnt) + " |")
        lines.append("")

    # --- Hour/weekday analysis ---
    leads_by_hour = summary.get("leads_by_hour", {})
    leads_by_weekday = summary.get("leads_by_weekday", {})
    if leads_by_hour or leads_by_weekday:
        lines += ["## Distribuição Temporal", ""]
        if leads_by_weekday:
            lines += ["**Por dia da semana:**", ""]
            lines += ["| Dia | Leads |", "| --- | ---: |"]
            for day, cnt in leads_by_weekday.items():
                lines.append("| " + day + " | " + str(cnt) + " |")
            lines.append("")
        if leads_by_hour:
            lines += ["**Por hora (UTC):**", ""]
            lines += ["| Hora | Leads |", "| --- | ---: |"]
            for hour, cnt in sorted(leads_by_hour.items(), key=lambda x: int(x[0])):
                lines.append("| " + hour + "h | " + str(cnt) + " |")
            lines.append("")

    # --- Urgent: stale unanswered ---
    stale_leads = [r for r in all_leads if r["stale_unanswered"]]
    if stale_leads:
        lines += ["## ⚠️ Urgente — Sem Resposta Vencida (" + str(len(stale_leads)) + " leads)", ""]
        lines += ["| ID | Lead | Especialidade | Cidade | Sem resposta | Ação |",
                  "| --- | --- | --- | --- | ---: | --- |"]
        for r in stale_leads:
            hours = str(r["unanswered_hours"]) + "h" if r["unanswered_hours"] else "—"
            spec = r.get("specialty_label") or "—"
            city = r.get("city") or "—"
            lines.append(
                "| `" + r["lead_id"] + "` | " + (r["name"] or r["lead_id"]) + " | " +
                spec + " | " + city + " | " + hours + " | " + r["recommendation"] + " |"
            )
        lines.append("")

    # --- Overdue tasks ---
    overdue_leads = [r for r in all_leads if r.get("has_overdue_task") and not r["stale_unanswered"]]
    if overdue_leads:
        lines += ["## 🔔 Tarefa Vencida (" + str(len(overdue_leads)) + " leads)", ""]
        lines += ["| ID | Lead | Especialidade | Score | Ação |",
                  "| --- | --- | --- | ---: | --- |"]
        for r in overdue_leads[:top_n]:
            spec = r.get("specialty_label") or "—"
            lines.append(
                "| `" + r["lead_id"] + "` | " + (r["name"] or r["lead_id"]) + " | " +
                spec + " | " + str(r["priority_score"]) + " | " + r["recommendation"] + " |"
            )
        lines.append("")

    # --- Unanswered (not stale) ---
    unanswered_leads = [r for r in all_leads if r["unanswered"] and not r["stale_unanswered"]]
    if unanswered_leads:
        lines += ["## 📬 Aguardando Resposta (" + str(len(unanswered_leads)) + " leads)", ""]
        lines += ["| ID | Lead | Especialidade | Cidade | Sem resposta | Score |",
                  "| --- | --- | --- | --- | ---: | ---: |"]
        for r in unanswered_leads[:top_n]:
            hours = str(r["unanswered_hours"]) + "h" if r["unanswered_hours"] else "—"
            spec = r.get("specialty_label") or "—"
            city = r.get("city") or "—"
            lines.append(
                "| `" + r["lead_id"] + "` | " + (r["name"] or r["lead_id"]) + " | " +
                spec + " | " + city + " | " + hours + " | " + str(r["priority_score"]) + " |"
            )
        lines.append("")

    # --- Full priority table ---
    lines += ["## Top " + str(top_n) + " Prioridades", ""]
    lines += [
        "| ID | Lead | Canal | Especialidade | Cidade | Etapa Bot | Δ | Pgto | Msgs | Sem resp. | Score | Recomendação |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- | ---: | --- |",
    ]
    for r in all_leads[:top_n]:
        if r["stale_unanswered"] and r["unanswered_hours"]:
            unanswered_label = str(r["unanswered_hours"]) + "h ⚠️"
        elif r["unanswered"] and r["unanswered_hours"]:
            unanswered_label = str(r["unanswered_hours"]) + "h"
        elif r["unanswered"]:
            unanswered_label = "sim"
        else:
            unanswered_label = "—"

        delta = r.get("delta_bot_stage")
        if delta is None:
            delta_label = "—"
        elif delta > 0:
            delta_label = "+" + str(delta)
        elif delta < 0:
            delta_label = str(delta)
        else:
            delta_label = "="

        source_label = SOURCE_LABELS.get(r.get("source", ""), r.get("source", "—")) if r.get("source") else "—"
        spec_label = r.get("specialty_label") or "—"
        city_label = r.get("city") or "—"
        stage_label = r.get("bot_stage_label") or "—"
        pgto = {"convenio": "Convênio", "particular": "Particular"}.get(r.get("payment_type", ""), "—")
        task_flag = " 🔔" if r.get("has_overdue_task") else ""
        lines.append(
            "| `" + r["lead_id"] + "` | " + (r["name"] or r["lead_id"]) + task_flag + " | " +
            source_label + " | " + spec_label + " | " + city_label + " | " +
            stage_label + " | " + delta_label + " | " + pgto + " | " +
            str(r["messages"]) + " | " + unanswered_label + " | " +
            str(r["priority_score"]) + " | " + r["recommendation"] + " |"
        )

    return "\n".join(lines) + "\n"


def format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "n/d"
    minutes = int(seconds // 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return str(hours) + "h " + str(minutes) + "min"
    return str(minutes) + "min"


# ---------------------------------------------------------------------------
# Report rendering — HTML (Chart.js)
# ---------------------------------------------------------------------------

def render_html(report: dict[str, Any], top_n: int = 30) -> str:
    summary = report["summary"]
    generated = report.get("generated_at", "")
    all_leads = report["leads"]

    def _esc(s: Any) -> str:
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    def _lead_row(r: dict[str, Any]) -> str:
        stale_cls = "stale" if r["stale_unanswered"] else ("unanswered" if r["unanswered"] else "")
        task_flag = " 🔔" if r.get("has_overdue_task") else ""
        hours = (str(r["unanswered_hours"]) + "h") if r.get("unanswered_hours") else "—"
        pgto = {"convenio": "Convênio", "particular": "Particular"}.get(r.get("payment_type", ""), "—")
        delta = r.get("delta_bot_stage")
        if delta and delta > 0:
            delta_html = "<span class='up'>+" + str(delta) + "</span>"
        elif delta and delta < 0:
            delta_html = "<span class='dn'>" + str(delta) + "</span>"
        else:
            delta_html = "—"
        score = r["priority_score"]
        score_cls = "hi" if score >= 70 else ("md" if score >= 40 else "lo")
        return (
            "<tr class='" + stale_cls + "'>"
            + "<td><code>" + _esc(r["lead_id"]) + "</code></td>"
            + "<td>" + _esc(r["name"] or r["lead_id"]) + _esc(task_flag) + "</td>"
            + "<td>" + _esc(SOURCE_LABELS.get(r.get("source", ""), r.get("source") or "—")) + "</td>"
            + "<td>" + _esc(r.get("specialty_label") or "—") + "</td>"
            + "<td>" + _esc(r.get("city") or "—") + "</td>"
            + "<td>" + _esc(r.get("bot_stage_label") or "—") + "</td>"
            + "<td>" + delta_html + "</td>"
            + "<td>" + _esc(pgto) + "</td>"
            + "<td>" + str(r["messages"]) + "</td>"
            + "<td>" + _esc(hours) + "</td>"
            + "<td><span class='score " + score_cls + "'>" + str(score) + "</span></td>"
            + "<td class='rec'>" + _esc(r["recommendation"]) + "</td>"
            + "</tr>\n"
        )

    _TBL_HDR = (
        "<tr><th>ID</th><th>Lead</th><th>Canal</th><th>Especialidade</th>"
        "<th>Cidade</th><th>Etapa Bot</th><th>Δ</th><th>Pgto</th>"
        "<th>Msgs</th><th>Sem resp.</th><th>Score</th><th>Recomendação</th></tr>\n"
    )

    def _lead_table(leads_list: list[dict[str, Any]], title: str) -> str:
        if not leads_list:
            return ""
        rows = "".join(_lead_row(r) for r in leads_list)
        return (
            "<section><h2>" + _esc(title) + "</h2>"
            "<div class='tw'><table class='leads'>" + _TBL_HDR + rows + "</table></div></section>\n"
        )

    # Summary cards
    avg_resp = format_seconds(summary.get("average_response_seconds"))
    avg_first = format_seconds(summary.get("average_first_response_seconds"))
    cards_html = "<div class='cards'>" + "".join(
        "<div class='card'><div class='val'>" + _esc(v) + "</div><div class='lbl'>" + _esc(l) + "</div></div>"
        for v, l in [
            (str(summary["total_leads"]),              "Total de Leads"),
            (str(summary["total_messages"]),           "Mensagens"),
            (str(summary["unanswered_leads"]),         "Sem Resposta"),
            (str(summary["stale_unanswered_leads"]),   "Vencidos"),
            (str(summary.get("overdue_task_leads", 0)), "Tarefa Vencida"),
            (str(summary.get("return_patients", 0)),   "Paciente Retorno"),
            (avg_resp,                                 "Tempo Méd. Resposta"),
            (avg_first,                                "1ª Resposta Média"),
        ]
    ) + "</div>\n"

    # Chart.js canvases + JS init blocks
    _GREEN  = ["#2c5f2e", "#4a9e4e", "#76c478", "#a8d5a2", "#c8e6c9", "#1b5e20", "#388e3c", "#66bb6a"]
    _BLUE   = ["#1d4ed8", "#3b82f6", "#60a5fa", "#93c5fd", "#bfdbfe"]
    _PURPLE = ["#6d28d9", "#8b5cf6", "#a78bfa", "#c4b5fd"]
    _ORANGE = ["#c2410c", "#ea580c", "#f97316", "#fb923c", "#fdba74"]

    charts_html: list[str] = []
    js_inits: list[str] = []

    def _chart(
        cid: str,
        title: str,
        ctype: str,
        data: dict[str, int],
        palette: list[str],
        horizontal: bool = False,
    ) -> None:
        if not data:
            return
        n = len(data)
        is_pie = ctype in ("doughnut", "pie")
        h = "260" if is_pie else (str(max(200, n * 34 + 50)) if horizontal else "220")
        labels_j = json.dumps(list(data.keys()), ensure_ascii=False)
        values_j = json.dumps(list(data.values()))
        colors_j = json.dumps([palette[i % len(palette)] for i in range(n)])
        charts_html.append(
            "<div class='cc'><h3>" + _esc(title) + "</h3>"
            "<div style='position:relative;height:" + h + "px'>"
            "<canvas id='" + cid + "'></canvas></div></div>\n"
        )
        if is_pie:
            js_inits.append(
                "new Chart(document.getElementById('" + cid + "'),{"
                "type:'" + ctype + "',"
                "data:{labels:" + labels_j + ",datasets:[{"
                "data:" + values_j + ",backgroundColor:" + colors_j + "}]},"
                "options:{responsive:true,maintainAspectRatio:false,"
                "plugins:{legend:{position:'bottom'}}}});\n"
            )
        elif horizontal:
            js_inits.append(
                "new Chart(document.getElementById('" + cid + "'),{"
                "type:'bar',"
                "data:{labels:" + labels_j + ",datasets:[{"
                "data:" + values_j + ",backgroundColor:" + colors_j + ",borderRadius:3}]},"
                "options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,"
                "plugins:{legend:{display:false}},"
                "scales:{x:{beginAtZero:true,ticks:{stepSize:1}}}}});\n"
            )
        else:
            js_inits.append(
                "new Chart(document.getElementById('" + cid + "'),{"
                "type:'" + ctype + "',"
                "data:{labels:" + labels_j + ",datasets:[{"
                "data:" + values_j + ",backgroundColor:" + colors_j + ",borderRadius:3}]},"
                "options:{responsive:true,maintainAspectRatio:false,"
                "plugins:{legend:{display:false}},"
                "scales:{y:{beginAtZero:true,ticks:{stepSize:1}}}}});\n"
            )

    sources_map = {SOURCE_LABELS.get(k, k): v for k, v in summary.get("sources", {}).items()}
    spec_map    = {SPECIALTY_LABELS.get(k, k): v
                   for k, v in sorted(summary.get("specialties", {}).items(), key=lambda x: -x[1])}
    city_map    = dict(sorted(summary.get("cities", {}).items(), key=lambda x: -x[1]))
    bot_map     = dict(sorted(summary.get("bot_stages", {}).items(), key=lambda x: -x[1]))
    hour_map    = {k + "h": v
                   for k, v in sorted(summary.get("leads_by_hour", {}).items(), key=lambda x: int(x[0]))}
    wday_map    = summary.get("leads_by_weekday", {})

    _chart("srcChart",  "Canal de Captação",      "doughnut", sources_map, _GREEN)
    _chart("specChart", "Por Especialidade",       "bar",      spec_map,    _GREEN,  horizontal=True)
    _chart("cityChart", "Por Unidade",             "doughnut", city_map,    _BLUE)
    _chart("botChart",  "Funil do Bot",            "bar",      bot_map,     _ORANGE, horizontal=True)
    _chart("hourChart", "Leads por Hora (UTC)",    "bar",      hour_map,    _PURPLE)
    _chart("wdayChart", "Leads por Dia da Semana", "bar",      wday_map,    _BLUE)

    stale_leads     = [r for r in all_leads if r["stale_unanswered"]]
    overdue_leads   = [r for r in all_leads if r.get("has_overdue_task") and not r["stale_unanswered"]]
    unanswered_only = [r for r in all_leads if r["unanswered"] and not r["stale_unanswered"]]

    tables_html = (
        _lead_table(stale_leads,      "⚠️ Urgente — Sem Resposta Vencida (" + str(len(stale_leads)) + ")")
        + _lead_table(overdue_leads,  "🔔 Tarefas Vencidas (" + str(len(overdue_leads)) + ")")
        + _lead_table(unanswered_only[:top_n], "📬 Aguardando Resposta (" + str(len(unanswered_only)) + ")")
        + _lead_table(all_leads[:top_n], "Top " + str(top_n) + " Prioridades")
    )

    css = (
        "*{box-sizing:border-box;margin:0;padding:0}"
        "body{font-family:system-ui,-apple-system,sans-serif;background:#f8fafc;color:#1e293b;padding:20px}"
        "h1{color:#14532d;font-size:1.75rem;margin-bottom:4px}"
        "h2{color:#166534;font-size:1.15rem;margin:28px 0 10px;border-bottom:2px solid #bbf7d0;padding-bottom:6px}"
        "h3{color:#374151;font-size:.95rem;font-weight:600;margin-bottom:8px}"
        ".sub{color:#6b7280;font-size:.88rem;margin-bottom:24px}"
        ".cards{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:28px}"
        ".card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:14px 20px;min-width:130px;box-shadow:0 1px 3px #0001}"
        ".card .val{font-size:1.8rem;font-weight:700;color:#15803d;line-height:1}"
        ".card .lbl{font-size:.75rem;color:#6b7280;margin-top:4px}"
        ".cg{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:20px;margin-bottom:28px}"
        ".cc{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:18px;box-shadow:0 1px 3px #0001}"
        "section{margin-bottom:28px}"
        ".tw{overflow-x:auto}"
        "table{border-collapse:collapse;width:100%;font-size:.82rem;background:#fff}"
        "th{background:#f1f5f9;color:#374151;font-weight:600;padding:8px 10px;border-bottom:2px solid #e2e8f0;white-space:nowrap;text-align:left}"
        "td{padding:7px 10px;border-bottom:1px solid #f1f5f9;vertical-align:top}"
        "td code{background:#eff6ff;color:#1d4ed8;padding:1px 5px;border-radius:4px;font-size:.78rem}"
        "tr.stale td{background:#fff1f2}"
        "tr.unanswered td{background:#fffbeb}"
        "tr:hover td{background:#f0fdf4!important}"
        ".score{display:inline-block;padding:2px 8px;border-radius:12px;font-weight:700;font-size:.8rem}"
        ".score.hi{background:#dcfce7;color:#15803d}"
        ".score.md{background:#fef9c3;color:#854d0e}"
        ".score.lo{background:#fee2e2;color:#991b1b}"
        ".up{color:#15803d;font-weight:600}"
        ".dn{color:#b91c1c;font-weight:600}"
        ".rec{max-width:260px;font-size:.8rem}"
        "@media(max-width:600px){.cg{grid-template-columns:1fr}}"
    )

    return (
        "<!DOCTYPE html>\n<html lang='pt-BR'>\n<head>\n"
        "<meta charset='UTF-8'>\n"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>\n"
        "<title>Análise de Leads — Clínica QARA</title>\n"
        "<script src='https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js'></script>\n"
        "<style>" + css + "</style>\n"
        "</head>\n<body>\n"
        "<h1>Análise de Leads — Clínica QARA</h1>\n"
        + ("<p class='sub'>Gerado em " + _esc(generated) + "</p>\n" if generated else "")
        + cards_html
        + "<div class='cg'>\n" + "".join(charts_html) + "</div>\n"
        + tables_html
        + "<script>\n" + "".join(js_inits) + "</script>\n"
        "</body>\n</html>\n"
    )


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_csv(report: dict[str, Any], path: str) -> None:
    rows = report["leads"]
    if not rows:
        fieldnames = ["lead_id", "name", "source", "specialty", "city", "bot_stage", "priority_score", "recommendation"]
    else:
        fieldnames = list(rows[0].keys())
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Delta snapshot helpers
# ---------------------------------------------------------------------------

def _snapshot_path(output_path: str) -> str:
    return output_path + ".snapshot.json"


def load_delta_snapshot(output_path: str) -> dict[str, Any] | None:
    path = _snapshot_path(output_path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_delta_snapshot(report: dict[str, Any], output_path: str) -> None:
    snapshot: dict[str, Any] = {}
    for lead in report["leads"]:
        snapshot[lead["lead_id"]] = {
            "bot_stage": lead["bot_stage"],
            "unanswered": lead["unanswered"],
            "priority_score": lead["priority_score"],
        }
    path = _snapshot_path(output_path)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False)


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _api_request(url: str, token: str, timeout: int = 30, max_retries: int = 4) -> bytes:
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
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
            raise RuntimeError("Kommo API returned HTTP " + str(exc.code) + ": " + details) from exc
        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError("Network error reaching Kommo API: " + str(exc.reason)) from exc
    raise RuntimeError("Max retries exceeded: " + str(last_exc)) from last_exc


def _chat_api_headers(
    method: str,
    path: str,
    secret: str,
    body: bytes = b"",
    date_header: str | None = None,
) -> dict[str, str]:
    content_type = "application/json"
    content_md5 = hashlib.md5(body).hexdigest()
    date_value = date_header or email.utils.formatdate(usegmt=True)
    signature_source = "\n".join([method.upper(), content_md5, content_type, date_value, path])
    signature = hmac.new(
        secret.encode("utf-8"),
        signature_source.encode("utf-8"),
        hashlib.sha1,
    ).hexdigest()
    return {
        "Accept": "application/json",
        "Content-Type": content_type,
        "Content-MD5": content_md5,
        "Date": date_value,
        "X-Signature": signature,
    }


def _chat_api_request(
    path: str,
    secret: str,
    query: dict[str, int] | None = None,
    timeout: int = 30,
) -> bytes:
    query_string = urllib.parse.urlencode(query or {})
    url = "https://amojo.kommo.com" + path + (("?" + query_string) if query_string else "")
    headers = _chat_api_headers("GET", path, secret, body=b"")
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError("Kommo Chats API returned HTTP " + str(exc.code) + ": " + details) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Network error reaching Kommo Chats API: " + str(exc.reason)) from exc


def fetch_kommo_collection(
    subdomain: str,
    token: str,
    path: str,
    limit: int = 250,
    filter_from: int | None = None,
    filter_to: int | None = None,
    extra_params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    base = "https://" + subdomain + ".kommo.com/api/v4/" + path.lstrip("/")
    params: dict[str, str] = {"limit": str(limit)}
    if filter_from is not None:
        params["filter[created_at][from]"] = str(filter_from)
    if filter_to is not None:
        params["filter[created_at][to]"] = str(filter_to)
    if extra_params:
        params.update(extra_params)
    url = base + "?" + urllib.parse.urlencode(params)
    rows: list[dict[str, Any]] = []
    while url:
        raw = _api_request(url, token).decode("utf-8").strip()
        if not raw:
            break
        payload = json.loads(raw)
        embedded = payload.get("_embedded", {}) if isinstance(payload, dict) else {}
        for value in embedded.values():
            if isinstance(value, list):
                rows.extend(item for item in value if isinstance(item, dict))
                break
        next_href = payload.get("_links", {}).get("next", {}).get("href") if isinstance(payload, dict) else None
        url = str(urllib.parse.urljoin(url, next_href)) if next_href else ""
    return rows


def fetch_pipeline_names(subdomain: str, token: str) -> dict[str, str]:
    """Return a dict mapping pipeline/status IDs to human-readable names."""
    url = "https://" + subdomain + ".kommo.com/api/v4/leads/pipelines"
    try:
        payload = json.loads(_api_request(url, token).decode("utf-8"))
    except RuntimeError:
        return {}
    name_map: dict[str, str] = {}
    pipelines = payload.get("_embedded", {}).get("pipelines", [])
    for pipeline in pipelines:
        if not isinstance(pipeline, dict):
            continue
        pid = str(pipeline.get("id", ""))
        if pid:
            name_map[pid] = str(pipeline.get("name", pid))
        statuses = pipeline.get("_embedded", {}).get("statuses", [])
        for status in statuses:
            if not isinstance(status, dict):
                continue
            sid = str(status.get("id", ""))
            if sid:
                name_map[sid] = str(status.get("name", sid))
    return name_map


def fetch_overdue_task_lead_ids(subdomain: str, token: str) -> set[str]:
    """Return set of lead IDs that have at least one incomplete overdue task."""
    now_ts = int(time.time())
    try:
        rows = fetch_kommo_collection(
            subdomain,
            token,
            "tasks",
            extra_params={
                "filter[is_completed]": "0",
                "filter[complete_till][to]": str(now_ts),
            },
        )
    except RuntimeError:
        return set()
    lead_ids: set[str] = set()
    for row in rows:
        entity_id = str(row.get("entity_id") or row.get("lead_id") or "").strip()
        entity_type = str(row.get("entity_type") or "").lower()
        if entity_id and (entity_type in ("leads", "lead", "") or not entity_type):
            lead_ids.add(entity_id)
    return lead_ids


def build_contact_lead_map(leads_raw: list[dict[str, Any]]) -> dict[str, str]:
    """Return {contact_id: lead_id} from leads fetched with with=contacts.

    Many Kommo WhatsApp integrations (Z-API, Wevo, etc.) attach conversation
    notes to the Contact record, not the Lead. This map lets us find the right
    lead when we fetch contacts/notes.
    """
    mapping: dict[str, str] = {}
    for lead in leads_raw:
        lead_id = str(lead.get("id", "")).strip()
        if not lead_id:
            continue
        embedded = lead.get("_embedded") or {}
        if not isinstance(embedded, dict):
            continue
        for contact in (embedded.get("contacts") or []):
            if isinstance(contact, dict):
                cid = str(contact.get("id", "")).strip()
                if cid:
                    mapping[cid] = lead_id
    return mapping


def fetch_contact_messages(
    subdomain: str,
    token: str,
    contact_lead_map: dict[str, str],
    filter_from: int | None = None,
    filter_to: int | None = None,
) -> list[Message]:
    """Fetch notes attached to contacts and translate them to lead messages.

    Needed when the WhatsApp integration (Z-API, native Kommo inbox, etc.)
    stores chat history on the Contact rather than on the Lead.
    """
    if not contact_lead_map:
        return []
    try:
        rows = fetch_kommo_collection(
            subdomain, token, "contacts/notes",
            filter_from=filter_from, filter_to=filter_to,
        )
    except RuntimeError as exc:
        print("Aviso: não foi possível buscar notas de contatos: " + str(exc), file=sys.stderr)
        return []

    messages: list[Message] = []
    for row in rows:
        contact_id = str(row.get("entity_id") or "").strip()
        lead_id = contact_lead_map.get(contact_id)
        if not lead_id:
            continue
        params = row.get("params") if isinstance(row.get("params"), dict) else {}
        text = normalize_text(first_present(row, TEXT_KEYS) or first_present(params, TEXT_KEYS) or params)
        direction = infer_direction(row, text)
        messages.append(Message(
            lead_id=lead_id,
            text=text,
            created_at=parse_timestamp(first_present(row, TIME_KEYS)),
            direction=direction,
            raw=row,
        ))
    return messages


def _chat_history_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    embedded = payload.get("_embedded")
    if isinstance(embedded, dict):
        for key in ("messages", "items"):
            value = embedded.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        for value in embedded.values():
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    for key in ("messages", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _chat_history_text(item: dict[str, Any]) -> str:
    message = item.get("message") if isinstance(item.get("message"), dict) else {}
    return normalize_text(
        item.get("text")
        or item.get("content")
        or item.get("body")
        or message.get("text")
        or message.get("content")
        or message.get("body")
        or ""
    )


def _chat_history_direction(item: dict[str, Any]) -> str:
    direct = str(first_present(item, ("type", "direction")) or "").lower()
    if direct in ("incoming", "inbound", "client", "customer", "contact", "external"):
        return "incoming"
    if direct in ("outgoing", "outbound", "user", "manager", "bot", "amo_bot", "salesbot"):
        return "outgoing"
    for key in ("sender", "author", "from", "user"):
        value = item.get(key)
        if isinstance(value, dict):
            raw_type = str(first_present(value, ("type", "role")) or "").lower()
        else:
            raw_type = str(value or "").lower()
        if raw_type in ("client", "customer", "contact", "external"):
            return "incoming"
        if raw_type in ("user", "manager", "bot", "amo", "amo_bot", "salesbot"):
            return "outgoing"
    return "unknown"


def _fetch_chat_history_messages(
    chat_scope_id: str,
    chat_secret: str,
    chat_id: str,
    lead_id: str,
    max_messages: int = 250,
) -> list[Message]:
    path = (
        "/v2/origin/custom/"
        + urllib.parse.quote(chat_scope_id, safe="")
        + "/chats/"
        + urllib.parse.quote(chat_id, safe="")
        + "/history"
    )
    messages: list[Message] = []
    offset = 0
    limit = 50
    while len(messages) < max_messages:
        raw = _chat_api_request(path, chat_secret, query={"offset": offset, "limit": limit}).decode("utf-8").strip()
        if not raw:
            break
        payload = json.loads(raw)
        items = _chat_history_items(payload)
        if not items:
            break
        for item in items:
            text = _chat_history_text(item)
            if not text:
                continue
            messages.append(Message(
                lead_id=lead_id,
                text=text,
                created_at=parse_timestamp(first_present(item, ("created_at", "timestamp", "msec_timestamp"))),
                direction=_chat_history_direction(item),
                raw=item,
            ))
            if len(messages) >= max_messages:
                break
        if len(items) < limit:
            break
        offset += len(items)
    return messages


def fetch_talks_messages(
    subdomain: str,
    token: str,
    known_lead_ids: set[str] | None = None,
    contact_lead_map: dict[str, str] | None = None,
    max_talks: int = 5000,
    chat_scope_id: str | None = None,
    chat_secret: str | None = None,
) -> list[Message]:
    """Fetch WhatsApp messages from Kommo's native inbox (Talks API).

    Talks can be linked to leads (entity_type=leads) OR to contacts
    (entity_type=contacts). We handle both cases using contact_lead_map to
    resolve contact talks back to lead IDs.
    """
    base_url = "https://" + subdomain + ".kommo.com/api/v4/"
    url: str = base_url + "talks?limit=250"
    talks: list[dict[str, Any]] = []

    _debug_printed = False
    while url and len(talks) < max_talks:
        try:
            raw = _api_request(url, token).decode("utf-8").strip()
        except RuntimeError as exc:
            print("Aviso: erro ao buscar talks: " + str(exc), file=sys.stderr)
            break
        if not raw:
            print("Aviso: /api/v4/talks retornou resposta vazia", file=sys.stderr)
            break
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            break
        embedded = payload.get("_embedded", {}) if isinstance(payload, dict) else {}
        if not _debug_printed:
            _debug_printed = True
            keys = list(embedded.keys()) if isinstance(embedded, dict) else []
            first_value = next(iter(embedded.values()), []) if isinstance(embedded, dict) else []
            sample_talk: dict[str, Any] = {}
            if isinstance(first_value, list) and first_value and isinstance(first_value[0], dict):
                sample_talk = first_value[0]
            elif isinstance(first_value, dict):
                sample_talk = first_value
            print(
                f"DEBUG talks pg1: _embedded={keys}, "
                f"talk fields={list(sample_talk.keys())}, "
                f"entity_type_sample={sample_talk.get('entity_type')!r}",
                file=sys.stderr,
            )
        for v in embedded.values():
            if isinstance(v, list):
                talks.extend(t for t in v if isinstance(t, dict))
                break
        next_href = (payload.get("_links", {}).get("next", {}).get("href")
                     if isinstance(payload, dict) else None)
        url = str(urllib.parse.urljoin(url, next_href)) if next_href else ""

    print(f"Talks total buscados: {len(talks)}", file=sys.stderr)

    # Resolve each talk to a lead_id, handling both entity_type values.
    talk_to_lead: list[tuple[str, str, str, str, str]] = []  # [(talk_id, lead_id, chat_id, origin, source_id)]
    for t in talks:
        talk_id = str(t.get("talk_id") or t.get("id") or "").strip()
        chat_id = str(t.get("chat_id") or t.get("conversation_id") or "").strip()
        origin = str(t.get("origin") or "").strip()
        source_id = str(t.get("source_id") or "").strip()
        entity_id = str(t.get("entity_id") or "").strip()
        entity_type = str(t.get("entity_type") or "").lower()
        if not talk_id or not entity_id:
            continue
        if entity_type in ("leads", "lead", ""):
            if known_lead_ids is None or entity_id in known_lead_ids:
                talk_to_lead.append((talk_id, entity_id, chat_id, origin, source_id))
        elif entity_type in ("contacts", "contact"):
            lead_id = (contact_lead_map or {}).get(entity_id)
            if lead_id and (known_lead_ids is None or lead_id in known_lead_ids):
                talk_to_lead.append((talk_id, lead_id, chat_id, origin, source_id))

    print(f"Talks encontrados: {len(talk_to_lead)} conversas com lead conhecido", file=sys.stderr)

    chat_scope_id = (chat_scope_id if chat_scope_id is not None else os.getenv("KOMMO_CHAT_SCOPE_ID", "")).strip()
    chat_secret = (chat_secret if chat_secret is not None else os.getenv("KOMMO_CHAT_SECRET", "")).strip()
    if not chat_scope_id or not chat_secret:
        if talk_to_lead:
            print(
                "Aviso: histórico de mensagens dos Talks exige KOMMO_CHAT_SCOPE_ID "
                "e KOMMO_CHAT_SECRET da Chats API; pulando mensagens de Talks.",
                file=sys.stderr,
            )
        return []

    messages: list[Message] = []
    missing_chat_ids = 0
    chat_history_errors = 0
    for idx, (talk_id, lead_id, chat_id, origin, source_id) in enumerate(talk_to_lead):
        if not chat_id:
            missing_chat_ids += 1
            continue
        try:
            messages.extend(_fetch_chat_history_messages(chat_scope_id, chat_secret, chat_id, lead_id))
        except (RuntimeError, json.JSONDecodeError) as exc:
            chat_history_errors += 1
            print(
                "Aviso: erro Chats API ao buscar histórico "
                f"(origin={origin or 'n/d'}, source_id={source_id or 'n/d'}, "
                f"talk_id={talk_id or 'n/d'}, chat_id={chat_id or 'n/d'}): {exc}",
                file=sys.stderr,
            )
            if chat_history_errors >= CHAT_HISTORY_ERROR_LOG_LIMIT:
                remaining = len(talk_to_lead) - idx - 1
                if remaining:
                    print(
                        f"Aviso: Chats API retornou {chat_history_errors} erros; "
                        f"pulando {remaining} talks restantes para evitar logs repetidos. "
                        "Confirme se KOMMO_CHAT_SCOPE_ID/KOMMO_CHAT_SECRET pertencem "
                        "ao origin/source_id desses Talks.",
                        file=sys.stderr,
                    )
                break
    if missing_chat_ids:
        print(f"Aviso: {missing_chat_ids} talks sem chat_id para histórico da Chats API", file=sys.stderr)
    return messages


def fetch_whatsapp_notes_per_lead(
    subdomain: str,
    token: str,
    lead_ids: list[str],
    max_leads: int = 500,
) -> list[Message]:
    """Fetch WhatsApp notes (note_type 102/103) per lead as a guaranteed fallback.

    The bulk /api/v4/leads/notes endpoint sometimes omits WhatsApp chat notes.
    Fetching per-lead with explicit note_type filters retrieves them directly.
    Limited to max_leads to keep runtime reasonable.
    """
    base = "https://" + subdomain + ".kommo.com/api/v4/leads/"
    messages: list[Message] = []
    fetched = 0
    for lead_id in lead_ids[:max_leads]:
        url = base + lead_id + "/notes?filter[note_type][]=102&filter[note_type][]=103&limit=250"
        try:
            raw = _api_request(url, token).decode("utf-8").strip()
        except RuntimeError:
            continue
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        embedded = payload.get("_embedded", {}) if isinstance(payload, dict) else {}
        for v in embedded.values():
            if isinstance(v, list):
                for row in v:
                    if not isinstance(row, dict):
                        continue
                    params = row.get("params") if isinstance(row.get("params"), dict) else {}
                    text = normalize_text(
                        first_present(row, TEXT_KEYS) or first_present(params, TEXT_KEYS) or params
                    )
                    if text:
                        messages.append(Message(
                            lead_id=lead_id,
                            text=text,
                            created_at=parse_timestamp(first_present(row, TIME_KEYS)),
                            direction=infer_direction(row, text),
                            raw=row,
                        ))
                break
        fetched += 1

    print(f"Notas WhatsApp por lead: {len(messages)} msgs em {fetched} leads verificados", file=sys.stderr)
    return messages


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analisa leads e mensagens do Kommo para priorizar follow-up — Clínica QARA."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--leads-file", help="Arquivo JSON/CSV com leads exportados do Kommo.")
    source.add_argument(
        "--from-api",
        action="store_true",
        help="Busca leads e notas pela API usando KOMMO_SUBDOMAIN e KOMMO_ACCESS_TOKEN.",
    )
    source.add_argument(
        "--from-report",
        metavar="REPORT_JSON",
        help="Lê um relatório JSON gerado anteriormente e re-renderiza no formato "
             "especificado sem chamar a API novamente.",
    )
    parser.add_argument("--messages-file", help="Arquivo JSON/CSV com mensagens ou notas. Obrigatório com --leads-file.")
    parser.add_argument("--output", default="kommo_analysis.md", help="Caminho do relatório de saída.")
    parser.add_argument(
        "--format",
        choices=("markdown", "json", "csv", "html"),
        default="markdown",
        help="Formato do relatório.",
    )
    parser.add_argument(
        "--stale-hours",
        type=int,
        default=48,
        help="Horas para considerar um lead sem resposta como vencido (padrão: 48).",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=30,
        help="Quantidade de leads exibidos nas tabelas do relatório (padrão: 30).",
    )
    parser.add_argument(
        "--filter-from",
        help="Filtrar leads criados a partir desta data ISO 8601 (ex: 2026-01-01). Apenas com --from-api.",
    )
    parser.add_argument(
        "--filter-to",
        help="Filtrar leads criados até esta data ISO 8601 (ex: 2026-12-31). Apenas com --from-api.",
    )
    parser.add_argument(
        "--no-delta",
        action="store_true",
        help="Desativa o relatório delta (não lê nem salva snapshot anterior).",
    )
    parser.add_argument(
        "--probe",
        metavar="LEAD_ID",
        help="Modo diagnóstico: consulta vários endpoints da API para 1 lead e "
             "imprime o JSON cru. Requer --from-api. Não gera relatório.",
    )
    return parser


def _probe_get(subdomain: str, token: str, path: str) -> Any:
    url = "https://" + subdomain + ".kommo.com/api/v4/" + path.lstrip("/")
    try:
        raw = _api_request(url, token).decode("utf-8", errors="replace").strip()
    except RuntimeError as exc:
        return {"__error__": str(exc)}
    if not raw:
        return {"__empty_body__": True}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"__non_json__": raw[:500]}


def run_probe(subdomain: str, token: str, lead_id: str) -> int:
    """Hit every candidate endpoint for one lead and dump raw JSON.

    This gives ground truth about where Kommo stores conversation content
    for this account, instead of guessing.
    """
    def show(label: str, data: Any) -> None:
        print("\n" + "=" * 70)
        print(label)
        print("=" * 70)
        print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])

    source_ids: set[str] = set()
    chat_history_diagnostic: dict[str, Any] | None = None

    def remember_talks(payload: Any) -> list[dict[str, Any]]:
        remembered: list[dict[str, Any]] = []
        if not isinstance(payload, dict):
            return remembered
        embedded = payload.get("_embedded", {})
        if not isinstance(embedded, dict):
            return remembered
        for value in embedded.values():
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict):
                    continue
                source_id = str(item.get("source_id") or "").strip()
                if source_id:
                    source_ids.add(source_id)
                remembered.append(item)
        return remembered

    lead = _probe_get(subdomain, token, f"leads/{lead_id}?with=contacts,custom_fields")
    show(f"GET /api/v4/leads/{lead_id}?with=contacts,custom_fields", lead)

    contact_ids: list[str] = []
    if isinstance(lead, dict):
        for c in (lead.get("_embedded", {}) or {}).get("contacts", []) or []:
            if isinstance(c, dict) and c.get("id"):
                contact_ids.append(str(c["id"]))

    show(f"GET /api/v4/leads/{lead_id}/notes",
         _probe_get(subdomain, token, f"leads/{lead_id}/notes?limit=50"))

    for cid in contact_ids[:2]:
        show(f"GET /api/v4/contacts/{cid}?with=custom_fields",
             _probe_get(subdomain, token, f"contacts/{cid}?with=custom_fields"))
        show(f"GET /api/v4/contacts/{cid}/notes",
             _probe_get(subdomain, token, f"contacts/{cid}/notes?limit=50"))

    lead_talks = _probe_get(subdomain, token, f"talks?filter[entity_id]={lead_id}&filter[entity_type]=leads")
    show(f"GET /api/v4/talks?filter[entity_id]={lead_id}&filter[entity_type]=leads", lead_talks)
    remember_talks(lead_talks)
    for cid in contact_ids[:1]:
        contact_talks = _probe_get(subdomain, token, f"talks?filter[entity_id]={cid}&filter[entity_type]=contacts")
        show(f"GET /api/v4/talks?filter[entity_id]={cid}&filter[entity_type]=contacts", contact_talks)
        remember_talks(contact_talks)

    talks_any = _probe_get(subdomain, token, "talks?limit=3")
    show("GET /api/v4/talks?limit=3 (amostra geral)", talks_any)
    remember_talks(talks_any)
    if isinstance(talks_any, dict):
        for t in (talks_any.get("_embedded", {}) or {}).get("talks", []) or []:
            if isinstance(t, dict) and (t.get("talk_id") or t.get("id")):
                tid = str(t.get("talk_id") or t["id"])
                chat_id = str(t.get("chat_id") or t.get("conversation_id") or "").strip()
                origin = str(t.get("origin") or "").strip()
                source_id = str(t.get("source_id") or "").strip()
                show(f"GET /api/v4/talks/{tid}",
                     _probe_get(subdomain, token, f"talks/{tid}"))
                if chat_id:
                    chat_scope_id = os.getenv("KOMMO_CHAT_SCOPE_ID", "").strip()
                    chat_secret = os.getenv("KOMMO_CHAT_SECRET", "").strip()
                    if chat_scope_id and chat_secret:
                        history_path = (
                            "/v2/origin/custom/"
                            + urllib.parse.quote(chat_scope_id, safe="")
                            + "/chats/"
                            + urllib.parse.quote(chat_id, safe="")
                            + "/history"
                        )
                        try:
                            raw = _chat_api_request(
                                history_path,
                                chat_secret,
                                query={"offset": 0, "limit": 10},
                            ).decode("utf-8", errors="replace").strip()
                            chat_history_diagnostic = {
                                "status": "ok",
                                "talk_id": tid,
                                "chat_id": chat_id,
                                "origin": origin or None,
                                "source_id": source_id or None,
                            }
                            show(
                                "GET https://amojo.kommo.com" + history_path + "?offset=0&limit=10",
                                json.loads(raw) if raw else {"__empty_body__": True},
                            )
                        except (RuntimeError, json.JSONDecodeError) as exc:
                            chat_history_diagnostic = {
                                "status": "error",
                                "talk_id": tid,
                                "chat_id": chat_id,
                                "origin": origin or None,
                                "source_id": source_id or None,
                                "error": str(exc),
                            }
                            show("Chats API history error", {"__error__": str(exc)})
                    else:
                        chat_history_diagnostic = {
                            "status": "skipped",
                            "talk_id": tid,
                            "chat_id": chat_id,
                            "origin": origin or None,
                            "source_id": source_id or None,
                            "required_secrets": ["KOMMO_CHAT_SCOPE_ID", "KOMMO_CHAT_SECRET"],
                        }
                        show(
                            "Chats API history skipped",
                            {
                                "chat_id": chat_id,
                                "required_secrets": ["KOMMO_CHAT_SCOPE_ID", "KOMMO_CHAT_SECRET"],
                            },
                        )
                show(f"GET /api/v4/talks/{tid}/messages",
                     _probe_get(subdomain, token, f"talks/{tid}/messages?limit=10"))
                break

    def sort_source_id(value: str) -> tuple[int, str]:
        return (0, f"{int(value):020d}") if value.isdigit() else (1, value)

    for source_id in sorted(source_ids, key=sort_source_id)[:5]:
        show(f"GET /api/v4/sources/{source_id}", _probe_get(subdomain, token, f"sources/{source_id}"))

    show("GET /api/v4/account?with=amojo_id (verifica acesso a chats)",
         _probe_get(subdomain, token, "account?with=amojo_id,amojo_rights"))

    diagnostic: dict[str, Any] = {
        "source_ids_found": sorted(source_ids, key=sort_source_id),
        "chat_api_configured": bool(
            os.getenv("KOMMO_CHAT_SCOPE_ID", "").strip()
            and os.getenv("KOMMO_CHAT_SECRET", "").strip()
        ),
        "sample_chat_history": chat_history_diagnostic or {"status": "not_tested"},
    }
    sample_error = str((chat_history_diagnostic or {}).get("error", ""))
    if "HTTP 404" in sample_error:
        diagnostic["next_step"] = (
            "Confirmar com Kommo qual KOMMO_CHAT_SCOPE_ID/KOMMO_CHAT_SECRET pertence "
            "ao source_id/origin destes Talks, ou se este origin nao permite historico "
            "via origin/custom/{scope_id}."
        )
    show("PROBE DIAGNOSTICO FINAL", diagnostic)

    print("\n" + "=" * 70)
    print("FIM DO PROBE — copie TODO este output e cole de volta para análise.")
    print("=" * 70)
    return 0


def _parse_date_to_timestamp(value: str) -> int:
    try:
        return int(dt.datetime.fromisoformat(value).timestamp())
    except ValueError:
        raise ValueError("Data inválida: " + repr(value) + ". Use formato ISO 8601 (ex: 2026-01-01).")


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

    # --from-report: re-render a previously saved JSON report without hitting the API.
    if getattr(args, "from_report", None):
        with open(args.from_report, "r", encoding="utf-8") as fh:
            report = json.load(fh)
        if args.format == "json":
            content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(content)
        elif args.format == "csv":
            write_csv(report, args.output)
        elif args.format == "html":
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(render_html(report, top_n=args.top_n))
        else:
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(render_markdown(report, top_n=args.top_n))
        print("Relatório salvo em " + args.output)
        return 0

    name_map: dict[str, str] | None = None
    overdue_task_lead_ids: set[str] | None = None

    if args.from_api:
        subdomain = os.getenv("KOMMO_SUBDOMAIN", "").strip().removesuffix(".kommo.com")
        token = os.getenv("KOMMO_ACCESS_TOKEN", "").strip()
        if not subdomain or not token:
            print("Defina KOMMO_SUBDOMAIN e KOMMO_ACCESS_TOKEN para usar --from-api.", file=sys.stderr)
            return 2
        if args.probe:
            return run_probe(subdomain, token, str(args.probe).strip())
        # Fetch leads with embedded contacts so we can map contact_id → lead_id.
        # Many WhatsApp integrations (Z-API, Wevo, etc.) attach notes to the
        # Contact record rather than the Lead.
        leads_raw = fetch_kommo_collection(
            subdomain, token, "leads",
            filter_from=filter_from, filter_to=filter_to,
            extra_params={"with": "contacts,custom_fields"},
        )
        leads = extract_leads(leads_raw)
        contact_lead_map = build_contact_lead_map(leads_raw)

        lead_notes = extract_messages(
            fetch_kommo_collection(subdomain, token, "leads/notes", filter_from=filter_from, filter_to=filter_to)
        )
        contact_msgs = fetch_contact_messages(subdomain, token, contact_lead_map, filter_from=filter_from, filter_to=filter_to)
        talks_msgs = fetch_talks_messages(
            subdomain, token,
            known_lead_ids=set(leads.keys()),
            contact_lead_map=contact_lead_map,
        )
        custom_field_msgs = extract_custom_field_messages(leads_raw)
        wa_notes = fetch_whatsapp_notes_per_lead(subdomain, token, list(leads.keys()))
        messages = lead_notes + contact_msgs + talks_msgs + custom_field_msgs + wa_notes
        print(
            f"Mensagens carregadas: {len(lead_notes)} notas de leads"
            f" + {len(contact_msgs)} notas de contatos"
            f" + {len(talks_msgs)} msgs de conversa"
            f" + {len(custom_field_msgs)} campos customizados"
            f" + {len(wa_notes)} notas WhatsApp por lead"
            f" = {len(messages)} total"
        )
        name_map = fetch_pipeline_names(subdomain, token)
        overdue_task_lead_ids = fetch_overdue_task_lead_ids(subdomain, token)
    else:
        if not args.messages_file:
            print("--messages-file é obrigatório quando --leads-file é usado.", file=sys.stderr)
            return 2
        leads = extract_leads(read_json_or_csv(args.leads_file))
        messages = extract_messages(read_json_or_csv(args.messages_file))

    prev_snapshot: dict[str, Any] | None = None
    if not args.no_delta:
        prev_snapshot = load_delta_snapshot(args.output)

    report = analyze(
        leads,
        messages,
        stale_hours=args.stale_hours,
        name_map=name_map,
        overdue_task_leads=overdue_task_lead_ids,
        prev_snapshot=prev_snapshot,
    )

    if not args.no_delta:
        save_delta_snapshot(report, args.output)

    if args.format == "json":
        content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(content)
    elif args.format == "csv":
        write_csv(report, args.output)
    elif args.format == "html":
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(render_html(report, top_n=args.top_n))
    else:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(render_markdown(report, top_n=args.top_n))

    print("Relatório salvo em " + args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
