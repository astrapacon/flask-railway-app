from flask import Blueprint, request, jsonify, current_app
import re, hashlib

matricula_bp = Blueprint("matricula", __name__)

def apenas_digitos(cpf: str) -> str:
    return re.sub(r"\D", "", cpf or "")

def matricula_from_cpf(cpf: str, prefixo: str, digits: int, salt: str) -> str:
    """
    Gera matrícula determinística: prefixo + N dígitos.
    Usa blake2b(cpf+salt) -> inteiro -> faixa [10^(d-1), 10^d - 1]
    """
    base = f"{cpf}:{salt}".encode("utf-8")
    # hash estável e rápido
    h = hashlib.blake2b(base, digest_size=8).hexdigest()  # 16 hex chars
    n = int(h, 16)
    low = 10 ** (digits - 1)
    high = (10 ** digits) - 1
    span = high - low + 1
    val = (n % span) + low
    return f"{prefixo}{val}"

@matricula_bp.post("/gerar")
def gerar():
    """
    Body: { "cpf": "123.456.789-01" }
    Retorna sempre a MESMA matrícula para o mesmo CPF.
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