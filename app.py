from flask import Flask, jsonify
from modules.auth.routes import auth_bp
from modules.matricula.routes import matricula_bp
from modules.workato.routes import workato_bp


app = Flask(__name__)

import os

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.config["TOKEN_TTL_SECONDS"] = int(os.getenv("TOKEN_TTL_SECONDS", "3600"))
app.config["MATRICULA_PREFIX"] = os.getenv("MATRICULA_PREFIX", "MR")
app.config["MATRICULA_DIGITS"] = int(os.getenv("MATRICULA_DIGITS", "5"))  # 5 dígitos
app.config["MATRICULA_SALT"] = os.getenv("MATRICULA_SALT", "salt-fixo-para-matricula")


# registre os blueprints
app.register_blueprint(auth_bp, url_prefix="/auth")
app.register_blueprint(matricula_bp, url_prefix="/matricula")
app.register_blueprint(workato_bp, url_prefix="/workato")

@app.get("/health")
def health():
    return jsonify(status="ok"), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)