from flask import Blueprint, request, jsonify, current_app
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

felicitacoes_bp = Blueprint("felicitacoes", __name__)

# ---------- Helpers ----------
TZ = ZoneInfo("America/Sao_Paulo")

def _parse_dt_any(s: str | None):
    """Tenta parsear várias formas de data. Retorna (dia, mes) ou None."""
    if not s:
        return None
    s = s.strip()
    fmts = [
        "%Y-%m-%d",  # 1990-10-19
        "%d/%m/%Y",  # 19/10/1990
        "%d/%m",     # 19/10
        "%m-%d",     # 10-19
    ]
    for f in fmts:
        try:
            dt = datetime.strptime(s, f)
            # quando não tem ano, o strptime coloca 1900; a gente ignora
            return dt.day, dt.month
        except Exception:
            pass
    return None

def _is_birthday_today(date_str: str | None) -> bool:
    dm = _parse_dt_any(date_str)
    if not dm:
        return False
    d, m = dm
    now = datetime.now(TZ)
    return (d == now.day and m == now.month)

def _compose_message(nome: str) -> str:
    nome_fmt = (nome or "").strip().title()
    return (
        f"🎉 Olá, {nome_fmt}! 🎂\n\n"
        f"A equipe ADEMICON deseja a você um FELIZ ANIVERSÁRIO! 🎈\n"
        f"Que seu novo ciclo venha cheio de alegrias, conquistas e sucesso! ✨"
    )

def _send_whatsapp(telefone: str, texto: str):
    """
    Envia texto via WhatsApp API. Espera:
    - WHATSAPP_API_URL  (ex: https://graph.facebook.com/v21.0/<PHONE_ID>/messages)
    - WHATSAPP_API_TOKEN (Bearer ...)
    Payload usa formato 'text' simples do WhatsApp Cloud API.
    """
    url = current_app.config.get("WHATSAPP_API_URL")
    token = current_app.config.get("WHATSAPP_API_TOKEN")
    if not url or not token:
        raise RuntimeError("WHATSAPP_API_URL/WHATSAPP_API_TOKEN não configurados")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "messaging_product": "whatsapp",
        "to": telefone,
        "type": "text",
        "text": {"body": texto},
    }
    resp = requests.post(url, json=body, headers=headers, timeout=30)
    ok = 200 <= resp.status_code < 300
    data = resp.json() if "application/json" in (resp.headers.get("Content-Type") or "") else resp.text
    return ok, resp.status_code, data

# ---------- Endpoint para o Workato ----------
@felicitacoes_bp.post("/disparar-aniversario")
def disparar_aniversario():
    """
    Endpoint para a receita do Workato chamar 1x/dia.

    Aceita:
    - Objeto único:
      {
        "nome": "Ana",
        "telefone": "5541999999999",
        "nascimento": "1990-10-19"  # pode ser "19/10/1990" ou "19/10"
      }

    - OU lista:
      {
        "itens": [
          {"nome": "...", "telefone": "...", "nascimento": "..."},
          ...
        ]
      }

    Query:
    - ?dry_run=1  -> não envia, só simula
    """
    payload = request.get_json(silent=True) or {}
    dry_run = (request.args.get("dry_run", "0") in {"1", "true", "yes"})

    # Normaliza para lista
    if isinstance(payload, dict) and "itens" in payload and isinstance(payload["itens"], list):
        itens = payload["itens"]
    elif isinstance(payload, dict) and ("nome" in payload or "telefone" in payload):
        itens = [payload]
    elif isinstance(payload, list):
        itens = payload
    else:
        return jsonify({"status": "error", "message": "Formato inválido. Envie objeto único, lista, ou {\"itens\": [...]}."}), 400

    today_dm = (datetime.now(TZ).day, datetime.now(TZ).month)
    sent, skipped, errors = 0, 0, 0
    details = []

    for idx, item in enumerate(itens, start=1):
        nome = (item.get("nome") or "").strip()
        telefone = (item.get("telefone") or "").strip()
        nascimento = (item.get("nascimento") or "").strip()

        if not nome or not telefone:
            errors += 1
            details.append({"idx": idx, "status": "error", "reason": "faltando nome/telefone"})
            continue

        # Se não é hoje, pula
        if not _is_birthday_today(nascimento):
            skipped += 1
            details.append({"idx": idx, "status": "skipped", "reason": "não é aniversário hoje"})
            continue

        texto = _compose_message(nome)

        if dry_run:
            sent += 1
            details.append({"idx": idx, "status": "ok(dry_run)", "telefone": telefone, "mensagem": texto})
            continue

        # Envio real
        try:
            ok, code, data = _send_whatsapp(telefone, texto)
            if ok:
                sent += 1
                details.append({"idx": idx, "status": "ok", "code": code, "response": data})
            else:
                errors += 1
                details.append({"idx": idx, "status": "error", "code": code, "response": data})
        except Exception as e:
            errors += 1
            details.append({"idx": idx, "status": "error", "exception": str(e)})

    return jsonify({
        "status": "ok",
        "summary": {"sent": sent, "skipped": skipped, "errors": errors, "total": len(itens)},
        "today": {"day": today_dm[0], "month": today_dm[1]},
        "dry_run": dry_run,
        "details": details
    }), 200