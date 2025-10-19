from flask import Blueprint, request, jsonify
import random, re

bp = Blueprint("matricula", __name__)

def gerar_matricula() -> str:
    numero = random.randint(10000, 99999)  # 5 dígitos
    return f"MR{numero}"

@bp.post("/gerar")
def gerar_matricula_endpoint():
    """
    POST /matricula/gerar
    Body: { "cpf": "123.456.789-09" }
    """
    try:
        data = request.get_json(silent=True) or {}
        cpf = str(data.get("cpf", "")).strip()

        # validação simples
        if not re.fullmatch(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}", cpf):
            return jsonify({"status": "error", "message": "CPF inválido"}), 400

        matricula = gerar_matricula()
        return jsonify({"status": "ok", "cpf": cpf, "matricula": matricula})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro inesperado: {e}"}), 500