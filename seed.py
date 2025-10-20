# seed.py
from app import create_app
from models import db, Matricula

app = create_app()

with app.app_context():
    # cria as tabelas (se ainda não existirem)
    db.create_all()

    # cria uma matrícula de exemplo
    m = Matricula(code="MR25684", holder_name="Ana Silva", status="active")

    db.session.add(m)
    db.session.commit()

    print("✅ Matrícula criada com sucesso:", m.code)