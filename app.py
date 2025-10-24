from dotenv import load_dotenv
load_dotenv()

import os
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from datetime import timezone
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException

# Models / DB (o SQLAlchemy e metadata estão definidos em models.py)
from models import db  # em models.py: db = SQLAlchemy(metadata=metadata)

# Opcional: Flask-Migrate (não quebra se faltar no deploy)
try:
    from flask_migrate import Migrate
    migrate = Migrate()
except ModuleNotFoundError:
    migrate = None

# Opcional: CORS
try:
    from flask_cors import CORS
except Exception:
    CORS = None

try:
    from dotenv import load_dotenv
    load_dotenv()  # lê .env automaticamente
except Exception:
    pass
# -----------------------------------------------------------------------------
# Helpers de conexão com o banco
# -----------------------------------------------------------------------------
def _normalize_scheme(url: str) -> str:
    """Converte 'postgres://' (heroku-style) para 'postgresql://'."""
    if url and url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url

def _ensure_ssl_if_public(url: str) -> str:
    """Para hosts públicos, garante sslmode=require (Railway/Heroku)."""
    if not url:
        return url
    u = urlparse(url)
    # se estiver em rede privada (ex.: *.railway.internal), não força SSL
    is_internal = u.hostname and u.hostname.endswith(".railway.internal")
    if is_internal:
        return url
    q = dict(parse_qsl(u.query))
    q.setdefault("sslmode", "require")
    return urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode(q), u.fragment))

def _force_psycopg3_if_available(url: str) -> str:
    """Se psycopg3 estiver instalado, usa 'postgresql+psycopg://'."""
    if not url:
        return url
    try:
        import psycopg  # noqa: F401 (verifica se existe)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
    except Exception:
        pass
    return url

def _mask_url(url: str) -> str:
    """Remove credenciais da URL (para logs)."""
    if not url:
        return ""
    u = urlparse(url)
    netloc = u.netloc.split("@", 1)[1] if "@" in u.netloc else u.netloc
    return urlunparse((u.scheme, netloc, u.path, u.params, u.query, u.fragment))

def _pick_database_url() -> str:
    """
    Escolhe a URL do banco exclusivamente de DATABASE_URL (ou DATABASE_PUBLIC_URL).
    Sem fallback para SQLite — se não houver Postgres, lança erro.
    """
    raw = os.getenv("DATABASE_URL") or os.getenv("DATABASE_PUBLIC_URL") or ""
    if not raw:
        raise RuntimeError(
            "DATABASE_URL não definida. Configure sua URL do Postgres "
            "(ex.: postgresql://user:pass@host:5432/db?sslmode=require)."
        )
    raw = _normalize_scheme(raw)
    raw = _ensure_ssl_if_public(raw)
    raw = _force_psycopg3_if_available(raw)
    return raw


# -----------------------------------------------------------------------------
# Hotfix idempotente (ex.: garantir coluna em bases antigas — opcional)
# -----------------------------------------------------------------------------
def _ensure_birth_date_column(app: Flask):
    """
    Exemplo de hotfix seguro: garantir coluna 'birth_date' em 'matriculas'.
    Em bases novas/migradas corretamente, não faz nada.
    """
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
                    app.logger.info("[INIT] (sqlite) Coluna birth_date criada em matriculas.")
            elif engine_name.startswith("postgres"):
                db.session.execute(text(
                    "ALTER TABLE matriculas ADD COLUMN IF NOT EXISTS birth_date VARCHAR"
                ))
                db.session.commit()
                app.logger.info("[INIT] (postgres) Garantida coluna birth_date em matriculas.")
        except Exception as e:
            app.logger.warning(f"[INIT] Aviso ao garantir coluna birth_date: {e}")


# -----------------------------------------------------------------------------
# Application Factory (recomendado)
# -----------------------------------------------------------------------------
def create_app() -> Flask:
    app = Flask(__name__)

    # ============================ Config base ================================
    db_uri = _pick_database_url()  # ❗ sem fallback para SQLite

    app.config.update(
        # Flask
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-change-me"),
        DEBUG=os.getenv("FLASK_DEBUG", "0") == "1",
        JSON_SORT_KEYS=False,

        # Branding (opcional)
        BRAND_PRIMARY=os.getenv("BRAND_PRIMARY", "#7a1315"),
        BRAND_ACCENT=os.getenv("BRAND_ACCENT", "#d1a34a"),
        BRAND_BG=os.getenv("BRAND_BG", "#231f20"),
        BRAND_CARD=os.getenv("BRAND_CARD", "#2e2b2c"),
        BRAND_LINE=os.getenv("BRAND_LINE", "#3a3536"),
        LOGO_URL=os.getenv("LOGO_URL", ""),

        # DB
        SQLALCHEMY_DATABASE_URI=db_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},

        # CORS (domínios permitidos; ajuste para seu front)
        CORS_ORIGINS=os.getenv("CORS_ORIGINS", "*"),
    )

    # Log de diagnóstico (sem credenciais)
    app.logger.info(f"DB URI efetiva (mascarada): { _mask_url(app.config['SQLALCHEMY_DATABASE_URI']) }")

    # ============================ Filtros Jinja (fuso) =======================
    def _to_brt(dt):
        """Converte datetime UTC (ou naive UTC) para America/Sao_Paulo."""
        if not dt:
            return None
        if getattr(dt, "tzinfo", None) is None:
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

    # ============================ Hotfix opcional ============================
    from sqlalchemy import inspect
    with app.app_context():
        try:
            insp = inspect(db.engine)
            if insp.has_table("matriculas"):
                _ensure_birth_date_column(app)
                app.logger.info("Tabela 'matriculas' encontrada; coluna 'birth_date' verificada.")
            else:
                app.logger.info("Tabela 'matriculas' ainda não existe; pulando ensure_birth_date_column.")
        except Exception as e:
            app.logger.warning(f"Erro ao verificar tabela 'matriculas': {e}")

    # ============================ CORS (opcional) ============================
    if CORS:
        CORS(app, resources={r"/*": {"origins": app.config["CORS_ORIGINS"]}})

    # ============================ Blueprints ================================
    def _safe_register(import_path: str, name: str):
        """
        Importa e registra um blueprint que já define seu próprio url_prefix.
        Evita crash na inicialização em produção.
        """
        try:
            module = __import__(import_path, fromlist=[name])
            bp = getattr(module, name)
            app.register_blueprint(bp)  # blueprint deve ter url_prefix internamente
            app.logger.info(f"Blueprint {import_path}.{name} registrado.")
        except Exception as e:
            app.logger.warning(f"Não foi possível registrar {import_path}.{name}: {e}")

    # Registre aqui seus módulos
    _safe_register("modules.auth.routes", "auth_bp")
    _safe_register("modules.matricula.routes", "matricula_bp")
    _safe_register("modules.workato.routes", "workato_bp")
    _safe_register("modules.presenca.routes", "presenca_bp")
    _safe_register("modules.checkin.routes", "checkin_bp")

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
        return jsonify(info), (200 if info["ok"] else 500)

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
        return jsonify({"count": len(routes), "routes": routes}), 200

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