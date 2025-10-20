# modules/matricula/routes.py
from flask import Blueprint, request, render_template_string, jsonify, current_app
from datetime import datetime, date
import re, hashlib

from models import db, Matricula  # garante que o modelo existe

matricula_bp = Blueprint("matricula", __name__)

# -----------------------------
# Helpers
# -----------------------------
def apenas_digitos(cpf: str) -> str:
    return re.sub(r"\D", "", cpf or "")

def build_format_regex() -> re.Pattern:
    """
    Constrói o regex de matrícula a partir da config:
    PREFIXO (literal) + {DIGITS} dígitos.
    Ex.: prefixo=MR, digits=5  -> ^MR\d{5}$
    """
    prefixo = current_app.config.get("MATRICULA_PREFIX", "MR")
    digits = int(current_app.config.get("MATRICULA_DIGITS", 5))
    # Escapa prefixo para evitar meta-caracteres acidentais
    prefixo_esc = re.escape(prefixo)
    return re.compile(rf"^{prefixo_esc}\d{{{digits}}}$")

def matricula_from_cpf(cpf: str, prefixo: str, digits: int, salt: str) -> str:
    """
    Gera matrícula determinística: prefixo + N dígitos.
    Usa blake2b(cpf+salt) -> inteiro -> faixa [10^(d-1), 10^d - 1].
    """
    base = f"{cpf}:{salt}".encode("utf-8")
    h = hashlib.blake2b(base, digest_size=8).hexdigest()  # 16 hex chars
    n = int(h, 16)
    low = 10 ** (digits - 1)
    high = (10 ** digits) - 1
    span = high - low + 1
    val = (n % span) + low
    return f"{prefixo}{val}"

# -----------------------------
# Páginas
# -----------------------------

# 1) Página: Consulta de Matrícula (/matricula/consulta)
@matricula_bp.get("/consulta")
def pagina_consulta():
    prefixo = current_app.config.get("MATRICULA_PREFIX", "MR")
    digits = int(current_app.config.get("MATRICULA_DIGITS", 5))
    exemplo = f"{prefixo}{'0'*digits}"
    html = f"""
    <!doctype html><meta charset="utf-8">
    <title>Consulta de Matrícula</title>
    <style>
      body{{font-family:system-ui;margin:40px}}
      .mono{{font-family:ui-monospace,Consolas,Menlo,monospace}}
      form{{display:flex;gap:8px}}
      input{{padding:8px 10px}}
      button{{padding:8px 12px;font-weight:700}}
    </style>
    <h1>Consulta de Matrícula</h1>
    <form action="/matricula/resultado" method="get">
      <input name="code" placeholder="{exemplo}" />
      <button>Consultar</button>
    </form>
    """
    return render_template_string(html)

# 2) Página: Resultado da Consulta (/matricula/resultado?code=...)
@matricula_bp.get("/resultado")
def pagina_resultado():
    code = (request.args.get("code") or "").strip().upper()
    fmt = build_format_regex()

    if not fmt.fullmatch(code):
        prefixo = current_app.config.get("MATRICULA_PREFIX", "MR")
        digits = int(current_app.config.get("MATRICULA_DIGITS", 5))
        exemplo = f"{prefixo}{'0'*digits}"
        return render_template_string(
            f"""
            <!doctype html><meta charset="utf-8">
            <style>body{{font-family:system-ui;margin:40px}} .mono{{font-family:ui-monospace,Consolas,Menlo,monospace}}</style>
            <p>❌ Formato inválido. Use <b>{prefixo} + {digits} dígitos</b> (ex.: <b class="mono">{exemplo}</b>).</p>
            <p><a href="/matricula/consulta">Voltar</a></p>
            """
        ), 400

    m = Matricula.query.filter_by(code=code).first()
    if not m:
        return render_template_string(
            f"""
            <!doctype html><meta charset="utf-8">
            <style>body{{font-family:system-ui;margin:40px}} .mono{{font-family:ui-monospace,Consolas,Menlo,monospace}}</style>
            <h1>❌ Não encontrada</h1>
            <p>Matrícula <b class="mono">{code}</b> não foi localizada.</p>
            <p><a href="/matricula/consulta">Nova consulta</a></p>
            """
        ), 404

    return render_template_string(
        f"""
        <!doctype html><meta charset="utf-8">
        <style>body{{font-family:system-ui;margin:40px}} .mono{{font-family:ui-monospace,Consolas,Menlo,monospace}}</style>
        <h1>✅ Encontrada</h1>
        <p>Matrícula <b class="mono">{m.code}</b>{' — ' + m.holder_name if m.holder_name else ''}</p>
        <p>Status: <b>{m.status}</b></p>
        <p><a href="/matricula/consulta">Nova consulta</a></p>
        """
    )

# -----------------------------
# APIs
# -----------------------------

# 3) API: Validar matrícula existente (/matricula/validate?code=...)
@matricula_bp.get("/validate")
def api_validate():
    code = (request.args.get("code") or "").strip().upper()
    fmt = build_format_regex()
    if not fmt.fullmatch(code):
        prefixo = current_app.config.get("MATRICULA_PREFIX", "MR")
        digits = int(current_app.config.get("MATRICULA_DIGITS", 5))
        return jsonify(valid=False, reason=f"Formato inválido ({prefixo}+{digits} dígitos)."), 400

    m = Matricula.query.filter_by(code=code).first()
    if not m:
        return jsonify(valid=False, reason="Não encontrada."), 404

    return jsonify(
        valid=True,
        code=m.code,
        holder_name=m.holder_name,
        status=m.status
    ), 200

# 4) API: Gerar matrícula a partir de CPF (determinística) - POST /matricula/gerar
@matricula_bp.post("/gerar")
def gerar():
    """
    Body JSON: { "cpf": "123.456.789-01" }
    - Limpa para 11 dígitos
    - Valida quantidade
    - Gera matrícula determinística: PREFIXO + N dígitos
    - Usa config do app: MATRICULA_PREFIX, MATRICULA_DIGITS, MATRICULA_SALT
    Retorna SEM criar no banco (apenas cálculo).
    """
    data = request.get_json(silent=True) or {}
    cpf_raw = (data.get("cpf") or "").strip()
    cpf = apenas_digitos(cpf_raw)

    if not re.fullmatch(r"\d{11}", cpf):
        return jsonify(error="CPF inválido (esperado 11 dígitos)"), 400

    prefixo = current_app.config.get("MATRICULA_PREFIX", "MR")
    digits = int(current_app.config.get("MATRICULA_DIGITS", 5))
    salt = current_app.config.get("MATRICULA_SALT", "salt-fixo-para-matricula")

    codigo = matricula_from_cpf(cpf, prefixo=prefixo, digits=digits, salt=salt)

    return jsonify(cpf=cpf, matricula=codigo), 200

    # ------------------ EXPORTS ------------------
import io, csv
from flask import Response

@matricula_bp.get("/list.json")
def list_matriculas_json():
    q = Matricula.query.order_by(Matricula.created_at.desc())
    items = [{
        "code": m.code,
        "holder_name": m.holder_name,
        "cpf": m.cpf,
        "status": m.status
    } for m in q.all()]
    return jsonify({"count": len(items), "items": items})

@matricula_bp.get("/export.csv")
def export_matriculas_csv():
    q = Matricula.query.order_by(Matricula.created_at.desc())
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["code","holder_name","cpf","status"])
    for m in q.all():
        writer.writerow([m.code, m.holder_name or "", m.cpf or "", m.status])
    resp = Response(output.getvalue(), mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = "attachment; filename=matriculas.csv"
    return resp
