import math
from functools import wraps
from flask import request, jsonify, current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import pandas as pd

# ============================
# 🔐 Autenticação por token
# ============================

def _serializer():
    # Requer: app.config["SECRET_KEY"]
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="auth-token")

def generate_token(identity: str) -> str:
    # Gera token assinado com SECRET_KEY
    return _serializer().dumps({"sub": identity})

def verify_token(token: str):
    # Valida token e TTL
    # Requer: app.config["TOKEN_TTL_SECONDS"]
    return _serializer().loads(token, max_age=current_app.config["TOKEN_TTL_SECONDS"])

def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        authz = request.headers.get("Authorization", "")
        if not authz.startswith("Bearer "):
            return jsonify({"status": "unauthorized", "message": "Missing Bearer token"}), 401
        token = authz.split(" ", 1)[1].strip()
        try:
            verify_token(token)
        except SignatureExpired:
            return jsonify({"status": "unauthorized", "message": "Token expired"}), 401
        except BadSignature:
            return jsonify({"status": "unauthorized", "message": "Invalid token"}), 401
        except Exception as e:
            return jsonify({"status": "unauthorized", "message": f"Auth error: {e}"}), 401
        return fn(*args, **kwargs)
    return wrapper

# ============================
# 📊 Constantes e helpers de dados
# ============================

# nomes de colunas
COL_ANO_NASC = "Ano Nascimento"
COL_UF = "Uf Cliente"
COL_CIDADE = "Cidade"
COL_DATA_VENDA = "Cotas Id Cliente → Data Venda"
COL_VALOR = "Cotas Id Cliente → Valor Credito Venda"
COL_ID_COTA = "Cotas Id Cliente → Id Cota"
COL_TEM_PAGTO = "Cotas Id Cliente → Tem Pagamento"
COL_SEGMENTO = "Cotas Id Cliente → Segmento"

def _safe_div(a, b):
    return float(a) / float(b) if b not in (0, 0.0, None) else None

def _to_millions(x):
    return float(x) / 1_000_000 if pd.notna(x) else None

def _jround(x, nd=2):
    if x is None:
        return None
    try:
        xf = float(x)
        if math.isnan(xf) or math.isinf(xf):
            return None
        return round(xf, nd)
    except Exception:
        return None

def _is_paid(v) -> bool:
    if isinstance(v, (bool, int, float)):
        try:
            return bool(int(v))
        except Exception:
            return bool(v)
    s = str(v).strip().lower().replace("não", "nao")
    return s in {"sim", "s", "pago", "paga", "true", "1", "yes", "y"}

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    def norm(c):
        c = str(c).replace("->", "→")
        c = " ".join(c.split())
        return c.strip()
    df = df.copy()
    df.columns = [norm(c) for c in df.columns]
    ALT_MAP = {
        "ano_nascimento": COL_ANO_NASC,
        "uf_cliente": COL_UF,
        "cidade": COL_CIDADE,
        "data_producao": "Cotas Id Cliente → Data Producao",
        "segmento_bacen": "Cotas Id Cliente → Segmento Bacen",
        "id_cota": COL_ID_COTA,
        "segmento": COL_SEGMENTO,
        "tem_pagamento": COL_TEM_PAGTO,
        "nome_ponto_venda": "Cotas Id Cliente → Nome Ponto Venda",
        "id_pessoa": "Cotas Id Cliente → Id Pessoa",
        "codigo_ponto_venda": "Cotas Id Cliente → Codigo Ponto Venda",
        "producao_oficial": "Cotas Id Cliente → Producao Oficial",
        "valor_credito_venda": COL_VALOR,
        "data_venda": COL_DATA_VENDA,
    }
    df.columns = [ALT_MAP.get(c, c) for c in df.columns]
    return df