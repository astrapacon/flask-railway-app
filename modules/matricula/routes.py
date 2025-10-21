# modules/matricula/routes.py
import re, io, csv, hmac, hashlib
from flask import (
    Blueprint, request, jsonify, Response, current_app, render_template
)
from models import db, Matricula

# Opcional: defina url_prefix="/matricula" se quiser
matricula_bp = Blueprint("matricula", __name__)

FORMAT = re.compile(r"^MR\d{5}$")  # padrão MR + 5 dígitos

# ======================== Funções auxiliares ========================
def _only_digits(s: str) -> str:
    """Remove tudo que não for número"""
    return re.sub(r"\D+", "", s or "")

def _is_valid_cpf_digits(d: str) -> bool:
    """Valida se o CPF contém exatamente 11 dígitos numéricos"""
    d = _only_digits(d)
    return len(d) == 11 and d.isdigit()

def _code_from_cpf(cpf_digits: str) -> str:
    """Gera código determinístico a partir do CPF e do SALT"""
    cpf_digits = _only_digits(cpf_digits)
    salt = (current_app.config.get("MATRICULA_SALT") or "salt-fixo-para-matricula").encode()
    digest = hmac.new(salt, cpf_digits.encode(), hashlib.sha1).hexdigest()
    digits = int(current_app.config.get("MATRICULA_DIGITS", 5))
    n = int(digest[:8], 16) % (10 ** digits)
    prefix = current_app.config.get("MATRICULA_PREFIX", "MR")
    return f"{prefix}{str(n).zfill(digits)}"


# ======================== PÁGINA HTML DE CONSULTA ========================
@matricula_bp.get("/consulta")
def consulta():
    """
    GET /matricula/consulta?code=MR12345
    - sem query: mostra formulário
    - com query: valida e mostra sucesso se matrícula estiver ativa
    """
    code = (request.args.get("code") or "").strip().upper()
    tried = bool(code)

    # Sem query → renderiza formulário
    if not tried:
        return render_template("matricula_consulta.html", tried=False)

    # Validação de formato
    if not FORMAT.fullmatch(code):
        return render_template(
            "matricula_consulta.html",
            tried=True,
            ok=False,
            msg="Formato inválido. Use MR + 5 dígitos (ex: MR25684).",
            code=code
        )

    # Busca
    m = Matricula.query.filter_by(code=code).first()
    if not m:
        return render_template(
            "matricula_consulta.html",
            tried=True,
            ok=False,
            msg="Matrícula não encontrada.",
            code=code
        )

    # Status
    status = getattr(m, "status", "active")
    if status != "active":
        return render_template(
            "matricula_consulta.html",
            tried=True,
            ok=False,
            msg=f"Matrícula inativa (status: {status}).",
            code=code
        )

    # Sucesso → página estilizada com animações (seu template)
    return render_template("matricula_success.html", code=m.code), 200


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
    # Evita colisão eventual
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
        "valid": getattr(m, "status", "active") == "active",
        "code": m.code,
        "status": getattr(m, "status", None),
        "cpf": getattr(m, "cpf", None)
    }), 200


# ======================== LISTAR E EXPORTAR ========================
@matricula_bp.get("/list.json")
def list_matriculas_json():
    # Ordena por created_at se existir; senão, por id desc
    order_col = getattr(Matricula, "created_at", None) or Matricula.id
    q = Matricula.query.order_by(order_col.desc()).limit(200)
    items = [{"code": m.code, "cpf": m.cpf, "status": m.status} for m in q.all()]
    return jsonify({"count": len(items), "items": items})

@matricula_bp.get("/export.csv")
def export_matriculas_csv():
    order_col = getattr(Matricula, "created_at", None) or Matricula.id
    q = Matricula.query.order_by(order_col.desc())
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["code","cpf","status"])
    for m in q.all():
        writer.writerow([m.code, m.cpf or "", m.status])
    resp = Response(output.getvalue(), mimetype="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = "attachment; filename=matriculas.csv"
    return resp