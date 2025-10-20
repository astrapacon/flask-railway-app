from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException
import os

# ======================================
# Imports dos Blueprints
# ======================================
from modules.auth.routes import auth_bp
from modules.matricula.routes import matricula_bp
from modules.workato.routes import workato_bp
from modules.presenca.routes import presenca_bp

# ======================================
# Importa o db (model base)
# ======================================
from models import db

# (Opcional) CORS – habilite se for consumir via browser de outro domínio
try:
    from flask_cors import CORS
except Exception:
    CORS = None  # se não tiver instalado, segue sem CORS


# ======================================
# Função de criação da aplicação
# ======================================
def create_app() -> Flask:
    app = Flask(__name__)

    # =========================
    # Configurações
    # =========================
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-change-me"),
        TOKEN_TTL_SECONDS=int(os.getenv("TOKEN_TTL_SECONDS", "3600")),
        MATRICULA_PREFIX=os.getenv("MATRICULA_PREFIX", "MR"),
        MATRICULA_DIGITS=int(os.getenv("MATRICULA_DIGITS", "5")),  # 5 dígitos
        MATRICULA_SALT=os.getenv("MATRICULA_SALT", "salt-fixo-para-matricula"),

        # ✅ Configuração de banco — SQLite local ou Postgres no Railway
        SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", "sqlite:///local.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,

        # Configuração opcional de debug
        DEBUG=os.getenv("FLASK_DEBUG", "0") == "1",
        JSON_SORT_KEYS=False,
    )
    # dentro de create_app(), antes de db.init_app(app)
    db_url = os.getenv("DATABASE_URL", "sqlite:///local.db")

    # normaliza heroku-style
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    # (apenas se você escolheu psycopg3)
    # Se quiser forçar o dialecto psycopg3:
    # if db_url.startswith("postgresql://"):
    #     db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

    app.config["SQLALCHEMY_DATABASE_URI"] = db_url

    db.init_app(app)

    # =========================
    # CORS (opcional)
    # =========================
    if CORS:
        # Libera tudo por padrão (ajuste para o domínio real depois)
        CORS(app)

    # =========================
    # Registro de Blueprints
    # =========================
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(matricula_bp, url_prefix="/matricula")
    app.register_blueprint(workato_bp, url_prefix="/workato")
    app.register_blueprint(presenca_bp, url_prefix="/presenca")  # ✅ incluído aqui

    # =========================
    # Healthchecks
    # =========================
    @app.get("/health")
    def health():
        return jsonify(status="ok"), 200

    @app.get("/ready")
    def ready():
        return jsonify(ready=True), 200

    # =========================
    # Tratamento de erros JSON
    # =========================
    @app.errorhandler(HTTPException)
    def handle_http_exc(e: HTTPException):
        resp = {
            "ok": False,
            "error": {
                "code": e.code,
                "name": e.name,
                "message": e.description,
            },
        }
        return jsonify(resp), e.code

    @app.errorhandler(Exception)
    def handle_generic_exc(e: Exception):
        resp = {
            "ok": False,
            "error": {
                "code": 500,
                "name": "Internal Server Error",
                "message": "Ocorreu um erro inesperado.",
            },
        }
        # Descomente se quiser logar exceções no console
        # app.logger.exception(e)
        return jsonify(resp), 500

    return app


# ======================================
# Exposição para o Gunicorn e execução local
# ======================================
app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=app.config.get("DEBUG", False))