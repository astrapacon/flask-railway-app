import os
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import pandas as pd

app = Flask(__name__)

# =========================
# Configurações/Constantes
# =========================
PERIOD_START = pd.Timestamp("2025-02-01", tz="UTC")  # desde fevereiro/2025

# Nomes de colunas esperadas (usamos constantes para evitar typos)
COL_ANO_NASC = "Ano Nascimento"
COL_UF = "Uf Cliente"
COL_CIDADE = "Cidade"
COL_DATA_VENDA = "Cotas Id Cliente → Data Venda"
COL_VALOR = "Cotas Id Cliente → Valor Credito Venda"
COL_ID_COTA = "Cotas Id Cliente → Id Cota"
COL_TEM_PAGTO = "Cotas Id Cliente → Tem Pagamento"

# -------------------------
# Utilidades
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

# -------------------------
# Cálculo principal
# -------------------------
def calcular_metricas(df: pd.DataFrame) -> dict:
    df = _normalize_columns(df)

    # Garante colunas mínimas
    _ensure_columns(df, [COL_DATA_VENDA, COL_VALOR, COL_ID_COTA, COL_TEM_PAGTO, COL_UF])

    # Tipos
    df = df.copy()
    df[COL_DATA_VENDA] = pd.to_datetime(df[COL_DATA_VENDA], utc=True, errors="coerce")
    df[COL_VALOR] = pd.to_numeric(df[COL_VALOR], errors="coerce")

    # Filtra período YTD (desde 2025-02-01)
    df = df[df[COL_DATA_VENDA] >= PERIOD_START]

    # Deduplica por Id Cota (cada cota conta uma vez)
    # Se houver várias linhas da mesma cota, ficamos com a mais recente pela Data Venda
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

    # Junta e calcula
    resultado = vendas.join(producao, how="left").fillna(0.0)
    # Conversões mensais
    resultado["Conv_RS_%"] = resultado.apply(lambda r: _safe_div(r["Prod_RS"], r["Venda_RS"]), axis=1)
    resultado["Conv_Qtde_%"] = resultado.apply(lambda r: _safe_div(r["Prod_Qtde"], r["Venda_Qtde"]), axis=1)
    # Ticket médio mensal (apenas produção oficial)
    resultado["Ticket_Medio"] = resultado.apply(lambda r: _safe_div(r["Prod_RS"], r["Prod_Qtde"]), axis=1)
    # Em milhões
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

    # Serialização para JSON
    monthly = []
    if not resultado.empty:
        resultado_reset = resultado.reset_index()  # tem a coluna 'mes'
        for _, row in resultado_reset.iterrows():
            monthly.append({
                "mes": row["mes"],
                "venda_rs": round(float(row["Venda_RS"]), 2),
                "venda_qtde": int(row["Venda_Qtde"]),
                "prod_rs": round(float(row["Prod_RS"]), 2),
                "prod_qtde": int(row["Prod_Qtde"]),
                "conv_rs_pct": round(row["Conv_RS_%"], 6) if row["Conv_RS_%"] is not None else None,
                "conv_qtde_pct": round(row["Conv_Qtde_%"], 6) if row["Conv_Qtde_%"] is not None else None,
                "ticket_medio": round(row["Ticket_Medio"], 2) if row["Ticket_Medio"] is not None else None,
                "venda_rs_m": round(row["Venda_RS_M"], 6) if row["Venda_RS_M"] is not None else None,
                "prod_rs_m": round(row["Prod_RS_M"], 6) if row["Prod_RS_M"] is not None else None,
            })

    uf_list = []
    if not por_uf.empty:
        por_uf_reset = por_uf.reset_index()
        for _, row in por_uf_reset.iterrows():
            uf_list.append({
                "uf": row[COL_UF],
                "cotas_pagas_qtde": int(row["Cotas_Pagas_Qtde"]),
                "cotas_pagas_rs": round(float(row["Cotas_Pagas_RS"]), 2),
                "cotas_pagas_rs_m": round(float(row["Cotas_Pagas_RS_M"]), 6) if pd.notna(row["Cotas_Pagas_RS_M"]) else None
            })

    out = {
        "status": "ok",
        "period_start_utc": PERIOD_START.isoformat(),
        "ytd": {
            "venda_rs": round(venda_total_rs, 2),
            "venda_qtde": venda_total_qtde,
            "prod_rs": round(prod_total_rs, 2),
            "prod_qtde": prod_total_qtde,
            "conv_rs_pct": round(conv_anual_rs, 6) if conv_anual_rs is not None else None,
            "conv_qtde_pct": round(conv_anual_qtde, 6) if conv_anual_qtde is not None else None,
            "venda_rs_m": round(_to_millions(venda_total_rs), 6) if venda_total_rs else 0.0,
            "prod_rs_m": round(_to_millions(prod_total_rs), 6) if prod_total_rs else 0.0,
        },
        "monthly": monthly,
        "cotas_pagas_por_uf": uf_list
    }

    return out

# -------------------------
# Rotas
# -------------------------
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "now_utc": datetime.now(timezone.utc).isoformat()})

@app.route("/receber_workato", methods=["POST"])
def receber_workato():
    """
    Espera:
    - body como lista de objetos JSON [ { ... }, { ... } ]
      OU
    - body como objeto com chave 'data': { "data": [ { ... }, ... ] }

    Retorna JSON com métricas mensais e YTD.
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
            return jsonify({"status": "ok", "message": "Lista vazia recebida.", "ytd": {}, "monthly": [], "cotas_pagas_por_uf": []})

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
        return jsonify(resultado)

    except ValueError as ve:
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Erro inesperado: {e}"}), 500

# Para rodar localmente
if __name__ == "__main__":
    # Em Railway, o Gunicorn cuida disso; localmente pode usar debug=True
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
