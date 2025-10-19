import os
import json
import math
from datetime import datetime, timezone
from functools import wraps

import pandas as pd
import requests
from flask import Flask, request, jsonify
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

app = Flask(__name__)

# =========================
# Configurações/Constantes
# =========================
# Agora inclui janeiro por padrão; pode sobrescrever via ENV: PERIOD_START=YYYY-MM-DD
PERIOD_START = pd.Timestamp(os.getenv("PERIOD_START", "2025-01-01"), tz="UTC")
# Dedup por id_cota opcional (0/1). Default 1 = dedup (IDs distintos).
DEFAULT_DEDUP = int(os.getenv("DEDUP_BY_ID", "1"))

# ENV para autenticação
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-prod")
API_USERNAME = os.getenv("API_USERNAME", "admin")
API_PASSWORD = os.getenv("API_PASSWORD", "admin")
TOKEN_TTL_SECONDS = int(os.getenv("TOKEN_TTL_SECONDS", "86400"))  # 24h

# ENV para chamar Workato
WORKATO_URL = os.getenv("WORKATO_URL", "")  # ex.: https://www.workato.com/webhooks/...
WORKATO_AUTH_HEADER = os.getenv("WORKATO_AUTH_HEADER", "")  # ex.: "Bearer abc123"

# Nomes de colunas esperadas
COL_ANO_NASC = "Ano Nascimento"
COL_UF = "Uf Cliente"
COL_CIDADE = "Cidade"
COL_DATA_VENDA = "Cotas Id Cliente → Data Venda"
COL_VALOR = "Cotas Id Cliente → Valor Credito Venda"
COL_ID_COTA = "Cotas Id Cliente → Id Cota"
COL_TEM_PAGTO = "Cotas Id Cliente → Tem Pagamento"
COL_SEGMENTO = "Cotas Id Cliente → Segmento"

# -------------------------
# Utilidades
# -------------------------
def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza nomes de colunas:
    - troca '->' por '→'
    - remove espaços duplicados
    - aceita snake_case da Workato e mapeia para nomes 'originais'
    """
    def norm(c):
        c = str(c).replace("->", "→")
        c = " ".join(c.split())
        return c.strip()

    df = df.copy()
    df.columns = [norm(c) for c in df.columns]

    # Mapa snake_case -> nomes originais com '→'
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

def _ensure_columns(df: pd.DataFrame, required_cols: list):
    faltando = [c for c in required_cols if c not in df.columns]
    if faltando:
        raise ValueError(f"Colunas obrigatórias ausentes: {faltando}. Recebido: {list(df.columns)}")

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
    """Normaliza valores de pagamento: aceita Sim, S, True, 1, Pago, etc."""
    if isinstance(v, (bool, int, float)):
        try:
            return bool(int(v))
        except Exception:
            return bool(v)
    s = str(v).strip().lower()
    s = s.replace('não', 'nao')
    return s in {"sim", "s", "pago", "paga", "true", "1", "yes", "y"}

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
    return data

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
def calcular_metricas(df: pd.DataFrame, *, dedup: int | None = None) -> dict:
    df = _normalize_columns(df)
    _ensure_columns(df, [COL_DATA_VENDA, COL_VALOR, COL_TEM_PAGTO, COL_UF, COL_ID_COTA])

    df = df.copy()
    df[COL_DATA_VENDA] = pd.to_datetime(df[COL_DATA_VENDA], utc=True, errors="coerce")
    df[COL_VALOR] = pd.to_numeric(df[COL_VALOR], errors="coerce")
    # id_cota como string pra preservar zeros à esquerda
    df[COL_ID_COTA] = df[COL_ID_COTA].astype(str).str.strip()

    # Filtro de período
    df = df[df[COL_DATA_VENDA] >= PERIOD_START].copy()

    # Dedup por Id Cota (opcional, default usa DEFAULT_DEDUP)
    if dedup is None:
        dedup = DEFAULT_DEDUP
    if dedup == 1:
        df = df.sort_values(COL_DATA_VENDA).drop_duplicates(subset=[COL_ID_COTA], keep="last")

    # Colunas de período
    df["mes"] = df[COL_DATA_VENDA].dt.to_period("M").astype(str)
    # mês corrente no fuso de São Paulo (para UF do mês)
    mes_ref_atual = pd.Timestamp.now(tz="America/Sao_Paulo").strftime("%Y-%m")

    # -------- VENDAS (tudo) --------
    vendas = df.groupby("mes").agg({COL_VALOR: "sum"}).rename(columns={COL_VALOR: "Venda_RS"})
    if dedup == 1:
        venda_qtde = df.groupby("mes")[COL_ID_COTA].nunique()
    else:
        venda_qtde = df.groupby("mes")[COL_ID_COTA].size()
    vendas["Venda_Qtde"] = venda_qtde

    # -------- PRODUÇÃO (pagas) --------
    pagas = df[df[COL_TEM_PAGTO].apply(_is_paid)].copy()
    producao = pagas.groupby("mes").agg({COL_VALOR: "sum"}).rename(columns={COL_VALOR: "Prod_RS"})
    if dedup == 1:
        prod_qtde = pagas.groupby("mes")[COL_ID_COTA].nunique()
    else:
        prod_qtde = pagas.groupby("mes")[COL_ID_COTA].size()
    producao["Prod_Qtde"] = prod_qtde

    # Junta e calcula métricas mensais
    resultado = vendas.join(producao, how="left").fillna(0.0)
    resultado["Conv_RS_%"] = resultado.apply(lambda r: _safe_div(r["Prod_RS"], r["Venda_RS"]), axis=1)
    resultado["Conv_Qtde_%"] = resultado.apply(lambda r: _safe_div(r["Prod_Qtde"], r["Venda_Qtde"]), axis=1)
    resultado["Ticket_Medio"] = resultado.apply(lambda r: _safe_div(r["Prod_RS"], r["Prod_Qtde"]), axis=1)
    resultado["Venda_RS_M"] = resultado["Venda_RS"].map(_to_millions)
    resultado["Prod_RS_M"] = resultado["Prod_RS"].map(_to_millions)

    # YTD (agregado do período)
    if dedup == 1:
        venda_total_qtde = int(df[COL_ID_COTA].nunique())
        prod_total_qtde  = int(pagas[COL_ID_COTA].nunique())
    else:
        venda_total_qtde = int(len(df))
        prod_total_qtde  = int(len(pagas))
    venda_total_rs = float(df[COL_VALOR].sum(skipna=True))
    prod_total_rs = float(pagas[COL_VALOR].sum(skipna=True))
    conv_anual_qtde = _safe_div(prod_total_qtde, venda_total_qtde)
    conv_anual_rs = _safe_div(prod_total_rs, venda_total_rs)

    # Cotas pagas por UF (total)
    if dedup == 1:
        por_uf_q = pagas.groupby(COL_UF)[COL_ID_COTA].nunique()
    else:
        por_uf_q = pagas.groupby(COL_UF)[COL_ID_COTA].size()
    por_uf_rs = pagas.groupby(COL_UF)[COL_VALOR].sum()
    por_uf = pd.concat([por_uf_q.rename("Cotas_Pagas_Qtde"), por_uf_rs.rename("Cotas_Pagas_RS")], axis=1)
    por_uf["Cotas_Pagas_RS_M"] = por_uf["Cotas_Pagas_RS"].map(_to_millions)

    # Cotas pagas por UF (mês corrente)
    pagas_mes = pagas[pagas["mes"] == mes_ref_atual].copy()
    if dedup == 1:
        por_uf_mes_q = pagas_mes.groupby(COL_UF)[COL_ID_COTA].nunique()
    else:
        por_uf_mes_q = pagas_mes.groupby(COL_UF)[COL_ID_COTA].size()
    por_uf_mes_rs = pagas_mes.groupby(COL_UF)[COL_VALOR].sum()
    por_uf_mes = pd.concat([por_uf_mes_q.rename("Cotas_Pagas_Qtde"),
                            por_uf_mes_rs.rename("Cotas_Pagas_RS")], axis=1).reset_index()
    por_uf_mes = por_uf_mes.sort_values("Cotas_Pagas_Qtde", ascending=False)

    # Cotas pagas por SEGMENTO e MÊS (se existir a coluna de segmento)
    if COL_SEGMENTO in df.columns:
        if dedup == 1:
            seg_q = pagas.groupby(["mes", COL_SEGMENTO])[COL_ID_COTA].nunique()
        else:
            seg_q = pagas.groupby(["mes", COL_SEGMENTO])[COL_ID_COTA].size()
        seg_rs = pagas.groupby(["mes", COL_SEGMENTO])[COL_VALOR].sum()
        seg_mes = pd.concat([seg_q.rename("Qtde"), seg_rs.rename("RS")], axis=1).reset_index()
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

    # Serialização monthly
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

    # UF total (lista)
    uf_list = []
    if not por_uf.empty:
        for _, row in por_uf.reset_index().iterrows():
            uf_list.append({
                "uf": row[COL_UF],
                "cotas_pagas_qtde": int(row["Cotas_Pagas_Qtde"]),
                "cotas_pagas_rs": _jround(row["Cotas_Pagas_RS"], 2),
                "cotas_pagas_rs_m": _jround(row.get("Cotas_Pagas_RS_M"), 6),
            })

    # UF mês corrente (lista)
    uf_mes_list = []
    if not por_uf_mes.empty:
        for _, r in por_uf_mes.iterrows():
            uf_mes_list.append({
                "uf": r[COL_UF],
                "cotas_pagas_qtde": int(r["Cotas_Pagas_Qtde"]),
                "cotas_pagas_rs": _jround(r["Cotas_Pagas_RS"], 2),
            })

    # Auditoria / Debug
    debug = {
        "period_start_utc": PERIOD_START.isoformat(),
        "dedup_by_id": bool(dedup),
        "rows_after_period": int(len(df)),
        "rows_paid": int(len(pagas)),
        "ids_pagos_distintos": int(pagas[COL_ID_COTA].nunique()),
        "months_covered": sorted(df["mes"].unique().tolist())
    }

    out = {
        "status": "ok",
        "period_start_utc": PERIOD_START.isoformat(),
        # Totais pedidos (qtde)
        "total_cotas_vendidas": venda_total_qtde,
        "total_cotas_pagas": prod_total_qtde,
        # YTD
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
        "cotas_pagas_por_uf_mes_corrente": {
            "mes": mes_ref_atual,
            "dados": uf_mes_list
        },
        "cotas_pagas_por_segmento_mes": pagas_por_segmento_mes,
        "debug": debug
    }
    return out

def _to_compact_output(resultado_dict: dict) -> dict:
    """
    Compacta resposta:
    - monthly: mes, venda_rs, prod_rs, conv_rs_pct, ticket_medio
    - cotas_pagas_por_uf (total): uf, qtde, rs
    - cotas_pagas_por_uf_mes_corrente: mes + lista
    - cotas_pagas_por_segmento_mes: mes, segmento, qtde, rs
    - mantém ytd e totais
    """
    out = {
        "status": resultado_dict.get("status", "ok"),
        "rows_received": resultado_dict.get("rows_received"),
        "period_start_utc": resultado_dict.get("period_start_utc"),
        "total_cotas_vendidas": resultado_dict.get("total_cotas_vendidas"),
        "total_cotas_pagas": resultado_dict.get("total_cotas_pagas"),
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

    # UF (total)
    ufs = resultado_dict.get("cotas_pagas_por_uf", [])
    out["cotas_pagas_por_uf"] = [
        {
            "uf": u.get("uf"),
            "cotas_pagas_qtde": int(u.get("cotas_pagas_qtde", 0)),
            "cotas_pagas_rs": _jround(u.get("cotas_pagas_rs"), 2)
        } for u in ufs
    ]

    # UF (mês corrente)
    uf_mes = resultado_dict.get("cotas_pagas_por_uf_mes_corrente", {})
    out["cotas_pagas_por_uf_mes_corrente"] = {
        "mes": uf_mes.get("mes"),
        "dados": [
            {
                "uf": d.get("uf"),
                "cotas_pagas_qtde": int(d.get("cotas_pagas_qtde", 0)),
                "cotas_pagas_rs": _jround(d.get("cotas_pagas_rs"), 2)
            }
            for d in uf_mes.get("dados", [])
        ]
    }

    # Segmento x Mês (pagas)
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
    Aceita:
      - lista JSON direta: [ {...}, {...} ]
      - objeto com {"data": [...]}
      - body stringificado (duplo-JSON): "[{...},{...}]"
      - chaves snake_case ou nomes originais com '→'
    Query params:
      - ?format=full  -> resposta completa
      - ?dedup=0|1    -> sobrescreve ENV DEDUP_BY_ID
    """
    try:
        payload = request.get_json(silent=True)

        # Se veio como string JSON, tenta decodificar
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                return jsonify({"status": "error", "message": "Body é string mas não é JSON válido."}), 400

        # Extrai lista de linhas
        if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], list):
            data = payload["data"]
        elif isinstance(payload, list):
            data = payload
        else:
            return jsonify({"status": "error", "message": "Formato esperado: lista de objetos ou {'data': [...]}."}), 400

        if not data:
            return jsonify({"status": "ok", "message": "Lista vazia recebida.", "monthly": [], "cotas_pagas_por_uf": [], "cotas_pagas_por_segmento_mes": []})

        df = pd.DataFrame(data)
        rows_received = len(df)

        # Dedup param
        dedup_qs = request.args.get("dedup")
        dedup = int(dedup_qs) if dedup_qs in {"0", "1"} else None

        # Calcula métricas
        resultado = calcular_metricas(df, dedup=dedup)

        # Auditoria opcional
        if os.getenv("SAVE_CSV", "false").lower() == "true":
            try:
                _normalize_columns(df).to_csv("dados_workato_recebidos.csv", index=False, encoding="utf-8-sig")
            except Exception as e:
                resultado["csv_warning"] = f"Falha ao salvar CSV: {e}"

        resultado["rows_received"] = rows_received

        fmt = request.args.get("format", "").lower()
        if fmt == "full":
            return jsonify(resultado)
        return jsonify(_to_compact_output(resultado))

    except ValueError as ve:
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro inesperado: {e}"}), 500

@app.route("/chamar_workato", methods=["POST"])
@require_auth
def chamar_workato():
    """
    Encaminha o JSON recebido para a Workato (POST).
    - Configure WORKATO_URL e, se quiser, WORKATO_AUTH_HEADER (ex.: "Bearer xyz").
    - Body desta rota = body enviado para a Workato.
    - Retorna o status e o corpo de resposta da Workato.
    """
    if not WORKATO_URL:
        return jsonify({"status": "error", "message": "WORKATO_URL não configurada no ambiente."}), 500

    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"status": "error", "message": "Body não é JSON válido."}), 400

    headers = {"Content-Type": "application/json"}
    if WORKATO_AUTH_HEADER:
        headers["Authorization"] = WORKATO_AUTH_HEADER

    try:
        resp = requests.post(WORKATO_URL, json=body, headers=headers, timeout=30)
        content_type = resp.headers.get("Content-Type", "")
        try:
            data = resp.json() if "application/json" in content_type else resp.text
        except Exception:
            data = resp.text
        return jsonify({
            "status": "ok",
            "workato_status_code": resp.status_code,
            "workato_response": data
        }), (200 if resp.ok else 502)
    except requests.RequestException as e:
        return jsonify({"status": "error", "message": f"Falha ao chamar Workato: {e}"}), 502

# Para rodar localmente
if __name__ == "__main__":
    # Em Railway, o Gunicorn cuida disso; localmente pode usar debug=True
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)