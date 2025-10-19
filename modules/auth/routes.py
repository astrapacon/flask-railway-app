from flask import Blueprint, jsonify, request
from modules.utils.common import generate_token, require_auth

auth_bp = Blueprint("auth", __name__)

@auth_bp.post("/login")
def login():
    """
    Body:
      { "user": "seu_usuario", "password": "..." }
    """
    body = request.get_json(silent=True) or {}
    user = (body.get("user") or "guest").strip()

    token = generate_token(identity=user)  # usa SECRET_KEY e TTL
    return jsonify(ok=True, user=user, token=token), 200

@auth_bp.get("/me")
@require_auth
def me():
    # Rota protegida por API_TOKEN (via env), não pelo token gerado no /login.
    return jsonify(ok=True, me="usuario_autenticado"), 200