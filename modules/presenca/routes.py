# modules/presenca/routes.py
import re
import io
import csv
import datetime as dt
from flask import Blueprint, request, jsonify, Response, render_template
from models import db, Matricula, Presenca

presenca_bp = Blueprint("presenca", __name__)  # adicione url_prefix="/presenca" se quiser

FORMAT = re.compile(r"^MR\d{5}$")  # padrão MR + 5 dígitos

# ------------------ PÁGINA (form + confirmação) ------------------
@presenca_bp.get("/")
def presenca_page():
    """
    GET /?matricula=MR12345
    - sem query: mostra formulário
    - com query: valida e registra presença (se ainda não houver no dia)
    """
    code = (request.args.get("matricula") or "").strip().upper()
    tried = bool(code)
    today = dt.datetime.utcnow().date()

    if not tried:
        return render_template("presenca_form.html", tried=False)

    # valida formato
    if not FORMAT.fullmatch(code):
        return render_template(
            "presenca_form.html",
            tried=True,
            ok=False,
            msg="Formato inválido. Use MR + 5 dígitos (ex: MR25684).",
            matricula=code
        )

    # busca matrícula
    m = Matricula.query.filter_by(code=code).first()
    if not m:
        return render_template(
            "presenca_form.html",
            tried=True,
            ok=False,
            msg="Matrícula não encontrada.",
            matricula=code
        )

    # defensivo
    status = getattr(m, "status", "active")
    if status != "active":
        return render_template(
            "presenca_form.html",
            tried=True,
            ok=False,
            msg=f"Matrícula inativa (status: {status}).",
            matricula=code
        )

    # verifica presença já existente no dia
    existing = Presenca.query.filter_by(matricula_id=m.id, date_key=today).first()
    if existing:
        # já confirmada hoje → página de sucesso
        return render_template("presenca_success.html", code=m.code), 200

    # registra nova presença
    p = Presenca(
        matricula_id=m.id,
        date_key=today,
        timestamp=dt.datetime.utcnow(),
        ip=request.headers.get("X-Forwarded-For", request.remote_addr),
        user_agent=(request.user_agent.string or "")[:300],
        source="link"
    )
    db.session.add(p)
    db.session.commit()

    return render_template("presenca_success.html", code=m.code), 200


# ------------------ API JSON ------------------
@presenca_bp.get("/api")
def presenca_api():
    code = (request.args.get("matricula") or "").strip().upper()
    if not FORMAT.fullmatch(code):
        return jsonify({"ok": False, "msg": "Formato inválido"}), 400

    m = Matricula.query.filter_by(code=code).first()
    if not m:
        return jsonify({"ok": False, "msg": "Matrícula não encontrada"}), 404

    today = dt.datetime.utcnow().date()
    existing = Presenca.query.filter_by(matricula_id=m.id, date_key=today).first()
    if existing:
        return jsonify({"ok": True, "already": True, "code": m.code}), 200

    p = Presenca(
        matricula_id=m.id,
        date_key=today,
        timestamp=dt.datetime.utcnow(),
        ip=request.headers.get("X-Forwarded-For", request.remote_addr),
        user_agent=(request.user_agent.string or "")[:300],
        source="api"
    )
    db.session.add(p)
    db.session.commit()
    return jsonify({"ok": True, "already": False, "code": m.code}), 200


# ------------------ EXPORTS (CSV e JSON) ------------------
def _parse_date(s):
    try:
        return None if not s else __import__("datetime").date.fromisoformat(s)
    except Exception:
        return None

@presenca_bp.get("/export.csv")
def export_presencas_csv():
    """
    Exporta presenças em CSV.
    Filtros opcionais:
      ?start=YYYY-MM-DD&end=YYYY-MM-DD&code=MR25684
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
        Presenca.source
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
    writer.writerow(["id","date_key","timestamp_utc","code","holder_name","cpf","status","ip","source"])
    for r in rows:
        writer.writerow([
            r.id, r.date_key, r.timestamp.isoformat(),
            r.code, r.holder_name or "", r.cpf or "",
            r.status, r.ip or "", r.source or ""
        ])

    resp = Response(output.getvalue(), mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = "attachment; filename=presencas.csv"
    return resp


@presenca_bp.get("/export.json")
def export_presencas_json():
    """
    Exporta presenças em JSON, com os mesmos filtros.
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
        Presenca.source
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
        "source": r.source
    } for r in rows]

    return jsonify({"count": len(data), "items": data}), 200