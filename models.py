# models.py
import datetime as dt
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import MetaData, text, UniqueConstraint

# ---------------------------------------------------------------------------
# Convenção de nomes para constraints/índices (evita "Constraint must have a name")
# ---------------------------------------------------------------------------
convention = {
    "ix": "ix_%(table_name)s_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=convention)

db = SQLAlchemy(metadata=metadata)

# ============================ MATRÍCULAS ============================
class Matricula(db.Model):
    __tablename__ = "matriculas"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(16), unique=True, nullable=False, index=True)   # Ex: MR25684
    holder_name = db.Column(db.String(120), nullable=True)

    # CPF armazenado só com dígitos (11)
    cpf = db.Column(db.String(11), unique=True, nullable=False, index=True)

    # Data de nascimento como string (YYYY-MM-DD)
    birth_date = db.Column(db.String(10), nullable=True)

    status = db.Column(db.String(20), nullable=False, server_default=text("'active'"))  # active|revoked|expired

    # Timestamps
    created_at = db.Column(db.DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    # Relação reversa: uma matrícula pode ter várias presenças
    presencas = db.relationship("Presenca", back_populates="matricula", lazy="dynamic")

    def __repr__(self):
        return f"<Matricula {self.code} ({self.status})>"


# ============================ PRESENÇAS ============================
class Presenca(db.Model):
    __tablename__ = "presencas"

    id = db.Column(db.Integer, primary_key=True)

    matricula_id = db.Column(
        db.Integer,
        db.ForeignKey("matriculas.id", name="fk_presencas_matricula_id_matriculas"),
        nullable=False,
        index=True,
    )

    # Uma presença por dia
    date_key = db.Column(db.String(10), nullable=False, index=True)  # 'YYYY-MM-DD'

    timestamp = db.Column(db.DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    ip = db.Column(db.String(64))
    user_agent = db.Column(db.String(300))
    source = db.Column(db.String(80))  # Ex: 'web', 'mobile', 'api'

    # Evita duplicidade de presença diária
    __table_args__ = (
        db.UniqueConstraint("matricula_id", "date_key", name="uq_presenca_por_dia"),
    )

    # Relação reversa
    matricula = db.relationship("Matricula", back_populates="presencas")

    def __repr__(self):
        return f"<Presenca {self.matricula_id} {self.date_key}>"


class EventCheckin(db.Model):
    __tablename__ = "event_checkins"

    id = db.Column(db.Integer, primary_key=True)
    event_date = db.Column(db.Date, nullable=False, index=True)

    # CPF normalizado: somente 11 dígitos
    cpf = db.Column(db.String(11), nullable=False, index=True)

    # Guardamos a data de nascimento como string 'YYYY-MM-DD' (compatível com seu padrão)
    birth_date = db.Column(db.String(10), nullable=False)

    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("event_date", "cpf", name="uq_event_date_cpf"),
    )

    def __repr__(self):
        return f"<EventCheckin {self.event_date} {self.cpf} {self.birth_date}>"