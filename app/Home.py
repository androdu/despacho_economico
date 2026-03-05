# app/Home.py
from __future__ import annotations

import os
from pathlib import Path
import pandas as pd
import streamlit as st

# -----------------------------
# Config
# -----------------------------
st.set_page_config(page_title="Despacho Económico MX", layout="wide")

# -----------------------------
# UI (solo diseño; no cambia contenido ni lógica)
# -----------------------------
st.markdown(
    """
<style>
/* ===== Layout general ===== */
.block-container { padding-top: 2.0rem; padding-bottom: 2.0rem; max-width: 1200px; }
section[data-testid="stSidebar"] { border-right: 1px solid rgba(49,51,63,0.10); }

/* ===== Tipografía / espaciado ===== */
h1 { margin-bottom: 0.35rem; letter-spacing: -0.6px; }
h2, h3 { letter-spacing: -0.3px; }
[data-testid="stCaptionContainer"] { margin-top: -0.25rem; }
hr { border: none; border-top: 1px solid rgba(49,51,63,0.10); }

/* ===== Hero ===== */
.hero {
  border: 1px solid rgba(49, 51, 63, 0.12);
  border-radius: 22px;
  padding: 20px 20px;
  background: linear-gradient(135deg, rgba(255,255,255,0.85), rgba(255,255,255,0.55));
  backdrop-filter: blur(8px);
}
.hero-title { font-size: 2.0rem; margin: 0; }
.hero-sub { color: rgba(49,51,63,0.70); margin-top: 6px; line-height: 1.35rem; }
.pills { margin-top: 14px; display: flex; flex-wrap: wrap; gap: 10px; }
.pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 1px solid rgba(49, 51, 63, 0.14);
  border-radius: 999px;
  padding: 6px 12px;
  font-size: 0.85rem;
  background: rgba(255,255,255,0.70);
}

/* ===== Cards ===== */
.card {
  border: 1px solid rgba(49, 51, 63, 0.12);
  border-radius: 18px;
  padding: 16px 16px;
  background: rgba(255,255,255,0.72);
  backdrop-filter: blur(6px);
}
.card h3 { margin: 0 0 0.45rem 0; font-size: 1.05rem; }
.card p, .card li { font-size: 0.95rem; line-height: 1.40rem; color: rgba(49,51,63,0.86); }
.muted { color: rgba(49,51,63,0.65); }

/* ===== KPIs (metric) ===== */
.kpi-wrap [data-testid="stMetric"] {
  border: 1px solid rgba(49,51,63,0.10);
  border-radius: 18px;
  padding: 10px 12px;
  background: rgba(255,255,255,0.65);
}
.kpi-wrap [data-testid="stMetricLabel"] { color: rgba(49,51,63,0.72); }

/* ===== Botones ===== */
.stButton > button {
  border-radius: 14px !important;
  padding: 0.65rem 0.9rem !important;
}

/* ===== Separadores suaves ===== */
.spacer { height: 14px; }
</style>
    """,
    unsafe_allow_html=True,
)

ROOT = Path(__file__).resolve().parents[1]  # repo root (…/despacho_economico)
DATA_CLEAN = ROOT / "data_clean"
DEMAND_DIR = DATA_CLEAN / "demand"
GEN_DIR = DATA_CLEAN / "generators"
NETWORK_PATH = ROOT / "pypsa_network.nc"

# Ajusta si tus nombres cambian:
GEN_CSV_CANDIDATES = [
    GEN_DIR / "Basedatos_Plantas.csv",
    GEN_DIR / "BaseDatos_Plantas.csv",
    GEN_DIR / "basedatos_plantas.csv",
]

BUS_LABELS = ["SIN", "BCS", "BCA", "Mulegé"]


# -----------------------------
# Helpers
# -----------------------------
def _find_latest_parquet(folder: Path, prefix: str = "demand_balance", suffix: str = ".parquet") -> Path | None:
    if not folder.exists():
        return None
    files = sorted(folder.glob(f"{prefix}*{suffix}"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


@st.cache_data(show_spinner=False)
def load_demand(parquet_path: Path) -> pd.DataFrame:
    return pd.read_parquet(parquet_path)


@st.cache_data(show_spinner=False)
def load_generators(csv_path: Path) -> pd.DataFrame:
    return pd.read_csv(csv_path)


@st.cache_data(show_spinner=False)
def load_pypsa_network(nc_path: Path):
    # Import local para no romper si pypsa no está en env
    import pypsa  # type: ignore
    return pypsa.Network(str(nc_path))


def nice_int(x) -> str:
    try:
        return f"{int(x):,}".replace(",", " ")
    except Exception:
        return "—"


def nice_float(x, nd=2) -> str:
    try:
        return f"{float(x):,.{nd}f}".replace(",", " ")
    except Exception:
        return "—"


# -----------------------------
# Header / Hero (mismo contenido)
# -----------------------------
st.markdown(
    """
<div class="hero">
  <div class="hero-title">Simulador de Despacho Económico – México</div>
  <div class="hero-sub">Optimización de costo mínimo con PyPSA usando datos tipo CENACE y una red simplificada a 4 buses.</div>
  <div class="pills">
    <span class="pill">⚡ Costo mínimo</span>
    <span class="pill">🧩 4 buses</span>
    <span class="pill">🕒 2025</span>
    <span class="pill">📦 data_clean</span>
    <span class="pill">🧠 PyPSA</span>
  </div>
</div>
    """,
    unsafe_allow_html=True,
)
st.markdown("<div class='spacer'></div>", unsafe_allow_html=True)

# -----------------------------
# Load data
# -----------------------------
latest_demand = _find_latest_parquet(DEMAND_DIR, prefix="demand_balance")

gen_csv = None
for p in GEN_CSV_CANDIDATES:
    if p.exists():
        gen_csv = p
        break

demand_df = None
gen_df = None
net = None

colA, colB = st.columns([2, 1], vertical_alignment="top")

with colA:
    # KPIs row
    k1, k2, k3, k4 = st.columns(4)

    # Demand KPIs
    if latest_demand:
        try:
            demand_df = load_demand(latest_demand)
            snapshots = len(demand_df)
            zones = list(demand_df.columns)
            n_zones = len(zones)
        except Exception:
            snapshots, n_zones = None, None
    else:
        snapshots, n_zones = None, None

    # Generators KPIs
    if gen_csv:
        try:
            gen_df = load_generators(gen_csv)
            # intenta detectar columna de capacidad
            cap_col = None
            for c in ["p_nom", "p_nom_mw", "cap_mw", "capacidad_mw", "capacity_mw", "Pnom"]:
                if c in gen_df.columns:
                    cap_col = c
                    break
            total_cap = gen_df[cap_col].sum() if cap_col else None
            n_gens = len(gen_df)
        except Exception:
            n_gens, total_cap = None, None
    else:
        n_gens, total_cap = None, None

    # PyPSA KPIs (si existe)
    n_buses = None
    n_generators_in_net = None
    total_cap_net = None
    if NETWORK_PATH.exists():
        try:
            net = load_pypsa_network(NETWORK_PATH)
            n_buses = len(net.buses)
            n_generators_in_net = len(net.generators)
            total_cap_net = float(net.generators.p_nom.sum()) if "p_nom" in net.generators.columns else None
        except Exception:
            pass

    # Preferimos métricas del network si están
    buses_to_show = n_buses if n_buses is not None else len(BUS_LABELS)
    gens_to_show = n_generators_in_net if n_generators_in_net is not None else n_gens
    cap_to_show = total_cap_net if total_cap_net is not None else total_cap

    # KPIs (mismo contenido)
    st.markdown('<div class="kpi-wrap">', unsafe_allow_html=True)
    k1.metric("Snapshots cargados", nice_int(snapshots) if snapshots is not None else "—")
    k2.metric("Buses modelados", nice_int(buses_to_show))
    k3.metric("Generadores", nice_int(gens_to_show) if gens_to_show is not None else "—")
    k4.metric("Capacidad total (MW)", nice_float(cap_to_show, nd=1) if cap_to_show is not None else "—")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='spacer'></div>", unsafe_allow_html=True)

    # Alcance del modelo (mismo contenido)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Alcance del modelo")
    st.markdown(
        """
- **Horizonte temporal:** 2025 (según tu configuración del proyecto)  
- **Red simplificada:** 4 buses (**SIN**, **BCS**, **BCA**, **Mulegé**)  
- **Datos:** demanda horaria (parquet) + base de generadores (CSV) + red PyPSA (NC, si aplica)  
- **Método:** optimización de despacho a **costo mínimo** (PyPSA / Linear OPF según tu implementación)  
        """.strip()
    )
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='spacer'></div>", unsafe_allow_html=True)

    # Arquitectura (mismo contenido)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Arquitectura del sistema")
    st.markdown(
        """
**Demanda (CENACE / histórico)** → **Limpieza & estandarización** → **PyPSA Network** → **Optimización** → **Resultados**
- Resultados típicos: **despacho por tecnología**, **costo marginal**, **energía generada**, **emisiones** (si las modelas).
        """.strip()
    )
    st.markdown("</div>", unsafe_allow_html=True)

with colB:
    # Panel lateral (mismo contenido)
    st.markdown('<div class="card">', unsafe_allow_html=True)

    st.subheader("Estado del sistema")

    ok_demand = "✅" if latest_demand else "⚠️"
    ok_gen = "✅" if gen_csv else "⚠️"
    ok_net = "✅" if NETWORK_PATH.exists() else "ℹ️"

    st.markdown(
        f"""
- {ok_demand} **Demanda**: `{latest_demand.name if latest_demand else "No se encontró parquet en data_clean/demand"}`  
- {ok_gen} **Generadores**: `{gen_csv.name if gen_csv else "No se encontró CSV en data_clean/generators"}`  
- {ok_net} **PyPSA network**: `{NETWORK_PATH.name if NETWORK_PATH.exists() else "Aún no existe pypsa_network.nc (opcional)"}`  
        """.strip()
    )

    st.markdown("<div class='spacer'></div>", unsafe_allow_html=True)

    st.subheader("Buses (agrupación)")
    st.write("SIN · BCS · BCA · Mulegé")

    st.caption("Tip: si quieres, aquí luego metemos un mini-mapa o diagrama unifilar simple.")

    st.markdown("<div class='spacer'></div>", unsafe_allow_html=True)

    st.subheader("Navegación rápida")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("📈 Ver Demanda CENACE", use_container_width=True):
            try:
                st.switch_page("pages/1_Demanda_CENACE.py")
            except Exception:
                st.info("No pude cambiar de página (depende de la versión de Streamlit). Ve al menú lateral.")
        if st.button("⚙️ Ejecutar / Ver Despacho PyPSA", use_container_width=True):
            try:
                st.switch_page("pages/2_Despacho_PyPSA.py")
            except Exception:
                st.info("No pude cambiar de página (depende de la versión de Streamlit). Ve al menú lateral.")

    with c2:
        st.button("📊 Resultados (próximamente)", use_container_width=True, disabled=True)
        st.button("🧾 Documentación (próximamente)", use_container_width=True, disabled=True)

    st.markdown("</div>", unsafe_allow_html=True)

# -----------------------------
# Optional: expandable technical details
# -----------------------------
with st.expander("Ver detalles técnicos de carga (debug)", expanded=False):
    st.write("Repo root:", str(ROOT))
    st.write("Demand dir:", str(DEMAND_DIR))
    st.write("Generators dir:", str(GEN_DIR))
    st.write("Network path:", str(NETWORK_PATH))

    if demand_df is not None:
        st.write("Demand shape:", demand_df.shape)
        st.dataframe(demand_df.head(10), use_container_width=True)

    if gen_df is not None:
        st.write("Generators shape:", gen_df.shape)
        st.dataframe(gen_df.head(10), use_container_width=True)

    if net is not None:
        st.write("Network buses:", len(net.buses))
        st.write("Network generators:", len(net.generators))
        st.write("Network loads:", len(net.loads))