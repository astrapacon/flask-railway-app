import pandas as pd
from flask import current_app
from modules.utils.common import (
    normalize_columns, _is_paid, _safe_div, _to_millions, _jround,
    COL_UF, COL_DATA_VENDA, COL_VALOR, COL_ID_COTA, COL_TEM_PAGTO, COL_SEGMENTO
)

def calcular_metricas(df: pd.DataFrame, *, dedup: int | None = None) -> dict:
    df = normalize_columns(df)
    required = [COL_DATA_VENDA, COL_VALOR, COL_TEM_PAGTO, COL_UF, COL_ID_COTA]
    faltando = [c for c in required if c not in df.columns]
    if faltando:
        raise ValueError(f"Colunas obrigatórias ausentes: {faltando}. Recebido: {list(df.columns)}")

    df = df.copy()
    df[COL_DATA_VENDA] = pd.to_datetime(df[COL_DATA_VENDA], utc=True, errors="coerce")
    df[COL_VALOR] = pd.to_numeric(df[COL_VALOR], errors="coerce")
    df[COL_ID_COTA] = df[COL_ID_COTA].astype(str).str.strip()

    period_start = current_app.config["PERIOD_START"]
    df = df[df[COL_DATA_VENDA] >= period_start].copy()

    if dedup is None:
        dedup = int(current_app.config["DEDUP_BY_ID"])
    if dedup == 1:
        df = df.sort_values(COL_DATA_VENDA).drop_duplicates(subset=[COL_ID_COTA], keep="last")

    df["mes"] = df[COL_DATA_VENDA].dt.to_period("M").astype(str)
    mes_ref_atual = pd.Timestamp.now(tz="America/Sao_Paulo").strftime("%Y-%m")

    # vendas
    vendas = df.groupby("mes").agg({COL_VALOR: "sum"}).rename(columns={COL_VALOR: "Venda_RS"})
    venda_qtde = df.groupby("mes")[COL_ID_COTA].nunique() if dedup == 1 else df.groupby("mes")[COL_ID_COTA].size()
    vendas["Venda_Qtde"] = venda_qtde

    # pagas
    pagas = df[df[COL_TEM_PAGTO].apply(_is_paid)].copy()
    producao = pagas.groupby("mes").agg({COL_VALOR: "sum"}).rename(columns={COL_VALOR: "Prod_RS"})
    prod_qtde = pagas.groupby("mes")[COL_ID_COTA].nunique() if dedup == 1 else pagas.groupby("mes")[COL_ID_COTA].size()
    producao["Prod_Qtde"] = prod_qtde

    # métricas
    resultado = vendas.join(producao, how="left").fillna(0.0)
    resultado["Conv_RS_%"] = resultado.apply(lambda r: _safe_div(r["Prod_RS"], r["Venda_RS"]), axis=1)
    resultado["Conv_Qtde_%"] = resultado.apply(lambda r: _safe_div(r["Prod_Qtde"], r["Venda_Qtde"]), axis=1)
    resultado["Ticket_Medio"] = resultado.apply(lambda r: _safe_div(r["Prod_RS"], r["Prod_Qtde"]), axis=1)
    resultado["Venda_RS_M"] = resultado["Venda_RS"].map(_to_millions)
    resultado["Prod_RS_M"] = resultado["Prod_RS"].map(_to_millions)

    # YTD
    venda_total_qtde = int(df[COL_ID_COTA].nunique()) if dedup == 1 else int(len(df))
    prod_total_qtde  = int(pagas[COL_ID_COTA].nunique()) if dedup == 1 else int(len(pagas))
    venda_total_rs = float(df[COL_VALOR].sum(skipna=True))
    prod_total_rs  = float(pagas[COL_VALOR].sum(skipna=True))
    conv_anual_qtde = _safe_div(prod_total_qtde, venda_total_qtde)
    conv_anual_rs   = _safe_div(prod_total_rs, venda_total_rs)

    # UF total
    por_uf_q = pagas.groupby(COL_UF)[COL_ID_COTA].nunique() if dedup == 1 else pagas.groupby(COL_UF)[COL_ID_COTA].size()
    por_uf_rs = pagas.groupby(COL_UF)[COL_VALOR].sum()
    por_uf = pd.concat([por_uf_q.rename("Cotas_Pagas_Qtde"), por_uf_rs.rename("Cotas_Pagas_RS")], axis=1)
    por_uf["Cotas_Pagas_RS_M"] = por_uf["Cotas_Pagas_RS"].map(_to_millions)

    # UF mês corrente
    pagas_mes = pagas[pagas["mes"] == mes_ref_atual].copy()
    por_uf_mes_q = pagas_mes.groupby(COL_UF)[COL_ID_COTA].nunique() if dedup == 1 else pagas_mes.groupby(COL_UF)[COL_ID_COTA].size()
    por_uf_mes_rs = pagas_mes.groupby(COL_UF)[COL_VALOR].sum()
    por_uf_mes = pd.concat([por_uf_mes_q.rename("Cotas_Pagas_Qtde"), por_uf_mes_rs.rename("Cotas_Pagas_RS")], axis=1).reset_index()

    # Segmento x Mês
    if COL_SEGMENTO in df.columns:
        seg_q = pagas.groupby(["mes", COL_SEGMENTO])[COL_ID_COTA].nunique() if dedup == 1 else pagas.groupby(["mes", COL_SEGMENTO])[COL_ID_COTA].size()
        seg_rs = pagas.groupby(["mes", COL_SEGMENTO])[COL_VALOR].sum()
        seg_mes = pd.concat([seg_q.rename("Qtde"), seg_rs.rename("RS")], axis=1).reset_index()
        seg_mes["RS_M"] = seg_mes["RS"].map(_to_millions)
        pagas_por_segmento_mes = [
            {"mes": r["mes"], "segmento": r[COL_SEGMENTO], "qtde": int(r["Qtde"]), "rs": _jround(r["RS"], 2), "rs_m": _jround(r["RS_M"], 6)}
            for _, r in seg_mes.iterrows()
        ]
    else:
        pagas_por_segmento_mes = []

    # monthly
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
                "cotas_pagas_rs_m": _jround(row.get("Cotas_Pagas_RS_M"), 6),
            })

    uf_mes_list = []
    if not por_uf_mes.empty:
        for _, r in por_uf_mes.iterrows():
            uf_mes_list.append({
                "uf": r[COL_UF],
                "cotas_pagas_qtde": int(r["Cotas_Pagas_Qtde"]),
                "cotas_pagas_rs": _jround(r["Cotas_Pagas_RS"], 2),
            })

    return {
        "status": "ok",
        "period_start_utc": str(period_start),
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
        "cotas_pagas_por_uf_mes_corrente": {"mes": mes_ref_atual, "dados": uf_mes_list},
        "cotas_pagas_por_segmento_mes": pagas_por_segmento_mes,
    }