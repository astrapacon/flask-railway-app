# wsgi.py
from app import create_app

# chama a função que cria a instância da aplicação Flask
app = create_app()

# permite rodar localmente com: python wsgi.py
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)