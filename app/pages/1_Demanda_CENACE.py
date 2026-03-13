from __future__ import annotations

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from lib.cenace_client import fetch_demand, fetch_demand_batch, CACHE_DIR

SERIES_LABELS = {
    "demanda_mw":    "Demanda",
    "generacion_mw": "Generación",
    "pronostico_mw": "Pronóstico",
}
COLORS_ACTIVE = {
    "demanda_mw":    "#2563EB",
    "generacion_mw": "#16A34A",
    "pronostico_mw": "#EA580C",
}
SYSTEM_COLORS = {"SIN": "#2563EB", "BCA": "#16A34A", "BCS": "#EA580C"}
COLOR_GREY = "rgba(160,160,160,0.35)"

MX_TZ = ZoneInfo("America/Mexico_City")
HIST_DIR = Path("data_clean")
HIST_PATH = HIST_DIR / "demanda_historica.parquet"

st.title("Demanda CENACE")
st.caption(
    "Datos del día actual. La API de CENACE (`obtieneValoresTotal`) solo expone la jornada en curso. "
    "El **cache** acelera consultas por entorno; el **histórico consolidado** guarda datos persistentes "
    "para que la app local y la app en línea muestren la misma serie."
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def quality_report(df: pd.DataFrame) -> dict:
    rep = {}
    expected_cols = ["hora", "demanda_mw", "generacion_mw", "pronostico_mw", "fecha", "timestamp"]
    rep["missing_cols"] = [c for c in expected_cols if c not in df.columns]

    if "hora" in df.columns:
        horas = pd.to_numeric(df["hora"], errors="coerce")
        rep["min_hora"] = int(horas.min()) if horas.notna().any() else None
        rep["max_hora"] = int(horas.max()) if horas.notna().any() else None
        horas_validas = set(horas.dropna().astype(int).tolist())
        rep["missing_hours"] = sorted(list(set(range(1, 25)) - horas_validas))
        rep["duplicate_hours"] = int(df["hora"].astype(str).duplicated().sum())
    else:
        rep["min_hora"] = rep["max_hora"] = None
        rep["missing_hours"] = list(range(1, 25))
        rep["duplicate_hours"] = 0

    rep["na_counts"] = df.isna().sum().to_dict()
    rep["rows"] = int(len(df))
    return rep


def to_clean_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "hora" in out.columns:
        out["hora"] = pd.to_numeric(out["hora"], errors="coerce")
    for col in ["demanda_mw", "generacion_mw", "pronostico_mw"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "hora" in out.columns:
        out = out.sort_values("hora").reset_index(drop=True)
    return out


def render_system_panel(df: pd.DataFrame, sys_label: str, res=None) -> None:
    """Renderiza KPIs + calidad + gráfica para un sistema."""
    if res is not None:
        col_cache, = st.columns(1)
        badge = "💾 Cache" if res.from_cache else "🌐 En vivo"
        st.caption(f"{badge} | Batches: {res.batches}")
        if res.from_cache:
            st.info("Datos cargados desde cache local.")
        else:
            st.success("Datos descargados en vivo desde CENACE.")

    # KPIs
    serie = df["demanda_mw"] if "demanda_mw" in df.columns else pd.Series(dtype=float)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Demanda máx (MW)",      f"{serie.max():,.0f}"  if serie.notna().any() else "—")
    c2.metric("Demanda mín (MW)",      f"{serie.min():,.0f}"  if serie.notna().any() else "—")
    c3.metric("Demanda promedio (MW)", f"{serie.mean():,.0f}" if serie.notna().any() else "—")
    if "hora" in df.columns and df["hora"].notna().any():
        c4.metric("Última hora", f"{int(df['hora'].dropna().max())}")
    else:
        c4.metric("Última hora", "—")

    # Calidad
    with st.expander("Calidad de datos", expanded=False):
        rep = quality_report(df)
        if rep["missing_cols"]:
            st.warning(f"Faltan columnas: {rep['missing_cols']}")
        if rep["missing_hours"]:
            st.warning(f"Faltan horas: {rep['missing_hours']}")
        else:
            st.success("Horas completas (1–24).")
        if rep["duplicate_hours"] > 0:
            st.warning(f"Horas duplicadas: {rep['duplicate_hours']}")
        na_imp = {k: v for k, v in rep["na_counts"].items() if k in ["demanda_mw", "generacion_mw", "pronostico_mw"]}
        if any(v > 0 for v in na_imp.values()):
            st.warning(f"NaN en columnas clave: {na_imp}")
        else:
            st.success("Sin NaN en columnas clave.")

    # Gráfica
    plot_cols = [c for c in ["demanda_mw", "generacion_mw", "pronostico_mw"] if c in df.columns]
    if "hora" in df.columns and plot_cols:
        opts = ["Todas"] + [SERIES_LABELS[c] for c in plot_cols]
        highlight_label = st.radio(
            "Destacar serie:",
            opts,
            horizontal=True,
            index=0,
            key=f"highlight_{sys_label}",
        )
        highlight_col = (
            None if highlight_label == "Todas"
            else next(c for c in plot_cols if SERIES_LABELS[c] == highlight_label)
        )
        fig = go.Figure()
        for col in plot_cols:
            if highlight_col is None:
                color, lw = COLORS_ACTIVE[col], 2
            elif col == highlight_col:
                color, lw = COLORS_ACTIVE[col], 3
            else:
                color, lw = COLOR_GREY, 1.5
            fig.add_trace(go.Scatter(
                x=df["hora"], y=df[col],
                mode="lines", name=SERIES_LABELS[col],
                line=dict(color=color, width=lw),
                hovertemplate=f"{SERIES_LABELS[col]} — Hora %{{x}}: %{{y:,.0f}} MW<extra></extra>",
            ))
        yaxis_cfg = (
            dict(autorange=False, range=[30000, 45000], tickmode="linear",
                 tick0=30000, dtick=2000, tickformat=",.0f", exponentformat="none")
            if sys_label == "SIN"
            else dict(autorange=True, tickformat=",.0f", exponentformat="none")
        )
        fig.update_layout(
            yaxis=yaxis_cfg, height=300,
            margin=dict(l=0, r=0, t=20, b=0),
        )
        st.plotly_chart(fig, use_container_width=True, key=f"chart_{sys_label}")

    # Tabla
    with st.expander("Tabla de datos", expanded=False):
        st.dataframe(df, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — histórico consolidado persistente
# ─────────────────────────────────────────────────────────────────────────────
def ensure_history_dir() -> None:
    HIST_DIR.mkdir(parents=True, exist_ok=True)


def normalize_history_df(df: pd.DataFrame, sistema: str | None = None) -> pd.DataFrame:
    """Normaliza dataframe para histórico consolidado."""
    out = df.copy()

    if sistema is not None and "sistema" not in out.columns:
        out["sistema"] = sistema

    if "fecha" in out.columns:
        out["fecha"] = pd.to_datetime(out["fecha"], errors="coerce").dt.date

    if "hora" in out.columns:
        out["hora"] = pd.to_numeric(out["hora"], errors="coerce")

    for col in ["demanda_mw", "generacion_mw", "pronostico_mw"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if "timestamp" not in out.columns and {"fecha", "hora"}.issubset(out.columns):
        out["timestamp"] = (
            pd.to_datetime(out["fecha"], errors="coerce")
            + pd.to_timedelta(out["hora"] - 1, unit="h")
        )

    cols_order = [
        "sistema", "fecha", "hora", "timestamp",
        "demanda_mw", "generacion_mw", "pronostico_mw"
    ]
    existing = [c for c in cols_order if c in out.columns]
    remaining = [c for c in out.columns if c not in existing]
    out = out[existing + remaining]

    return out


def save_to_history(df: pd.DataFrame, sistema: str) -> None:
    """Guarda/actualiza datos en el histórico consolidado."""
    ensure_history_dir()

    new_df = normalize_history_df(df, sistema=sistema)

    required_cols = [c for c in ["sistema", "fecha", "hora"] if c in new_df.columns]
    if len(required_cols) < 3:
        return

    new_df = new_df.dropna(subset=required_cols)
    if new_df.empty:
        return

    if HIST_PATH.exists():
        try:
            hist_df = pd.read_parquet(HIST_PATH)
            hist_df = normalize_history_df(hist_df)
        except Exception:
            hist_df = pd.DataFrame()
    else:
        hist_df = pd.DataFrame()

    combined = pd.concat([hist_df, new_df], ignore_index=True)

    combined = combined.dropna(subset=["sistema", "fecha", "hora"])
    combined = (
        combined.sort_values(["sistema", "fecha", "hora"])
        .drop_duplicates(subset=["sistema", "fecha", "hora"], keep="last")
        .reset_index(drop=True)
    )

    if "timestamp" in combined.columns:
        combined = combined.sort_values(["sistema", "timestamp"]).reset_index(drop=True)

    combined.to_parquet(HIST_PATH, index=False)


def load_history_last_7_days() -> pd.DataFrame:
    """Carga el histórico consolidado y devuelve los últimos 7 días con base en hora CDMX."""
    if not HIST_PATH.exists():
        return pd.DataFrame()

    try:
        df = pd.read_parquet(HIST_PATH)
    except Exception:
        return pd.DataFrame()

    df = normalize_history_df(df)

    if "fecha" not in df.columns:
        return pd.DataFrame()

    today_mx = datetime.now(MX_TZ).date()
    cutoff = today_mx - timedelta(days=7)

    df = df.dropna(subset=["fecha"])
    df = df[df["fecha"] >= cutoff].copy()

    if "timestamp" in df.columns:
        df = df.sort_values(["sistema", "timestamp"]).reset_index(drop=True)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Sección 0: Histórico últimos 7 días (desde cache local)
# ─────────────────────────────────────────────────────────────────────────────
with st.expander("Histórico — últimos 7 días", expanded=False):
    hist_df = load_history_last_7_days()

    if hist_df.empty:
        st.info("No hay datos en cache para los últimos 7 días. Descarga datos usando las secciones de abajo.")
    else:
        sistemas_hist = sorted(hist_df["sistema"].unique())
        dias_hist = sorted(hist_df["fecha"].unique()) if "fecha" in hist_df.columns else []
        h1, h2 = st.columns(2)
        h1.caption(f"Sistemas con datos: **{', '.join(sistemas_hist)}**")
        h2.caption(f"Días disponibles: **{len(dias_hist)}** ({dias_hist[0] if dias_hist else '—'} → {dias_hist[-1] if dias_hist else '—'})")

        sistemas_sel = st.multiselect(
            "Sistemas a mostrar",
            options=sistemas_hist,
            default=sistemas_hist,
            key="hist_sistemas_sel",
        )

        fig_hist = go.Figure()
        for s in sistemas_sel:
            df_s = hist_df[hist_df["sistema"] == s].copy()
            if "timestamp" not in df_s.columns or "demanda_mw" not in df_s.columns:
                continue
            df_s = df_s.dropna(subset=["timestamp", "demanda_mw"]).sort_values("timestamp")
            fig_hist.add_trace(go.Scatter(
                x=df_s["timestamp"],
                y=df_s["demanda_mw"],
                mode="lines",
                name=s,
                line=dict(color=SYSTEM_COLORS.get(s, "#888"), width=1.8),
                hovertemplate=f"{s} — %{{x|%d %b %H:00}}: %{{y:,.0f}} MW<extra></extra>",
            ))
        fig_hist.update_layout(
            xaxis=dict(tickformat="%d %b\n%H:%M", tickangle=0),
            yaxis=dict(tickformat=",.0f", exponentformat="none"),
            height=350,
            margin=dict(l=0, r=0, t=20, b=0),
            legend=dict(orientation="h", y=-0.18),
        )
        st.plotly_chart(fig_hist, use_container_width=True, key="chart_hist")

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# Sección 1: Descarga de un solo sistema (igual que antes)
# ─────────────────────────────────────────────────────────────────────────────
st.subheader("Descarga individual")
col1, col2 = st.columns([2, 1])
with col1:
    system = st.selectbox("Sistema", ["SIN", "BCA", "BCS"], index=0)
with col2:
    use_cache = st.checkbox("Usar cache", value=True)

if st.button("Descargar"):
    with st.spinner("Consultando CENACE..."):
        res = fetch_demand(system=system, use_cache=use_cache)

    df = to_clean_df(res.df)
    save_to_history(df, system)

    st.session_state["demand_df"]     = df
    st.session_state["demand_df_raw"] = res.df
    st.session_state["demand_res"]    = res
    st.session_state["demand_system"] = system

if "demand_df" in st.session_state:
    df      = st.session_state["demand_df"]
    df_raw  = st.session_state["demand_df_raw"]
    res     = st.session_state["demand_res"]
    sys_loaded = st.session_state["demand_system"]

    render_system_panel(df, sys_loaded, res)

    st.subheader("Descargas")
    dl1, dl2 = st.columns(2)
    dl1.download_button(
        "Descargar CSV (limpio)", data=df.to_csv(index=False).encode("utf-8"),
        file_name=f"demanda_{sys_loaded}_clean.csv", mime="text/csv",
    )
    dl2.download_button(
        "Descargar CSV (raw)", data=df_raw.to_csv(index=False).encode("utf-8"),
        file_name=f"demanda_{sys_loaded}_raw.csv", mime="text/csv",
    )

# ─────────────────────────────────────────────────────────────────────────────
# Sección 2: Batching — descarga SIN + BCA + BCS en un clic
# ─────────────────────────────────────────────────────────────────────────────
st.divider()
st.subheader("Descarga en lote (batching)")
st.caption(
    "Descarga los 3 sistemas en secuencia. Cada resultado se **cachea** "
    "por separado en disco (`data_cache/`) con clave `{sistema}|{fecha}`."
)

batch_col1, batch_col2 = st.columns([3, 1])
with batch_col1:
    batch_systems = st.multiselect(
        "Sistemas a descargar en lote",
        ["SIN", "BCA", "BCS"],
        default=["SIN", "BCA", "BCS"],
    )
with batch_col2:
    batch_use_cache = st.checkbox("Cache (lote)", value=True, key="batch_cache_chk")

if st.button("Descargar todos los sistemas seleccionados", type="primary"):
    if not batch_systems:
        st.warning("Selecciona al menos un sistema.")
    else:
        prog = st.progress(0.0, text="Iniciando descarga por lotes…")
        batch_results: dict[str, tuple] = {}
        n_sys = len(batch_systems)
        for i, s in enumerate(batch_systems):
            prog.progress(i / n_sys, text=f"Descargando {s}… ({i+1}/{n_sys})")
            res_s = fetch_demand(system=s, use_cache=batch_use_cache)
            df_s = to_clean_df(res_s.df)
            save_to_history(df_s, s)
            batch_results[s] = (df_s, res_s)
        prog.progress(1.0, text="¡Descarga completada!")
        st.session_state["batch_results"] = batch_results

# ── Mostrar resultados del batch ──────────────────────────────────────────────
if "batch_results" in st.session_state:
    batch_results = st.session_state["batch_results"]

    # Resumen de estado de cache
    st.markdown("**Estado de cache por sistema:**")
    status_cols = st.columns(len(batch_results))
    for i, (s, (df_s, res_s)) in enumerate(batch_results.items()):
        icon = "💾" if res_s.from_cache else "🌐"
        status_cols[i].metric(s, f"{icon} {'Cache' if res_s.from_cache else 'En vivo'}")

    # Tabs por sistema + comparativa
    tab_labels = list(batch_results.keys()) + ["📊 Comparativa"]
    batch_tabs = st.tabs(tab_labels)

    for i, (s, (df_s, res_s)) in enumerate(batch_results.items()):
        with batch_tabs[i]:
            render_system_panel(df_s, f"batch_{s}", res_s)
            st.download_button(
                f"CSV {s}",
                data=df_s.to_csv(index=False).encode("utf-8"),
                file_name=f"demanda_{s}_batch.csv",
                mime="text/csv",
                key=f"dl_batch_{s}",
            )

    # Comparativa: todas las series de demanda_mw en una sola gráfica
    with batch_tabs[-1]:
        st.markdown("### Comparativa de demanda por sistema")
        plot_cols_avail = {
            s: df_s
            for s, (df_s, _) in batch_results.items()
            if "hora" in df_s.columns and "demanda_mw" in df_s.columns
        }
        if plot_cols_avail:
            fig_cmp = go.Figure()
            for s, df_s in plot_cols_avail.items():
                fig_cmp.add_trace(go.Scatter(
                    x=df_s["hora"], y=df_s["demanda_mw"],
                    mode="lines",
                    name=f"Demanda {s}",
                    line=dict(color=SYSTEM_COLORS.get(s, "#888"), width=2),
                    hovertemplate=f"{s} — Hora %{{x}}: %{{y:,.0f}} MW<extra></extra>",
                ))
            fig_cmp.update_layout(
                yaxis=dict(tickformat=",.0f", exponentformat="none"),
                height=380,
                margin=dict(l=0, r=0, t=20, b=0),
                legend=dict(orientation="h", y=-0.15),
            )
            st.plotly_chart(fig_cmp, use_container_width=True, key="chart_cmp")

            # Tabla comparativa
            rows = []
            for s, df_s in plot_cols_avail.items():
                serie = df_s["demanda_mw"]
                rows.append({
                    "Sistema": s,
                    "Máx (MW)": f"{serie.max():,.0f}",
                    "Mín (MW)": f"{serie.min():,.0f}",
                    "Promedio (MW)": f"{serie.mean():,.0f}",
                    "Última hora": int(df_s["hora"].max()) if df_s["hora"].notna().any() else "—",
                })
            st.dataframe(pd.DataFrame(rows).set_index("Sistema"), use_container_width=True)
        else:
            st.info("No hay datos de demanda suficientes para la comparativa.")
