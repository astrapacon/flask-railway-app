import os, sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from models import db, Matricula

app = create_app()

with app.app_context():
    code = "MR25684"
    existing = Matricula.query.filter_by(code=code).first()
    if existing:
        print(f"⚠️ Matrícula {code} já existe, ignorando inserção.")
    else:
        m = Matricula(code=code, holder_name="Ana Silva", cpf="10688046967", status="active")
        db.session.add(m)
        db.session.commit()
        print(f"✅ Matrícula de teste criada: {m.code}")