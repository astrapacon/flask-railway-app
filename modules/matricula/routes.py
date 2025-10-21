# modules/matricula/routes.py
import re, io, csv, hmac, hashlib
import datetime as _dt

from flask import (
    Blueprint, request, jsonify, Response, current_app, render_template
)
from models import db, Matricula

# Blueprint com prefixo para ficar organizado
matricula_bp = Blueprint("matricula", __name__, url_prefix="/matricula")

# Padrão MR + 5 dígitos
FORMAT = re.compile(r"^MR\d{5}$")

# ======================== Funções auxiliares ========================
def _only_digits(s: str) -> str:
    """Remove tudo que não for número."""
    return re.sub(r"\D+", "", s or "")

def _is_valid_cpf_digits(d: str) -> bool:
    """Valida se o CPF contém exatamente 11 dígitos numéricos."""
    d = _only_digits(d)
    return len(d) == 11 and d.isdigit()

def _parse_birth_date(s: str):
    """
    Converte string para date.
    Aceita 'YYYY-MM-DD' (ISO) ou 'DD/MM/YYYY'.
    Retorna _dt.date ou None.
    """
    if not s:
        return None
    s = s.strip()
    try:
        if "-" in s:
            return _dt.date.fromisoformat(s)
        if "/" in s:
            d, m, y = s.split("/")
            return _dt.date(int(y), int(m), int(d))
    except Exception:
        return None
    return None

def _code_from_cpf(cpf_digits: str) -> str:
    """Gera código determinístico a partir do CPF e do SALT."""
    cpf_digits = _only_digits(cpf_digits)
    salt = (current_app.config.get("MATRICULA_SALT") or "salt-fixo-para-matricula").encode()
    digest = hmac.new(salt, cpf_digits.encode(), hashlib.sha1).hexdigest()
    digits = int(current_app.config.get("MATRICULA_DIGITS", 5))
    n = int(digest[:8], 16) % (10 ** digits)
    prefix = current_app.config.get("MATRICULA_PREFIX", "MR")
    return f"{prefix}{str(n).zfill(digits)}"

# ======================== Páginas HTML ========================
@matricula_bp.get("/check")
def check_page():
    """Página com formulário/JS (matricula_check.html)."""
    return render_template("matricula_check.html")

@matricula_bp.get("/consulta")
def consulta():
    """
    Versão interativa (AJAX no front): apenas entrega o HTML.
    O JS chama /matricula/validate e redireciona para /matricula/sucesso.
    """
    return render_template("matricula_consulta.html")

@matricula_bp.get("/sucesso")
def sucesso_page():
    """Página estilizada de sucesso (usa ?code=MR12345)."""
    code = (request.args.get("code") or "").strip().upper()
    return render_template("matricula_success.html", code=code), 200

@matricula_bp.get("/confirmacao")
def confirmacao():
    """
    Versão server-side (sem JS): valida via querystring (?code=MR12345)
    e reaproveita matricula_check.html mostrando painel verde/erros.
    """
    code = (request.args.get("code") or "").strip().upper()
    tried = bool(code)

    if not tried:
        return render_template("matricula_check.html", tried=False)

    if not FORMAT.fullmatch(code):
        return render_template("matricula_check.html",
                               tried=True, valida=False,
                               msg="Formato inválido. Use MR + 5 dígitos (ex: MR25684).",
                               code=code)

    m = Matricula.query.filter_by(code=code).first()
    if not m:
        return render_template("matricula_check.html",
                               tried=True, valida=False,
                               msg="Matrícula não encontrada.",
                               code=code)

    if getattr(m, "status", "active") != "active":
        return render_template("matricula_check.html",
                               tried=True, valida=False,
                               msg=f"Matrícula inativa (status: {m.status}).",
                               code=code)

    # ok, só exibe o painel verde
    return render_template("matricula_check.html",
                           tried=True, valida=True,
                           code=m.code,
                           nome=getattr(m, "holder_name", None))

# ===== Página "Esqueci minha matrícula" =====
@matricula_bp.get("/lembrar")
def lembrar_page():
    """Página para recuperar matrícula por CPF + data de nascimento."""
    return render_template("matricula_lembrar.html")

# ======================== APIs JSON ========================
@matricula_bp.post("/api/check")
def api_check():
    """
    Compatível com o JS do matricula_check.html (fetch url_for('matricula.api_check')).
    Corpo: { "matricula": "MR12345" }
    """
    data = request.get_json(silent=True) or {}
    code = (data.get("matricula") or "").upper().strip()

    if not code:
        return jsonify(ok=False, message="Informe a matrícula."), 400
    if not FORMAT.fullmatch(code):
        return jsonify(ok=False, code=code, message="Formato inválido. Use MR + 5 dígitos."), 200

    m = Matricula.query.filter_by(code=code).first()
    if not m:
        return jsonify(ok=False, code=code, message="Matrícula não encontrada."), 200
    if getattr(m, "status", "active") != "active":
        return jsonify(ok=False, code=code, message=f"Matrícula inativa (status: {m.status})."), 200

    return jsonify(ok=True, code=code, message=f"Matrícula {code} validada."), 200

@matricula_bp.get("/validate")
def validate():
    """Validação via GET (?code=MR12345) para integrações/consulta AJAX."""
    code = (request.args.get("code") or "").strip().upper()
    if not FORMAT.fullmatch(code):
        return jsonify({"valid": False, "message": "Formato inválido (MR + 5 dígitos)"}), 400
    m = Matricula.query.filter_by(code=code).first()
    if not m:
        return jsonify({"valid": False, "message": "Matrícula não encontrada"}), 404
    return jsonify({
        "valid": getattr(m, "status", "active") == "active",
        "code": m.code,
        "status": getattr(m, "status", None),
        "cpf": getattr(m, "cpf", None)
    }), 200

# ===== API "Esqueci minha matrícula" =====
@matricula_bp.post("/api/lembrar")
def api_lembrar():
    """
    Recebe JSON: { "cpf": "...", "birth_date": "DD/MM/YYYY" ou "YYYY-MM-DD" }
    Retorna a matrícula se encontrar correspondência exata.
    """
    data = request.get_json(silent=True) or {}
    cpf_raw = data.get("cpf")
    birth_raw = data.get("birth_date")

    cpf = _only_digits(cpf_raw)
    birth = _parse_birth_date(birth_raw)

    if not _is_valid_cpf_digits(cpf):
        return jsonify(ok=False, message="CPF inválido. Informe 11 dígitos."), 400
    if not birth:
        return jsonify(ok=False, message="Data de nascimento inválida. Use DD/MM/AAAA."), 400

    m = Matricula.query.filter_by(cpf=cpf, birth_date=birth).first()

    if not m:
        # Resposta neutra (não revela se o CPF existe)
        return jsonify(ok=False, message="Não encontramos matrícula para os dados informados."), 200

    if getattr(m, "status", "active") != "active":
        return jsonify(ok=False, message=f"Matrícula localizada, porém está '{m.status}'."), 200

    return jsonify(ok=True, code=m.code, holder_name=m.holder_name), 200

# ======================== Gerar matrícula a partir do CPF ========================
@matricula_bp.post("/gerar")
def gerar_post():
    """
    Gera (ou retorna existente) a matrícula a partir do CPF e, opcionalmente, birth_date.
    Corpo JSON:
      { "cpf": "12345678909", "birth_date": "DD/MM/AAAA" ou "YYYY-MM-DD", "holder_name": "opcional" }
    """
    data = request.get_json(silent=True) or {}
    cpf_raw = data.get("cpf")
    cpf = _only_digits(cpf_raw)
    birth_raw = data.get("birth_date")
    holder_name = (data.get("holder_name") or "").strip() or None

    if not _is_valid_cpf_digits(cpf):
        return jsonify({"ok": False, "message": "CPF inválido. Envie 11 dígitos (com ou sem .-)."}), 400

    birth_date = _parse_birth_date(birth_raw) if birth_raw else None
    if birth_raw and not birth_date:
        return jsonify({"ok": False, "message": "Data de nascimento inválida. Use DD/MM/AAAA."}), 400

    existing = Matricula.query.filter_by(cpf=cpf).first()
    if existing:
        # atualiza birth_date/holder_name se vierem agora e ainda não existirem
        changed = False
        if birth_date and not existing.birth_date:
            existing.birth_date = birth_date; changed = True
        if holder_name and not existing.holder_name:
            existing.holder_name = holder_name; changed = True
        if changed:
            db.session.commit()
        return jsonify({"ok": True, "matricula": {
            "code": existing.code,
            "cpf": existing.cpf,
            "birth_date": existing.birth_date.isoformat() if getattr(existing, "birth_date", None) else None,
            "holder_name": existing.holder_name,
            "status": existing.status
        }}), 200

    code = _code_from_cpf(cpf)
    # Evita colisão eventual
    if Matricula.query.filter_by(code=code).first():
        code = _code_from_cpf(cpf + "|1")

    m = Matricula(code=code, cpf=cpf, birth_date=birth_date, holder_name=holder_name, status="active")
    db.session.add(m)
    db.session.commit()
    return jsonify({"ok": True, "matricula": {
        "code": m.code, "cpf": m.cpf,
        "birth_date": m.birth_date.isoformat() if m.birth_date else None,
        "holder_name": m.holder_name,
        "status": m.status
    }}), 200

@matricula_bp.get("/gerar")
def gerar_get():
    """
    Variante GET (útil p/ testes rápidos):
      /matricula/gerar?cpf=12345678909&birth_date=DD/MM/AAAA&holder_name=Fulana
    """
    cpf_raw = request.args.get("cpf")
    cpf = _only_digits(cpf_raw)
    birth_raw = request.args.get("birth_date")
    holder_name = (request.args.get("holder_name") or "").strip() or None

    if not _is_valid_cpf_digits(cpf):
        return jsonify({"ok": False, "message": "CPF inválido. Envie 11 dígitos (com ou sem .-)."}), 400

    birth_date = _parse_birth_date(birth_raw) if birth_raw else None
    if birth_raw and not birth_date:
        return jsonify({"ok": False, "message": "Data de nascimento inválida. Use DD/MM/AAAA."}), 400

    existing = Matricula.query.filter_by(cpf=cpf).first()
    if existing:
        changed = False
        if birth_date and not existing.birth_date:
            existing.birth_date = birth_date; changed = True
        if holder_name and not existing.holder_name:
            existing.holder_name = holder_name; changed = True
        if changed:
            db.session.commit()
        return jsonify({"ok": True, "matricula": {
            "code": existing.code,
            "cpf": existing.cpf,
            "birth_date": existing.birth_date.isoformat() if getattr(existing, "birth_date", None) else None,
            "holder_name": existing.holder_name,
            "status": existing.status
        }}), 200

    code = _code_from_cpf(cpf)
    if Matricula.query.filter_by(code=code).first():
        code = _code_from_cpf(cpf + "|1")

    m = Matricula(code=code, cpf=cpf, birth_date=birth_date, holder_name=holder_name, status="active")
    db.session.add(m)
    db.session.commit()
    return jsonify({"ok": True, "matricula": {
        "code": m.code, "cpf": m.cpf,
        "birth_date": m.birth_date.isoformat() if m.birth_date else None,
        "holder_name": m.holder_name,
        "status": m.status
    }}), 200

# ======================== Listar / Exportar ========================
@matricula_bp.get("/list.json")
def list_matriculas_json():
    # Ordena por created_at se existir; senão, por id desc
    order_col = getattr(Matricula, "created_at", None) or Matricula.id
    q = Matricula.query.order_by(order_col.desc()).limit(200)
    items = [{
        "code": m.code,
        "cpf": m.cpf,
        "birth_date": m.birth_date.isoformat() if getattr(m, "birth_date", None) else None,
        "holder_name": m.holder_name,
        "status": m.status
    } for m in q.all()]
    return jsonify({"count": len(items), "items": items})

@matricula_bp.get("/export.csv")
def export_matriculas_csv():
    order_col = getattr(Matricula, "created_at", None) or Matricula.id
    q = Matricula.query.order_by(order_col.desc())
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["code", "cpf", "birth_date", "holder_name", "status"])
    for m in q.all():
        writer.writerow([
            m.code,
            m.cpf or "",
            (m.birth_date.isoformat() if getattr(m, "birth_date", None) else ""),
            m.holder_name or "",
            m.status
        ])
    resp = Response(output.getvalue(), mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = "attachment; filename=matriculas.csv"
    return resp