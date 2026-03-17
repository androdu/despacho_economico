# app/pages/2_Despacho_PyPSA.py
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pypsa
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ──────────────────────────────────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────────────────────────────────
ROOT          = Path(__file__).resolve().parents[2]
DATA_CLEAN    = ROOT / "data_clean"
CENTRALES_CSV = DATA_CLEAN / "generators" / "Centrales_gen_mx.csv"
PERFIL_CSV    = DATA_CLEAN / "generators" / "Perfil_Generaciom.csv"
DEMAND_RAW_DIR  = ROOT / "data_raw" / "demand" / "balance_2026"
DEMAND_API_DIR  = ROOT / "data_raw" / "demand" / "daily_api"

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
    "hydro":         "#0051FF",
    "nuclear":       "#FF0000",
    "solar":         "#FFB700",
    "onwind":        "#10B981",
    "solar_thermal": "#FBDC60",
    "geothermal":    "#CDC1E9",
    "biogas":        "#446F12",
    "biomass":       "#0A5137",
    "chp":           "#393E47",
    "gas_ccgt":      "#441F05",
    "gas_ocgt":      "#FB923C",
    "steam_other":   "#78716C",
    "diesel_engine": "#EF4444",
    "battery":       "#06B6D4",
    "shedding":      "#500E0E",
}




# Default marginal costs ($/MWh) — based on CFE/CENACE reference
DEFAULT_COSTS: dict[str, float] = {
    "hydro":         8,
    "nuclear":       5,
    "solar":         0,
    "onwind":        0,
    "solar_thermal": 3,
    "geothermal":    10,
    "biogas":        15,
    "biomass":       20,
    "chp":           50,
    "gas_ccgt":      50,
    "gas_ocgt":      70,
    "steam_other":   65,
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
        "desc":   "Costos de referencia del SEN. Renovables e hidro despachan primero; gas CCGT cubre la demanda residual; vapor es respaldo caro.",
        "lesson": "Gas CCGT es el unit marginal en la mayoría de horas. Solar/eólica/hidro comprimen el precio en horas de alta generación renovable.",
        "narrative": {
            "cambio":   "Costos variables de referencia sin modificación. Gas CCGT = 50 $/MWh, vapor = 65 $/MWh.",
            "observa":  "En el despacho: renovables ocupan la base de la pila; gas CCGT llena la demanda residual. En el precio marginal: ~50 $/MWh en horas de valle, cae a cero en horas de alta solar.",
            "leccion":  "El **precio marginal** lo fija el último generador despachado (gas CCGT). Las renovables no tienen costo variable, así que reducen el precio cuando hay suficiente recurso — el fenómeno de *merit order effect*.",
        },
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
            "battery_enable":           False,
        },
    },
    "⛽ Fuel Price Shock": {
        "desc":   "Encarecimiento de combustibles fósiles — gas sube más, carbón sube poco. Adders: CCGT +30, OCGT +40, CHP +10, Diésel +50, Vapor +15.",
        "lesson": "Sube el costo total y el precio marginal nodal. Renovables e hidro se vuelven relativamente más atractivas. Si el sistema depende mucho de térmicas caras, puede aparecer shedding.",
        "narrative": {
            "cambio":   "Gas CCGT sube de 50 → 80 $/MWh, OCGT de 70 → 110, vapor de 65 → 80, diésel de 100 → 150. Las renovables e hidro **no cambian**.",
            "observa":  "El precio marginal nodal sube en todos los sistemas. El mix de generación desplaza más trabajo a renovables e hidro. Compara el costo total vs. el caso base en el panel de comparación.",
            "leccion":  "Un shock de combustible se transmite íntegramente al precio de mercado cuando las térmicas son el *unit marginal*. La penetración renovable actúa como amortiguador natural del precio.",
        },
        "params": {
            "marginal_cost_multiplier": {},
            "marginal_cost_adder": {
                "gas_ccgt":      30,   # 50 + 30 = 80 $/MWh
                "gas_ocgt":      40,   # 70 + 40 = 110 $/MWh
                "chp":           10,   # 50 + 10 = 60 $/MWh
                "diesel_engine": 50,   # 100 + 50 = 150 $/MWh
                "steam_other":   15,   # 65 + 15 = 80 $/MWh
            },
            "voll_value":               3000,
            "demand_multiplier":        {"SIN": 1.0, "BCA": 1.0, "BCS": 1.0},
        },
    },
    "☀️ Renewables Boom 2026": {
        "desc":   "Expansión renovable 2026: más capacidad solar y eólica instalada por sistema (×1.4–×1.8). Perfiles horarios iguales, pero más MW disponibles.",
        "lesson": "Agregar MW renovables no garantiza aprovechamiento total. Aparece más curtailment en horas de alta producción cuando la demanda no absorbe toda la oferta. La flexibilidad del sistema es clave.",
        "narrative": {
            "cambio":   "Solar SIN ×1.6, eólica SIN ×1.4, solar BCA ×1.4, solar BCS ×1.8. Los **perfiles horarios no cambian** — solo aumenta la potencia instalada.",
            "observa":  "El curtailment sube (tab por sistema → sección de curtailment). El precio marginal cae en horas solares. El costo total puede bajar aunque haya más capacidad sin usar.",
            "leccion":  "Añadir MW renovables sin flexibilidad (almacenamiento, interconexión, demanda flexible) genera *curtailment* creciente. El valor marginal de cada MW adicional decrece — ley de rendimientos marginales decrecientes en VRE.",
        },
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
        "desc":   "Falla forzada: 35% de la capacidad diésel de BCS queda fuera de servicio. BCS depende del diésel como respaldo firme. VoLL sube a $5 000/MWh.",
        "lesson": "La pérdida de capacidad firme puede disparar costos y afectar confiabilidad, especialmente en sistemas aislados como BCS. El precio marginal refleja directamente la falta de alternativas.",
        "narrative": {
            "cambio":   "BCS pierde 35% de su capacidad diésel (único respaldo firme disponible). VoLL = 5 000 $/MWh para simular política de confiabilidad estricta.",
            "observa":  "En el tab BCS: el precio marginal sube bruscamente. Posible aparición de **carga no servida** (barra roja en el despacho) si la demanda supera la capacidad reducida.",
            "leccion":  "Los sistemas aislados son extremadamente vulnerables a la pérdida de capacidad firme. Sin interconexión, no hay respaldo externo — el VoLL es la única válvula de escape del LP, lo que refleja el costo económico real de un blackout.",
        },
        "params": {
            "marginal_cost_multiplier": {},
            "marginal_cost_adder":      {},
            "voll_value":               5000,
            "demand_multiplier":        {"SIN": 1.0, "BCA": 1.0, "BCS": 1.0},
            "forced_outage": {
                "enabled":                True,
                "system":                 "BCS",
                "technology":             "diesel",
                "capacity_loss_fraction": 0.35,
            },
        },
    },
    "🔋 Add Storage – Flexibility": {
        "desc":   "Instala baterías en los tres sistemas: SIN 600 MW / 2 400 MWh, BCA 150 MW / 600 MWh, BCS 100 MW / 400 MWh. Eficiencia 95%, SOC cíclico.",
        "lesson": "La batería vale más cuando hay spreads de precios, picos de demanda o excedentes renovables. Reduce curtailment, suaviza picos de precio marginal y puede evitar shedding.",
        "narrative": {
            "cambio":   "Se añaden baterías BESS en cada sistema: SIN 600 MW/2 400 MWh (4 h), BCA 150/600, BCS 100/400. Eficiencia ida y vuelta 90.25% (0.95²). SOC cíclico.",
            "observa":  "En el tab de cada sistema: aparece la sección **SOC de batería**. La batería carga en horas solares (precio bajo) y descarga al atardecer/noche (precio alto). El curtailment baja; el precio marginal se aplana.",
            "leccion":  "El almacenamiento realiza **arbitraje temporal**: compra energía barata (solar) y la vende cara (pico). El spread de precio que la batería captura es exactamente su valor de mercado — si el spread baja a cero, la batería no tiene incentivo económico para operar.",
        },
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
        "narrative": {
            "cambio":   "Solo cambia el VoLL: de 3 000 → 10 000 $/MWh. Todos los costos de generación permanecen iguales.",
            "observa":  "El costo total sube porque el modelo despacha unidades más caras para evitar el shedding. El precio marginal puede alcanzar 10 000 $/MWh si hay escasez. El shedding es casi cero.",
            "leccion":  "El VoLL es un **parámetro de política**, no técnico. Refleja cuánto está dispuesta a pagar la sociedad por evitar un blackout. Con VoLL alto, la curva de demanda es perfectamente inelástica — la electricidad se produce a cualquier costo.",
        },
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
        "narrative": {
            "cambio":   "VoLL baja de 3 000 → 2 000 $/MWh. Esto significa que el modelo prefiere cortar carga antes que despachar unidades con costo > 2 000 $/MWh.",
            "observa":  "Si hay horas donde el único generador disponible cuesta más de 2 000 $/MWh, aparece **carga no servida** (barra oscura en la gráfica de despacho). Compara el shedding vs. el caso base.",
            "leccion":  "El shedding no es un error del modelo — es una decisión de costo-beneficio. Cuando el costo de producir una unidad supera el VoLL, es \"más barato\" no servir la demanda. El VoLL implícito en el SEN real es mucho mayor (~20 000–50 000 $/MWh).",
        },
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

VRE_CARRIERS = {"solar", "onwind"}  # Variable renewable energy: curtailment-eligible only
SYSTEM_COLORS = {"SIN": "#2563EB", "BCA": "#16A34A", "BCS": "#EA580C"}

# CO₂ emission factors (tCO₂/MWh electrical output, IPCC AR6 median)
CO2_FACTOR: dict[str, float] = {
    "gas_ccgt":      0.37,
    "gas_ocgt":      0.55,
    "steam_other":   0.85,
    "diesel_engine": 0.70,
    "chp":           0.45,
    "nuclear":       0.012,
    "hydro":         0.024,
    "solar":         0.0,
    "onwind":        0.0,
    "solar_thermal": 0.0,
    "geothermal":    0.038,
    "biogas":        0.0,
    "biomass":       0.0,
    "battery":       0.0,
}

# p_min_pu for inflexible technologies (cannot ramp down freely)
INFLEXIBLE_PMIN: dict[str, float] = {
    "nuclear":    0.85,
    "geothermal": 0.80,
    "chp":        0.40,
}

# Dispatch category — determines p_max_pu default when no profile is available
#   vre        → profile mandatory; 0.0 if missing (no sun/wind = no generation)
#   hydro      → availability factor P_MAX_AVAIL["hydro"] (partial reservoir constraint)
#   inflexible → rated availability factor P_MAX_AVAIL[carrier] (forced baseload)
#   thermal    → 1.0 (fully dispatchable on demand)
DISPATCH_CATEGORY: dict[str, str] = {
    "solar":         "vre",
    "onwind":        "vre",
    "hydro":         "hydro",
    "nuclear":       "inflexible",
    "geothermal":    "inflexible",
    "chp":           "inflexible",
    "gas_ccgt":      "thermal",
    "gas_ocgt":      "thermal",
    "diesel_engine": "thermal",
    "steam_other":   "thermal",
    "solar_thermal": "thermal",
    "biogas":        "thermal",
    "biomass":       "thermal",
    "battery":       "thermal",
}

# Default p_max_pu for categories / carriers without a real-time profile
P_MAX_AVAIL: dict[str, float] = {
    "hydro":      0.55,   # seasonal reservoir + run-of-river constraint
    "nuclear":    0.90,   # planned outage factor
    "geothermal": 0.90,   # high capacity factor but not 100%
    "chp":        0.85,   # heat-demand coupling limits full output
}

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

    # ── 1. CSVs oficiales de balance CENACE ───────────────────────────────────
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
        df["zona"]      = df["zona"].astype(str).str.strip().str.strip('"').str.upper()
        df["hora"]      = pd.to_numeric(df["hora"], errors="coerce")
        df["demand_mw"] = pd.to_numeric(
            df["demand_mw"].astype(str).str.strip().str.replace(",", ""), errors="coerce"
        )
        df = df.dropna(subset=["hora", "demand_mw"])
        df["snapshot"] = op_date + pd.to_timedelta(df["hora"].astype(int) - 1, unit="h")
        # SIN is split into 7 areas in the CSV — sum areas to get system total
        df = df.groupby(["snapshot", "zona"], as_index=False)["demand_mw"].sum()
        all_dfs.append(df[["snapshot", "zona", "demand_mw"]])

    # ── 2. CSVs de la API diaria (fetch_daily_demand.py / GitHub Action) ──────
    if DEMAND_API_DIR.exists():
        for f in sorted(DEMAND_API_DIR.glob("demand_*.csv")):
            try:
                df = pd.read_csv(f, parse_dates=["snapshot"])
                df["zona"]      = df["zona"].astype(str).str.upper()
                df["demand_mw"] = pd.to_numeric(df["demand_mw"], errors="coerce")
                df = df.dropna(subset=["snapshot", "zona", "demand_mw"])
                all_dfs.append(df[["snapshot", "zona", "demand_mw"]])
            except Exception:
                continue

    if not all_dfs:
        raise ValueError(f"No se encontraron archivos de demanda en {DEMAND_RAW_DIR}")

    dem = pd.concat(all_dfs, ignore_index=True)
    dem["zona"] = dem["zona"].replace({"BSA": "BCA"})

    # Deduplicar: si un día tiene datos en balance oficial Y en daily_api,
    # el balance oficial tiene prioridad (viene primero por orden de concatenación)
    dem = (
        dem.drop_duplicates(subset=["snapshot", "zona"], keep="first")
        .sort_values("snapshot")
        .reset_index(drop=True)
    )

    # Descartar días con datos incompletos (< 20 horas en al menos un sistema)
    # Evita que archivos parciales de la API causen errores en la optimización
    dem["_date"] = dem["snapshot"].dt.date
    hours_per_day_zona = dem.groupby(["_date", "zona"])["snapshot"].nunique()
    bad_dates = hours_per_day_zona[hours_per_day_zona < 20].index.get_level_values("_date").unique()
    if len(bad_dates) > 0:
        dem = dem[~dem["_date"].isin(bad_dates)]
    dem = dem.drop(columns=["_date"]).reset_index(drop=True)

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
# ── Validar integridad de demanda ─────────────────────────────────────────────
for s in SISTEMAS:
    if dem_z_full[s].sum() == 0:
        st.warning(f"⚠️ Advertencia: La demanda del sistema **{s}** es totalmente 0 MW en los datos cargados. Revisa la descarga de datos.")

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
    nav = sc.get("narrative")
    if nav:
        st.markdown(
            f"""
<div style="border:1px solid rgba(49,51,63,0.12);border-radius:16px;
            padding:16px 20px;background:rgba(255,255,255,0.70);margin-bottom:0.5rem;">
  <div style="font-weight:700;font-size:1.02rem;margin-bottom:10px;">{active_scenario}</div>
  <div style="display:flex;flex-direction:column;gap:8px;">
    <div style="display:flex;gap:10px;align-items:flex-start;">
      <span style="background:#DBEAFE;color:#1D4ED8;border-radius:6px;padding:2px 8px;
                   font-size:0.78rem;font-weight:700;white-space:nowrap;margin-top:1px;">
        ① QUÉ CAMBIÓ
      </span>
      <span style="font-size:0.93rem;color:rgba(49,51,63,0.85);">{nav['cambio']}</span>
    </div>
    <div style="display:flex;gap:10px;align-items:flex-start;">
      <span style="background:#D1FAE5;color:#065F46;border-radius:6px;padding:2px 8px;
                   font-size:0.78rem;font-weight:700;white-space:nowrap;margin-top:1px;">
        ② QUÉ OBSERVAR
      </span>
      <span style="font-size:0.93rem;color:rgba(49,51,63,0.85);">{nav['observa']}</span>
    </div>
    <div style="display:flex;gap:10px;align-items:flex-start;">
      <span style="background:#FEF3C7;color:#92400E;border-radius:6px;padding:2px 8px;
                   font-size:0.78rem;font-weight:700;white-space:nowrap;margin-top:1px;">
        ③ LECCIÓN
      </span>
      <span style="font-size:0.93rem;color:rgba(49,51,63,0.85);">{nav['leccion']}</span>
    </div>
  </div>
</div>
            """,
            unsafe_allow_html=True,
        )
    else:
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
    st.dataframe(cap_base.style.format("{:,.0f}"), width='stretch')

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
        st.dataframe(growth_agg.style.format("{:,.0f}"), width='stretch')

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
# Metrics extractor (reused for comparison table)
# ──────────────────────────────────────────────────────────────────────────────
def extract_metrics(n: pypsa.Network) -> dict:
    """Return a flat dict of summary metrics for one solved network."""
    gi = n.generators[["bus", "carrier", "p_nom", "marginal_cost"]].copy()
    disp = n.generators_t.p.copy()
    voll_g = [g for g in disp.columns if g.startswith("VoLL_")]
    non_voll = [g for g in disp.columns if not g.startswith("VoLL_")]

    gen_mwh = disp[non_voll].sum()
    total_mwh = gen_mwh.sum()
    shedding = disp[voll_g].sum().sum() if voll_g else 0.0

    # CO₂
    co2_total = sum(
        gen_mwh[g] * CO2_FACTOR.get(gi.loc[g, "carrier"], 0.0)
        for g in non_voll if g in gi.index
    )
    intensity = co2_total / total_mwh * 1000 if total_mwh > 0 else 0.0  # gCO₂/kWh

    # Renewable share
    ren_carriers = {"solar", "onwind", "hydro", "geothermal", "solar_thermal", "biogas", "biomass", "nuclear"}
    ren_mwh = sum(
        gen_mwh[g] for g in non_voll
        if g in gi.index and gi.loc[g, "carrier"] in ren_carriers
    )
    ren_pct = ren_mwh / total_mwh * 100 if total_mwh > 0 else 0.0

    # Curtailment (VRE only)
    curt_total = 0.0
    if not n.generators_t.p_max_pu.empty:
        vre_g = [g for g in n.generators_t.p_max_pu.columns if g in gi.index and gi.loc[g, "carrier"] in VRE_CARRIERS]
        if vre_g:
            avail = n.generators_t.p_max_pu[vre_g].multiply(n.generators.loc[vre_g, "p_nom"])
            curt_total = (avail - disp.reindex(columns=vre_g, fill_value=0.0)).clip(lower=0).sum().sum()

    # Avg shadow price
    sp = n.buses_t.marginal_price
    avg_price = sp.values.mean() if not sp.empty else 0.0

    return {
        "Costo total ($M)":   float(n.objective) / 1e6,
        "CO₂ (MtCO₂)":       co2_total / 1e6,
        "Intensidad (gCO₂/kWh)": intensity,
        "% Renovable":        ren_pct,
        "Curtailment (GWh)":  curt_total / 1e3,
        "Shedding (MWh)":     shedding,
        "Precio med. ($/MWh)": avg_price,
    }


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
        def safe_replace_year(ts, new_year):
            try:
                return ts.replace(year=new_year)
            except ValueError:
                # Manejo de años bisiestos (29 feb -> 28 feb en año no bisiesto)
                return ts.replace(year=new_year, day=28)

        p_max_pu_aligned = p_max_pu_raw.copy()
        # Usar map con función segura para evitar crash en 29 de febrero
        p_max_pu_aligned.index = p_max_pu_raw.index.map(lambda ts: safe_replace_year(ts, demand_year))
    else:
        p_max_pu_aligned = p_max_pu_raw

    # Three isolated buses (no links)
    for s in SISTEMAS:
        n.add("Bus", s)

    # Generators from CSV — apply slider marginal costs by carrier
    for _, row in centrales.iterrows():
        carrier = str(row["carrier"])
        mc      = costs.get(carrier, float(row["marginal_cost"]))
        pmin    = INFLEXIBLE_PMIN.get(carrier, 0.0)
        n.add(
            "Generator",
            name=str(row["name"]),
            bus=str(row["bus"]),
            carrier=carrier,
            p_nom=float(row["p_nom"]),
            marginal_cost=float(mc),
            efficiency=float(row.get("efficiency", 1.0)),
            p_min_pu=float(pmin),
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

    # ── p_max_pu: 4-category dispatch logic ──────────────────────────────────
    # Category    | Source             | Missing-data default
    # ------------|--------------------|-----------------------------------------
    # vre         | Perfil CSV (real)  | 0.0  (no resource = no generation)
    # hydro       | Perfil CSV + cap   | P_MAX_AVAIL["hydro"] (reservoir factor)
    # inflexible  | Perfil CSV + cap   | P_MAX_AVAIL[carrier] (rated availability)
    # thermal     | Perfil CSV or 1.0  | 1.0  (fully dispatchable)
    all_gens        = n.generators.index.tolist()
    carrier_map_all = n.generators["carrier"]
    profile_gens    = [g for g in all_gens if g in p_max_pu_aligned.columns]

    if profile_gens:
        p_raw = p_max_pu_aligned[profile_gens].reindex(index=snapshots)
        if p_raw.isna().all().all() and not p_max_pu_aligned.empty:
            raise ValueError(
                "Error de alineación de tiempo: Los índices de fecha del perfil de generadores y la demanda no coinciden. "
                "Verifica si uno tiene Timezone y el otro no."
            )
        # Per-column fill based on dispatch category
        for g in profile_gens:
            if p_raw[g].isna().any():
                cat = DISPATCH_CATEGORY.get(carrier_map_all[g], "thermal")
                if cat == "vre":
                    p_raw[g] = p_raw[g].fillna(0.0)
                elif cat in ("hydro", "inflexible"):
                    p_raw[g] = p_raw[g].fillna(P_MAX_AVAIL.get(carrier_map_all[g], 1.0))
                else:  # thermal
                    p_raw[g] = p_raw[g].fillna(1.0)
        p_raw = p_raw.clip(0.0, 1.0)
    else:
        p_raw = pd.DataFrame(index=snapshots)

    # Expand to ALL generators — those absent from Perfil CSV get category defaults
    p_max_pu_full = p_raw.reindex(columns=all_gens)
    for g in all_gens:
        if p_max_pu_full[g].isna().all():
            cat = DISPATCH_CATEGORY.get(carrier_map_all[g], "thermal")
            if cat == "vre":
                p_max_pu_full[g] = 0.0
            elif cat in ("hydro", "inflexible"):
                p_max_pu_full[g] = P_MAX_AVAIL.get(carrier_map_all[g], 1.0)
            else:
                p_max_pu_full[g] = 1.0
    n.generators_t.p_max_pu = p_max_pu_full.clip(0.0, 1.0)

    # Enforce availability caps for hydro and inflexibles (even if profile exists)
    for carrier, cap in P_MAX_AVAIL.items():
        capped_gens = n.generators.index[n.generators["carrier"] == carrier]
        if not capped_gens.empty:
            n.generators_t.p_max_pu.loc[:, capped_gens] = (
                n.generators_t.p_max_pu.loc[:, capped_gens].clip(upper=cap)
            )

    # Loads
    for s in SISTEMAS:
        if s in dem_z.columns:
            mult = demand_mult.get(s, 1.0) if demand_mult else 1.0
            n.add("Load", f"load_{s}", bus=s, p_set=dem_z[s] * mult)

    n.optimize(solver_name="highs", include_objective_constant=False)
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
    # Filter to VRE carriers only (solar/wind): curtailment is only meaningful for variable renewables
    vre_profile_gens = [
        g for g in profile_gens_solved
        if g in gen_info.index and gen_info.loc[g, "carrier"] in VRE_CARRIERS
    ]
    if vre_profile_gens:
        p_avail = n.generators_t.p_max_pu[vre_profile_gens].multiply(
            n.generators.loc[vre_profile_gens, "p_nom"]
        )
        p_disp_ren = n.generators_t.p.reindex(columns=vre_profile_gens, fill_value=0.0)
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
k1.metric("Costo total ($)",            f"{float(n.objective):,.0f}")
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

        _b_cost    = float(_n_base.objective)
        _b_gen     = _b_dispatch.drop(columns=_b_voll_gens, errors="ignore").sum().sum()
        _b_shed    = _b_dispatch[_b_voll_gens].sum().sum() if _b_voll_gens else 0.0

        # Base curtailment total
        _b_curt_total = 0.0
        if not _n_base.generators_t.p_max_pu.empty:
            _b_gen_info = _n_base.generators[["bus", "carrier"]].copy()
            _b_ren_gens = [
                g for g in _n_base.generators_t.p_max_pu.columns
                if g in _b_gen_info.index and _b_gen_info.loc[g, "carrier"] in VRE_CARRIERS
            ]
            if _b_ren_gens:
                _b_avail = _n_base.generators_t.p_max_pu[_b_ren_gens].multiply(
                    _n_base.generators.loc[_b_ren_gens, "p_nom"]
                )
                _b_disp  = _n_base.generators_t.p.reindex(columns=_b_ren_gens, fill_value=0.0)
                _b_curt_total = (_b_avail - _b_disp).clip(lower=0).sum().sum()

        _s_gen   = dispatch.drop(columns=voll_gens, errors="ignore").sum().sum()
        delta_cost  = float(n.objective) - _b_cost
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
            f"{float(n.objective):,.0f}",
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

# ── Helper: identifica unidad marginal plausible por hora y bus ───────────────
def identify_marginal_generator(
    n: pypsa.Network,
    bus: str,
    tol_mw: float = 1.0,
    tol_price: float = 1.0,
) -> pd.DataFrame:
    """
    Identifica una unidad marginal plausible por hora en un bus.
    Criterio:
    1) unidad despachada
    2) no pegada al mínimo ni al máximo
    3) costo variable ~ precio sombra
    Fallback: unidad despachada más cara.
    """
    gens = n.generators.index[
        (n.generators["bus"] == bus) & (~n.generators.index.str.startswith("VoLL_"))
    ].tolist()

    if not gens or bus not in n.buses_t.marginal_price.columns:
        return pd.DataFrame()

    dispatch = n.generators_t.p[gens].copy()
    mc = n.generators.loc[gens, "marginal_cost"].copy()
    p_nom = n.generators.loc[gens, "p_nom"].copy()
    p_min_pu = n.generators.loc[gens, "p_min_pu"].fillna(0.0).copy()

    if n.generators_t.p_max_pu.empty:
        p_max_pu = pd.DataFrame(1.0, index=n.snapshots, columns=gens)
    else:
        p_max_pu = n.generators_t.p_max_pu.reindex(index=n.snapshots, columns=gens, fill_value=1.0)

    rows = []

    for t in n.snapshots:
        sp = float(n.buses_t.marginal_price.loc[t, bus])

        p_t = dispatch.loc[t]
        pmax_t = p_nom * p_max_pu.loc[t]
        pmin_t = p_nom * p_min_pu

        active = p_t[p_t > tol_mw].index.tolist()

        if not active:
            rows.append({
                "snapshot": t,
                "shadow_price": sp,
                "marginal_generator": None,
                "carrier": None,
                "CV ($/MWh)": None,
                "status": "sin generación",
            })
            continue

        interior = []
        for g in active:
            at_min = p_t[g] <= (pmin_t[g] + tol_mw)
            at_max = p_t[g] >= (pmax_t[g] - tol_mw)
            if (not at_min) and (not at_max):
                interior.append(g)

        # 1) Interior con costo cercano al precio
        close_to_price = [g for g in interior if abs(float(mc[g]) - sp) <= tol_price]
        if close_to_price:
            chosen = max(close_to_price, key=lambda g: mc[g])
            status = "interior ~ precio"
        # 2) Interior aunque no empate exacto
        elif interior:
            chosen = min(interior, key=lambda g: abs(float(mc[g]) - sp))
            status = "interior cercano"
        # 3) Fallback: despachado más caro
        else:
            chosen = max(active, key=lambda g: mc[g])
            status = "fallback más caro despachado"

        chosen_at_min = p_t[chosen] <= (pmin_t[chosen] + tol_mw)
        chosen_at_max = p_t[chosen] >= (pmax_t[chosen] - tol_mw)
        rows.append({
            "snapshot": t,
            "shadow_price": sp,
            "marginal_generator": chosen,
            "carrier": n.generators.loc[chosen, "carrier"],
            "CV ($/MWh)": float(mc[chosen]),
            "dispatch_MW": float(p_t[chosen]),
            "pmin_MW": float(pmin_t[chosen]),
            "pmax_MW": float(pmax_t[chosen]),
            "at_min": bool(chosen_at_min),
            "at_max": bool(chosen_at_max),
            "status": status,
        })

    return pd.DataFrame(rows).set_index("snapshot")


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

    # Battery net dispatch handled separately (positive=discharge, negative=charge)
    bat_col = f"battery_{bus}"
    _bat_series = None
    if not n.storage_units.empty and bat_col in n.storage_units.index:
        if bat_col in n.storage_units_t.p.columns:
            _bat_series = n.storage_units_t.p[bat_col]

    # Append shedding if any
    voll_col = f"VoLL_{bus}"
    if voll_col in dispatch.columns and dispatch[voll_col].sum() > 0.1:
        disp_carrier["shedding"] = dispatch[voll_col]

    # Exclude battery from stack carriers (handled as separate line below)
    stack_carriers = [c for c in CARRIERS if c != "battery"] + ["shedding"]
    carrier_order = [c for c in stack_carriers if c in disp_carrier.columns]
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

    # Battery as separate dashed line (avoids negative values breaking stacked area)
    if _bat_series is not None and _bat_series.abs().sum() > 0.1:
        fig.add_trace(go.Scatter(
            x=_bat_series.index,
            y=_bat_series.values,
            mode="lines",
            name=CARRIER_LABELS["battery"],
            line=dict(color=CARRIER_COLORS["battery"], width=2, dash="dash"),
            hovertemplate=f"Batería neta<br>%{{x|%d-%b %H:%M}}<br>%{{y:,.0f}} MW<extra></extra>",
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
    st.plotly_chart(fig, width='stretch')


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
    st.plotly_chart(fig, width='stretch')
    st.caption(
        f"Máximo: **{mx:,.1f} $/MWh** — Promedio: **{avg:,.1f} $/MWh**  \n"
        "ℹ️ El PML real incluye componentes de **congestión** y **pérdidas** que este modelo no calcula "
        "(red copper-plate, sin transmisión entre zonas). El precio aquí refleja únicamente el costo "
        "variable del generador marginal."
    )


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
    st.plotly_chart(fig, width='stretch')


# ── Helper: battery SOC chart ─────────────────────────────────────────────────
def battery_soc_chart(bus: str) -> None:
    bat_col = f"battery_{bus}"
    if n.storage_units.empty or bat_col not in n.storage_units.index:
        return  # no battery in this system — silently skip

    soc = n.storage_units_t.state_of_charge.get(bat_col)
    if soc is None or soc.empty:
        st.caption("SOC no disponible para esta batería.")
        return

    e_max_mwh = float(n.storage_units.loc[bat_col, "p_nom"]) * float(
        n.storage_units.loc[bat_col, "max_hours"]
    )

    # Charge / discharge power
    p_net = n.storage_units_t.p.get(bat_col, pd.Series(0.0, index=soc.index))
    try:
        p_dispatch = n.storage_units_t.p_dispatch[bat_col].reindex(soc.index, fill_value=0.0)
        p_store    = n.storage_units_t.p_store[bat_col].reindex(soc.index, fill_value=0.0)
    except (KeyError, AttributeError):
        # Fallback: derive from net power
        p_dispatch = p_net.clip(lower=0)
        p_store    = (-p_net).clip(lower=0)

    soc_pct = soc / e_max_mwh * 100  # 0–100 %

    # ── KPIs ──────────────────────────────────────────────────────────────────
    energy_dispatched = p_dispatch.sum()           # MWh discharged total
    equiv_cycles      = energy_dispatched / e_max_mwh if e_max_mwh > 0 else 0.0
    soc_avg_pct       = soc_pct.mean()
    soc_final_pct     = float(soc_pct.iloc[-1])

    st.markdown("**🔋 Batería — Estado de Carga (SOC)**")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Capacidad", f"{e_max_mwh:,.0f} MWh",
              help=f"Potencia: {n.storage_units.loc[bat_col, 'p_nom']:,.0f} MW")
    m2.metric("Energía arbitrada", f"{energy_dispatched:,.0f} MWh")
    m3.metric("Ciclos equivalentes", f"{equiv_cycles:.1f}",
              help="Total descargado / capacidad nominal")
    m4.metric("SOC promedio", f"{soc_avg_pct:.1f} %",
              delta=f"Final: {soc_final_pct:.1f} %")

    # ── Figure: 2 subplots ─────────────────────────────────────────────────────
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.65, 0.35],
        vertical_spacing=0.06,
        subplot_titles=("Estado de carga (MWh)", "Carga / Descarga (MW)"),
    )

    # ── Row 1: SOC area ────────────────────────────────────────────────────────
    # Gradient-like effect: color by SOC level using a filled area
    fig.add_trace(
        go.Scatter(
            x=soc.index, y=soc.values,
            mode="lines",
            name="SOC (MWh)",
            line=dict(color="#06B6D4", width=2),
            fill="tozeroy",
            fillcolor="rgba(6,182,212,0.18)",
            hovertemplate="%{x|%d-%b %H:%M}<br>SOC: %{y:,.0f} MWh<extra></extra>",
        ),
        row=1, col=1,
    )
    # 100 % reference line
    fig.add_hline(
        y=e_max_mwh, line_dash="dot", line_color="rgba(6,182,212,0.55)", row=1, col=1,
        annotation_text=f"Máx {e_max_mwh:,.0f} MWh",
        annotation_position="top right",
        annotation_font_size=11,
    )
    # 50 % reference line
    fig.add_hline(
        y=e_max_mwh * 0.5, line_dash="dot", line_color="rgba(148,163,184,0.5)", row=1, col=1,
        annotation_text="50 %",
        annotation_position="bottom right",
        annotation_font_size=10,
    )

    # ── Row 2: charge / discharge bars ────────────────────────────────────────
    fig.add_trace(
        go.Bar(
            x=p_dispatch.index, y=p_dispatch.values,
            name="Descarga (→ red)",
            marker_color="rgba(6,182,212,0.80)",
            hovertemplate="%{x|%d-%b %H:%M}<br>Descarga: %{y:,.1f} MW<extra></extra>",
        ),
        row=2, col=1,
    )
    fig.add_trace(
        go.Bar(
            x=p_store.index, y=(-p_store).values,
            name="Carga (← red)",
            marker_color="rgba(37,99,235,0.65)",
            hovertemplate="%{x|%d-%b %H:%M}<br>Carga: %{customdata:,.1f} MW<extra></extra>",
            customdata=p_store.values,
        ),
        row=2, col=1,
    )

    fig.update_layout(
        height=480,
        barmode="relative",
        legend=dict(orientation="h", y=-0.12, font=dict(size=11)),
        margin=dict(l=0, r=0, t=36, b=60),
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="MWh", row=1, col=1)
    fig.update_yaxes(title_text="MW",  row=2, col=1, zeroline=True,
                     zerolinecolor="rgba(100,100,100,0.3)")

    st.plotly_chart(fig, width='stretch')


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

        battery_soc_chart(s)

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
            pie_cols[idx].plotly_chart(fig_pie, width='stretch')

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
            st.plotly_chart(fig_sp, width='stretch')

    # ── Scenario comparison ────────────────────────────────────────────────────
    st.subheader("Comparativa de escenarios")
    st.caption("Corre todos los escenarios con el mismo período de demanda y compara métricas clave.")

    _cmp_btn = st.button("⚡ Comparar todos los escenarios", type="secondary")
    if _cmp_btn:
        _cmp_rows: dict[str, dict] = {}
        _cmp_prog = st.progress(0, text="Iniciando…")
        for _si, (_skey, _sval) in enumerate(SCENARIOS.items()):
            _cmp_prog.progress((_si) / len(SCENARIOS), text=f"Optimizando: {_skey}…")
            try:
                _sp = _sval["params"]
                _sc = compute_effective_costs(_sp)
                _sdm = _sp.get("demand_multiplier", {})
                _sdm = {k: v for k, v in _sdm.items() if v != 1.0} or None
                _scm = _sp.get("capacity_multiplier", {}) or None
                _sfo = _sp.get("forced_outage", {})
                _sfo = _sfo if _sfo.get("enabled", False) else None
                _sbat = _sp if _sp.get("battery_enable", False) else None
                _s_voll = float(_sp.get("voll_value", voll_input))
                _sn = build_and_solve(
                    centrales_base.copy(), p_max_pu_raw, dem_z,
                    _sc, growth_2026, _s_voll,
                    demand_mult=_sdm, capacity_mult=_scm,
                    forced_outage=_sfo, battery_config=_sbat,
                )
                _cmp_rows[_skey] = extract_metrics(_sn)
            except Exception as _ex:
                _cmp_rows[_skey] = {"error": str(_ex)}
        _cmp_prog.progress(1.0, text="Listo.")
        st.session_state["scenario_comparison"] = _cmp_rows

    if "scenario_comparison" in st.session_state:
        _cmp_data = st.session_state["scenario_comparison"]
        _valid = {k: v for k, v in _cmp_data.items() if "error" not in v}
        _failed = {k: v for k, v in _cmp_data.items() if "error" in v}

        if _valid:
            _cmp_df = pd.DataFrame(_valid).T
            _cmp_df.index.name = "Escenario"

            # Highlight: best value per column (green = better)
            _better_low  = {"Costo total ($M)", "CO₂ (MtCO₂)", "Intensidad (gCO₂/kWh)", "Curtailment (GWh)", "Shedding (MWh)", "Precio med. ($/MWh)"}
            _better_high = {"% Renovable"}

            def _style_col(col: pd.Series) -> list[str]:
                if col.name in _better_low:
                    best = col.min()
                    return ["background-color:#14532d; color:white; font-weight:bold" if v == best else "" for v in col]
                elif col.name in _better_high:
                    best = col.max()
                    return ["background-color:#14532d; color:white; font-weight:bold" if v == best else "" for v in col]
                return [""] * len(col)

            st.dataframe(
                _cmp_df.style
                    .apply(_style_col, axis=0)
                    .format({
                        "Costo total ($M)":      "{:,.2f}",
                        "CO₂ (MtCO₂)":          "{:.4f}",
                        "Intensidad (gCO₂/kWh)": "{:.0f}",
                        "% Renovable":           "{:.1f}%",
                        "Curtailment (GWh)":     "{:.1f}",
                        "Shedding (MWh)":        "{:,.0f}",
                        "Precio med. ($/MWh)":   "{:.1f}",
                    }),
                width='stretch',
                height=310,
            )
            st.caption("Verde = mejor valor en esa columna.")

            # Bar charts: cost + CO2 + renewable share
            _metrics_to_plot = [
                ("Costo total ($M)",  "Costo total ($M)", "#2563EB"),
                ("CO₂ (MtCO₂)",      "CO₂ (MtCO₂)",      "#EF4444"),
                ("% Renovable",       "% Renovable",       "#10B981"),
            ]
            _bar_cols = st.columns(3)
            for _bi, (_mkey, _mlabel, _mcolor) in enumerate(_metrics_to_plot):
                _fig_bar = go.Figure(go.Bar(
                    x=list(_valid.keys()),
                    y=[_valid[s].get(_mkey, 0) for s in _valid],
                    marker_color=_mcolor,
                    text=[f"{_valid[s].get(_mkey, 0):.2f}" for s in _valid],
                    textposition="auto",
                ))
                _fig_bar.update_layout(
                    title=_mlabel, height=300,
                    margin=dict(l=0, r=0, t=36, b=0),
                    xaxis=dict(tickangle=-30, tickfont=dict(size=9)),
                )
                _bar_cols[_bi].plotly_chart(_fig_bar, width='stretch')

        if _failed:
            st.warning(f"Escenarios con error: {', '.join(_failed.keys())}")

    # ── Reserve margin ────────────────────────────────────────────────────────
    st.subheader("Margen de reserva por sistema")
    _rm_cols = st.columns(len(SISTEMAS))
    for _i, _s in enumerate(SISTEMAS):
        _bus_gens_s = gen_info[
            (gen_info["bus"] == _s) & (~gen_info.index.str.startswith("VoLL_"))
        ].index.tolist()
        _cap_mw = gen_info.loc[_bus_gens_s, "p_nom"].sum() if _bus_gens_s else 0.0
        _load_col_rm = f"load_{_s}"
        _load_s = n.loads_t.p_set[[_load_col_rm]] if _load_col_rm in n.loads_t.p_set.columns else pd.DataFrame()
        _peak_mw = _load_s.sum(axis=1).max() if not _load_s.empty else 0.0
        _rm = ((_cap_mw - _peak_mw) / _peak_mw * 100) if _peak_mw > 0 else float("nan")
        _color = "normal" if _rm >= 20 else ("off" if _rm < 10 else "inverse")
        _rm_cols[_i].metric(
            label=f"Margen {_s}",
            value=f"{_rm:.1f}%" if not pd.isna(_rm) else "N/D",
            delta=f"Cap {_cap_mw:,.0f} MW | Pico {_peak_mw:,.0f} MW",
            delta_color="off",
        )
    st.caption("Margen de reserva = (capacidad instalada − pico de demanda) / pico de demanda. Referencia mínima: 20%.")

    # ── CO₂ emissions ─────────────────────────────────────────────────────────
    st.subheader("Emisiones de CO₂ estimadas")
    _non_voll = [g for g in dispatch.columns if not g.startswith("VoLL_")]
    _gen_mwh_co2 = dispatch[_non_voll].sum()
    _co2_by_gen = pd.Series({
        g: _gen_mwh_co2[g] * CO2_FACTOR.get(gen_info.loc[g, "carrier"], 0.0)
        for g in _non_voll if g in gen_info.index
    })
    _co2_by_carrier = _co2_by_gen.groupby(
        gen_info.loc[_co2_by_gen.index, "carrier"]
    ).sum().sort_values(ascending=False)
    _total_co2 = _co2_by_carrier.sum()
    _total_mwh = _gen_mwh_co2.sum()
    _intensity  = _total_co2 / _total_mwh * 1000 if _total_mwh > 0 else 0  # gCO₂/kWh

    _co2m1, _co2m2, _co2m3 = st.columns(3)
    _co2m1.metric("Total CO₂", f"{_total_co2/1e6:.3f} MtCO₂")
    _co2m2.metric("Intensidad carbónica", f"{_intensity:.0f} gCO₂/kWh")
    _co2m3.metric("Sin emisiones (VRE+hidro)", f"{_co2_by_carrier.get('solar', 0) + _co2_by_carrier.get('onwind', 0):.0f} tCO₂")

    _co2_df = pd.DataFrame({
        "Tecnología": [CARRIER_LABELS.get(c, c) for c in _co2_by_carrier.index],
        "tCO₂": _co2_by_carrier.values.round(1),
        "Factor (tCO₂/MWh)": [CO2_FACTOR.get(c, 0.0) for c in _co2_by_carrier.index],
    }).set_index("Tecnología")
    _co2_df = _co2_df[_co2_df["tCO₂"] > 0.1]
    if not _co2_df.empty:
        st.dataframe(
            _co2_df.style.format({"tCO₂": "{:,.1f}", "Factor (tCO₂/MWh)": "{:.3f}"}),
            height=280, width='stretch',
        )
    st.caption("Factores de emisión orientativos (IPCC AR6 + CFE). No incluyen emisiones de ciclo de vida.")

    # ── Price & Load Duration Curves ──────────────────────────────────────────
    st.subheader("Curvas de duración de precio y carga")
    _pdc_tabs = st.tabs([f"PDC / LDC — {s}" for s in SISTEMAS])
    for _ti, _s in enumerate(SISTEMAS):
        with _pdc_tabs[_ti]:
            _sp_s = shadow_prices[_s] if (not shadow_prices.empty and _s in shadow_prices.columns) else pd.Series(dtype=float)
            _load_col_ldc = f"load_{_s}"
            _load_s = (
                n.loads_t.p_set[[_load_col_ldc]].sum(axis=1)
                if _load_col_ldc in n.loads_t.p_set.columns
                else pd.Series(dtype=float)
            )

            _fig_dur = make_subplots(
                rows=1, cols=2,
                subplot_titles=["Price Duration Curve (PDC)", "Load Duration Curve (LDC)"],
                horizontal_spacing=0.10,
            )

            if not _sp_s.empty:
                _pdc_sorted = _sp_s.sort_values(ascending=False).values
                _fig_dur.add_trace(
                    go.Scatter(
                        x=list(range(1, len(_pdc_sorted) + 1)),
                        y=_pdc_sorted,
                        mode="lines",
                        name="Precio marginal",
                        fill="tozeroy",
                        line=dict(color=SYSTEM_COLORS.get(_s, "#888"), width=1.5),
                        hovertemplate="Hora %{x}: %{y:.0f} $/MWh<extra></extra>",
                    ),
                    row=1, col=1,
                )
                _fig_dur.update_xaxes(title_text="Horas (ordenadas)", row=1, col=1)
                _fig_dur.update_yaxes(title_text="$/MWh", row=1, col=1)

            if not _load_s.empty:
                _ldc_sorted = _load_s.sort_values(ascending=False).values
                _fig_dur.add_trace(
                    go.Scatter(
                        x=list(range(1, len(_ldc_sorted) + 1)),
                        y=_ldc_sorted,
                        mode="lines",
                        name="Demanda MW",
                        fill="tozeroy",
                        line=dict(color="#64748B", width=1.5),
                        hovertemplate="Hora %{x}: %{y:,.0f} MW<extra></extra>",
                    ),
                    row=1, col=2,
                )
                _fig_dur.update_xaxes(title_text="Horas (ordenadas)", row=1, col=2)
                _fig_dur.update_yaxes(title_text="MW", row=1, col=2)

            _fig_dur.update_layout(
                height=360,
                margin=dict(l=0, r=0, t=40, b=0),
                showlegend=False,
            )
            st.plotly_chart(_fig_dur, width='stretch')
            if not _sp_s.empty:
                _pct_zero = (_sp_s == 0).mean() * 100
                _voll_solved = n.generators.loc[
                    n.generators.index.str.startswith("VoLL_"), "marginal_cost"
                ].max()
                if pd.isna(_voll_solved):
                    _voll_solved = float(voll_input)
                _pct_voll = (_sp_s >= _voll_solved * 0.99).mean() * 100
                st.caption(
                    f"Horas con precio = 0 $/MWh (exceso renovable): **{_pct_zero:.1f}%**  |  "
                    f"Horas con precio ≥ VoLL (escasez): **{_pct_voll:.1f}%**"
                )

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
        width='stretch',
        height=420,
    )

    # ── Diagnóstico: generadores sin perfil ──────────────────────────────────
    with st.expander("🔍 Diagnóstico: generadores sin perfil horario", expanded=False):
        _all_gens = [g for g in n.generators.index if not g.startswith("VoLL_")]
        _explicit_profile_cols = p_max_pu_raw.columns.tolist()
        _missing_prof = [g for g in _all_gens if g not in _explicit_profile_cols]
        st.write(f"Generadores sin perfil explícito en el CSV: **{len(_missing_prof)}** de {len(_all_gens)}")
        st.caption(
            "Estos generadores usan disponibilidad por defecto según su categoría operativa "
            "(VRE=0, hidro/parcial, inflexibles<1, térmicas=1)."
        )
        if _missing_prof:
            st.dataframe(
                gen_info.loc[[g for g in _missing_prof if g in gen_info.index], ["bus", "carrier", "p_nom"]].head(40),
                width='stretch',
            )

    # ── Generador marginal por hora y sistema ────────────────────────────────
    with st.expander("📌 Candidato a unidad marginal", expanded=False):
        st.caption(
            "El precio marginal en un LP puede surgir de varias restricciones activas simultáneamente. "
            "Por ello, esta tabla identifica una unidad marginal plausible, "
            "no necesariamente una unidad marginal única exacta."
        )
        for _ps in SISTEMAS:
            _ps_df = identify_marginal_generator(n, _ps, tol_mw=1.0, tol_price=2.0)
            if not _ps_df.empty:
                st.markdown(f"**{_ps}** — carriers más frecuentes como marginal plausible:")
                _most_common = _ps_df["carrier"].fillna("N/D").value_counts().head(5)
                st.dataframe(_most_common.rename("horas"), width='content')
                with st.expander(f"Ver tabla completa {_ps}", expanded=False):
                    st.dataframe(
                        _ps_df.style.format({
                            "shadow_price": "{:.1f}",
                            "CV ($/MWh)": "{:.1f}",
                            "dispatch_MW": "{:.1f}",
                            "pmin_MW": "{:.1f}",
                            "pmax_MW": "{:.1f}",
                        }),
                        width='stretch',
                        height=320,
                    )

    # Downloads
    st.subheader("Descargas")
    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)

    dl1, dl2, dl3, dl4 = st.columns(4)
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
    if not n.storage_units_t.state_of_charge.empty:
        dl4.download_button(
            "📥 SOC baterías (CSV)",
            data=n.storage_units_t.state_of_charge.to_csv().encode("utf-8"),
            file_name="battery_soc.csv", mime="text/csv",
        )
