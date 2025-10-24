# modules/checkin/routes.py
import csv
import io
import re
import datetime as _dt
from zoneinfo import ZoneInfo

from flask import (
    Blueprint, request, render_template, redirect, url_for, flash, Response, jsonify
)
from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError

# Use UMA única origem de modelos. Primeiro tenta 'models', se falhar usa 'yourapp.models'.
try:
    from models import db, EventCheckin, Matricula
except ImportError:
    from yourapp.models import db, EventCheckin, Matricula

# Defina o blueprint UMA vez só, com prefixo
checkin_bp = Blueprint("checkin", __name__, url_prefix="/checkin")

_ONLY_DIGITS = re.compile(r"\D+")


# ======================== Helpers ========================
def _only_digits(s: str) -> str:
    return _ONLY_DIGITS.sub("", s or "")


def _cpf_is_valid(cpf_raw: str) -> bool:
    """
    Validação completa de CPF:
      - 11 dígitos
      - não permite todos iguais
      - confere dígitos verificadores
    """
    cpf = _only_digits(cpf_raw)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False

    # cálculo dos DVs
    def dv(c: str) -> int:
        s = sum(int(n) * w for n, w in zip(c, range(len(c) + 1, 1, -1)))
        r = (s * 10) % 11
        return 0 if r == 10 else r

    d1 = dv(cpf[:9])
    d2 = dv(cpf[:10])
    return cpf[-2:] == f"{d1}{d2}"


def _parse_birth_date_to_iso(s: str) -> str | None:
    """
    Aceita 'YYYY-MM-DD' ou 'DD/MM/YYYY' e devolve 'YYYY-MM-DD'.
    """
    s = (s or "").strip()
    if not s:
        return None
    # ISO direto
    try:
        _dt.date.fromisoformat(s)
        return s
    except ValueError:
        pass
    # BR 'DD/MM/YYYY'
    try:
        d = _dt.datetime.strptime(s, "%d/%m/%Y").date()
        return d.isoformat()
    except ValueError:
        return None


def _parse_event_date() -> _dt.date:
    event = request.args.get("event") or request.form.get("event")
    if event:
        try:
            return _dt.date.fromisoformat(event)
        except ValueError:
            pass
    return _dt.date.today()


# ======================== Rotas ========================
@checkin_bp.get("/")
def checkin_page():
    event_date = _parse_event_date()
    return render_template("checkin/page.html", event_date=event_date)


@checkin_bp.post("/")
def checkin_submit():
    event_date = _parse_event_date()
    cpf_raw = (request.form.get("cpf") or "").strip()
    birth_raw = (request.form.get("birth_date") or "").strip()

    cpf = _only_digits(cpf_raw)
    if not _cpf_is_valid(cpf):
        flash("Informe um CPF válido.", "error")
        return redirect(url_for("checkin.checkin_page", event=event_date.isoformat()))

    birth_iso = _parse_birth_date_to_iso(birth_raw)
    if not birth_iso:
        flash("Informe a data de nascimento válida (YYYY-MM-DD ou DD/MM/YYYY).", "error")
        return redirect(url_for("checkin.checkin_page", event=event_date.isoformat()))

    # (Opcional) Checagem na base de multiplicadores
    # Se existir Matricula com mesmo CPF e birth_date preenchida, exige match:
    m = Matricula.query.filter_by(cpf=cpf).first()
    if m and m.birth_date and m.birth_date != birth_iso:
        flash("Data de nascimento não confere com a base de multiplicadores.", "error")
        return redirect(url_for("checkin.checkin_page", event=event_date.isoformat()))

    # Upsert por (event_date, cpf)
    existing = EventCheckin.query.filter_by(event_date=event_date, cpf=cpf).first()
    if existing:
        existing.birth_date = birth_iso
        db.session.commit()
        flash("Check-in já existia e foi atualizado. ✅", "success")
        return redirect(url_for("checkin.checkin_success", event=event_date.isoformat()))

    try:
        db.session.add(EventCheckin(event_date=event_date, cpf=cpf, birth_date=birth_iso))
        db.session.commit()
        return redirect(url_for("checkin.checkin_success", event=event_date.isoformat()))
    except IntegrityError:
        # Conflito de unique (concorrência): confirma como sucesso
        db.session.rollback()
        flash("Este CPF já realizou o check-in para esta data. ✅", "success")
        return redirect(url_for("checkin.checkin_success", event=event_date.isoformat()))


@checkin_bp.get("/sucesso")
def checkin_success():
    event_date = _parse_event_date()
    return render_template("checkin/success.html", event_date=event_date)


@checkin_bp.get("/lista")
def checkin_list():
    """Lista HTML dos check-ins do dia (ou ?event=YYYY-MM-DD)."""
    event_date = _parse_event_date()
    rows = (
        EventCheckin.query
        .filter_by(event_date=event_date)
        .order_by(EventCheckin.created_at.asc())
        .all()
    )
    return render_template("checkin/list.html", event_date=event_date, rows=rows)


@checkin_bp.get("/csv")
@checkin_bp.get("/csv")
def checkin_csv():
    """
    Exporta CSV dos check-ins de uma data (ou hoje se não informado).
    """
    # 1) defina event_date primeiro
    event_date = _parse_event_date()

    # 2) carregue as linhas do banco
    rows = (
        EventCheckin.query
        .filter_by(event_date=event_date)
        .order_by(EventCheckin.created_at.asc())
        .all()
    )

    # 3) crie o buffer e o writer ANTES de usar
    buf = io.StringIO()
    w = csv.writer(buf)

    # 4) cabeçalho
    w.writerow(["event_date", "cpf", "birth_date", "created_at_brt", "updated_at_brt"])

    # 5) escreva as linhas (normalizando timezone)
    tz = ZoneInfo("America/Sao_Paulo")
    for r in rows:
        ca = r.created_at
        ua = r.updated_at
        # assume UTC se datetime vier "naive"
        if ca and ca.tzinfo is None:
            ca = ca.replace(tzinfo=_dt.timezone.utc)
        if ua and ua.tzinfo is None:
            ua = ua.replace(tzinfo=_dt.timezone.utc)

        w.writerow([
            r.event_date.isoformat() if r.event_date else "",
            r.cpf,
            r.birth_date or "",
            ca.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S") if ca else "",
            ua.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S") if ua else "",
        ])

    # 6) finalize e retorne
    out = buf.getvalue().encode("utf-8")
    return Response(
        out,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=checkins_{event_date.isoformat()}.csv"}
    )
@checkin_bp.get("/api")
def checkin_api_get():
    """
    Retorna check-ins.
    Query params:
      - cpf=10688046967 (opcional; obrigatório se não autenticado)
      - page=1 (opcional)
      - per_page=50 (opcional; máx 100 recomendado)
      - from=YYYY-MM-DD (opcional)
      - to=YYYY-MM-DD (opcional)
    """
    cpf = (request.args.get("cpf") or "").strip()
    page = max(int(request.args.get("page", 1) or 1), 1)
    per_page = min(max(int(request.args.get("per_page", 50) or 50), 1), 100)

    user = None  # troque por sua verificação de Bearer/JWT

    if not user and not cpf:
        return jsonify(ok=False, error="cpf_required"), 400

    q = EventCheckin.query
    if cpf:
        q = q.filter(EventCheckin.cpf == cpf)

    _from = request.args.get("from")
    _to = request.args.get("to")

    from datetime import datetime
    if _from:
        q = q.filter(EventCheckin.created_at >= datetime.fromisoformat(_from))
    if _to:
        q = q.filter(EventCheckin.created_at <= datetime.fromisoformat(_to))

    q = q.order_by(desc(EventCheckin.created_at))

    items = q.paginate(page=page, per_page=per_page, error_out=False)
    data = [{
        "id": c.id,
        "cpf": c.cpf,
        "birth_date": c.birth_date,
        "event_date": c.event_date.isoformat() if c.event_date else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    } for c in items.items]

    return jsonify(
        ok=True,
        total=items.total,
        page=items.page,
        pages=items.pages,
        per_page=items.per_page,
        checkins=data
    ), 200