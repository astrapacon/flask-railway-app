from flask import Blueprint, request, jsonify, current_app
from modules.utils.common import generate_token

bp = Blueprint("auth", __name__)

@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    user = str(data.get("username", ""))
    pwd  = str(data.get("password", ""))
    if user == current_app.config["API_USERNAME"] and pwd == current_app.config["API_PASSWORD"]:
        token = generate_token(user)
        return jsonify({"status": "ok", "access_token": token, "expires_in": current_app.config["TOKEN_TTL_SECONDS"]})
    return jsonify({"status": "unauthorized", "message": "Invalid credentials"}), 401
