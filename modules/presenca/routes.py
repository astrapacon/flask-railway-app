# modules/presenca/routes.py
import re
import datetime as dt
from flask import Blueprint, request, jsonify, render_template_string
from models import db, Matricula, Presenca

presenca_bp = Blueprint("presenca", __name__)
FORMAT = re.compile(r"^MR\d{5}$")  # padrão MR + 5 dígitos

# ------------------ Página HTML ------------------
PAGE = """
<!doctype html><meta charset="utf-8">
<title>Confirmação de Presença</title>
<style>
  body{font-family:system-ui;background:#0f172a;color:#e5e7eb;margin:0;
       display:flex;align-items:center;justify-content:center;height:100vh}
  .card{background:#111827;border:1px solid #1f2937;border-radius:16px;padding:24px;
        width:92%;max-width:500px;box-shadow:0 16px 40px rgba(0,0,0,.35)}
  input{width:100%;padding:10px;border-radius:8px;border:1px solid #374151;
        background:#0b1222;color:#e5e7eb;margin-bottom:10px}
  button{width:100%;padding:10px;border:0;border-radius:8px;background:#10b981;
         color:#052e24;font-weight:700;cursor:pointer}
  .msg{margin-top:16px;padding:10px;border-radius:10px;text-align:center}
  .ok{background:#064e3b;color:#a7f3d0}
  .err{background:#7f1d1d;color:#fecaca}
  .warn{background:#78350f;color:#fde68a}
  .mono{font-family:ui-monospace,Menlo,Consolas,monospace}
</style>

<div class="card">
  <h1>Confirmação de Presença</h1>
  <form method="get" action="/presenca">
    <input name="matricula" placeholder="Digite sua matrícula (ex: MR25684)" value="{{ matricula or '' }}">
    <button>Confirmar Presença</button>
  </form>

  {% if tried %}
    {% if ok %}
      {% if already %}
        <div class="msg warn">⚠️ Presença já confirmada hoje<br><b>{{ code }}</b></div>
      {% else %}
        <div class="msg ok">✅ Presença registrada com sucesso!<br><b>{{ code }}</b></div>
      {% endif %}
    {% else %}
      <div class="msg err">❌ {{ msg }}</div>
    {% endif %}
  {% endif %}
</div>
"""

# ------------------ Rota principal ------------------
@presenca_bp.route("/", methods=["GET"])
def presenca_page():
    code = (request.args.get("matricula") or "").strip().upper()
    tried = bool(code)
    today = dt.datetime.utcnow().date()

    if not tried:
        return render_template_string(PAGE, tried=False)

    # valida formato
    if not FORMAT.fullmatch(code):
        return render_template_string(PAGE, tried=True, ok=False,
                                      msg="Formato inválido. Use MR + 5 dígitos (ex: MR25684)",
                                      matricula=code)

    # busca matrícula
    m = Matricula.query.filter_by(code=code).first()
    if not m:
        return render_template_string(PAGE, tried=True, ok=False,
                                      msg="Matrícula não encontrada.",
                                      matricula=code)

    if m.status != "active":
        return render_template_string(PAGE, tried=True, ok=False,
                                      msg=f"Matrícula inativa (status: {m.status})",
                                      matricula=code)

    # verifica presença já existente
    existing = Presenca.query.filter_by(matricula_id=m.id, date_key=today).first()
    if existing:
        return render_template_string(PAGE, tried=True, ok=True, already=True,
                                      code=m.code, matricula=code)

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

    return render_template_string(PAGE, tried=True, ok=True, already=False,
                                  code=m.code, matricula=code)

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
import io, csv
from flask import Response

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

    return jsonify({"count": len(data), "items": data})
