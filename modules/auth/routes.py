from flask import Blueprint, jsonify, request
from modules.utils.common import generate_token, require_auth

auth_bp = Blueprint("auth", __name__)

@auth_bp.post("/login")
def login():
    """
    Body:
      { "user": "a.strapacon", "password": "123" }
    Retorna um token simples (HMAC base64) só para testes.
    """
    body = request.get_json(silent=True) or {}
    user = (body.get("user") or "guest").strip()

    # Gere um token para o usuário informado
    token = generate_token(subject=user)

    return jsonify(ok=True, user=user, token=token), 200

@auth_bp.get("/me")
@require_auth
def me():
    # Rota protegida por API_TOKEN (via env), não pelo token gerado no /login.
    return jsonify(ok=True, me="usuario_autenticado"), 200