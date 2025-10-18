import os
import math
from datetime import datetime, timezone
from functools import wraps

import pandas as pd
from flask import Flask, request, jsonify
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

# ====== NOVOS IMPORTS (WhatsApp + Excel) ======
import time
import mimetypes
import requests
import win32com.client as win32  # Excel (somente Windows)

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
def calcular_metricas(df: pd.DataFrame, dedup: int = None) -> dict:
    df = _normalize_columns(df)

    # ---- Mapear possíveis nomes do ID da cota para 'id_cota'
    renomes_id = {
        "id_cota": "id_cota",
        "ID Cota": "id_cota",
        "Id Cota": "id_cota",
        "Cota → ID": "id_cota",
        "Cotas → ID Cota": "id_cota",
        "Cotas - ID Cliente → ID Cota": "id_cota",
        "ID da Cota": "id_cota",
        "id": "id_cota",
        "cota_id": "id_cota",
    }
    for k, v in renomes_id.items():
        if k in df.columns and k != v:
            df = df.rename(columns={k: v})

    # Agora garantimos colunas obrigatórias
    _ensure_columns(df, [COL_DATA_VENDA, COL_VALOR, COL_TEM_PAGTO, COL_UF, COL_ID_COTA])

    # Tipos
    df = df.copy()
    df[COL_DATA_VENDA] = pd.to_datetime(df[COL_DATA_VENDA], utc=True, errors="coerce")
    df[COL_VALOR] = pd.to_numeric(df[COL_VALOR], errors="coerce")
    # Normaliza id_cota como string (preserva zeros à esquerda)
    df[COL_ID_COTA] = df[COL_ID_COTA].astype(str).str.strip()

    # Filtro de período
    df = df[df[COL_DATA_VENDA] >= PERIOD_START].copy()

    # Dedup por Id Cota (opcional, default usa DEFAULT_DEDUP)
    if dedup is None:
        dedup = DEFAULT_DEDUP
    if dedup == 1:
        # mantém a linha mais recente por cota
        df = df.sort_values(COL_DATA_VENDA).drop_duplicates(subset=[COL_ID_COTA], keep="last")

    # Colunas de período
    df["mes"] = df[COL_DATA_VENDA].dt.to_period("M").astype(str)
    mes_ref_atual = pd.Timestamp.now(tz="America/Sao_Paulo").strftime("%Y-%m")

    # -------- VENDAS (tudo) --------
    vendas = df.groupby("mes").agg({
        COL_VALOR: "sum",
        COL_ID_COTA: ("nunique" if dedup == 1 else "count")
    }).rename(columns={COL_VALOR: "Venda_RS", COL_ID_COTA: "Venda_Qtde"})

    # -------- PRODUÇÃO (pagas) --------
    pagas = df[df[COL_TEM_PAGTO].apply(_is_paid)].copy()
    producao = pagas.groupby("mes").agg({
        COL_VALOR: "sum",
        COL_ID_COTA: ("nunique" if dedup == 1 else "count")
    }).rename(columns={COL_VALOR: "Prod_RS", COL_ID_COTA: "Prod_Qtde"})

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
    por_uf = pagas.groupby(COL_UF).agg({
        COL_ID_COTA: ("nunique" if dedup == 1 else "count"),
        COL_VALOR: "sum"
    }).rename(columns={COL_ID_COTA: "Cotas_Pagas_Qtde", COL_VALOR: "Cotas_Pagas_RS"})
    por_uf["Cotas_Pagas_RS_M"] = por_uf["Cotas_Pagas_RS"].map(_to_millions)

    # Cotas pagas por UF (mês corrente)
    por_uf_mes = (
        pagas[pagas["mes"] == mes_ref_atual]
        .groupby(COL_UF)
        .agg({
            COL_ID_COTA: ("nunique" if dedup == 1 else "count"),
            COL_VALOR: "sum"
        })
        .rename(columns={COL_ID_COTA: "Cotas_Pagas_Qtde", COL_VALOR: "Cotas_Pagas_RS"})
        .reset_index()
        .sort_values("Cotas_Pagas_Qtde", ascending=False)
    )

    # 🔥 Cotas pagas por SEGMENTO e MÊS (se existir a coluna de segmento)
    if COL_SEGMENTO in df.columns:
        seg_mes = pagas.groupby(["mes", COL_SEGMENTO]).agg({
            COL_ID_COTA: ("nunique" if dedup == 1 else "count"),
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

    uf_mes_list = [
        {
            "uf": r[COL_UF],
            "cotas_pagas_qtde": int(r["Cotas_Pagas_Qtde"]),
            "cotas_pagas_rs": _jround(r["Cotas_Pagas_RS"], 2),
        }
        for _, r in por_uf_mes.iterrows()
    ] if not por_uf_mes.empty else []

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
        # --- Totais pedidos (qtde) ---
        "total_cotas_vendidas": venda_total_qtde,
        "total_cotas_pagas": prod_total_qtde,
        # --- YTD em R$ e conversões ---
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
    - inclui 'total_cotas_*' e 'cotas_pagas_por_uf_mes_corrente'
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
# Rotas principais
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
    - Dedup por id_cota controlável com ?dedup=0|1 (default via ENV DEDUP_BY_ID)
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

        # Dedup param
        dedup_qs = request.args.get("dedup")
        dedup = int(dedup_qs) if dedup_qs in {"0", "1"} else None

        # Calcula métricas
        resultado = calcular_metricas(df, dedup=dedup)

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


# ======================================================
# ========== NOVO ENDPOINT: /whatsapp/send-graph =======
# ======================================================

# Credenciais WhatsApp Cloud API (variáveis de ambiente)
WABA_PHONE_NUMBER_ID = os.getenv("WABA_PHONE_NUMBER_ID")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WA_API_BASE = f"https://graph.facebook.com/v22.0/{WABA_PHONE_NUMBER_ID}" if WABA_PHONE_NUMBER_ID else None
WA_HEADERS_JSON = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"} if WHATSAPP_TOKEN else {}
WA_HEADERS_FORM = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"} if WHATSAPP_TOKEN else {}

def _wa_raise(r: requests.Response):
    if r.status_code >= 400:
        try:
            detail = r.json()
        except Exception:
            detail = r.text
        raise RuntimeError(f"WhatsApp API error {r.status_code}: {detail}")

def wa_upload_media(file_path: str, mime: str = None) -> str:
    if not WA_API_BASE or not WHATSAPP_TOKEN:
        raise RuntimeError("WABA_PHONE_NUMBER_ID/WHATSAPP_TOKEN não configurados.")
    if mime is None:
        mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f, mime)}
        data = {"messaging_product": "whatsapp"}
        r = requests.post(f"{WA_API_BASE}/media", headers=WA_HEADERS_FORM, files=files, data=data, timeout=60)
    _wa_raise(r)
    return r.json()["id"]

def wa_send_image_by_id(to: str, media_id: str, caption: str = None):
    if not WA_API_BASE or not WHATSAPP_TOKEN:
        raise RuntimeError("WABA_PHONE_NUMBER_ID/WHATSAPP_TOKEN não configurados.")
    payload = {
        "messaging_product": "whatsapp",
        "to": "".join(filter(str.isdigit, to)),  # 55DDDNUMERO (sem +)
        "type": "image",
        "image": {"id": media_id, **({"caption": caption} if caption else {})},
    }
    r = requests.post(f"{WA_API_BASE}/messages", headers=WA_HEADERS_JSON, json=payload, timeout=20)
    _wa_raise(r)
    return r.json()

def wa_send_template(to: str, name: str, lang_code: str = "pt_BR", body_params: list = None):
    if not WA_API_BASE or not WHATSAPP_TOKEN:
        raise RuntimeError("WABA_PHONE_NUMBER_ID/WHATSAPP_TOKEN não configurados.")
    template = {"name": name, "language": {"code": lang_code}}
    if body_params:
        template["components"] = [{"type": "body", "parameters": body_params}]
    payload = {
        "messaging_product": "whatsapp",
        "to": "".join(filter(str.isdigit, to)),
        "type": "template",
        "template": template,
    }
    r = requests.post(f"{WA_API_BASE}/messages", headers=WA_HEADERS_JSON, json=payload, timeout=20)
    _wa_raise(r)
    return r.json()

def wa_send_template_with_image_header(to: str, name: str, media_id: str, body_text: str, lang_code: str = "pt_BR"):
    """Envia template com cabeçalho de imagem (fora das 24h)."""
    if not WA_API_BASE or not WHATSAPP_TOKEN:
        raise RuntimeError("WABA_PHONE_NUMBER_ID/WHATSAPP_TOKEN não configurados.")
    payload = {
        "messaging_product": "whatsapp",
        "to": "".join(filter(str.isdigit, to)),
        "type": "template",
        "template": {
            "name": name,
            "language": {"code": lang_code},
            "components": [
                {"type": "header", "parameters": [{ "type": "image", "image": {"id": media_id} }]},
                {"type": "body", "parameters": [{ "type": "text", "text": body_text }]}
            ]
        }
    }
    r = requests.post(f"{WA_API_BASE}/messages", headers=WA_HEADERS_JSON, json=payload, timeout=20)
    _wa_raise(r)
    return r.json()

def exportar_grafico_para_png(caminho_excel: str, caminho_png: str, sheet_index: int = 1, chart_index: int = 1):
    excel = win32.Dispatch("Excel.Application")
    excel.Visible = False
    try:
        wb = excel.Workbooks.Open(caminho_excel)
        ws = wb.Sheets(sheet_index)
        if ws.ChartObjects().Count < chart_index:
            raise RuntimeError(f"Nenhum gráfico na planilha {sheet_index} (ou índice {chart_index} inexistente).")
        chart = ws.ChartObjects(chart_index).Chart
        chart.Export(caminho_png)  # extensão .png define o formato
    finally:
        wb.Close(False)
        excel.Quit()

@app.route("/whatsapp/send-graph", methods=["POST"])
@require_auth
def whatsapp_send_graph():
    """
    Body JSON:
    {
      "numeros": ["5541999000000", "554188888888"],   # obrigatório (lista)
      "caption": "Dados Teste A/B: 01/mm - dd/mm",    # opcional (usada como legenda e/ou texto)
      "template_name": "resumo_semanal",              # opcional (default usado fora das 24h)
      "force_template": true,                         # se true, usa template sempre (fora das 24h garantido)
      "template_header_media": true,                  # se true, usa imagem no header do template
      "caminho_excel": "C:/.../arquivo.xlsx",         # opcional
      "caminho_png": "C:/.../grafico.png",            # opcional
      "sheet_index": 1,                               # opcional (1-based)
      "chart_index": 1                                # opcional (1-based)
    }
    """
    if not WA_API_BASE or not WHATSAPP_TOKEN:
        return jsonify({"status": "error", "message": "Defina WABA_PHONE_NUMBER_ID e WHATSAPP_TOKEN."}), 500

    body = request.get_json(silent=True) or {}
    numeros = body.get("numeros") or []
    if not numeros:
        return jsonify({"status": "error", "message": "Informe 'numeros' como lista de destinos."}), 400

    caminho_excel = body.get("caminho_excel") or r"C:/Users/anne.strapacon/OneDrive - ADEMICON ADMINISTRADORA DE CONSORCIOS S A/MKT - DADOS/teste_dados_posthog.xlsx"
    caminho_png   = body.get("caminho_png")   or r"C:/Users/anne.strapacon/Desktop/PASTA - IMAGENS GRÁFICO/grafico_extraido.png"
    sheet_index   = int(body.get("sheet_index", 1))
    chart_index   = int(body.get("chart_index", 1))

    # texto/caption padrão
    caption = body.get("caption")
    if not caption:
        hoje = datetime.now().date()
        mes = hoje.strftime("%m")
        dia = hoje.strftime("%d")
        caption = f"Dados Teste A/B: 01/{mes} - {dia}/{mes}"

    template_name = body.get("template_name") or "resumo_semanal"  # seu template oficial
    force_template = bool(body.get("force_template", False))
    use_header_media = bool(body.get("template_header_media", False))

    # 1) Exporta PNG
    try:
        exportar_grafico_para_png(caminho_excel, caminho_png, sheet_index, chart_index)
    except Exception as e:
        return jsonify({"status": "error", "step": "export_png", "message": str(e)}), 500

    # 2) Upload
    try:
        media_id = wa_upload_media(caminho_png, mime="image/png")
    except Exception as e:
        return jsonify({"status": "error", "step": "upload_media", "message": str(e)}), 502

    # 3) Envia para cada número (com suporte a template e fora de 24h)
    resultados = {}
    for n in numeros:
        try:
            # Caminho 1: forçar template antes (fora das 24h garantido)
            if force_template and template_name:
                if use_header_media:
                    # Envia template com header de imagem (resumo_semanal)
                    wa_send_template_with_image_header(
                        n,
                        template_name,
                        media_id,
                        "Olá, segue o resumo de dados do Teste A/B 📊",
                        "pt_BR"
                    )
                    resultados[n] = {"status": "enviado_via_template_com_imagem"}
                else:
                    # Envia template texto + depois a imagem normal
                    wa_send_template(
                        n,
                        template_name,
                        "pt_BR",
                        [{"type": "text", "text": "Olá, segue o resumo de dados do Teste A/B 📊"}]
                    )
                    time.sleep(2)
                    wa_send_image_by_id(n, media_id, caption=caption)
                    resultados[n] = {"status": "enviado_via_template"}
                continue

            # Caminho 2: tentativa direta (dentro da janela de 24h)
            resp = wa_send_image_by_id(n, media_id, caption=caption)
            resultados[n] = {"status": "enviado", "resp": resp}

        except Exception as e:
            # Fallback: detecta regra de 24h e aplica template automaticamente
            err = str(e).lower()
            precisa_template = any(x in err for x in ["24-hour", "24 hour", "outside the 24", "requires a template"])
            if precisa_template and template_name:
                try:
                    if use_header_media:
                        wa_send_template_with_image_header(
                            n,
                            template_name,
                            media_id,
                            "Olá, segue o resumo de dados do Teste A/B 📊",
                            "pt_BR"
                        )
                        resultados[n] = {"status": "enviado_via_template_com_imagem"}
                    else:
                        wa_send_template(
                            n,
                            template_name,
                            "pt_BR",
                            [{"type": "text", "text": "Olá, segue o resumo de dados do Teste A/B 📊"}]
                        )
                        time.sleep(2)
                        resp2 = wa_send_image_by_id(n, media_id, caption=caption)
                        resultados[n] = {"status": "enviado_apos_template", "resp": resp2}
                except Exception as e2:
                    resultados[n] = {"status": "falha_template", "erro": str(e2)}
            else:
                resultados[n] = {"status": "falha", "erro": str(e)}

    return jsonify({"status": "ok", "resultados": resultados}), 200

# Para rodar localmente
if __name__ == "__main__":
    # Em Railway, o Gunicorn cuida disso; localmente pode usar debug=True
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)