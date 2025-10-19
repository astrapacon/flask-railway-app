from flask import Blueprint, jsonify, request

# o nome tem que ser EXATAMENTE esse
auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    user = data.get("user", "guest")
    return jsonify({
        "ok": True,
        "user": user
    })
