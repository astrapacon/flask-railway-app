import os, sys
# Sobe até a raiz do projeto (onde está app.py)
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app import create_app
from models import db

app = create_app()

with app.app_context():
    db.create_all()
    print("✅ Banco de dados criado com sucesso!")
    print("Arquivo:", app.config["SQLALCHEMY_DATABASE_URI"])