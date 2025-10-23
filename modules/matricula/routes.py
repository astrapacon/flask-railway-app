# modules/matricula/routes.py
import re, io, csv, hmac, hashlib
import datetime as _dt

from flask import (
    Blueprint, request, jsonify, Response, current_app, render_template
)
from models import db, Matricula

# -------------------------------------------------------------------
# Blueprint
# -------------------------------------------------------------------
matricula_bp = Blueprint("matricula", __name__, url_prefix="/matricula")

# Padrão MR + 5 dígitos
FORMAT = re.compile(r"^MR\d{5}$")

# Aceita somente DD/MM/AAAA
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")

# ======================== Funções auxiliares ========================
def _only_digits(s: str) -> str:
    """Remove tudo que não for número."""
    return re.sub(r"\D+", "", s or "")

def _is_valid_cpf_digits(d: str) -> bool:
    """Valida se o CPF contém exatamente 11 dígitos numéricos."""
    d = _only_digits(d)
    return len(d) == 11 and d.isdigit()

def _parse_birth_date(raw: str):
    """
    Converte 'DD/MM/AAAA' para _dt.date. Retorna None se inválida.
    """
    raw = (raw or "").strip()
    if not raw or not DATE_RE.fullmatch(raw):
        return None
    try:
        d, m, y = [int(x) for x in raw.split("/")]
        return _dt.date(y, m, d)
    except Exception:
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
    Recebe JSON: { "cpf": "...", "birth_date": "DD/MM/AAAA" } (também aceita chave "birth").
    Retorna a matrícula se encontrar correspondência exata.
    """
    data = request.get_json(silent=True) or {}
    cpf_raw = data.get("cpf")
    birth_raw = data.get("birth_date") or data.get("birth")  # aceita as duas chaves

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

# ======================== Gerar matrícula (CPF e opcionais birth/name) ========================
@matricula_bp.post("/gerar")
def gerar_post():
    """
    Gera (ou retorna existente) a matrícula a partir do CPF e, opcionalmente, birth_date/holder_name.
    Body JSON:
      { "cpf": "12345678909", "birth_date": "DD/MM/AAAA", "holder_name": "opcional" }
      (também aceita chave "birth")
    """
    data = request.get_json(silent=True) or {}
    cpf_raw = data.get("cpf")
    cpf = _only_digits(cpf_raw)
    birth_raw = data.get("birth_date") or data.get("birth")
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
        if birth_date and not getattr(existing, "birth_date", None):
            existing.birth_date = birth_date; changed = True
        if holder_name and not getattr(existing, "holder_name", None):
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
      (também aceita ?birth=DD/MM/AAAA)
    """
    cpf_raw = request.args.get("cpf")
    cpf = _only_digits(cpf_raw)
    birth_raw = request.args.get("birth_date") or request.args.get("birth")
    holder_name = (request.args.get("holder_name") or "").strip() or None

    if not _is_valid_cpf_digits(cpf):
        return jsonify({"ok": False, "message": "CPF inválido. Envie 11 dígitos (com ou sem .-)."}), 400

    birth_date = _parse_birth_date(birth_raw) if birth_raw else None
    if birth_raw and not birth_date:
        return jsonify({"ok": False, "message": "Data de nascimento inválida. Use DD/MM/AAAA."}), 400

    existing = Matricula.query.filter_by(cpf=cpf).first()
    if existing:
        changed = False
        if birth_date and not getattr(existing, "birth_date", None):
            existing.birth_date = birth_date; changed = True
        if holder_name and not getattr(existing, "holder_name", None):
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

# ======================== Versão CPF + data (rotas dedicadas) ========================
@matricula_bp.post("/gerar_dados")
def gerar_com_dados_post():
    """
    Gera (ou retorna) a matrícula usando CPF + Data de Nascimento (DD/MM/AAAA).
    Body JSON: { "cpf": "10688046967", "birth": "04/07/2001" }  // ou "birth_date"
    """
    data = request.get_json(silent=True) or {}
    cpf = _only_digits(data.get("cpf"))
    birth_raw = data.get("birth") or data.get("birth_date")
    birth = _parse_birth_date(birth_raw)

    if not _is_valid_cpf_digits(cpf):
        return jsonify(ok=False, message="CPF inválido. Envie 11 dígitos."), 400
    if not birth:
        return jsonify(ok=False, message="Data de nascimento inválida. Use DD/MM/AAAA."), 400

    rows = Matricula.query.filter_by(cpf=cpf).all()

    if rows:
        # 1) Já existe alguma matrícula com a MESMA data? -> retorna ela
        for r in rows:
            if getattr(r, "birth_date", None) == birth:
                return jsonify({"ok": True, "matricula": {
                    "code": r.code,
                    "cpf": r.cpf,
                    "birth_date": _birth_iso(getattr(r, "birth_date", None)),
                    "holder_name": getattr(r, "holder_name", None),
                    "status": r.status
                }}), 200

        # 2) Existe alguma sem data? -> preenche e retorna
        for r in rows:
            if getattr(r, "birth_date", None) in (None, ""):
                r.birth_date = birth
                db.session.commit()
                return jsonify({"ok": True, "matricula": {
                    "code": r.code,
                    "cpf": r.cpf,
                    "birth_date": _birth_iso(getattr(r, "birth_date", None)),
                    "holder_name": getattr(r, "holder_name", None),
                    "status": r.status
                }}), 200

        # 3) Todas têm data diferente -> conflito
        return jsonify(ok=False, message="Data de nascimento não confere para este CPF."), 409

    # 4) Nenhuma matrícula com esse CPF -> cria
    code = _code_from_cpf(cpf)
    if Matricula.query.filter_by(code=code).first():
        code = _code_from_cpf(cpf + "|1")

    m = Matricula(code=code, cpf=cpf, status="active")
    m.birth_date = birth
    db.session.add(m)
    db.session.commit()

    return jsonify({
        "ok": True,
        "matricula": {
            "code": m.code,
            "cpf": m.cpf,
            "birth_date": _birth_iso(getattr(m, "birth_date", None)),
            "holder_name": getattr(m, "holder_name", None),
            "status": m.status
        }
    }), 200


@matricula_bp.get("/gerar_dados")
def gerar_com_dados_get():
    """
    Versão GET para teste rápido:
    /matricula/gerar_dados?cpf=12345678900&birth=DD/MM/AAAA (ou birth_date=DD/MM/AAAA)
    """
    cpf = _only_digits(request.args.get("cpf"))
    birth_raw = request.args.get("birth") or request.args.get("birth_date")
    birth = _parse_birth_date(birth_raw)

    if not _is_valid_cpf_digits(cpf):
        return jsonify(ok=False, message="CPF inválido. Envie 11 dígitos."), 400
    if not birth:
        return jsonify(ok=False, message="Data de nascimento inválida. Use DD/MM/AAAA."), 400

    existing = Matricula.query.filter_by(cpf=cpf).first()
    if existing:
        if getattr(existing, "birth_date", None) and existing.birth_date != birth:
            return jsonify(ok=False, message="Data de nascimento não confere para este CPF."), 409
        if not getattr(existing, "birth_date", None):
            existing.birth_date = birth
            db.session.commit()
        return jsonify({
            "ok": True,
            "matricula": {
                "code": existing.code,
                "cpf": existing.cpf,
                "birth_date": getattr(existing, "birth_date", None).isoformat() if getattr(existing, "birth_date", None) else None,
                "status": existing.status
            }
        }), 200

        # Converte um valor de birth_date (date | str | None) para ISO 'YYYY-MM-DD'
DATE_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

def _birth_iso(value):
    if not value:
        return None
    if isinstance(value, _dt.date):
        return value.isoformat()
    s = str(value).strip()
    if DATE_ISO_RE.fullmatch(s):
        return s
    if DATE_RE.fullmatch(s):  # 'DD/MM/AAAA' -> ISO
        d, m, y = [int(x) for x in s.split("/")]
        try:
            return _dt.date(y, m, d).isoformat()
        except Exception:
            return None
    # formato desconhecido
    return None

def _json_matricula(m):
    return {
        "code": m.code,
        "cpf": m.cpf,
        "birth_date": _birth_iso(getattr(m, "birth_date", None)),
        "holder_name": getattr(m, "holder_name", None),
        "status": getattr(m, "status", None),
    }


    code = _code_from_cpf(cpf)
    if Matricula.query.filter_by(code=code).first():
        code = _code_from_cpf(cpf + "|1")

    m = Matricula(code=code, cpf=cpf, status="active")
    m.birth_date = birth
    db.session.add(m)
    db.session.commit()

    return jsonify({
        "ok": True,
        "matricula": {
            "code": m.code,
            "cpf": m.cpf,
            "birth_date": m.birth_date.isoformat() if getattr(m, "birth_date", None) else None,
            "status": m.status
        }
    }), 200

# ======================== Listar / Exportar ========================
@matricula_bp.get("/list.json")
def list_matriculas_json():
    # Ordena por created_at se existir; senão, por id desc
    order_col = getattr(Matricula, "created_at", None) or Matricula.id
    q = Matricula.query.order_by(order_col.desc()).limit(200)
    items = [{
    "code": m.code,
    "cpf": m.cpf,
    "birth_date": _birth_iso(getattr(m, "birth_date", None)),
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