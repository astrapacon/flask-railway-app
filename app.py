# app.py
# -----------------------------------------------------------------------------
# Programa de Multiplicadores — Aplicação Flask principal
# -----------------------------------------------------------------------------

import os
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from datetime import timezone
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException

# DB (SQLAlchemy inicializado em models.py)
from models import db  # em models.py: db = SQLAlchemy()

# Migrate — protegido para evitar crash se faltar o pacote no deploy
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


# -----------------------------------------------------------------------------
# Helpers de conexão com o banco
# -----------------------------------------------------------------------------
def _normalize_scheme(url: str) -> str:
    """SQLAlchemy quer 'postgresql://' (não 'postgres://')."""
    if url and url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def _ensure_ssl_if_public(url: str) -> str:
    """Se não for host interno da Railway, força sslmode=require."""
    if not url:
        return url
    u = urlparse(url)
    is_internal = u.hostname and u.hostname.endswith(".railway.internal")
    if is_internal:
        return url  # private network → sem necessidade de sslmode
    q = dict(parse_qsl(u.query))
    q.setdefault("sslmode", "require")
    return urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode(q), u.fragment))


def _force_psycopg3_if_available(url: str) -> str:
    """
    Se psycopg3 estiver instalado, força o driver 'postgresql+psycopg://'.
    (Mantém compatível se você estiver usando psycopg2-binary.)
    """
    if not url:
        return url
    try:
        import psycopg  # psycopg3
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
    except Exception:
        pass
    return url


def _mask_url(url: str) -> str:
    """Remove credenciais da URL p/ logs/diagnósticos."""
    if not url:
        return ""
    u = urlparse(url)
    netloc = u.netloc.split("@", 1)[1] if "@" in u.netloc else u.netloc
    return urlunparse((u.scheme, netloc, u.path, u.params, u.query, u.fragment))


def _pick_database_url() -> str:
    """
    Escolhe DATABASE_URL (privada) e cai para DATABASE_PUBLIC_URL se necessário.
    Normaliza esquema, força ssl em host público e usa psycopg3 se disponível.
    """
    raw = os.getenv("DATABASE_URL") or os.getenv("DATABASE_PUBLIC_URL") or ""
    raw = _normalize_scheme(raw)
    raw = _ensure_ssl_if_public(raw)
    raw = _force_psycopg3_if_available(raw)
    return raw


# -----------------------------------------------------------------------------
# Hotfix idempotente para garantir coluna birth_date na tabela 'matriculas'
# -----------------------------------------------------------------------------
def _ensure_birth_date_column(app: Flask):
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
                db.session.execute(text(
                    "ALTER TABLE matriculas ADD COLUMN IF NOT EXISTS birth_date VARCHAR"
                ))
                db.session.commit()
                print("[INIT] (postgresql) Garantida coluna birth_date em matriculas.")
        except Exception as e:
            print(f"[INIT] Aviso ao garantir coluna birth_date: {e}")


# -----------------------------------------------------------------------------
# Application Factory
# -----------------------------------------------------------------------------
def create_app() -> Flask:
    app = Flask(__name__)

    # ============================ Config base ================================
    db_uri = _pick_database_url() or "sqlite:///local.db"

    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-change-me"),
        TOKEN_TTL_SECONDS=int(os.getenv("TOKEN_TTL_SECONDS", "3600")),
        MATRICULA_PREFIX=os.getenv("MATRICULA_PREFIX", "MR"),
        MATRICULA_DIGITS=int(os.getenv("MATRICULA_DIGITS", "5")),
        MATRICULA_SALT=os.getenv("MATRICULA_SALT", "salt-fixo-para-matricula"),

        SQLALCHEMY_DATABASE_URI=db_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},

        JSON_SORT_KEYS=False,
        DEBUG=os.getenv("FLASK_DEBUG", "0") == "1",
        BRAND_PRIMARY=os.getenv("BRAND_PRIMARY", "#7a1315"),
        BRAND_ACCENT=os.getenv("BRAND_ACCENT", "#d1a34a"),
        BRAND_BG=os.getenv("BRAND_BG", "#231f20"),
        BRAND_CARD=os.getenv("BRAND_CARD", "#2e2b2c"),
        BRAND_LINE=os.getenv("BRAND_LINE", "#3a3536"),
        LOGO_URL=os.getenv("LOGO_URL", ""),
        CORS_ORIGINS=os.getenv("CORS_ORIGINS", "*"),
    )

    # Log de diagnóstico (sem credenciais)
    app.logger.info(f"DB URI efetiva (mascarada): { _mask_url(app.config['SQLALCHEMY_DATABASE_URI']) }")

    # ============================ Filtros Jinja (fuso horário) ===============
    def _to_brt(dt):
        """Converte datetime UTC (ou naive UTC) para America/Sao_Paulo."""
        if not dt:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("America/Sao_Paulo"))

    def _fmt_brt(dt, fmt="%d/%m/%Y %H:%M"):
        dt_brt = _to_brt(dt)
        return dt_brt.strftime(fmt) if dt_brt else ""

    app.jinja_env.filters["to_brt"] = _to_brt
    app.jinja_env.filters["fmt_brt"] = _fmt_brt

    # ============================ DB & Migrate ===============================
    db.init_app(app)
    if migrate:
        migrate.init_app(app, db)

    # ============================ Hotfix birth_date ==========================
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

    # ============================ CORS (opcional) ============================
    if CORS:
        CORS(app, resources={r"/*": {"origins": app.config["CORS_ORIGINS"]}})

    # ============================ Blueprints ================================
    def _safe_register(import_path: str, name: str, prefix: str):
        try:
            module = __import__(import_path, fromlist=[name])
            bp = getattr(module, name)
            app.register_blueprint(bp, url_prefix=prefix)
            app.logger.info(f"Blueprint {import_path}.{name} registrado em {prefix}")
        except Exception as e:
            app.logger.warning(f"Não foi possível registrar {import_path}.{name}: {e}")

    _safe_register("modules.auth.routes", "auth_bp", "/auth")
    _safe_register("modules.matricula.routes", "matricula_bp", "/matricula")
    _safe_register("modules.workato.routes", "workato_bp", "/workato")
    _safe_register("modules.presenca.routes", "presenca_bp", "/presenca")
    _safe_register("modules.checkin.routes", "checkin_bp", "/checkin")

    # ============================ Healthchecks ===============================
    @app.get("/health")
    def health():
        return jsonify(status="ok"), 200

    @app.get("/ready")
    def ready():
        return jsonify(ready=True), 200

    # ============================ DB Check ==================================
    from sqlalchemy import text as _sql_text

    @app.get("/dbcheck")
    def dbcheck():
        info = {
            "ok": False,
            "driver": None,
            "uri_masked": _mask_url(app.config.get("SQLALCHEMY_DATABASE_URI", "")),
            "error": None,
            "server_version": None,
        }
        try:
            bind = db.session.get_bind()
            info["driver"] = str(bind.dialect.name)
            ver = db.session.execute(_sql_text("SELECT version()")).scalar()
            info["server_version"] = ver
            info["ok"] = True
        except Exception as e:
            info["error"] = str(e)
        return jsonify(info)

    # ============================ Debug: mapa de rotas =======================
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

    # ============================ Favicon ===================================
    @app.get("/favicon.ico")
    def favicon():
        static_dir = os.path.join(app.root_path, "static")
        if os.path.exists(os.path.join(static_dir, "favicon.ico")):
            return send_from_directory(static_dir, "favicon.ico")
        return ("", 204)

    # ============================ Handlers de erro ===========================
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


# -----------------------------------------------------------------------------
# Instância WSGI para o Gunicorn
# -----------------------------------------------------------------------------
app = create_app()