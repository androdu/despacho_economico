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
    "battery",
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
    "battery":       "Batería (descarga neta)",
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
    "battery":       "#06B6D4",
    "shedding":      "#DC2626",
}

# Default marginal costs ($/MWh) — based on CFE/CENACE reference
DEFAULT_COSTS: dict[str, float] = {
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


def compute_effective_costs(params: dict) -> dict[str, float]:
    """Apply marginal_cost_multiplier and marginal_cost_adder on top of DEFAULT_COSTS."""
    costs = {c: float(v) for c, v in DEFAULT_COSTS.items()}
    for carrier, mult in params.get("marginal_cost_multiplier", {}).items():
        if carrier in costs:
            costs[carrier] = round(costs[carrier] * float(mult), 4)
    for carrier, add in params.get("marginal_cost_adder", {}).items():
        if carrier in costs:
            costs[carrier] = round(costs[carrier] + float(add), 4)
    return costs

# ──────────────────────────────────────────────────────────────────────────────
# Preset scenarios  (5 lecciones pedagógicas)
# Schema: params.marginal_cost_multiplier / adder applied ON TOP of DEFAULT_COSTS
# Implemented: capacity_multiplier, forced_outage, battery_enable + battery_*
# Reserved (no-op): capacity_delta_mw, vre_profile_multiplier
# ──────────────────────────────────────────────────────────────────────────────
BASE_SCENARIO_KEY = "🏭 Base 2026"

SCENARIOS: dict[str, dict] = {
    BASE_SCENARIO_KEY: {
        "desc":   "Costos de referencia del SEN. Hidro y renovables despachan primero; el gas cubre la demanda residual.",
        "lesson": "Gas domina BCA/BCS. En SIN, la hidro + eólica + solar son la base. Sin shocks externos.",
        "params": {
            "marginal_cost_multiplier": {},
            "marginal_cost_adder":      {},
            "voll_value":               3000,
            "demand_multiplier":        {"SIN": 1.0, "BCA": 1.0, "BCS": 1.0},
            # reserved
            "capacity_multiplier":      {},
            "capacity_delta_mw":        {},
            "forced_outage":            {"enabled": False},
            "vre_profile_multiplier":   {},
            "battery_enable":           True,
        },
    },
    "⛽ Fuel Price Shock": {
        "desc":   "Encarecimiento de combustibles fósiles — gas sube más, carbón sube poco. Adders: CCGT +30, OCGT +40, CHP +25, Diésel +50, Carbón +10.",
        "lesson": "Sube el costo total y el precio marginal nodal. Renovables e hidro se vuelven relativamente más atractivas. Si el sistema depende mucho de térmicas caras, puede aparecer shedding.",
        "params": {
            "marginal_cost_multiplier": {},
            "marginal_cost_adder": {
                "gas_ccgt":      30,   # 50 + 30 = 80 $/MWh
                "gas_ocgt":      40,   # 70 + 40 = 110 $/MWh
                "chp":           25,   # 35 + 25 = 60 $/MWh
                "diesel_engine": 50,   # 100 + 50 = 150 $/MWh
                "steam_other":   10,   # 30 + 10 = 40 $/MWh (carbón sube poco)
            },
            "voll_value":               3000,
            "demand_multiplier":        {"SIN": 1.0, "BCA": 1.0, "BCS": 1.0},
        },
    },
    "☀️ Renewables Boom 2026": {
        "desc":   "Expansión renovable 2026: más capacidad solar y eólica instalada por sistema (×1.4–×1.8). Perfiles horarios iguales, pero más MW disponibles.",
        "lesson": "Agregar MW renovables no garantiza aprovechamiento total. Aparece más curtailment en horas de alta producción cuando la demanda no absorbe toda la oferta. La flexibilidad del sistema es clave.",
        "params": {
            "marginal_cost_multiplier": {},
            "marginal_cost_adder":      {},
            "voll_value":               3000,
            "demand_multiplier":        {"SIN": 1.0, "BCA": 1.0, "BCS": 1.0},
            "capacity_multiplier": {
                "SIN": {"solar": 1.6, "wind": 1.4},
                "BCA": {"solar": 1.4, "wind": 1.2},
                "BCS": {"solar": 1.8, "wind": 1.3},
            },
        },
    },
    "🔧 Forced Outage – BCS Diésel": {
        "desc":   "Falla forzada: 35% de la capacidad diésel de BCS queda fuera de servicio. BCS depende del diésel como respaldo firme. VoLL sube a $5 000/MWh para enfatizar la escasez.",
        "lesson": "La pérdida de capacidad firme puede disparar costos y afectar confiabilidad, especialmente en sistemas aislados como BCS. El precio marginal refleja directamente la falta de alternativas.",
        "params": {
            "marginal_cost_multiplier": {},
            "marginal_cost_adder":      {},
            "voll_value":               5000,
            "demand_multiplier":        {"SIN": 1.0, "BCA": 1.0, "BCS": 1.0},
            "forced_outage": {
                "enabled":                True,
                "system":                 "BCS",
                "technology":             "diesel",   # alias → diesel_engine
                "capacity_loss_fraction": 0.35,
            },
        },
    },
    "🔋 Add Storage – Flexibility": {
        "desc":   "Instala baterías en los tres sistemas: SIN 600 MW / 2 400 MWh, BCA 150 MW / 600 MWh, BCS 100 MW / 400 MWh. Eficiencia ida y vuelta 95%, SOC cíclico.",
        "lesson": "La batería vale más cuando hay spreads de precios, picos de demanda o excedentes renovables. Reduce curtailment, suaviza picos de precio marginal y puede evitar shedding.",
        "params": {
            "marginal_cost_multiplier":      {},
            "marginal_cost_adder":           {},
            "voll_value":                    3000,
            "demand_multiplier":             {"SIN": 1.0, "BCA": 1.0, "BCS": 1.0},
            "battery_enable":                True,
            "battery_power_mw":              {"SIN": 600, "BCA": 150, "BCS": 100},
            "battery_energy_mwh":            {"SIN": 2400, "BCA": 600, "BCS": 400},
            "battery_efficiency_store":      0.95,
            "battery_efficiency_dispatch":   0.95,
            "battery_initial_soc":           0.5,
            "battery_cyclic_state_of_charge": True,
        },
    },
    "🔴 VOLL Alto ($10 000)": {
        "desc":   "VoLL = $10 000/MWh — política de confiabilidad estricta. El modelo prefiere usar generación carísima antes que cortar carga.",
        "lesson": "Con VoLL alto, el shedding es el último recurso. Sube el uso de térmicas de respaldo y el costo total; la confiabilidad tiene un precio implícito muy alto.",
        "params": {
            "marginal_cost_multiplier": {},
            "marginal_cost_adder":      {},
            "voll_value":               10_000,
            "demand_multiplier":        {"SIN": 1.0, "BCA": 1.0, "BCS": 1.0},
        },
    },
    "🟡 VOLL Bajo ($2 000)": {
        "desc":   "VoLL = $2 000/MWh — política de confiabilidad laxa. El modelo puede sheddear antes si producir cuesta demasiado.",
        "lesson": "Con VoLL bajo, el shedding compite directamente con las térmicas caras. Aparece carga no servida cuando el costo marginal supera $2 000/MWh. La confiabilidad es una decisión política.",
        "params": {
            "marginal_cost_multiplier": {},
            "marginal_cost_adder":      {},
            "voll_value":               2_000,
            "demand_multiplier":        {"SIN": 1.0, "BCA": 1.0, "BCS": 1.0},
        },
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

# Carrier alias map: scenario-facing names → internal carrier keys
CARRIER_ALIAS: dict[str, str] = {
    "wind":   "onwind",
    "diesel": "diesel_engine",
}

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
        effective = compute_effective_costs(SCENARIOS[sname]["params"])
        for carrier, cost in effective.items():
            st.session_state[f"cost_{carrier}"] = int(round(cost))
        voll_override = SCENARIOS[sname]["params"].get("voll_value", VOLL_DEFAULT)
        st.session_state["voll_input"] = voll_override
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
    if "voll_input" not in st.session_state:
        st.session_state["voll_input"] = VOLL_DEFAULT
    voll_input = st.number_input(
        "VoLL — carga no servida ($/MWh)",
        value=st.session_state["voll_input"],
        min_value=100, max_value=10_000, step=100,
        key="voll_input",
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
        "Los sliders afectan el orden de mérito y, por tanto, el despacho y el precio sombra. "
        "🔵 = costo sube vs. base  •  🔴 = costo baja vs. base"
    )
    sl_cols = st.columns(4)
    costs: dict[str, float] = {}
    for i, carrier in enumerate(carriers_present):
        label     = CARRIER_LABELS.get(carrier, carrier)
        default_v = DEFAULT_COSTS.get(carrier, 0)
        current_v = st.session_state.get(f"cost_{carrier}", default_v)

        with sl_cols[i % 4]:
            # Colored badge above slider when value differs from default
            if current_v > default_v:
                diff = current_v - default_v
                st.markdown(
                    f'<div style="font-size:11px;color:#1D4ED8;background:#DBEAFE;'
                    f'display:inline-block;padding:1px 7px;border-radius:4px;'
                    f'font-weight:600;margin-bottom:2px;">↑ +{diff:.0f} $/MWh</div>',
                    unsafe_allow_html=True,
                )
            elif current_v < default_v:
                diff = default_v - current_v
                st.markdown(
                    f'<div style="font-size:11px;color:#B91C1C;background:#FEE2E2;'
                    f'display:inline-block;padding:1px 7px;border-radius:4px;'
                    f'font-weight:600;margin-bottom:2px;">↓ -{diff:.0f} $/MWh</div>',
                    unsafe_allow_html=True,
                )
            costs[carrier] = st.slider(
                label,
                min_value=0,
                max_value=700,
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
    costs: dict[str, float],
    use_growth: bool,
    voll: float,
    demand_mult: dict[str, float] | None = None,
    capacity_mult: dict | None = None,
    forced_outage: dict | None = None,
    battery_config: dict | None = None,
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

    # Capacity multipliers — scale p_nom of specific carriers per bus
    if capacity_mult:
        for bus, carrier_mults in capacity_mult.items():
            for carrier_alias, mult in carrier_mults.items():
                carrier = CARRIER_ALIAS.get(carrier_alias, carrier_alias)
                mask = (n.generators["bus"] == bus) & (n.generators["carrier"] == carrier)
                n.generators.loc[mask, "p_nom"] *= float(mult)

    # Forced outage — derate a specific technology in a specific system
    fo = forced_outage or {}
    if fo.get("enabled", False):
        fo_bus     = fo.get("system", "")
        fo_alias   = fo.get("technology", "")
        fo_carrier = CARRIER_ALIAS.get(fo_alias, fo_alias)
        fo_loss    = float(fo.get("capacity_loss_fraction", 0.0))
        if fo_bus and fo_carrier and 0.0 < fo_loss <= 1.0:
            mask = (n.generators["bus"] == fo_bus) & (n.generators["carrier"] == fo_carrier)
            n.generators.loc[mask, "p_nom"] *= (1.0 - fo_loss)

    # Battery storage units (StorageUnit per bus)
    bc = battery_config or {}
    if bc.get("battery_enable", False):
        eff_store    = float(bc.get("battery_efficiency_store", 0.95))
        eff_dispatch = float(bc.get("battery_efficiency_dispatch", 0.95))
        init_soc_frac = float(bc.get("battery_initial_soc", 0.5))
        cyclic_soc   = bool(bc.get("battery_cyclic_state_of_charge", True))
        power_mw     = bc.get("battery_power_mw", {})
        energy_mwh   = bc.get("battery_energy_mwh", {})
        for s in SISTEMAS:
            p_nom_bat = float(power_mw.get(s, 0))
            e_mwh_bat = float(energy_mwh.get(s, 0))
            if p_nom_bat > 0 and e_mwh_bat > 0:
                max_hours = e_mwh_bat / p_nom_bat
                n.add(
                    "StorageUnit",
                    name=f"battery_{s}",
                    bus=s,
                    carrier="battery",
                    p_nom=p_nom_bat,
                    max_hours=max_hours,
                    efficiency_store=eff_store,
                    efficiency_dispatch=eff_dispatch,
                    state_of_charge_initial=init_soc_frac * p_nom_bat * max_hours,
                    cyclic_state_of_charge=cyclic_soc,
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
            mult = demand_mult.get(s, 1.0) if demand_mult else 1.0
            n.add("Load", f"load_{s}", bus=s, p_set=dem_z[s] * mult)

    n.optimize(solver_name="highs")
    return n


if run_btn:
    # Extract scenario-level params
    _demand_mult: dict[str, float] | None = None
    _capacity_mult: dict | None = None
    _forced_outage: dict | None = None
    _battery_config: dict | None = None
    if active_scenario and active_scenario in SCENARIOS:
        _sc_params = SCENARIOS[active_scenario]["params"]
        _dm = _sc_params.get("demand_multiplier", {})
        if any(v != 1.0 for v in _dm.values()):
            _demand_mult = _dm
        _cm = _sc_params.get("capacity_multiplier", {})
        if _cm:
            _capacity_mult = _cm
        _fo = _sc_params.get("forced_outage", {})
        if _fo.get("enabled", False):
            _forced_outage = _fo
        if _sc_params.get("battery_enable", False):
            _battery_config = _sc_params

    with st.spinner("Optimizando con HiGHS… puede tardar ~30 s para períodos largos."):
        try:
            n_solved = build_and_solve(
                centrales_base.copy(),
                p_max_pu_raw,
                dem_z,
                costs,
                growth_2026,
                float(voll_input),
                demand_mult=_demand_mult,
                capacity_mult=_capacity_mult,
                forced_outage=_forced_outage,
                battery_config=_battery_config,
            )
        except Exception as e:
            st.exception(e)
            st.stop()

        # Auto-run base scenario for comparison whenever not running base
        _is_base = (active_scenario == BASE_SCENARIO_KEY)
        if not _is_base:
            try:
                _base_costs = compute_effective_costs(SCENARIOS[BASE_SCENARIO_KEY]["params"])
                n_base_solved = build_and_solve(
                    centrales_base.copy(),
                    p_max_pu_raw,
                    dem_z,
                    _base_costs,
                    growth_2026,
                    float(VOLL_DEFAULT),
                    demand_mult=None,
                    capacity_mult=None,
                )
                st.session_state["n_base_solved"] = n_base_solved
            except Exception:
                st.session_state.pop("n_base_solved", None)
        else:
            st.session_state["n_base_solved"] = n_solved

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

# ── Comparison vs. Base scenario ──────────────────────────────────────────────
_n_base: pypsa.Network | None = st.session_state.get("n_base_solved")
_show_comparison = (
    _n_base is not None
    and scen_label != BASE_SCENARIO_KEY
)
if _show_comparison:
    with st.expander("⚖️ Comparación vs. Caso Base", expanded=True):
        _b_dispatch  = _n_base.generators_t.p.copy()
        _b_voll_gens = [g for g in _b_dispatch.columns if g.startswith("VoLL_")]
        _b_shadow    = _n_base.buses_t.marginal_price

        _b_cost    = _n_base.objective
        _b_gen     = _b_dispatch.drop(columns=_b_voll_gens, errors="ignore").sum().sum()
        _b_shed    = _b_dispatch[_b_voll_gens].sum().sum() if _b_voll_gens else 0.0

        # Base curtailment total
        _b_curt_total = 0.0
        if not _n_base.generators_t.p_max_pu.empty:
            _b_gen_info = _n_base.generators[["bus", "carrier"]].copy()
            _b_ren_gens = [
                g for g in _n_base.generators_t.p_max_pu.columns
                if g in _b_gen_info.index and _b_gen_info.loc[g, "carrier"] in RENEWABLE_CARRIERS
            ]
            if _b_ren_gens:
                _b_avail = _n_base.generators_t.p_max_pu[_b_ren_gens].multiply(
                    _n_base.generators.loc[_b_ren_gens, "p_nom"]
                )
                _b_disp  = _n_base.generators_t.p.reindex(columns=_b_ren_gens, fill_value=0.0)
                _b_curt_total = (_b_avail - _b_disp).clip(lower=0).sum().sum()

        _s_gen   = dispatch.drop(columns=voll_gens, errors="ignore").sum().sum()
        delta_cost  = n.objective - _b_cost
        delta_gen   = _s_gen - _b_gen
        delta_shed  = shedding_total - _b_shed
        delta_curt  = curtailment_total - _b_curt_total

        def _fmt_delta(val: float, invert: bool = False) -> str:
            sign = "+" if val >= 0 else ""
            arrow = ("↑" if val > 0 else "↓") if abs(val) > 0.5 else "="
            if invert:
                arrow = ("↑" if val > 0 else "↓") if abs(val) > 0.5 else "="
            return f"{arrow} {sign}{val:,.0f}"

        c1, c2, c3, c4 = st.columns(4)
        c1.metric(
            "Δ Costo total ($)",
            f"{n.objective:,.0f}",
            delta=_fmt_delta(delta_cost, invert=True),
            delta_color="inverse",
            help=f"Base: ${_b_cost:,.0f}",
        )
        c2.metric(
            "Δ Generación (MWh)",
            f"{_s_gen:,.0f}",
            delta=_fmt_delta(delta_gen),
            delta_color="normal",
            help=f"Base: {_b_gen:,.0f} MWh",
        )
        c3.metric(
            "Δ Carga no servida (MWh)",
            f"{shedding_total:,.0f}",
            delta=_fmt_delta(delta_shed, invert=True),
            delta_color="inverse",
            help=f"Base: {_b_shed:,.0f} MWh",
        )
        c4.metric(
            "Δ Curtailment (MWh)",
            f"{curtailment_total:,.0f}",
            delta=_fmt_delta(delta_curt),
            delta_color="normal",
            help=f"Base: {_b_curt_total:,.0f} MWh",
        )

        # Per-system shadow price comparison
        st.markdown("**Precio marginal nodal promedio ($/MWh)**")
        sp_cmp_cols = st.columns(len(SISTEMAS))
        for idx_s, s in enumerate(SISTEMAS):
            if s in shadow_prices.columns and s in _b_shadow.columns:
                sc_avg  = shadow_prices[s].mean()
                bc_avg  = _b_shadow[s].mean()
                d_avg   = sc_avg - bc_avg
                sp_cmp_cols[idx_s].metric(
                    s,
                    f"{sc_avg:,.1f} $/MWh",
                    delta=f"{'+'if d_avg>=0 else ''}{d_avg:,.1f} vs base",
                    delta_color="inverse",
                    help=f"Base: {bc_avg:,.1f} $/MWh",
                )

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

    # Add battery net dispatch (positive = discharge, negative = charge)
    bat_col = f"battery_{bus}"
    if not n.storage_units.empty and bat_col in n.storage_units.index:
        if bat_col in n.storage_units_t.p.columns:
            disp_carrier["battery"] = n.storage_units_t.p[bat_col]

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

