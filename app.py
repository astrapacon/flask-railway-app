# app.py
# -----------------------------------------------------------------------------
# Programa de Multiplicadores — Aplicação Flask principal
# -----------------------------------------------------------------------------

import os
from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

# -----------------------------------------------------------------------------
# DB (SQLAlchemy inicializado em models.py)
# -----------------------------------------------------------------------------
from models import db

# -----------------------------------------------------------------------------
# Blueprints — garanta que esses módulos existem
# -----------------------------------------------------------------------------
from modules.auth.routes import auth_bp
from modules.matricula.routes import matricula_bp
from modules.workato.routes import workato_bp
from modules.presenca.routes import presenca_bp

# -----------------------------------------------------------------------------
# CORS (opcional). Se não estiver instalado, segue sem erro.
# -----------------------------------------------------------------------------
try:
    from flask_cors import CORS
except Exception:
    CORS = None


# -----------------------------------------------------------------------------
# Application Factory
# -----------------------------------------------------------------------------
def create_app() -> Flask:
    """Cria e configura a aplicação Flask"""
    app = Flask(__name__)

    # =========================================================================
    # Configurações BASE (com defaults seguros para dev)
    # =========================================================================
    app.config.update(
        # Segurança
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-change-me"),

        # Tokens / regras internas
        TOKEN_TTL_SECONDS=int(os.getenv("TOKEN_TTL_SECONDS", "3600")),
        MATRICULA_PREFIX=os.getenv("MATRICULA_PREFIX", "MR"),
        MATRICULA_DIGITS=int(os.getenv("MATRICULA_DIGITS", "5")),  # 5 dígitos
        MATRICULA_SALT=os.getenv("MATRICULA_SALT", "salt-fixo-para-matricula"),

        # Banco — Railway fornece DATABASE_URL (Postgres). Local usa SQLite.
        SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", "sqlite:///local.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,

        # JSON
        JSON_SORT_KEYS=False,

        # Debug controlado por env (NÃO use 1 em produção)
        DEBUG=os.getenv("FLASK_DEBUG", "0") == "1",

        # =========================================================================
        # BRANDING via ENV (cores e logo)
        # =========================================================================
        BRAND_PRIMARY=os.getenv("BRAND_PRIMARY", "#7a1315"),  # vermelho institucional
        BRAND_ACCENT=os.getenv("BRAND_ACCENT",  "#d1a34a"),   # dourado leve
        BRAND_BG=os.getenv("BRAND_BG",          "#231f20"),   # fundo escuro
        BRAND_CARD=os.getenv("BRAND_CARD",      "#2e2b2c"),   # cards
        BRAND_LINE=os.getenv("BRAND_LINE",      "#3a3536"),   # bordas
        LOGO_URL=os.getenv("LOGO_URL",          ""),          # opcional (Railway ENV)

        # CORS (defina CORS_ORIGINS no Railway para restringir)
        CORS_ORIGINS=os.getenv("CORS_ORIGINS", "*"),
    )

    # =========================================================================
    # Inicializa o Banco de Dados
    # =========================================================================
    db.init_app(app)

    # =========================================================================
    # CORS opcional — habilite amplo em dev; restrinja em prod
    # =========================================================================
    if CORS:
        CORS(app, resources={r"/*": {"origins": app.config["CORS_ORIGINS"]}})

    # =========================================================================
    # Registro dos Blueprints
    # =========================================================================
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(matricula_bp, url_prefix="/matricula")
    app.register_blueprint(workato_bp, url_prefix="/workato")
    app.register_blueprint(presenca_bp, url_prefix="/presenca")

    # =========================================================================
    # Healthchecks (para orquestradores e uptime checks)
    # =========================================================================
    @app.get("/health")
    def health():
        """Retorna OK simples para verificação de vida"""
        return jsonify(status="ok"), 200

    @app.get("/ready")
    def ready():
        """Verifica se o app está pronto (poderia testar o DB aqui)"""
        return jsonify(ready=True), 200

    # =========================================================================
    # Handlers de erro — respostas padronizadas em JSON
    # =========================================================================
    @app.errorhandler(HTTPException)
    def handle_http_exc(e: HTTPException):
        """Erros HTTP (404, 400, etc.)"""
        return (
            jsonify(
                ok=False,
                error={
                    "code": e.code,
                    "name": e.name,
                    "message": e.description,
                },
            ),
            e.code,
        )

    @app.errorhandler(Exception)
    def handle_generic_exc(e: Exception):
        """Erro genérico (500). Evita vazar stacktrace."""
        # Para logar no console, descomente:
        # app.logger.exception(e)
        return (
            jsonify(
                ok=False,
                error={
                    "code": 500,
                    "name": "Internal Server Error",
                    "message": "Ocorreu um erro inesperado.",
                },
            ),
            500,
        )

    return app


# -----------------------------------------------------------------------------
# Exposição para Gunicorn e execução local
# -----------------------------------------------------------------------------
app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=app.config.get("DEBUG", False))
