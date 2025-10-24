# models.py
import datetime as dt
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import MetaData, Index
from sqlalchemy.sql import func
from werkzeug.security import generate_password_hash, check_password_hash

# 1) Convenção de nomes (alegórica e previsível p/ Alembic)
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# 2) Metadata único com naming convention
metadata = MetaData(naming_convention=NAMING_CONVENTION)

# 3) SQLAlchemy usando esse metadata
db = SQLAlchemy(metadata=metadata)

# ============== EXEMPLOS DE MODELOS (opcional) ==============

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.BigInteger, primary_key=True)
    username = db.Column(db.String(80), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="admin")
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def set_password(self, raw: str):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

# Unicidade case-insensitive (Postgres): índice único em lower(username)
Index("uq_users_username_lower", db.func.lower(User.username), unique=True, postgresql_using="btree")

# Exemplo de outros modelos (ajuste aos seus campos reais)
class Matricula(db.Model):
    __tablename__ = "matriculas"
    id = db.Column(db.BigInteger, primary_key=True)
    code = db.Column(db.String(8), nullable=False, unique=True, index=True)  # ex.: MR41081
    cpf = db.Column(db.String(11), nullable=False, index=True)
    holder_name = db.Column(db.String(120))
    birth_date = db.Column(db.String(10))  # "YYYY-MM-DD"
    status = db.Column(db.String(20), default="active")
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)

class Presenca(db.Model):
    __tablename__ = "presencas"
    id = db.Column(db.BigInteger, primary_key=True)
    matricula_id = db.Column(db.BigInteger, db.ForeignKey("matriculas.id"), nullable=False, index=True)
    date_key = db.Column(db.Date, nullable=False, index=True)  # 1x por dia
    timestamp = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    ip = db.Column(db.String(64))
    user_agent = db.Column(db.String(300))
    source = db.Column(db.String(20), default="web")

class EventCheckin(db.Model):
    __tablename__ = "event_checkins"
    id = db.Column(db.BigInteger, primary_key=True)
    event_date = db.Column(db.Date, nullable=False, index=True)
    cpf = db.Column(db.String(11), nullable=False, index=True)
    birth_date = db.Column(db.String(10))
    name = db.Column(db.String(120))  # se você incluiu o nome
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        db.UniqueConstraint("event_date", "cpf", name="uq_event_checkins_event_date_cpf"),
    )