from flask import Flask, jsonify, request

app = Flask(__name__)

@app.route("/")
def home():
    return {"status": "ok", "mensagem": "API Flask funcionando!"}

@app.route("/processar", methods=["POST"])
def processar():
    dados = request.get_json() or {}
    numeros = dados.get("numeros", [])
    soma = sum(numeros)
    media = soma / len(numeros) if numeros else 0
    return jsonify({
        "soma": soma,
        "media": media,
        "qtd_itens": len(numeros)
    })

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)