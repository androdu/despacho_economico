# app/pages/2_Despacho_PyPSA.py
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pypsa
import streamlit as st
import plotly.graph_objects as go

# ──────────────────────────────────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────────────────────────────────
ROOT          = Path(__file__).resolve().parents[2]
DATA_CLEAN    = ROOT / "data_clean"
CENTRALES_CSV = DATA_CLEAN / "generators" / "Centrales_gen_mx.csv"
PERFIL_CSV    = DATA_CLEAN / "generators" / "Perfil_Generaciom.csv"
DEMAND_RAW_DIR = ROOT / "data_raw" / "demand" / "balance_2026"

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
SISTEMAS = ["SIN", "BCA", "BCS"]

VOLL_DEFAULT = 3_000  # $/MWh — Value of Lost Load (carga no servida)

# Ordered for charts (cheaper first = bottom of stack)
CARRIERS = [
    "hydro", "nuclear", "solar", "onwind", "solar_thermal",
    "geothermal", "biogas", "biomass", "chp",
    "gas_ccgt", "gas_ocgt", "steam_other", "diesel_engine",
]
CARRIER_LABELS = {
    "hydro":         "Hidroeléctrica",
    "nuclear":       "Nuclear",
    "solar":         "Solar FV",
    "onwind":        "Eólica",
    "solar_thermal": "Solar térmica",
    "geothermal":    "Geotérmica",
    "biogas":        "Biogás",
    "biomass":       "Biomasa",
    "chp":           "Cogeneración",
    "gas_ccgt":      "Gas CCGT",
    "gas_ocgt":      "Gas OCGT",
    "steam_other":   "Termoeléctrica (carbón/FO)",
    "diesel_engine": "Diesel",
    "shedding":      "Carga no servida",
}
CARRIER_COLORS = {
    "hydro":         "#2563EB",
    "nuclear":       "#7C3AED",
    "solar":         "#FBBF24",
    "onwind":        "#10B981",
    "solar_thermal": "#F59E0B",
    "geothermal":    "#8B5CF6",
    "biogas":        "#6EE7B7",
    "biomass":       "#34D399",
    "chp":           "#9CA3AF",
    "gas_ccgt":      "#F97316",
    "gas_ocgt":      "#FB923C",
    "steam_other":   "#78716C",
    "diesel_engine": "#EF4444",
    "shedding":      "#DC2626",
}

# Default marginal costs ($/MWh) — based on CFE/CENACE reference
DEFAULT_COSTS: dict[str, int] = {
    "hydro":         0,
    "nuclear":       5,
    "solar":         0,
    "onwind":        0,
    "solar_thermal": 3,
    "geothermal":    10,
    "biogas":        15,
    "biomass":       20,
    "chp":           35,
    "gas_ccgt":      50,
    "gas_ocgt":      70,
    "steam_other":   30,
    "diesel_engine": 100,
}

# ──────────────────────────────────────────────────────────────────────────────
# Preset scenarios  (5 lecciones pedagógicas)
# ──────────────────────────────────────────────────────────────────────────────
SCENARIOS: dict[str, dict] = {
    "🏭 Base 2026": {
        "desc":   "Costos estándar del SEN. La hidro y renovables despachan primero; el gas cubre la demanda residual.",
        "lesson": "Gas domina BCA/BCS. En SIN, la hidro + eólica + solar son la base.",
        "costs":  {**DEFAULT_COSTS},
    },
    "⛽ Gas caro (×2)": {
        "desc":   "Gas (CCGT y OCGT) al doble de precio — simula un shock de precios de gas natural.",
        "lesson": "La hidro desplaza al gas; el precio marginal en BCA sube bruscamente. El precio sombra refleja la escasez de alternativas.",
        "costs":  {**DEFAULT_COSTS, "gas_ccgt": 100, "gas_ocgt": 140, "chp": 70},
    },
    "☀️ Renovables gratis": {
        "desc":   "Solar y eólica con costo variable = 0 (LCOE amortizado o subsidio total).",
        "lesson": "Las renovables saturan su capacidad antes que cualquier fósil. Aparece curtailment en horas de alta irradiación. El precio marginal cae a cero en esas horas.",
        "costs":  {**DEFAULT_COSTS, "solar": 0, "onwind": 0, "hydro": 0,
                   "solar_thermal": 0, "geothermal": 0, "biogas": 0, "biomass": 0},
    },
    "🌵 Crisis BCA (sin gas)": {
        "desc":   "Gas en BCA a precio extremo — escasez severa de combustible en la península norte.",
        "lesson": "BCA depende casi 100% de gas. Sin él, el precio marginal explota (= VoLL) y hay carga no servida visible.",
        "costs":  {**DEFAULT_COSTS, "gas_ccgt": 500, "gas_ocgt": 600, "steam_other": 400},
    },
    "🌱 Precio al carbono ($100/tCO₂)": {
        "desc":   "Impuesto implícito de $100 USD/tCO₂. Factores: CCGT=0.37, OCGT=0.50, Termoeléctrica=0.82, Diesel=0.70 tCO₂/MWh.",
        "lesson": "Con precio al carbono, la termoeléctrica y el diesel se encarecen. El gas CCGT sigue competitivo; hidro y renovables ganan terreno.",
        "costs":  {**DEFAULT_COSTS, "gas_ccgt": 87, "gas_ocgt": 120,
                   "steam_other": 112, "diesel_engine": 170, "chp": 70},
    },
}
SCENARIO_NAMES = list(SCENARIOS.keys())

# ──────────────────────────────────────────────────────────────────────────────
# 2026 expected capacity growth
# Fuente: PRODESEN 2026-2030 / CFE Plan de Expansión (valores representativos)
# ──────────────────────────────────────────────────────────────────────────────
GROWTH_2026: list[tuple[str, str, str, float]] = [
    # (name,               bus,   carrier,      p_nom MW)
    # SIN — proyectos adjudicados en subastas 2025/2026
    ("new_solar_SIN_1",    "SIN", "solar",       1_500.0),
    ("new_solar_SIN_2",    "SIN", "solar",         500.0),
    ("new_onwind_SIN",     "SIN", "onwind",        500.0),
    ("new_gas_ccgt_SIN",   "SIN", "gas_ccgt",      500.0),
    # BCA — Mexicali y norte de Baja California
    ("new_solar_BCA",      "BCA", "solar",          300.0),
    ("new_onwind_BCA",     "BCA", "onwind",         200.0),
    # BCS — La Paz y Los Cabos
    ("new_solar_BCS",      "BCS", "solar",          200.0),
    ("new_gas_ocgt_BCS",   "BCS", "gas_ocgt",       100.0),
]
GROWTH_TOTAL_MW = sum(r[3] for r in GROWTH_2026)

RENEWABLE_CARRIERS = {"solar", "onwind", "solar_thermal", "geothermal", "hydro"}
SYSTEM_COLORS = {"SIN": "#2563EB", "BCA": "#16A34A", "BCS": "#EA580C"}

# ──────────────────────────────────────────────────────────────────────────────
# Cached data loaders
# ──────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_generators() -> pd.DataFrame:
    df = pd.read_csv(CENTRALES_CSV)
    df["bus"] = (
        df["bus"].astype(str).str.strip().str.upper()
        .replace({"BSA": "BCA", "MUGELE": "BCS", "MUG": "BCS"})
    )
    return df[df["bus"].isin(SISTEMAS)].reset_index(drop=True)


@st.cache_data(show_spinner=False)
def load_profiles() -> pd.DataFrame:
    perfil = pd.read_csv(PERFIL_CSV)
    perfil["snapshot"] = pd.to_datetime(perfil["snapshot"])
    return perfil.set_index("snapshot").sort_index()


@st.cache_data(show_spinner=False)
def load_demand_raw() -> pd.DataFrame:
    all_dfs = []
    for f in sorted(DEMAND_RAW_DIR.glob("*.csv")):
        with open(f, encoding="latin-1") as fh:
            header_lines = [fh.readline() for _ in range(8)]
        m = re.search(r"(\d{2}/\d{2}/\d{4})", header_lines[7].strip().strip('"'))
        if not m:
            continue
        op_date = pd.to_datetime(m.group(1), format="%d/%m/%Y")
        df = pd.read_csv(f, skiprows=8, header=0, encoding="latin-1")
        df.columns = [c.strip().strip('"').strip() for c in df.columns]
        col_s, col_h, col_d = "Sistema", "Hora", "Estimacion de Demanda por Balance (MWh)"
        if not {col_s, col_h, col_d}.issubset(df.columns):
            continue
        df = df[[col_s, col_h, col_d]].copy()
        df.columns = ["zona", "hora", "demand_mw"]
        df["zona"]     = df["zona"].astype(str).str.strip().str.strip('"').str.upper()
        df["hora"]     = pd.to_numeric(df["hora"], errors="coerce")
        df["demand_mw"] = pd.to_numeric(
            df["demand_mw"].astype(str).str.strip().str.replace(",", ""), errors="coerce"
        )
        df = df.dropna(subset=["hora", "demand_mw"])
        df["snapshot"] = op_date + pd.to_timedelta(df["hora"].astype(int) - 1, unit="h")
        all_dfs.append(df[["snapshot", "zona", "demand_mw"]])

    if not all_dfs:
        raise ValueError(f"No se encontraron archivos de demanda en {DEMAND_RAW_DIR}")

    dem = pd.concat(all_dfs, ignore_index=True).sort_values("snapshot").reset_index(drop=True)
    dem["zona"] = dem["zona"].replace({"BSA": "BCA"})
    return dem

# ──────────────────────────────────────────────────────────────────────────────
# Page config
# ──────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Despacho PyPSA", layout="wide")
st.title("Despacho económico — SIN / BCA / BCS (PyPSA)")
st.caption(
    "Optimización LP de costo mínimo. Cada sistema es un **bus independiente** (sin interconexión). "
    "Datos de demanda: balance real CENACE 2026. Capacidad instalada: PRODESEN / CFE."
)

# ──────────────────────────────────────────────────────────────────────────────
# Load data (check files first)
# ──────────────────────────────────────────────────────────────────────────────
missing = [p for p in [CENTRALES_CSV, PERFIL_CSV] if not p.exists()]
if not DEMAND_RAW_DIR.exists() or not any(DEMAND_RAW_DIR.glob("*.csv")):
    missing.append(DEMAND_RAW_DIR)
if missing:
    st.error("Faltan archivos de datos:")
    for p in missing:
        st.write("-", str(p))
    st.stop()

try:
    with st.spinner("Cargando datos…"):
        centrales_base = load_generators()
        p_max_pu_raw   = load_profiles()
        dem_raw        = load_demand_raw()
except Exception as e:
    st.exception(e)
    st.stop()

# Pivot demand → (snapshot × sistema)
dem_z_full = (
    dem_raw[dem_raw["zona"].isin(SISTEMAS)]
    .pivot_table(index="snapshot", columns="zona", values="demand_mw", aggfunc="sum")
    .sort_index()
)
for s in SISTEMAS:
    if s not in dem_z_full.columns:
        dem_z_full[s] = 0.0
dem_z_full = dem_z_full[SISTEMAS]

_avail_min = dem_z_full.index.min().date()
_avail_max = dem_z_full.index.max().date()

# Carriers present in data
carriers_present = sorted(
    [c for c in CARRIERS if c in centrales_base["carrier"].unique()]
)

# ──────────────────────────────────────────────────────────────────────────────
# Initialize session state for cost sliders
# ──────────────────────────────────────────────────────────────────────────────
for carrier, default in DEFAULT_COSTS.items():
    if f"cost_{carrier}" not in st.session_state:
        st.session_state[f"cost_{carrier}"] = default

# ──────────────────────────────────────────────────────────────────────────────
# Scenario preset buttons
# ──────────────────────────────────────────────────────────────────────────────
st.subheader("① Escenarios predefinidos")
st.caption("Cada botón carga automáticamente los costos variables correspondientes en los sliders.")

scen_cols = st.columns(len(SCENARIO_NAMES))
for i, sname in enumerate(SCENARIO_NAMES):
    if scen_cols[i].button(sname, use_container_width=True, key=f"btn_scen_{i}"):
        for carrier, cost in SCENARIOS[sname]["costs"].items():
            st.session_state[f"cost_{carrier}"] = cost
        st.session_state["active_scenario"] = sname

active_scenario: str | None = st.session_state.get("active_scenario")
if active_scenario and active_scenario in SCENARIOS:
    sc = SCENARIOS[active_scenario]
    st.info(f"**{active_scenario}** — {sc['desc']}\n\n_Lección esperada:_ {sc['lesson']}")

st.divider()

# ──────────────────────────────────────────────────────────────────────────────
# Parameters
# ──────────────────────────────────────────────────────────────────────────────
st.subheader("② Parámetros de simulación")

col_date, col_ctrl = st.columns([2, 1])
with col_date:
    date_range = st.date_input(
        "Rango de simulación",
        value=(_avail_min, _avail_max),
        min_value=_avail_min,
        max_value=_avail_max,
        format="DD/MM/YYYY",
    )
with col_ctrl:
    growth_2026 = st.toggle(
        f"📈 Capacidad 2026 esperada (+{GROWTH_TOTAL_MW:,.0f} MW)",
        value=False,
        help="Añade nueva capacidad de acuerdo con PRODESEN 2026-2030 / CFE Plan de Expansión.",
    )
    voll_input = st.number_input(
        "VoLL — carga no servida ($/MWh)",
        value=VOLL_DEFAULT, min_value=100, max_value=10_000, step=100,
        help="Value of Lost Load: costo que representa 1 MWh de demanda no atendida. "
             "Cuando el VoLL se despacha, hay blackout parcial.",
    )

if not isinstance(date_range, (list, tuple)) or len(date_range) != 2:
    st.warning("Selecciona fecha de inicio **y** fin.")
    st.stop()

start_date, end_date = date_range
dem_z = dem_z_full.loc[
    (dem_z_full.index.date >= start_date) & (dem_z_full.index.date <= end_date)
]
if dem_z.empty:
    st.error("No hay datos de demanda en el rango seleccionado.")
    st.stop()

n_hours = len(dem_z)
n_days  = (end_date - start_date).days + 1
col_info1, col_info2 = st.columns(2)
col_info1.metric("Días seleccionados", n_days)
col_info2.metric("Horas a optimizar", n_hours)
if n_hours > 336:  # > 2 weeks
    st.warning(
        f"Estás optimizando {n_hours} horas ({n_days} días). Puede tardar varios minutos. "
        "Usa un rango más corto (p.ej. 1-7 días) para pruebas rápidas."
    )

# ── Capacidad instalada (expandible) ─────────────────────────────────────────
with st.expander("📋 Capacidad instalada por tecnología y sistema", expanded=False):
    cap_base = (
        centrales_base.groupby(["bus", "carrier"])["p_nom"]
        .sum()
        .unstack(fill_value=0)
        .reindex(index=SISTEMAS, fill_value=0)
    )
    # Reorder columns to CARRIERS order
    cap_base = cap_base.reindex(
        columns=[c for c in CARRIERS if c in cap_base.columns], fill_value=0
    )
    st.markdown("**Base (MW)** — Fuente: PRODESEN / CFE registros de capacidad instalada")
    st.dataframe(cap_base.style.format("{:,.0f}"), use_container_width=True)

    if growth_2026:
        growth_df  = pd.DataFrame(GROWTH_2026, columns=["name", "bus", "carrier", "p_nom"])
        growth_agg = (
            growth_df.groupby(["bus", "carrier"])["p_nom"].sum()
            .unstack(fill_value=0)
            .reindex(index=SISTEMAS, fill_value=0)
        )
        st.markdown(
            "**Adiciones 2026 esperadas (MW)** — Fuente: PRODESEN 2026-2030 / CFE Plan de Expansión"
        )
        st.dataframe(growth_agg.style.format("{:,.0f}"), use_container_width=True)

# ── Sliders de costos variables ───────────────────────────────────────────────
with st.expander("③ 🎚️ Costos variables por tecnología ($/MWh)", expanded=True):
    st.caption(
        "Ajusta manualmente o usa un escenario predefinido. "
        "Los sliders afectan el orden de mérito y, por tanto, el despacho y el precio sombra."
    )
    sl_cols = st.columns(4)
    costs: dict[str, int] = {}
    for i, carrier in enumerate(carriers_present):
        label = CARRIER_LABELS.get(carrier, carrier)
        costs[carrier] = sl_cols[i % 4].slider(
            label,
            min_value=0,
            max_value=500,
            key=f"cost_{carrier}",
        )

run_btn = st.button("▶ Correr despacho", type="primary")
st.divider()

# ──────────────────────────────────────────────────────────────────────────────
# Build & solve
# ──────────────────────────────────────────────────────────────────────────────
def build_and_solve(
    centrales: pd.DataFrame,
    p_max_pu_raw: pd.DataFrame,
    dem_z: pd.DataFrame,
    costs: dict[str, int],
    use_growth: bool,
    voll: float,
) -> pypsa.Network:
    n = pypsa.Network()
    snapshots = dem_z.index
    n.set_snapshots(snapshots)

    # Align profile year to demand year (perfil 2025 → demanda 2026)
    profile_year = p_max_pu_raw.index.year[0]
    demand_year  = snapshots.year[0]
    if profile_year != demand_year:
        p_max_pu_aligned = p_max_pu_raw.copy()
        p_max_pu_aligned.index = p_max_pu_raw.index.map(
            lambda ts: ts.replace(year=demand_year)
        )
    else:
        p_max_pu_aligned = p_max_pu_raw

    # Three isolated buses (no links)
    for s in SISTEMAS:
        n.add("Bus", s)

    # Generators from CSV — apply slider marginal costs by carrier
    for _, row in centrales.iterrows():
        carrier = str(row["carrier"])
        mc      = costs.get(carrier, float(row["marginal_cost"]))
        n.add(
            "Generator",
            name=str(row["name"]),
            bus=str(row["bus"]),
            carrier=carrier,
            p_nom=float(row["p_nom"]),
            marginal_cost=float(mc),
            efficiency=float(row.get("efficiency", 1.0)),
        )

    # 2026 expected growth generators
    if use_growth:
        for gname, gbus, gcarrier, gpnom in GROWTH_2026:
            mc = costs.get(gcarrier, DEFAULT_COSTS.get(gcarrier, 0))
            n.add(
                "Generator",
                name=gname, bus=gbus, carrier=gcarrier,
                p_nom=float(gpnom), marginal_cost=float(mc),
            )

    # VoLL shedding generators (one per bus — model load shedding)
    for s in SISTEMAS:
        n.add(
            "Generator",
            name=f"VoLL_{s}", bus=s, carrier="shedding",
            p_nom=1e6, marginal_cost=float(voll),
        )

    # Time-varying p_max_pu profiles (only for generators present in Perfil CSV)
    profile_gens = [g for g in n.generators.index if g in p_max_pu_aligned.columns]
    if profile_gens:
        p_max_pu = (
            p_max_pu_aligned[profile_gens]
            .reindex(index=snapshots, fill_value=1.0)
            .clip(0.0, 1.0)
        )
        n.generators_t.p_max_pu = p_max_pu

    # Loads
    for s in SISTEMAS:
        if s in dem_z.columns:
            n.add("Load", f"load_{s}", bus=s, p_set=dem_z[s])

    n.optimize(solver_name="highs")
    return n


if run_btn:
    with st.spinner("Optimizando con HiGHS… puede tardar ~30 s para períodos largos."):
        try:
            n_solved = build_and_solve(
                centrales_base.copy(),
                p_max_pu_raw,
                dem_z,
                costs,
                growth_2026,
                float(voll_input),
            )
        except Exception as e:
            st.exception(e)
            st.stop()

    st.session_state["n_solved"]        = n_solved
    st.session_state["dem_z_solved"]    = dem_z.copy()
    st.session_state["scenario_solved"] = active_scenario
    st.success("Optimización completada.")

# ──────────────────────────────────────────────────────────────────────────────
# Results
# ──────────────────────────────────────────────────────────────────────────────
if "n_solved" not in st.session_state:
    st.info("Ajusta los parámetros y presiona **▶ Correr despacho** para ver resultados.")
    st.stop()

n: pypsa.Network         = st.session_state["n_solved"]
dem_solved: pd.DataFrame = st.session_state["dem_z_solved"]
scen_label: str | None   = st.session_state.get("scenario_solved")

dispatch  = n.generators_t.p.copy()
gen_info  = n.generators[["bus", "carrier", "p_nom", "marginal_cost"]].copy()

# Shadow prices (nodal prices, $/MWh)
shadow_prices: pd.DataFrame = n.buses_t.marginal_price

# Curtailment: only for RENEWABLE generators with time-varying p_max_pu profiles
# (gas/coal/diesel operating below profile limit is NOT curtailment)
curtailment_by_bus: dict[str, pd.DataFrame] = {}
if not n.generators_t.p_max_pu.empty:
    profile_gens_solved = n.generators_t.p_max_pu.columns.tolist()
    # Filter to renewable carriers only
    ren_profile_gens = [
        g for g in profile_gens_solved
        if g in gen_info.index and gen_info.loc[g, "carrier"] in RENEWABLE_CARRIERS
    ]
    if ren_profile_gens:
        p_avail = n.generators_t.p_max_pu[ren_profile_gens].multiply(
            n.generators.loc[ren_profile_gens, "p_nom"]
        )
        p_disp_ren = n.generators_t.p.reindex(columns=ren_profile_gens, fill_value=0.0)
        curt_df = (p_avail - p_disp_ren).clip(lower=0)
        curt_df = curt_df.loc[:, curt_df.sum() > 0.1]
        for s in SISTEMAS:
            bus_curt_gens = [
                g for g in curt_df.columns
                if g in gen_info.index and gen_info.loc[g, "bus"] == s
            ]
            if bus_curt_gens:
                curtailment_by_bus[s] = curt_df[bus_curt_gens]

# Shedding (VoLL dispatches)
voll_gens = [g for g in dispatch.columns if g.startswith("VoLL_")]
shedding_total = dispatch[voll_gens].sum().sum() if voll_gens else 0.0
curtailment_total = sum(
    df.sum().sum() for df in curtailment_by_bus.values()
)

# ── Global KPIs ───────────────────────────────────────────────────────────────
st.subheader("④ Resultados")
if scen_label:
    st.caption(f"Escenario: **{scen_label}**")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Costo total ($)",            f"{n.objective:,.0f}")
k2.metric("Generación total (MWh)",
          f"{dispatch.drop(columns=voll_gens, errors='ignore').sum().sum():,.0f}")
k3.metric("Carga no servida (MWh)",
          f"{shedding_total:,.0f}",
          delta=f"{'⚠️ hay shedding' if shedding_total > 0.1 else ''}",
          delta_color="inverse")
k4.metric("Curtailment renovables (MWh)", f"{curtailment_total:,.0f}")

st.divider()

# ── Helper: dispatch stacked-area chart ───────────────────────────────────────
def dispatch_chart(bus: str, title: str) -> None:
    bus_gens = gen_info[
        (gen_info["bus"] == bus) & (~gen_info.index.str.startswith("VoLL_"))
    ].index.tolist()
    bus_gens = [g for g in bus_gens if g in dispatch.columns]

    disp_carrier: pd.DataFrame
    if bus_gens:
        carrier_map  = gen_info.loc[bus_gens, "carrier"]
        disp_carrier = dispatch[bus_gens].T.groupby(carrier_map).sum().T
    else:
        disp_carrier = pd.DataFrame(index=n.snapshots)

    # Append shedding if any
    voll_col = f"VoLL_{bus}"
    if voll_col in dispatch.columns and dispatch[voll_col].sum() > 0.1:
        disp_carrier["shedding"] = dispatch[voll_col]

    carrier_order = [c for c in CARRIERS + ["shedding"] if c in disp_carrier.columns]
    disp_carrier = disp_carrier.reindex(columns=carrier_order, fill_value=0.0)

    fig = go.Figure()
    for carrier in carrier_order:
        if disp_carrier[carrier].abs().sum() < 0.1:
            continue
        color = CARRIER_COLORS.get(carrier, "#888")
        label = CARRIER_LABELS.get(carrier, carrier)
        fig.add_trace(go.Scatter(
            x=disp_carrier.index,
            y=disp_carrier[carrier],
            mode="lines",
            stackgroup="one",
            name=label,
            line=dict(width=0.5, color=color),
            fillcolor=color,
            hovertemplate=f"{label}<br>%{{x|%d-%b %H:%M}}<br>%{{y:,.0f}} MW<extra></extra>",
        ))

    # Demand overlay
    if bus in dem_solved.columns:
        fig.add_trace(go.Scatter(
            x=dem_solved.index,
            y=dem_solved[bus],
            mode="lines",
            name="Demanda real",
            line=dict(color="black", width=2, dash="dot"),
        ))

    fig.update_layout(
        title=title, height=400,
        yaxis_title="MW",
        legend=dict(orientation="h", y=-0.25, font=dict(size=11)),
        margin=dict(l=0, r=0, t=36, b=80),
    )
    st.plotly_chart(fig, use_container_width=True)


# ── Helper: shadow-price chart ────────────────────────────────────────────────
def shadow_price_chart(bus: str) -> None:
    if bus not in shadow_prices.columns:
        st.caption("Precio sombra no disponible para este bus.")
        return
    sp  = shadow_prices[bus]
    avg = sp.mean()
    mx  = sp.max()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=sp.index, y=sp.values,
        mode="lines",
        line=dict(color=CARRIER_COLORS["gas_ccgt"], width=1.5),
        hovertemplate="%{x|%d-%b %H:%M}<br>%{y:,.2f} $/MWh<extra></extra>",
        fill="tozeroy",
        fillcolor="rgba(249,115,22,0.12)",
    ))
    fig.add_hline(y=avg, line_dash="dot", line_color="gray",
                  annotation_text=f"Promedio: {avg:,.1f} $/MWh",
                  annotation_position="top right")
    fig.update_layout(
        title=f"Precio marginal nodal — {bus}",
        yaxis_title="$/MWh", height=260,
        margin=dict(l=0, r=0, t=36, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"Máximo: **{mx:,.1f} $/MWh** — Promedio: **{avg:,.1f} $/MWh**")


# ── Helper: curtailment chart ─────────────────────────────────────────────────
def curtailment_chart(bus: str) -> None:
    curt = curtailment_by_bus.get(bus)
    if curt is None or curt.empty:
        st.caption("Sin curtailment en este sistema.")
        return

    total_mwh = curt.sum().sum()
    st.caption(f"Curtailment total: **{total_mwh:,.0f} MWh**")

    carrier_map = gen_info.loc[curt.columns, "carrier"]
    curt_c = curt.T.groupby(carrier_map).sum().T

    fig = go.Figure()
    for carrier in curt_c.columns:
        if curt_c[carrier].sum() < 0.1:
            continue
        fig.add_trace(go.Scatter(
            x=curt_c.index, y=curt_c[carrier],
            mode="lines", stackgroup="one",
            name=CARRIER_LABELS.get(carrier, carrier),
            line=dict(width=0.5, color=CARRIER_COLORS.get(carrier, "#888")),
            fillcolor=CARRIER_COLORS.get(carrier, "#888"),
        ))
    fig.update_layout(
        yaxis_title="MW (curtailed)", height=220,
        margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(orientation="h", y=-0.3),
    )
    st.plotly_chart(fig, use_container_width=True)


# ── Tabs per system + global ──────────────────────────────────────────────────
tabs = st.tabs([f"🗺 {s}" for s in SISTEMAS] + ["📊 Global"])

for idx, s in enumerate(SISTEMAS):
    with tabs[idx]:
        st.markdown(f"### Sistema {s}")

        # Shadow price KPIs
        if s in shadow_prices.columns:
            sp_avg = shadow_prices[s].mean()
            sp_max = shadow_prices[s].max()
            sc1, sc2 = st.columns(2)
            sc1.metric(f"Precio marginal promedio", f"{sp_avg:,.1f} $/MWh")
            sc2.metric(f"Precio marginal máximo",   f"{sp_max:,.1f} $/MWh")

        # Shedding alert
        voll_col = f"VoLL_{s}"
        if voll_col in dispatch.columns:
            shed = dispatch[voll_col].sum()
            if shed > 0.1:
                st.error(f"⚠️ Carga no servida en {s}: **{shed:,.0f} MWh**  — el sistema no puede atender la demanda con la capacidad disponible.")

        dispatch_chart(s, f"Despacho por tecnología — {s}")
        shadow_price_chart(s)

        st.markdown("**Curtailment de renovables**")
        curtailment_chart(s)

# ── Global tab ────────────────────────────────────────────────────────────────
with tabs[-1]:
    st.markdown("### Vista global")

    # Generation-mix pie charts (one per system)
    pie_cols = st.columns(3)
    for idx, s in enumerate(SISTEMAS):
        bus_gens_s = gen_info[
            (gen_info["bus"] == s) & (~gen_info.index.str.startswith("VoLL_"))
        ].index.tolist()
        bus_gens_s = [g for g in bus_gens_s if g in dispatch.columns]

        if bus_gens_s:
            gen_mwh      = dispatch[bus_gens_s].sum()
            carrier_s    = gen_info.loc[bus_gens_s, "carrier"]
            gen_by_car   = gen_mwh.groupby(carrier_s).sum()
            gen_by_car   = gen_by_car[gen_by_car > 1]

            colors = [CARRIER_COLORS.get(c, "#888") for c in gen_by_car.index]
            fig_pie = go.Figure(go.Pie(
                labels=[CARRIER_LABELS.get(c, c) for c in gen_by_car.index],
                values=gen_by_car.values,
                marker=dict(colors=colors),
                hole=0.35,
                textinfo="percent",
                hovertemplate="%{label}: %{value:,.0f} MWh<extra></extra>",
            ))
            fig_pie.update_layout(
                title=f"Mix {s}", height=320,
                margin=dict(l=0, r=0, t=36, b=0),
                legend=dict(font=dict(size=10)),
            )
            pie_cols[idx].plotly_chart(fig_pie, use_container_width=True)

    # Shadow-price comparison
    if not shadow_prices.empty:
        sp_cols_avail = [s for s in SISTEMAS if s in shadow_prices.columns]
        if sp_cols_avail:
            fig_sp = go.Figure()
            for s in sp_cols_avail:
                fig_sp.add_trace(go.Scatter(
                    x=shadow_prices.index,
                    y=shadow_prices[s],
                    mode="lines",
                    name=s,
                    line=dict(color=SYSTEM_COLORS.get(s), width=1.5),
                ))
            fig_sp.update_layout(
                title="Precio marginal nodal por sistema",
                yaxis_title="$/MWh", height=320,
                margin=dict(l=0, r=0, t=36, b=0),
            )
            st.plotly_chart(fig_sp, use_container_width=True)

    # Cost breakdown table
    st.subheader("Desglose de generación y costo por central")
    gen_mwh_all = dispatch.drop(columns=voll_gens, errors="ignore").sum()
    used_idx    = gen_mwh_all[gen_mwh_all > 1e-6].sort_values(ascending=False).index
    used_df     = gen_info.loc[used_idx].copy()
    used_df["gen_MWh"]  = gen_mwh_all[used_idx].values
    used_df["costo_$"]  = (used_df["gen_MWh"] * used_df["marginal_cost"]).round(0)
    used_df["share_%"]  = (100 * used_df["gen_MWh"] / used_df["gen_MWh"].sum()).round(2)

    st.dataframe(
        used_df[["bus", "carrier", "p_nom", "marginal_cost", "gen_MWh", "costo_$", "share_%"]]
        .rename(columns={
            "p_nom": "Cap. MW", "marginal_cost": "CV $/MWh",
            "gen_MWh": "Gen. MWh", "costo_$": "Costo $", "share_%": "Parte %",
        })
        .style.format({
            "Cap. MW": "{:,.1f}", "CV $/MWh": "{:.0f}",
            "Gen. MWh": "{:,.0f}", "Costo $": "{:,.0f}", "Parte %": "{:.2f}",
        }),
        use_container_width=True,
        height=420,
    )

    # Downloads
    st.subheader("Descargas")
    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)

    dl1, dl2, dl3 = st.columns(3)
    dl1.download_button(
        "📥 Despacho por central (CSV)",
        data=dispatch.to_csv().encode("utf-8"),
        file_name="despacho_mw.csv", mime="text/csv",
    )
    dl2.download_button(
        "📥 Precio marginal por sistema (CSV)",
        data=shadow_prices.to_csv().encode("utf-8"),
        file_name="precio_marginal.csv", mime="text/csv",
    )
    dl3.download_button(
        "📥 Generación + costos (CSV)",
        data=used_df.to_csv().encode("utf-8"),
        file_name="generacion_centrales.csv", mime="text/csv",
    )

