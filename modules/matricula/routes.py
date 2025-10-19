from flask import Blueprint, request, jsonify
import random
import re

# 🔹 O nome PRECISA ser exatamente matricula_bp
matricula_bp = Blueprint("matricula", __name__)

def gerar_codigo():
    return f"MR{random.randint(10000, 99999)}"

@matricula_bp.post("/gerar")
def gerar():
    data = request.get_json(silent=True) or {}
    cpf = (data.get("cpf") or "").strip()

    # valida CPF simples (só dígitos)
    if not re.fullmatch(r"\d{11}", re.sub(r"\D", "", cpf)):
        return jsonify(error="CPF inválido"), 400

    return jsonify(cpf=cpf, matricula=gerar_codigo())