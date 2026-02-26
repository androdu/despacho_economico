import streamlit as st
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NETWORK_NC  = ROOT / "data_clean" / "pypsa_network.nc"
DEMAND_PAR  = ROOT / "data_clean" / "demand" / "demand_balance_2026.parquet"

TECH_COLORS = {
    "gas":   "#EF4444",
    "solar": "#FBBF24",
    "wind":  "#34D399",
}
TECH_LABELS = {"gas": "Gas", "solar": "Solar", "wind": "Eólico"}

st.title("Despacho PyPSA")
st.caption("Despacho económico óptimo (LOPF) con red PyPSA — supuestos Semana 2 (dummy).")


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def load_and_optimize(nc_path: str, demand_path: str):
    """Carga la red PyPSA y corre LOPF. Cachea el objeto Network."""
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import pypsa
    from build_pypsa_network import build_network

    demand = pd.read_parquet(demand_path)
    if demand.index.tz is not None:
        demand.index = demand.index.tz_localize(None)

    n = build_network(demand)

    # Eliminar snapshots con NaN en demanda
    if n.loads_t.p_set.isna().any().any():
        good = ~n.loads_t.p_set.isna().any(axis=1)
        n = n[good]

    status, cond = n.optimize(solver_name="highs")
    return n, status, cond


def tech_of(gen_name: str) -> str:
    return gen_name.split("_")[0]


def zone_of(gen_name: str) -> str:
    return "_".join(gen_name.split("_")[1:])


# ──────────────────────────────────────────────
# Sidebar / controles
# ──────────────────────────────────────────────

zones_available = ["BCA", "BCS", "SIN"]
sel_zone = st.selectbox("Zona a visualizar", ["Todas"] + zones_available, index=0)

# ──────────────────────────────────────────────
# Botón de ejecución
# ──────────────────────────────────────────────

run = st.button("▶ Run PyPSA", type="primary")

if run:
    with st.spinner("Construyendo red y resolviendo LOPF con HiGHS…"):
        try:
            n, status, cond = load_and_optimize(str(NETWORK_NC), str(DEMAND_PAR))
            st.session_state["pypsa_n"]      = n
            st.session_state["pypsa_status"] = status
            st.session_state["pypsa_cond"]   = cond
        except Exception as exc:
            st.error(f"Error al resolver: {exc}")
            st.stop()

# ──────────────────────────────────────────────
# Resultados
# ──────────────────────────────────────────────

if "pypsa_n" not in st.session_state:
    st.info("Presiona **▶ Run PyPSA** para construir y resolver la red.")
    st.stop()

n      = st.session_state["pypsa_n"]
status = st.session_state["pypsa_status"]
cond   = st.session_state["pypsa_cond"]

# ── Solver status ──
if status == "ok":
    st.success(f"Solver: {status} — {cond}")
else:
    st.error(f"Solver: {status} — {cond}")

# ──────────────────────────────────────────────
# KPIs
# ──────────────────────────────────────────────
st.subheader("Resumen (KPIs)")

gen_p = n.generators_t.p  # (snapshots × generators)

# Generación por tecnología (MWh)
gen_by_tech = gen_p.sum().groupby(gen_p.columns.map(tech_of)).sum()

# Curtailment
p_max_pu = n.generators_t.p_max_pu.reindex(columns=n.generators.index, fill_value=1.0)
avail     = n.generators.p_nom * p_max_pu
curtail   = (avail - gen_p).clip(lower=0).sum()
curtail_by_tech = curtail.groupby(curtail.index.map(tech_of)).sum()

objective = float(n.objective)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Costo total (MXN)", f"{objective:,.0f}")
c2.metric("Generación gas (MWh)",   f"{gen_by_tech.get('gas',   0):,.0f}")
c3.metric("Generación solar (MWh)", f"{gen_by_tech.get('solar', 0):,.0f}")
c4.metric("Generación eólica (MWh)",f"{gen_by_tech.get('wind',  0):,.0f}")

# ──────────────────────────────────────────────
# Gráfica 1: Despacho en el tiempo
# ──────────────────────────────────────────────
st.subheader("Despacho en el tiempo (MW)")

import plotly.graph_objects as go

# Filtrar por zona si se seleccionó una específica
if sel_zone == "Todas":
    gens_plot = gen_p.columns.tolist()
else:
    gens_plot = [g for g in gen_p.columns if zone_of(g) == sel_zone]

# Agrupar por tecnología
techs = ["wind", "solar", "gas"]
fig1 = go.Figure()
for tech in techs:
    cols_tech = [g for g in gens_plot if tech_of(g) == tech]
    if not cols_tech:
        continue
    series = gen_p[cols_tech].sum(axis=1)
    fig1.add_trace(go.Scatter(
        x=n.snapshots,
        y=series,
        name=TECH_LABELS[tech],
        mode="lines",
        stackgroup="one",
        line=dict(color=TECH_COLORS[tech], width=0),
        fillcolor=TECH_COLORS[tech],
        hovertemplate=f"{TECH_LABELS[tech]}: %{{y:,.0f}} MW<extra></extra>",
    ))

# Línea de demanda
demand_cols = [c for c in n.loads_t.p_set.columns if sel_zone == "Todas" or c == sel_zone]
demand_total = n.loads_t.p_set[demand_cols].sum(axis=1)
fig1.add_trace(go.Scatter(
    x=n.snapshots,
    y=demand_total,
    name="Demanda",
    mode="lines",
    line=dict(color="#1E3A5F", width=2, dash="dot"),
    hovertemplate="Demanda: %{y:,.0f} MW<extra></extra>",
))

fig1.update_layout(
    yaxis=dict(title="MW", tickformat=",.0f", exponentformat="none"),
    xaxis=dict(title="Fecha"),
    height=380,
    margin=dict(l=0, r=0, t=10, b=0),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
st.plotly_chart(fig1, use_container_width=True)

# ──────────────────────────────────────────────
# Gráfica 2: Generación total por tipo (MWh)
# ──────────────────────────────────────────────
st.subheader("Generación total por tecnología (MWh)")

if sel_zone == "Todas":
    gen_tot = gen_p.sum().groupby(gen_p.columns.map(tech_of)).sum()
else:
    cols_z  = [g for g in gen_p.columns if zone_of(g) == sel_zone]
    gen_tot = gen_p[cols_z].sum().groupby(pd.Index(cols_z).map(tech_of)).sum()

fig2 = go.Figure(go.Bar(
    x=[TECH_LABELS.get(t, t) for t in gen_tot.index],
    y=gen_tot.values,
    marker_color=[TECH_COLORS.get(t, "#999") for t in gen_tot.index],
    text=[f"{v:,.0f}" for v in gen_tot.values],
    textposition="outside",
    hovertemplate="%{x}: %{y:,.0f} MWh<extra></extra>",
))
fig2.update_layout(
    yaxis=dict(title="MWh", tickformat=",.0f", exponentformat="none"),
    height=300,
    margin=dict(l=0, r=0, t=10, b=0),
)
st.plotly_chart(fig2, use_container_width=True)

# ──────────────────────────────────────────────
# Curtailment
# ──────────────────────────────────────────────
with st.expander("Curtailment por tecnología (MWh)"):
    if sel_zone == "Todas":
        curt_tot = curtail_by_tech
    else:
        cols_z   = [g for g in curtail.index if zone_of(g) == sel_zone]
        curt_tot = curtail[cols_z].groupby(pd.Index(cols_z).map(tech_of)).sum()

    df_curt = pd.DataFrame({
        "Tecnología": [TECH_LABELS.get(t, t) for t in curt_tot.index],
        "Curtailment (MWh)": curt_tot.values,
    })
    st.dataframe(df_curt, hide_index=True, use_container_width=True)
    st.caption(
        "Curtailment = energía disponible no despachada. "
        "Solar/eólico alto indica que la demanda local se cubre con fuentes más baratas "
        "y no hay red para exportar el excedente."
    )

# ──────────────────────────────────────────────
# Demanda histórica por zona
# ──────────────────────────────────────────────
with st.expander("Demanda histórica por zona (MW)"):
    demand_df = n.loads_t.p_set.copy()
    fig3 = go.Figure()
    for col in demand_df.columns:
        if sel_zone != "Todas" and col != sel_zone:
            continue
        fig3.add_trace(go.Scatter(
            x=n.snapshots, y=demand_df[col],
            name=col, mode="lines",
            hovertemplate=f"{col}: %{{y:,.0f}} MW<extra></extra>",
        ))
    fig3.update_layout(
        yaxis=dict(title="MW", tickformat=",.0f", exponentformat="none"),
        height=300,
        margin=dict(l=0, r=0, t=10, b=0),
    )
    st.plotly_chart(fig3, use_container_width=True)

# ──────────────────────────────────────────────
# Tabla de generadores
# ──────────────────────────────────────────────
with st.expander("Parámetros de generadores"):
    gens_df = n.generators[["bus", "p_nom", "marginal_cost"]].copy()
    gens_df["tecnología"] = gens_df.index.map(tech_of)
    gens_df = gens_df.rename(columns={"bus": "zona", "p_nom": "p_nom (MW)", "marginal_cost": "costo marginal (MXN/MWh)"})
    st.dataframe(gens_df, use_container_width=True)
    st.caption("Supuestos Semana 2 (dummy). Reemplazar con datos reales del PRODESEN en Semana 3+.")
