# modules/matricula/routes.py
import re, io, csv, hmac, hashlib
from flask import (
    Blueprint, request, jsonify, render_template_string, Response, current_app
)
from models import db, Matricula

matricula_bp = Blueprint("matricula", __name__)
FORMAT = re.compile(r"^MR\d{5}$")  # padrão MR + 5 dígitos


# ======================== Funções auxiliares ========================
def _only_digits(s: str) -> str:
    """Remove tudo que não for número"""
    return re.sub(r"\D", "", s or "")

def _is_valid_cpf_digits(d: str) -> bool:
    """Valida se o CPF contém exatamente 11 dígitos numéricos"""
    return bool(d) and len(d) == 11 and d.isdigit()

def _code_from_cpf(cpf_digits: str) -> str:
    """Gera código determinístico a partir do CPF e do SALT"""
    cpf_digits = _only_digits(cpf_digits)
    salt = (current_app.config.get("MATRICULA_SALT") or "salt-fixo-para-matricula").encode()
    digest = hmac.new(salt, cpf_digits.encode(), hashlib.sha1).hexdigest()
    digits = int(current_app.config.get("MATRICULA_DIGITS", 5))
    n = int(digest[:8], 16) % (10 ** digits)
    prefix = current_app.config.get("MATRICULA_PREFIX", "MR")
    return f"{prefix}{str(n).zfill(digits)}"


# ======================== Página HTML simples ========================
PAGE = """
<!doctype html><meta charset="utf-8">
<title>Consulta de Matrícula</title>
<style>
  body{font-family:system-ui;background:#0f172a;color:#e5e7eb;margin:0;
       display:flex;align-items:center;justify-content:center;height:100vh}
  .card{background:#111827;border:1px solid #1f2937;border-radius:16px;padding:24px;
        width:92%;max-width:520px;box-shadow:0 16px 40px rgba(0,0,0,.35)}
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
  <h1>Consulta de Matrícula</h1>
  <form method="get" action="/matricula/consulta">
    <input name="code" placeholder="Digite sua matrícula (ex: MR25684)" value="{{ code or '' }}">
    <button>Consultar</button>
  </form>

  {% if tried %}
    {% if ok %}
      <div class="msg ok">✅ Matrícula válida: <b class="mono">{{ code }}</b>{% if name %} — {{ name }}{% endif %}</div>
    {% else %}
      <div class="msg err">❌ {{ msg }}</div>
    {% endif %}
  {% endif %}
</div>
"""

# ------------------ Página de SUCESSO com animações ------------------
PAGE_SUCCESS_MATRICULA = """
""
<!doctype html><meta charset="utf-8">
<title>Consulta de Matrícula</title>
<style>
  body{font-family:system-ui;background:#0f172a;color:#e5e7eb;margin:0;
       display:flex;align-items:center;justify-content:center;height:100vh}
  .card{background:#111827;border:1px solid #1f2937;border-radius:16px;padding:24px;
        width:92%;max-width:520px;box-shadow:0 16px 40px rgba(0,0,0,.35)}
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
  <h1>Consulta de Matrícula</h1>
  <form method="get" action="/matricula/consulta">
    <input name="code" placeholder="Digite sua matrícula (ex: MR25684)" value="{{ code or '' }}">
    <button>Consultar</button>
  </form>

  {% if tried %}
    {% if ok %}
      <div class="msg ok">✅ Matrícula válida: <b class="mono">{{ code }}</b>{% if name %} — {{ name }}{% endif %}</div>
    {% else %}
      <div class="msg err">❌ {{ msg }}</div>
    {% endif %}
  {% endif %}
</div>
"""
# ======================== Página de sucesso ========================
PAGE_SUCCESS_MATRICULA = """<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Matrícula válida</title>
<style>
  :root{--bg:#231f20;--card:#2e2b2c;--line:#3a3536;--text:#f4f4f4;
        --ok:#7a1315;--accent:#d1a34a;}
  html,body{margin:0;background:var(--bg);color:var(--text);
            font-family:system-ui,Segoe UI,Roboto,Arial}
  .wrap{min-height:100vh;display:flex;flex-direction:column;gap:18px;
        align-items:center;justify-content:center;padding:24px}
  h1{text-align:center;margin:0 0 4px;font-size:1.5rem}
  .mono{font-family:ui-monospace,Menlo,Consolas,monospace}
  .pill{display:inline-flex;align-items:center;gap:8px;padding:8px 12px;
        border-radius:999px;border:1px solid var(--line);background:#2e2b2c;
        font-weight:700;color:var(--ok)}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
        gap:18px;margin-top:12px;width:100%;max-width:700px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:16px;
        padding:16px;box-shadow:0 8px 24px rgba(0,0,0,.3);
        text-align:center;min-height:220px}
  .btn{margin-top:18px;padding:10px 14px;background:var(--ok);color:#fff;
       border-radius:10px;text-decoration:none;font-weight:800}
</style>
<div class="wrap">
  <div class="pill">✅ Matrícula válida</div>
  <h1>Matrícula <b>{{code}}</b></h1>
  <div class="grid">
    <div class="card">✈️ Avião voando</div>
    <div class="card">🎓 Formatura</div>
    <div class="card">🚗 Carro na estrada</div>
    <div class="card">🏠 Chegando em casa</div>
  </div>
  <a class="btn" href="/matricula/consulta">Nova consulta</a>
</div></html>
"""

def _success_page_matricula(code):
    return render_template_string(PAGE_SUCCESS_MATRICULA, code=code)


# ======================== Página HTML de consulta ========================
@matricula_bp.get("/consulta")
def consulta():
    code = (request.args.get("code") or "").strip().upper()
    tried = bool(code)

    if not tried:
        return render_template_string(PAGE, tried=False)

    if not FORMAT.fullmatch(code):
        return render_template_string(PAGE, tried=True, ok=False,
                                      msg="Formato inválido. Use MR + 5 dígitos (ex: MR25684)",
                                      code=code)

    m = Matricula.query.filter_by(code=code).first()
    if not m:
        return render_template_string(PAGE, tried=True, ok=False,
                                      msg="Matrícula não encontrada.", code=code)

    if getattr(m, "status", "active") != "active":
        return render_template_string(PAGE, tried=True, ok=False,
                                      msg=f"Matrícula inativa (status: {m.status})", code=code)

    return _success_page_matricula(m.code)


# ======================== GERAR MATRÍCULA ========================
@matricula_bp.post("/gerar")
def gerar_post():
    data = request.get_json(silent=True) or {}
    cpf_raw = data.get("cpf")
    cpf = _only_digits(cpf_raw)

    if not _is_valid_cpf_digits(cpf):
        return jsonify({"ok": False, "message": "CPF inválido. Envie 11 dígitos (com ou sem .-)."}), 400

    existing = Matricula.query.filter_by(cpf=cpf).first()
    if existing:
        return jsonify({"ok": True, "matricula": {
            "code": existing.code, "cpf": existing.cpf, "status": existing.status
        }}), 200

    code = _code_from_cpf(cpf)
    if Matricula.query.filter_by(code=code).first():
        code = _code_from_cpf(cpf + "|1")

    m = Matricula(code=code, cpf=cpf, status="active")
    db.session.add(m)
    db.session.commit()
    return jsonify({"ok": True, "matricula": {"code": m.code, "cpf": m.cpf, "status": m.status}}), 200


@matricula_bp.get("/gerar")
def gerar_get():
    cpf_raw = request.args.get("cpf")
    cpf = _only_digits(cpf_raw)

    if not _is_valid_cpf_digits(cpf):
        return jsonify({"ok": False, "message": "CPF inválido. Envie 11 dígitos (com ou sem .-)."}), 400

    existing = Matricula.query.filter_by(cpf=cpf).first()
    if existing:
        return jsonify({"ok": True, "matricula": {
            "code": existing.code, "cpf": existing.cpf, "status": existing.status
        }}), 200

    code = _code_from_cpf(cpf)
    if Matricula.query.filter_by(code=code).first():
        code = _code_from_cpf(cpf + "|1")

    m = Matricula(code=code, cpf=cpf, status="active")
    db.session.add(m)
    db.session.commit()
    return jsonify({"ok": True, "matricula": {"code": m.code, "cpf": m.cpf, "status": m.status}}), 200


# ======================== VALIDATE (JSON API) ========================
@matricula_bp.get("/validate")
def validate():
    code = (request.args.get("code") or "").strip().upper()
    if not FORMAT.fullmatch(code):
        return jsonify({"valid": False, "message": "Formato inválido (MR + 5 dígitos)"}), 400
    m = Matricula.query.filter_by(code=code).first()
    if not m:
        return jsonify({"valid": False, "message": "Matrícula não encontrada"}), 404
    return jsonify({
        "valid": m.status == "active",
        "code": m.code,
        "status": m.status,
        "cpf": m.cpf
    }), 200


# ======================== LISTAR E EXPORTAR ========================
@matricula_bp.get("/list.json")
def list_matriculas_json():
    q = Matricula.query.order_by(Matricula.created_at.desc()).limit(200)
    items = [{
        "code": m.code, "cpf": m.cpf, "status": m.status
    } for m in q.all()]
    return jsonify({"count": len(items), "items": items})


@matricula_bp.get("/export.csv")
def export_matriculas_csv():
    q = Matricula.query.order_by(Matricula.created_at.desc())
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["code","cpf","status"])
    for m in q.all():
        writer.writerow([m.code, m.cpf or "", m.status])
    resp = Response(output.getvalue(), mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = "attachment; filename=matriculas.csv"
    return resp