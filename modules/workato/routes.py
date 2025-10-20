# modules/workato/routes.py
# -----------------------------------------------------------------------------
# Módulo Workato — rotas de integração e testes
# -----------------------------------------------------------------------------
from flask import Blueprint, request, jsonify, current_app

workato_bp = Blueprint("workato", __name__)

# -----------------------------------------------------------------------------
# Rota simples de teste (GET /workato/test)
# -----------------------------------------------------------------------------
@workato_bp.get("/test")
def test():
    """
    Endpoint básico para testar se o módulo Workato está ativo.
    """
    return jsonify({
        "ok": True,
        "message": "Workato ativo e respondendo!",
        "branding": {
            "primary": current_app.config.get("BRAND_PRIMARY"),
            "accent": current_app.config.get("BRAND_ACCENT"),
        }
    }), 200


# -----------------------------------------------------------------------------
# Rota de trigger (POST /workato/trigger)
# -----------------------------------------------------------------------------
@workato_bp.post("/trigger")
def trigger():
    """
    Exemplo de endpoint que o Workato pode chamar.
    Aceita JSON e retorna dados simulados.
    """
    data = request.get_json(silent=True) or {}
    event = data.get("event", "none")
    payload = data.get("payload", {})

    # log opcional no console do servidor
    current_app.logger.info(f"[WORKATO] evento recebido: {event} - {payload}")

    return jsonify({
        "ok": True,
        "received_event": event,
        "payload_echo": payload,
        "note": "Este endpoint é um exemplo. Personalize conforme a automação Workato."
    }), 200


# -----------------------------------------------------------------------------
# Rota protegida opcional (POST /workato/secure)
# -----------------------------------------------------------------------------
@workato_bp.post("/secure")
def secure_trigger():
    """
    Endpoint protegido com token simples.
    Configure a variável de ambiente WORKATO_API_KEY.
    """
    api_key = current_app.config.get("WORKATO_API_KEY") or os.getenv("WORKATO_API_KEY")
    provided = request.headers.get("X-API-Key")

    if not api_key or provided != api_key:
        return jsonify({"ok": False, "error": "Chave de API inválida"}), 401

    data = request.get_json(silent=True) or {}
    return jsonify({"ok": True, "received": data, "status": "Authorized"}), 200
