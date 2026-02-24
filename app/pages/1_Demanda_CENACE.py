import streamlit as st
import pandas as pd
from lib.cenace_client import fetch_demand

st.title("Demanda CENACE")
st.caption("Datos del día actual. La API de CENACE (obtieneValoresTotal) solo expone la jornada en curso.")

col1, col2 = st.columns([2, 1])
with col1:
    system = st.selectbox("Sistema", ["SIN", "BCA", "BCS"], index=0)
with col2:
    use_cache = st.checkbox("Usar cache", value=True)

def quality_report(df: pd.DataFrame) -> dict:
    rep = {}

    # Columnas esperadas (según tu cliente)
    expected_cols = ["hora", "demanda_mw", "generacion_mw", "pronostico_mw", "fecha", "timestamp"]
    rep["missing_cols"] = [c for c in expected_cols if c not in df.columns]

    # Horas esperadas
    if "hora" in df.columns:
        horas = pd.to_numeric(df["hora"], errors="coerce")
        rep["min_hora"] = int(horas.min()) if horas.notna().any() else None
        rep["max_hora"] = int(horas.max()) if horas.notna().any() else None

        horas_validas = set(horas.dropna().astype(int).tolist())
        esperadas = set(range(1, 25))
        rep["missing_hours"] = sorted(list(esperadas - horas_validas))
        rep["duplicate_hours"] = (
            df["hora"].astype(str).duplicated().sum() if "hora" in df.columns else 0
        )
    else:
        rep["min_hora"] = None
        rep["max_hora"] = None
        rep["missing_hours"] = list(range(1, 25))
        rep["duplicate_hours"] = 0

    # NAs
    rep["na_counts"] = df.isna().sum().to_dict()
    rep["rows"] = int(len(df))

    return rep

def to_clean_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    # Asegurar tipos numéricos
    if "hora" in out.columns:
        out["hora"] = pd.to_numeric(out["hora"], errors="coerce")

    for col in ["demanda_mw", "generacion_mw", "pronostico_mw"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    # Orden por hora si existe
    if "hora" in out.columns:
        out = out.sort_values("hora").reset_index(drop=True)

    return out

if st.button("Descargar"):
    with st.spinner("Consultando CENACE..."):
        res = fetch_demand(system=system, use_cache=use_cache)

    df_raw = res.df
    df = to_clean_df(df_raw)

    # Estado cache
    st.caption(f"Cache: {res.from_cache} | Batches: {res.batches}")
    if res.from_cache:
        st.info("Datos cargados desde cache local.")
    else:
        st.success("Datos descargados en vivo desde CENACE.")

    # ====== KPIs ======
    st.subheader("Resumen (KPIs)")

    # Elegimos la serie principal para KPIs
    serie = df["demanda_mw"] if "demanda_mw" in df.columns else pd.Series(dtype=float)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Demanda máx (MW)", f"{serie.max():,.0f}" if serie.notna().any() else "—")
    c2.metric("Demanda mín (MW)", f"{serie.min():,.0f}" if serie.notna().any() else "—")
    c3.metric("Demanda promedio (MW)", f"{serie.mean():,.0f}" if serie.notna().any() else "—")

    # “Última hora disponible”
    if "hora" in df.columns and df["hora"].notna().any():
        last_hour = int(df["hora"].dropna().max())
        c4.metric("Última hora", f"{last_hour}")
    else:
        c4.metric("Última hora", "—")

    # ====== Calidad de datos ======
    st.subheader("Calidad de datos")
    rep = quality_report(df)

    # Alertas claras (sin rollo)
    if rep["missing_cols"]:
        st.warning(f"Faltan columnas esperadas: {rep['missing_cols']}")

    if rep["missing_hours"]:
        st.warning(f"Faltan horas (1–24): {rep['missing_hours']}")
    else:
        st.success("Horas completas (1–24).")

    if rep["duplicate_hours"] and rep["duplicate_hours"] > 0:
        st.warning(f"Horas duplicadas: {rep['duplicate_hours']}")

    # NAs por columna importante
    na_imp = {k: v for k, v in rep["na_counts"].items() if k in ["demanda_mw", "generacion_mw", "pronostico_mw"]}
    if any(v > 0 for v in na_imp.values()):
        st.warning(f"Valores faltantes (NaN) en columnas clave: {na_imp}")
    else:
        st.success("Sin NaN en columnas clave.")

    # ====== Tabla ======
    st.subheader("Tabla")
    st.dataframe(df, width="stretch")

    # ====== Gráfica ======
    st.subheader("Gráfica")
    plot_cols = [c for c in ["demanda_mw", "generacion_mw", "pronostico_mw"] if c in df.columns]
    if "hora" in df.columns and plot_cols:
        st.line_chart(
            df.set_index("hora")[plot_cols],
            height=320
        )
    else:
        st.info("No hay columnas suficientes para graficar.")

    # ====== Descargas ======
    st.subheader("Descargas")

    csv_clean = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Descargar CSV (limpio)",
        data=csv_clean,
        file_name=f"demanda_{system}_clean.csv",
        mime="text/csv"
    )

    csv_raw = df_raw.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Descargar CSV (raw)",
        data=csv_raw,
        file_name=f"demanda_{system}_raw.csv",
        mime="text/csv"
    )