import os, sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text
from app import create_app
from models import db

app = create_app()

with app.app_context():
    conn = db.engine.connect()
    try:
        # Cria colunas se não existirem
        conn.execute(text("ALTER TABLE matriculas ADD COLUMN cpf VARCHAR(14)"))
        print("✅ Coluna 'cpf' adicionada.")
    except Exception as e:
        if "duplicate column name" in str(e) or "already exists" in str(e).lower():
            print("⚠️  Coluna 'cpf' já existe, ignorando.")
        else:
            raise

    try:
        conn.execute(text("ALTER TABLE matriculas ADD COLUMN holder_name VARCHAR(120)"))
        print("✅ Coluna 'holder_name' adicionada.")
    except Exception as e:
        if "duplicate column name" in str(e) or "already exists" in str(e).lower():
            print("⚠️  Coluna 'holder_name' já existe, ignorando.")
        else:
            raise

    conn.close()
    print("🚀 Migração concluída com sucesso.")