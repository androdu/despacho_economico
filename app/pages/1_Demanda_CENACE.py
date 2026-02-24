import streamlit as st
from lib.cenace_client import fetch_demand

st.title("Demanda CENACE")
st.caption("Datos del día actual. La API de CENACE (obtieneValoresTotal) solo expone la jornada en curso.")

col1, col2 = st.columns([2, 1])
with col1:
    system = st.selectbox("Sistema", ["SIN", "BCA", "BCS"], index=0)
with col2:
    use_cache = st.checkbox("Usar cache", value=True)

if st.button("Descargar"):
    with st.spinner("Consultando CENACE..."):
        res = fetch_demand(system=system, use_cache=use_cache)
    st.caption(f"Cache: {res.from_cache} | Batches: {res.batches}")
    if res.from_cache:
        st.info("Mostrando datos desde caché.")
    else:
        st.success("Datos descargados en vivo desde CENACE.")
    st.dataframe(res.df, width="stretch")

    # Gráfica rápida
    if {"timestamp", "demanda_mw", "generacion_mw", "pronostico_mw"}.issubset(res.df.columns):
        st.line_chart(
            res.df.set_index("timestamp")[["demanda_mw", "generacion_mw", "pronostico_mw"]],
            height=320
        )

    # Descargar CSV
    csv = res.df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Descargar CSV",
        data=csv,
        file_name=f"demanda_{system}.csv",
        mime="text/csv"
    )