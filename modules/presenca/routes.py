# modules/presenca/routes.py
import csv
import io
import re
import datetime as dt
from typing import Optional

from flask import Blueprint, request, jsonify, Response, render_template
from sqlalchemy.exc import IntegrityError

from models import db, Matricula, Presenca

# -------------------------------------------------------------------
# Blueprint com prefixo /presenca
# -------------------------------------------------------------------
presenca_bp = Blueprint("presenca", __name__, url_prefix="/presenca")

# padrão MR + 5 dígitos (ex.: MR25684)
FORMAT = re.compile(r"^MR\d{5}$")

# ===================== Helpers =====================

def _today_utc() -> dt.date:
    """Retorna a data (UTC) para controle de presença diária."""
    return dt.datetime.utcnow().date()

def _utcnow() -> dt.datetime:
    return dt.datetime.utcnow()

def _client_ip() -> Optional[str]:
    """Extrai o IP real do cliente (considera X-Forwarded-For)."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        # XFF pode vir como 'ip1, ip2, ip3' — o primeiro é o cliente
        return xff.split(",")[0].strip()
    return request.remote_addr

def _json_error(message: str, status_code: int = 200):
    """Convenção de erro padrão."""
    return jsonify(ok=False, message=message), status_code

def _parse_date(s: Optional[str]) -> Optional[dt.date]:
    try:
        return None if not s else dt.date.fromisoformat(s)
    except Exception:
        return None

# ===================== PÁGINAS (HTML) =====================

@presenca_bp.get("/")
def presenca_page():
    """Entrega o formulário. A interação é via fetch (JS no template)."""
    return render_template("presenca_form.html")

@presenca_bp.get("/sucesso")
def sucesso():
    """Renderiza a página de sucesso. Uso: /presenca/sucesso?code=MR25684"""
    code = (request.args.get("code") or "").strip().upper()
    return render_template("presenca_success.html", code=code)

# ===================== APIs JSON (usadas pelo fetch) =====================

@presenca_bp.post("/api/check")
def api_check():
    """Verifica se a matrícula existe e está ativa. Corpo: { "matricula": "MR25684" }"""
    data = request.get_json(silent=True) or {}
    code = (data.get("matricula") or "").strip().upper()

    if not code:
        return _json_error("Informe a matrícula.", 400)
    if not FORMAT.fullmatch(code):
        return _json_error("Formato inválido (MR + 5 dígitos).")

    m = Matricula.query.filter_by(code=code).first()
    if not m:
        return _json_error("Matrícula não encontrada.")
    if getattr(m, "status", "active") != "active":
        return _json_error(f"Matrícula inativa (status: {m.status}).")

    return jsonify(ok=True, code=code), 200

@presenca_bp.post("/api/registrar")
def api_registrar():
    """
    Registra presença 1x por dia (controle por (matricula_id, date_key)).
    Corpo: { "matricula": "MR25684" }
    """
    data = request.get_json(silent=True) or {}
    code = (data.get("matricula") or "").strip().upper()

    if not FORMAT.fullmatch(code):
        return _json_error("Formato inválido (MR + 5 dígitos).")

    m = Matricula.query.filter_by(code=code).first()
    if not m or getattr(m, "status", "active") != "active":
        return _json_error("Matrícula inválida ou inativa.")

    today = _today_utc()
    p = Presenca(
        matricula_id=m.id,
        date_key=today,
        timestamp=_utcnow(),
        ip=_client_ip(),
        user_agent=(request.user_agent.string or "")[:300],
        source="web",
    )

    db.session.add(p)
    try:
        db.session.commit()
        return jsonify(ok=True, already=False, id=p.id, code=code), 200
    except IntegrityError:
        # Já existe presença para (matricula_id, date_key)
        db.session.rollback()
        return jsonify(ok=True, already=True, code=code, message="Presença já registrada hoje."), 200

# ===================== API GET (REGISTRAR idempotente) =====================

@presenca_bp.get("/api/register")
def presenca_api_register_get():
    """
    Variante GET idempotente para registrar (antes era GET /presenca/api).
    Uso: /presenca/api/register?matricula=MR25684
    """
    code = (request.args.get("matricula") or "").strip().upper()
    if not FORMAT.fullmatch(code):
        return jsonify({"ok": False, "msg": "Formato inválido"}), 400

    m = Matricula.query.filter_by(code=code).first()
    if not m:
        return jsonify({"ok": False, "msg": "Matrícula não encontrada"}), 404
    if getattr(m, "status", "active") != "active":
        return jsonify({"ok": False, "msg": f"Matrícula inativa (status: {m.status})."}), 200

    today = _today_utc()
    p = Presenca(
        matricula_id=m.id,
        date_key=today,
        timestamp=_utcnow(),
        ip=_client_ip(),
        user_agent=(request.user_agent.string or "")[:300],
        source="api",
    )
    db.session.add(p)
    try:
        db.session.commit()
        return jsonify({"ok": True, "already": False, "code": m.code}), 200
    except IntegrityError:
        db.session.rollback()
        return jsonify({"ok": True, "already": True, "code": m.code}), 200

# ===================== API GET (LISTAR) =====================

@presenca_bp.get("/api")
def presenca_api_get():
    """
    Lista presenças com filtros.
    Query params:
      - matricula=MR41081 (opcional; recomendado sem auth)
      - start=YYYY-MM-DD (opcional)
      - end=YYYY-MM-DD (opcional)
      - page=1 (opcional)
      - per_page=50 (opcional; máx 100)
    Obs.: modelo usa Presenca.matricula_id, date_key (date) e timestamp (datetime).
    """
    code  = (request.args.get("matricula") or "").strip().upper()
    start = _parse_date(request.args.get("start"))
    end   = _parse_date(request.args.get("end"))

    # paginação segura
    try:
        page = max(int(request.args.get("page", 1) or 1), 1)
    except ValueError:
        page = 1
    try:
        per_page = min(max(int(request.args.get("per_page", 50) or 50), 1), 100)
    except ValueError:
        per_page = 50

    # base: join para trazer o code, pois a tabela tem matricula_id
    q = db.session.query(
        Presenca.id,
        Presenca.date_key,
        Presenca.timestamp,
        Presenca.ip,
        Presenca.source,
        Matricula.code,
        Matricula.holder_name,
        Matricula.status,
    ).join(Matricula, Matricula.id == Presenca.matricula_id)

    if code:
        if not FORMAT.fullmatch(code):
            return jsonify(ok=False, error="invalid_code_format"), 400
        q = q.filter(Matricula.code == code)
    if start:
        q = q.filter(Presenca.date_key >= start)
    if end:
        q = q.filter(Presenca.date_key <= end)

    q = q.order_by(Presenca.date_key.desc(), Presenca.timestamp.desc())

    # compat 2.x / 3.x
    try:
        page_obj = q.paginate(page=page, per_page=per_page, error_out=False)  # FSAlchemy 2.x
    except AttributeError:
        page_obj = db.paginate(q, page=page, per_page=per_page, error_out=False)  # 3.x

    items = [{
        "id": r.id,
        "date_key": r.date_key.isoformat(),
        "timestamp_utc": r.timestamp.isoformat(),
        "code": r.code,
        "holder_name": r.holder_name,
        "status": r.status,
        "ip": r.ip,
        "source": r.source,
    } for r in page_obj.items]

    return jsonify(
        ok=True,
        total=page_obj.total,
        page=page_obj.page,
        pages=page_obj.pages,
        per_page=page_obj.per_page,
        items=items
    ), 200

# ===================== EXPORTS (CSV e JSON) =====================

@presenca_bp.get("/export.csv")
def export_presencas_csv():
    """
    Exporta presenças em CSV.
    Filtros opcionais: ?start=YYYY-MM-DD&end=YYYY-MM-DD&code=MR25684
    """
    start = _parse_date(request.args.get("start"))
    end   = _parse_date(request.args.get("end"))
    code  = (request.args.get("code") or "").strip().upper()

    q = db.session.query(
        Presenca.id,
        Presenca.date_key,
        Presenca.timestamp,
        Matricula.code,
        Matricula.holder_name,
        Matricula.cpf,
        Matricula.status,
        Presenca.ip,
        Presenca.source,
    ).join(Matricula, Matricula.id == Presenca.matricula_id)

    if start:
        q = q.filter(Presenca.date_key >= start)
    if end:
        q = q.filter(Presenca.date_key <= end)
    if code:
        q = q.filter(Matricula.code == code)

    q = q.order_by(Presenca.date_key.desc(), Presenca.timestamp.desc())
    rows = q.all()

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["id", "date_key", "timestamp_utc", "code", "holder_name", "cpf", "status", "ip", "source"])
    for r in rows:
        writer.writerow([
            r.id,
            r.date_key.isoformat(),
            r.timestamp.isoformat(),
            r.code,
            r.holder_name or "",
            r.cpf or "",
            r.status,
            r.ip or "",
            r.source or "",
        ])

    resp = Response(output.getvalue(), mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = "attachment; filename=presencas.csv"
    return resp

@presenca_bp.get("/export.json")
def export_presencas_json():
    """Exporta presenças em JSON (mesmos filtros do CSV)."""
    start = _parse_date(request.args.get("start"))
    end   = _parse_date(request.args.get("end"))
    code  = (request.args.get("code") or "").strip().upper()

    q = db.session.query(
        Presenca.id,
        Presenca.date_key,
        Presenca.timestamp,
        Matricula.code,
        Matricula.holder_name,
        Matricula.cpf,
        Matricula.status,
        Presenca.ip,
        Presenca.source,
    ).join(Matricula, Matricula.id == Presenca.matricula_id)

    if start:
        q = q.filter(Presenca.date_key >= start)
    if end:
        q = q.filter(Presenca.date_key <= end)
    if code:
        q = q.filter(Matricula.code == code)

    q = q.order_by(Presenca.date_key.desc(), Presenca.timestamp.desc())
    rows = q.all()

    data = [{
        "id": r.id,
        "date_key": r.date_key.isoformat(),
        "timestamp_utc": r.timestamp.isoformat(),
        "code": r.code,
        "holder_name": r.holder_name,
        "cpf": r.cpf,
        "status": r.status,
        "ip": r.ip,
        "source": r.source,
    } for r in rows]

    return jsonify({"count": len(data), "items": data}), 200