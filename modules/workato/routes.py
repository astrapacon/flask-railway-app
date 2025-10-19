import os, json, requests, pandas as pd
from flask import Blueprint, request, jsonify, current_app
from modules.utils.common import require_auth
from .analytics import calcular_metricas

bp = Blueprint("workato", __name__)

@bp.post("/receber")
@require_auth
def receber_workato():
    """
    Aceita:
      - lista JSON: [ {...}, {...} ]
      - objeto com {"data":[...]}
      - body stringificado: "[{...}]"
    Query:
      - ?format=full
      - ?dedup=0|1 (sobrepõe DEDUP_BY_ID do ENV)
    """
    try:
        payload = request.get_json(silent=True)

        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                return jsonify({"status":"error","message":"Body é string mas não é JSON válido."}), 400

        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            data = payload["data"]
        elif isinstance(payload, list):
            data = payload
        else:
            return jsonify({"status":"error","message":"Formato esperado: lista de objetos ou {'data':[...]}."}), 400

        if not data:
            return jsonify({"status":"ok","message":"Lista vazia","monthly":[],"cotas_pagas_por_uf":[],"cotas_pagas_por_segmento_mes":[]})

        df = pd.DataFrame(data)
        rows_received = len(df)

        dedup_qs = request.args.get("dedup")
        dedup = int(dedup_qs) if dedup_qs in {"0","1"} else None

        result = calcular_metricas(df, dedup=dedup)
        result["rows_received"] = rows_received

        # opcional: salvar CSV de auditoria
        if os.getenv("SAVE_CSV","false").lower() == "true":
            try:
                df.to_csv("dados_workato_recebidos_raw.csv", index=False, encoding="utf-8-sig")
            except Exception as e:
                result["csv_warning"] = f"Falha ao salvar CSV: {e}"

        return jsonify(result if request.args.get("format","").lower()=="full" else _compact(result))
    except ValueError as ve:
        return jsonify({"status":"error","message":str(ve)}), 400
    except Exception as e:
        return jsonify({"status":"error","message":f"Erro inesperado: {e}"}), 500

def _compact(r: dict) -> dict:
    out = {
        "status": r.get("status","ok"),
        "rows_received": r.get("rows_received"),
        "period_start_utc": r.get("period_start_utc"),
        "ytd": r.get("ytd", {}),
        "monthly": [
            {
                "mes": m.get("mes"),
                "venda_rs": m.get("venda_rs"),
                "prod_rs": m.get("prod_rs"),
                "conv_rs_pct": m.get("conv_rs_pct"),
                "ticket_medio": m.get("ticket_medio"),
            } for m in sorted(r.get("monthly",[]), key=lambda x: x.get("mes",""))
        ],
        "cotas_pagas_por_uf": r.get("cotas_pagas_por_uf", []),
        "cotas_pagas_por_uf_mes_corrente": r.get("cotas_pagas_por_uf_mes_corrente", {}),
        "cotas_pagas_por_segmento_mes": r.get("cotas_pagas_por_segmento_mes", []),
    }
    return out

@bp.post("/chamar")
@require_auth
def chamar_workato():
    """
    Encaminha o JSON recebido para a Workato (POST).
    - Configure WORKATO_URL e, se precisar, WORKATO_AUTH_HEADER
    """
    url = current_app.config["WORKATO_URL"]
    if not url:
        return jsonify({"status":"error","message":"WORKATO_URL não configurada."}), 500

    body = request.get_json(silent=True)
    if body is None:
        return jsonify({"status":"error","message":"Body não é JSON válido."}), 400

    headers = {"Content-Type":"application/json"}
    if current_app.config["WORKATO_AUTH_HEADER"]:
        headers["Authorization"] = current_app.config["WORKATO_AUTH_HEADER"]

    try:
        resp = requests.post(url, json=body, headers=headers, timeout=30)
        ctype = resp.headers.get("Content-Type","")
        try:
            data = resp.json() if "application/json" in ctype else resp.text
        except Exception:
            data = resp.text
        return jsonify({"status":"ok","workato_status_code":resp.status_code,"workato_response":data}), (200 if resp.ok else 502)
    except requests.RequestException as e:
        return jsonify({"status":"error","message":f"Falha ao chamar Workato: {e}"}), 502