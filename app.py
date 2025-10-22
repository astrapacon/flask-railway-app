# app.py
# -----------------------------------------------------------------------------
# Programa de Multiplicadores — Aplicação Flask principal
# -----------------------------------------------------------------------------

import os
from flask import Flask, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException
from datetime import timezone
from zoneinfo import ZoneInfo

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
    """Railway/Heroku às vezes expõem 'postgres://...' (formato legado)."""
    if url and url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def _ensure_birth_date_column(app: Flask):
    """Hotfix idempotente para garantir a coluna 'birth_date' em produção."""
    from sqlalchemy import text
    with app.app_context():
        try:
            engine_name = db.engine.name
            if engine_name == "sqlite":
                cols = db.session.execute(text("PRAGMA table_info(matriculas)")).fetchall()
                names = {c[1] for c in cols}
                if "birth_date" not in names:
                    db.session.execute(text("ALTER TABLE matriculas ADD COLUMN birth_date TEXT"))
                    db.session.commit()
                    print("[INIT] (sqlite) Coluna birth_date criada em matriculas.")
            elif engine_name == "postgresql":
                db.session.execute(
                    text("ALTER TABLE matriculas ADD COLUMN IF NOT EXISTS birth_date VARCHAR")
                )
                db.session.commit()
                print("[INIT] (postgresql) Garantida coluna birth_date em matriculas.")
        except Exception as e:
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
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-change-me"),
        TOKEN_TTL_SECONDS=int(os.getenv("TOKEN_TTL_SECONDS", "3600")),
        MATRICULA_PREFIX=os.getenv("MATRICULA_PREFIX", "MR"),
        MATRICULA_DIGITS=int(os.getenv("MATRICULA_DIGITS", "5")),
        MATRICULA_SALT=os.getenv("MATRICULA_SALT", "salt-fixo-para-matricula"),
        SQLALCHEMY_DATABASE_URI=database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JSON_SORT_KEYS=False,
        DEBUG=os.getenv("FLASK_DEBUG", "0") == "1",
        BRAND_PRIMARY=os.getenv("BRAND_PRIMARY", "#7a1315"),
        BRAND_ACCENT=os.getenv("BRAND_ACCENT",  "#d1a34a"),
        BRAND_BG=os.getenv("BRAND_BG",          "#231f20"),
        BRAND_CARD=os.getenv("BRAND_CARD",      "#2e2b2c"),
        BRAND_LINE=os.getenv("BRAND_LINE",      "#3a3536"),
        LOGO_URL=os.getenv("LOGO_URL",          ""),
        CORS_ORIGINS=os.getenv("CORS_ORIGINS", "*"),
    )

    # =========================================================================
    # Filtros Jinja para fuso horário (UTC -> America/Sao_Paulo)
    # =========================================================================
    def _to_brt(dt):
        """Converte datetime UTC (ou naive UTC) para America/Sao_Paulo."""
        if not dt:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("America/Sao_Paulo"))

    def _fmt_brt(dt, fmt="%d/%m/%Y %H:%M"):
        """Converte e formata para string já no fuso de SP."""
        dt_brt = _to_brt(dt)
        return dt_brt.strftime(fmt) if dt_brt else ""

    app.jinja_env.filters["to_brt"] = _to_brt
    app.jinja_env.filters["fmt_brt"] = _fmt_brt

    # =========================================================================
    # Inicializa o Banco de Dados e Migrations
    # =========================================================================
    db.init_app(app)
    if migrate:
        migrate.init_app(app, db)

    # =========================================================================
    # Hotfix birth_date
    # =========================================================================
    from sqlalchemy import inspect
    with app.app_context():
        try:
            insp = inspect(db.engine)
            if insp.has_table("matriculas"):
                _ensure_birth_date_column(app)
                app.logger.info("Tabela 'matriculas' encontrada; coluna 'birth_date' verificada.")
            else:
                app.logger.warning("Tabela 'matriculas' ainda não existe; pulando ensure_birth_date_column.")
        except Exception as e:
            app.logger.error(f"Erro ao verificar tabela 'matriculas': {e}")

    # =========================================================================
    # CORS opcional
    # =========================================================================
    if CORS:
        CORS(app, resources={r"/*": {"origins": app.config["CORS_ORIGINS"]}})

    # =========================================================================
    # Registro dos Blueprints
    # =========================================================================
    from modules.auth.routes import auth_bp
    from modules.matricula.routes import matricula_bp
    from modules.workato.routes import workato_bp
    from modules.presenca.routes import presenca_bp
    from modules.checkin.routes import checkin_bp  # novo módulo

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(matricula_bp, url_prefix="/matricula")
    app.register_blueprint(workato_bp, url_prefix="/workato")
    app.register_blueprint(presenca_bp, url_prefix="/presenca")
    app.register_blueprint(checkin_bp, url_prefix="/checkin")  # ✅ novo módulo

    # =========================================================================
    # Healthchecks
    # =========================================================================
    @app.get("/health")
    def health():
        return jsonify(status="ok"), 200

    @app.get("/ready")
    def ready():
        return jsonify(ready=True), 200

    # =========================================================================
    # ROTA DE DEBUG: mapa de rotas
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

    # =========================================================================
    # Favicon básico
    # =========================================================================
    @app.get("/favicon.ico")
    def favicon():
        static_dir = os.path.join(app.root_path, "static")
        if os.path.exists(os.path.join(static_dir, "favicon.ico")):
            return send_from_directory(static_dir, "favicon.ico")
        return ("", 204)

    # =========================================================================
    # Handlers de erro
    # =========================================================================
    @app.errorhandler(HTTPException)
    def handle_http_exc(e: HTTPException):
        return jsonify(
            ok=False,
            error={"code": e.code, "name": e.name, "message": e.description},
        ), e.code

    @app.errorhandler(Exception)
    def handle_generic_exc(e: Exception):
        app.logger.exception(e)
        return jsonify(
            ok=False,
            error={"code": 500, "name": "Internal Server Error", "message": "Ocorreu um erro inesperado."},
        ), 500

    return app
