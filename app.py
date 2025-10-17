import os
import math
from datetime import datetime, timezone
from functools import wraps

import pandas as pd
from flask import Flask, request, jsonify
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

app = Flask(__name__)

# =========================
# Configurações/Constantes
# =========================
PERIOD_START = pd.Timestamp("2025-02-01", tz="UTC")  # desde fevereiro/2025

# ENV para autenticação
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-prod")
API_USERNAME = os.getenv("API_USERNAME", "admin")
API_PASSWORD = os.getenv("API_PASSWORD", "admin")
TOKEN_TTL_SECONDS = int(os.getenv("TOKEN_TTL_SECONDS", "86400"))  # 24h

# Nomes de colunas esperadas (usamos constantes para evitar typos)
COL_ANO_NASC = "ano_nascimento"
COL_UF = "uf_cliente"
COL_CIDADE = "cidade"
COL_DATA_VENDA = "data_producao"
COL_VALOR = "valor_credito_venda"
COL_ID_COTA = "id_cota"
COL_TEM_PAGTO = "tem_pagamento"
COL_SEGMENTO = "segmento_bacen"

# -------------------------
# Utilidades gerais
# -------------------------
def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza nomes de colunas vindos da Workato:
    - remove espaços extras,
    - padroniza seta '->' para '→'
    - mantém a acentuação (pois é exatamente como você recebe)
    """
    def norm(c):
        c = str(c).replace("->", "→")
        c = " ".join(c.split())  # colapsa múltiplos espaços
        return c.strip()
    df = df.copy()
    df.columns = [norm(c) for c in df.columns]
    return df

def _ensure_columns(df: pd.DataFrame, required_cols: list):
    faltando = [c for c in required_cols if c not in df.columns]
    if faltando:
        raise ValueError(f"Colunas obrigatórias ausentes: {faltando}. "
                         f"Recebido: {list(df.columns)}")

def _safe_div(a, b):
    return float(a) / float(b) if b not in (0, 0.0, None) else None

def _to_millions(x):
    return float(x) / 1_000_000 if pd.notna(x) else None

def _jround(x, nd=2):
    """Round seguro que troca NaN/inf por None (JSON válido)."""
    if x is None:
        return None
    try:
        xf = float(x)
        if math.isnan(xf) or math.isinf(xf):
            return None
        return round(xf, nd)
    except Exception:
        return None

# -------------------------
# Autenticação
# -------------------------
def _serializer():
    return URLSafeTimedSerializer(SECRET_KEY, salt="auth-token")

def generate_token(identity: str) -> str:
    s = _serializer()
    return s.dumps({"sub": identity})

def verify_token(token: str):
    s = _serializer()
    data = s.loads(token, max_age=TOKEN_TTL_SECONDS)
    return data  # {"sub": "..."}

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

# -------------------------
# Cálculo principal
# -------------------------
def calcular_metricas(df: pd.DataFrame) -> dict:
    df = _normalize_columns(df)
    _ensure_columns(df, [COL_DATA_VENDA, COL_VALOR, COL_ID_COTA, COL_TEM_PAGTO, COL_UF])

    # Tipos
    df = df.copy()
    df[COL_DATA_VENDA] = pd.to_datetime(df[COL_DATA_VENDA], utc=True, errors="coerce")
    df[COL_VALOR] = pd.to_numeric(df[COL_VALOR], errors="coerce")

    # Filtra período YTD (desde 2025-02-01)
    df = df[df[COL_DATA_VENDA] >= PERIOD_START]

    # Dedup por Id Cota (cada cota conta uma vez) — mantém a linha mais recente pela Data de Venda
    df = df.sort_values(COL_DATA_VENDA).drop_duplicates(subset=[COL_ID_COTA], keep="last")

    # Coluna de mês (YYYY-MM)
    df["mes"] = df[COL_DATA_VENDA].dt.to_period("M").astype(str)

    # -------- VENDAS (tudo) --------
    vendas = df.groupby("mes").agg({
        COL_VALOR: "sum",
        COL_ID_COTA: "nunique"
    }).rename(columns={COL_VALOR: "Venda_RS", COL_ID_COTA: "Venda_Qtde"})

    # -------- PRODUÇÃO (pagas) --------
    pagas = df[df[COL_TEM_PAGTO].astype(str).str.strip().str.lower() == "sim"]
    producao = pagas.groupby("mes").agg({
        COL_VALOR: "sum",
        COL_ID_COTA: "nunique"
    }).rename(columns={COL_VALOR: "Prod_RS", COL_ID_COTA: "Prod_Qtde"})

    # Junta e calcula métricas mensais
    resultado = vendas.join(producao, how="left").fillna(0.0)
    resultado["Conv_RS_%"] = resultado.apply(lambda r: _safe_div(r["Prod_RS"], r["Venda_RS"]), axis=1)
    resultado["Conv_Qtde_%"] = resultado.apply(lambda r: _safe_div(r["Prod_Qtde"], r["Venda_Qtde"]), axis=1)
    resultado["Ticket_Medio"] = resultado.apply(lambda r: _safe_div(r["Prod_RS"], r["Prod_Qtde"]), axis=1)
    resultado["Venda_RS_M"] = resultado["Venda_RS"].map(_to_millions)
    resultado["Prod_RS_M"] = resultado["Prod_RS"].map(_to_millions)

    # YTD (agregado do período)
    venda_total_qtde = int(df[COL_ID_COTA].nunique())
    venda_total_rs = float(df[COL_VALOR].sum(skipna=True))
    prod_total_qtde = int(pagas[COL_ID_COTA].nunique())
    prod_total_rs = float(pagas[COL_VALOR].sum(skipna=True))
    conv_anual_qtde = _safe_div(prod_total_qtde, venda_total_qtde)
    conv_anual_rs = _safe_div(prod_total_rs, venda_total_rs)

    # Cotas pagas por UF
    por_uf = pagas.groupby(COL_UF).agg({
        COL_ID_COTA: "nunique",
        COL_VALOR: "sum"
    }).rename(columns={COL_ID_COTA: "Cotas_Pagas_Qtde", COL_VALOR: "Cotas_Pagas_RS"})
    por_uf["Cotas_Pagas_RS_M"] = por_uf["Cotas_Pagas_RS"].map(_to_millions)

    # 🔥 Cotas pagas por SEGMENTO e MÊS (se existir a coluna de segmento)
    if COL_SEGMENTO in df.columns:
        seg_mes = pagas.groupby(["mes", COL_SEGMENTO]).agg({
            COL_ID_COTA: "nunique",
            COL_VALOR: "sum"
        }).rename(columns={COL_ID_COTA: "Qtde", COL_VALOR: "RS"}).reset_index()
        seg_mes["RS_M"] = seg_mes["RS"].map(_to_millions)
        pagas_por_segmento_mes = [
            {
                "mes": r["mes"],
                "segmento": r[COL_SEGMENTO],
                "qtde": int(r["Qtde"]),
                "rs": _jround(r["RS"], 2),
                "rs_m": _jround(r["RS_M"], 6)
            }
            for _, r in seg_mes.iterrows()
        ]
    else:
        pagas_por_segmento_mes = []

    # Serialização mensal
    monthly = []
    if not resultado.empty:
        for _, row in resultado.reset_index().iterrows():
            monthly.append({
                "mes": row["mes"],
                "venda_rs": _jround(row["Venda_RS"], 2),
                "venda_qtde": int(row["Venda_Qtde"]),
                "prod_rs": _jround(row["Prod_RS"], 2),
                "prod_qtde": int(row["Prod_Qtde"]),
                "conv_rs_pct": _jround(row["Conv_RS_%"], 6),
                "conv_qtde_pct": _jround(row["Conv_Qtde_%"], 6),
                "ticket_medio": _jround(row["Ticket_Medio"], 2),
                "venda_rs_m": _jround(row["Venda_RS_M"], 6),
                "prod_rs_m": _jround(row["Prod_RS_M"], 6),
            })

    uf_list = []
    if not por_uf.empty:
        for _, row in por_uf.reset_index().iterrows():
            uf_list.append({
                "uf": row[COL_UF],
                "cotas_pagas_qtde": int(row["Cotas_Pagas_Qtde"]),
                "cotas_pagas_rs": _jround(row["Cotas_Pagas_RS"], 2),
                "cotas_pagas_rs_m": _jround(row["Cotas_Pagas_RS_M"], 6),
            })

    out = {
        "status": "ok",
        "period_start_utc": PERIOD_START.isoformat(),
        "ytd": {
            "venda_rs": _jround(venda_total_rs, 2),
            "venda_qtde": venda_total_qtde,
            "prod_rs": _jround(prod_total_rs, 2),
            "prod_qtde": prod_total_qtde,
            "conv_rs_pct": _jround(conv_anual_rs, 6),
            "conv_qtde_pct": _jround(conv_anual_qtde, 6),
            "venda_rs_m": _jround(_to_millions(venda_total_rs), 6) if venda_total_rs else 0.0,
            "prod_rs_m": _jround(_to_millions(prod_total_rs), 6) if prod_total_rs else 0.0,
        },
        "monthly": monthly,
        "cotas_pagas_por_uf": uf_list,
        "cotas_pagas_por_segmento_mes": pagas_por_segmento_mes
    }

    return out

# -------------------------
# Transformação para payload compacto
# -------------------------
def _to_compact_output(resultado_dict: dict) -> dict:
    """
    Compacto por padrão:
    - monthly: mes, venda_rs, prod_rs, conv_rs_pct, ticket_medio (ordenado por mes)
    - cotas_pagas_por_uf: uf, cotas_pagas_qtde, cotas_pagas_rs
    - cotas_pagas_por_segmento_mes: mes, segmento, qtde, rs
    - mantém ytd
    """
    out = {
        "status": resultado_dict.get("status", "ok"),
        "rows_received": resultado_dict.get("rows_received"),
        "period_start_utc": resultado_dict.get("period_start_utc"),
        "ytd": resultado_dict.get("ytd", {})
    }

    # monthly
    monthly = resultado_dict.get("monthly", [])
    out["monthly"] = []
    for row in sorted(monthly, key=lambda r: r.get("mes", "")):
        out["monthly"].append({
            "mes": row.get("mes"),
            "venda_rs": _jround(row.get("venda_rs"), 2),
            "prod_rs": _jround(row.get("prod_rs"), 2),
            "conv_rs_pct": _jround(row.get("conv_rs_pct"), 6),
            "ticket_medio": _jround(row.get("ticket_medio"), 2),
        })

    # UF
    ufs = resultado_dict.get("cotas_pagas_por_uf", [])
    out["cotas_pagas_por_uf"] = [
        {
            "uf": u.get("uf"),
            "cotas_pagas_qtde": int(u.get("cotas_pagas_qtde", 0)),
            "cotas_pagas_rs": _jround(u.get("cotas_pagas_rs"), 2)
        } for u in ufs
    ]

    # Segmento x Mês
    seg = resultado_dict.get("cotas_pagas_por_segmento_mes", [])
    out["cotas_pagas_por_segmento_mes"] = [
        {
            "mes": s.get("mes"),
            "segmento": s.get("segmento"),
            "qtde": int(s.get("qtde", 0)),
            "rs": _jround(s.get("rs"), 2)
        } for s in seg
    ]

    return out

# -------------------------
# Rotas
# -------------------------
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "now_utc": datetime.now(timezone.utc).isoformat()})

@app.route("/auth", methods=["POST"])
def auth():
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username", ""))
    password = str(payload.get("password", ""))
    if username == API_USERNAME and password == API_PASSWORD:
        token = generate_token(username)
        return jsonify({"status": "ok", "access_token": token, "expires_in": TOKEN_TTL_SECONDS})
    return jsonify({"status": "unauthorized", "message": "Invalid credentials"}), 401

@app.route("/receber_workato", methods=["POST"])
@require_auth
def receber_workato():
    """
    Espera:
    - body como lista de objetos JSON [ { ... }, { ... } ]
      OU
    - body como objeto com chave 'data': { "data": [ { ... }, ... ] }

    Retorna JSON com métricas mensais e YTD.
    - Compacto por padrão
    - Completo com ?format=full
    """
    try:
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({"status": "error", "message": "Body não é JSON válido."}), 400

        # Permite payload = {"data": [...] } ou diretamente [...]
        if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], list):
            data = payload["data"]
        elif isinstance(payload, list):
            data = payload
        else:
            return jsonify({"status": "error", "message": "Formato esperado: lista de objetos ou {'data': [...]}."}), 400

        if not data:
            vazio = {"status": "ok", "message": "Lista vazia recebida.", "monthly": [],
                     "cotas_pagas_por_uf": [], "cotas_pagas_por_segmento_mes": []}
            return jsonify(vazio)

        df = pd.DataFrame(data)
        rows_received = len(df)

        # Calcula métricas
        resultado = calcular_metricas(df)

        # Opcional: salvar um CSV no disco para auditoria (defina SAVE_CSV=true)
        if os.getenv("SAVE_CSV", "false").lower() == "true":
            try:
                df_norm = _normalize_columns(df)
                df_norm.to_csv("dados_workato_recebidos.csv", index=False, encoding="utf-8-sig")
            except Exception as e:
                # não falha a requisição por isso
                resultado["csv_warning"] = f"Falha ao salvar CSV: {e}"

        resultado["rows_received"] = rows_received

        # Formato
        fmt = request.args.get("format", "").lower()
        if fmt == "full":
            return jsonify(resultado)
        return jsonify(_to_compact_output(resultado))

    except ValueError as ve:
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro inesperado: {e}"}), 500

# Para rodar localmente
if __name__ == "__main__":
    # Em Railway, o Gunicorn cuida disso; localmente pode usar debug=True
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)