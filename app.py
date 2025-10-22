# app.py
# -----------------------------------------------------------------------------
# Programa de Multiplicadores — Aplicação Flask principal
# -----------------------------------------------------------------------------

import os
from flask import Flask, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException

# -----------------------------------------------------------------------------
# DB (SQLAlchemy inicializado em models.py)
# -----------------------------------------------------------------------------
from models import db  # onde você definiu: db = SQLAlchemy()

# -----------------------------------------------------------------------------
# Migrate — protegido para evitar crash se faltar o pacote no deploy
# -----------------------------------------------------------------------------
try:
    from flask_migrate import Migrate
    migrate = Migrate()
except ModuleNotFoundError:
    Migrate = None
    migrate = None

# CORS (opcional)
try:
    from flask_cors import CORS
except Exception:
    CORS = None


def _normalize_database_url(url: str) -> str:
    """
    Railway/Heroku às vezes expõem 'postgres://...' (formato legado).
    SQLAlchemy espera 'postgresql://...'.
    """
    if url and url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def _ensure_birth_date_column(app: Flask):
    """
    Hotfix idempotente para garantir a coluna 'birth_date' em produção.
    - Em SQLite: usa PRAGMA table_info + ALTER TABLE ADD COLUMN
    - Em PostgreSQL: usa ALTER TABLE ... ADD COLUMN IF NOT EXISTS
    - Em outros dialetos, tenta um caminho seguro e ignora falhas silenciosamente
    """
    from sqlalchemy import text

    with app.app_context():
        try:
            engine_name = db.engine.name  # 'sqlite', 'postgresql', etc.

            if engine_name == "sqlite":
                cols = db.session.execute(text("PRAGMA table_info(matriculas)")).fetchall()
                names = {c[1] for c in cols}  # (cid, name, type, notnull, dflt_value, pk)
                if "birth_date" not in names:
                    db.session.execute(text("ALTER TABLE matriculas ADD COLUMN birth_date TEXT"))
                    db.session.commit()
                    print("[INIT] (sqlite) Coluna birth_date criada em matriculas.")
                else:
                    print("[INIT] (sqlite) Coluna birth_date já existe.")

            elif engine_name == "postgresql":
                # VARCHAR padrão aqui; ajuste para DATE se seu modelo usar db.Date
                db.session.execute(
                    text("ALTER TABLE matriculas ADD COLUMN IF NOT EXISTS birth_date VARCHAR")
                )
                db.session.commit()
                print("[INIT] (postgresql) Garantida coluna birth_date em matriculas.")

            else:
                # Tentativa genérica
                try:
                    db.session.execute(text("ALTER TABLE matriculas ADD COLUMN birth_date TEXT"))
                    db.session.commit()
                    print(f"[INIT] ({engine_name}) birth_date adicionada (best-effort).")
                except Exception as e_inner:
                    print(f"[INIT] ({engine_name}) Ignorando criação de birth_date: {e_inner}")

        except Exception as e:
            # Não quebra o boot do app por causa do hotfix
            print(f"[INIT] Aviso ao garantir coluna birth_date: {e}")


# -----------------------------------------------------------------------------
# Application Factory
# -----------------------------------------------------------------------------
def create_app() -> Flask:
    """Cria e configura a aplicação Flask"""
    app = Flask(__name__)

    # =========================================================================
    # Configurações BASE (com defaults seguros para dev)
    # =========================================================================
    database_url = os.getenv("DATABASE_URL", "sqlite:///local.db")
    database_url = _normalize_database_url(database_url)

    app.config.update(
        # Segurança
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-change-me"),

        # Tokens / regras internas
        TOKEN_TTL_SECONDS=int(os.getenv("TOKEN_TTL_SECONDS", "3600")),
        MATRICULA_PREFIX=os.getenv("MATRICULA_PREFIX", "MR"),
        MATRICULA_DIGITS=int(os.getenv("MATRICULA_DIGITS", "5")),  # 5 dígitos
        MATRICULA_SALT=os.getenv("MATRICULA_SALT", "salt-fixo-para-matricula"),

        # Banco — Railway fornece DATABASE_URL (Postgres). Local usa SQLite.
        SQLALCHEMY_DATABASE_URI=database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,

        # JSON
        JSON_SORT_KEYS=False,

        # Debug controlado por env (NÃO use 1 em produção)
        DEBUG=os.getenv("FLASK_DEBUG", "0") == "1",

        # BRANDING via ENV (cores e logo)
        BRAND_PRIMARY=os.getenv("BRAND_PRIMARY", "#7a1315"),
        BRAND_ACCENT=os.getenv("BRAND_ACCENT",  "#d1a34a"),
        BRAND_BG=os.getenv("BRAND_BG",          "#231f20"),
        BRAND_CARD=os.getenv("BRAND_CARD",      "#2e2b2c"),
        BRAND_LINE=os.getenv("BRAND_LINE",      "#3a3536"),
        LOGO_URL=os.getenv("LOGO_URL",          ""),

        # CORS (defina CORS_ORIGINS no Railway para restringir)
        CORS_ORIGINS=os.getenv("CORS_ORIGINS", "*"),
    )

    # =========================================================================
    # Inicializa o Banco de Dados e Migrations
    # =========================================================================
    db.init_app(app)
    if migrate:
        migrate.init_app(app, db)

    # =========================================================================
    # Hotfix: garantir coluna 'birth_date' em prod (idempotente)
    # =========================================================================
    _ensure_birth_date_column(app)

    # =========================================================================
    # CORS opcional — habilite amplo em dev; restrinja em prod
    # =========================================================================
    if CORS:
        CORS(app, resources={r"/*": {"origins": app.config["CORS_ORIGINS"]}})

    # =========================================================================
    # Registro dos Blueprints (import tardio para evitar circularidades)
    # =========================================================================
    from modules.auth.routes import auth_bp
    from modules.matricula.routes import matricula_bp
    from modules.workato.routes import workato_bp
    from modules.presenca.routes import presenca_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(matricula_bp, url_prefix="/matricula")
    app.register_blueprint(workato_bp, url_prefix="/workato")
    app.register_blueprint(presenca_bp, url_prefix="/presenca")

    # =========================================================================
    # Healthchecks (para orquestradores e uptime checks)
    # =========================================================================
    @app.get("/health")
    def health():
        return jsonify(status="ok"), 200

    @app.get("/ready")
    def ready():
        return jsonify(ready=True), 200

    # =========================================================================
    # ROTA DE DEBUG: Mapa de rotas
    # =========================================================================
    @app.get("/debug/routes")
    def routes_map():
        routes = []
        for rule in app.url_map.iter_rules():
            if rule.endpoint != "static":
                routes.append({
                    "path": str(rule),
                    "methods": sorted([m for m in rule.methods if m not in ("HEAD", "OPTIONS")]),
                    "endpoint": rule.endpoint,
                })
        return jsonify({"count": len(routes), "routes": routes})

    # Favicon básico (evita 404 do navegador)
    @app.get("/favicon.ico")
    def favicon():
        static_dir = os.path.join(app.root_path, "static")
        if os.path.exists(os.path.join(static_dir, "favicon.ico")):
            return send_from_directory(static_dir, "favicon.ico")
        return ("", 204)  # No Content

    # =========================================================================
    # Handlers de erro — respostas padronizadas em JSON
    # =========================================================================
    @app.errorhandler(HTTPException)
    def handle_http_exc(e: HTTPException):
        return jsonify(
            ok=False,
            error={"code": e.code, "name": e.name, "message": e.description},
        ), e.code

    @app.errorhandler(Exception)
    def handle_generic_exc(e: Exception):
        app.logger.exception(e)  # loga stacktrace
        return jsonify(
            ok=False,
            error={"code": 500, "name": "Internal Server Error", "message": "Ocorreu um erro inesperado."},
        ), 500

    return app


# -----------------------------------------------------------------------------
# Exposição para Gunicorn e execução local
# -----------------------------------------------------------------------------
app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=app.config.get("DEBUG", False))
