# models.py
import datetime as dt
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Matricula(db.Model):
    __tablename__ = "matriculas"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(16), unique=True, nullable=False, index=True)  # ex: MR25684
    holder_name = db.Column(db.String(120))
    cpf = db.Column(db.String(14))  # <<--- ADICIONE ESTA COLUNA
    status = db.Column(db.String(16), default="active")  # active|revoked|expired
    created_at = db.Column(db.DateTime, default=dt.datetime.utcnow)

class Presenca(db.Model):
    __tablename__ = "presencas"
    id = db.Column(db.Integer, primary_key=True)
    matricula_id = db.Column(db.Integer, db.ForeignKey("matriculas.id"), nullable=False, index=True)
    date_key = db.Column(db.Date, nullable=False, index=True)  # 1 por dia
    timestamp = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)
    ip = db.Column(db.String(64))
    user_agent = db.Column(db.String(300))
    source = db.Column(db.String(80))
    __table_args__ = (db.UniqueConstraint("matricula_id", "date_key", name="uq_presenca_por_dia"),)
