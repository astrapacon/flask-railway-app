from flask import Blueprint, request, jsonify
import requests

# 🔹 O nome precisa ser exatamente este:
workato_bp = Blueprint("workato", __name__)

@workato_bp.post("/enviar")
def enviar():
    payload = request.get_json(silent=True) or {}

    # Exemplo de requisição simulada (pode adaptar depois)
    try:
        # Aqui você chamaria sua automação Workato real.
        # Exemplo fictício:
        # response = requests.post("https://hooks.workato.com/seu_hook", json=payload)
        # return jsonify(status=response.status_code, data=response.json())

        # Por enquanto, só retorna o payload recebido
        return jsonify({
            "mensagem": "Dados recebidos com sucesso",
            "payload": payload
        })
    except Exception as e:
        return jsonify(error=str(e)), 500