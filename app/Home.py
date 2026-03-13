from __future__ import annotations

from pathlib import Path
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Despacho Económico MX", layout="wide", page_icon="⚡")

st.markdown(
    """
<style>
.block-container { padding-top: 1.6rem; padding-bottom: 2rem; max-width: 1200px; }
h1, h2, h3 { letter-spacing: -0.4px; }
hr { border: none; border-top: 1px solid rgba(49,51,63,0.10); }

.hero {
  border: 1px solid rgba(49,51,63,0.12);
  border-radius: 22px;
  padding: 24px 28px;
  background: linear-gradient(135deg, rgba(37,99,235,0.06), rgba(22,163,74,0.05));
  margin-bottom: 1rem;
}
.hero-title { font-size: 2.1rem; font-weight: 700; margin: 0; letter-spacing: -0.8px; }
.hero-sub   { color: rgba(49,51,63,0.68); margin-top: 8px; font-size: 1.0rem; line-height: 1.5; }
.pills      { margin-top: 16px; display: flex; flex-wrap: wrap; gap: 8px; }
.pill {
  display: inline-flex; align-items: center; gap: 6px;
  border: 1px solid rgba(49,51,63,0.14); border-radius: 999px;
  padding: 5px 12px; font-size: 0.83rem;
  background: rgba(255,255,255,0.75);
}

.card {
  border: 1px solid rgba(49,51,63,0.11);
  border-radius: 18px;
  padding: 18px 20px;
  background: rgba(255,255,255,0.70);
  margin-bottom: 1rem;
}
.card h3 { margin: 0 0 0.5rem 0; font-size: 1.05rem; }
.card p, .card li { font-size: 0.95rem; line-height: 1.5; color: rgba(49,51,63,0.85); }

.kpi-wrap [data-testid="stMetric"] {
  border: 1px solid rgba(49,51,63,0.09);
  border-radius: 16px;
  padding: 10px 14px;
  background: rgba(255,255,255,0.70);
}
.kpi-wrap [data-testid="stMetricLabel"] { color: rgba(49,51,63,0.68); font-size: 0.88rem; }

.stButton > button { border-radius: 14px !important; padding: 0.6rem 0.9rem !important; }
.spacer { height: 12px; }
</style>
    """,
    unsafe_allow_html=True,
)

ROOT       = Path(__file__).resolve().parents[1]
GEN_CSV    = ROOT / "data_clean" / "generators" / "Centrales_gen_mx.csv"
DEMAND_DIR = ROOT / "data_raw" / "demand" / "balance_2026"


@st.cache_data(show_spinner=False)
def _load_gen_stats() -> tuple[int, float, dict[str, float]]:
    df = pd.read_csv(GEN_CSV)
    df["bus"] = df["bus"].astype(str).str.strip().str.upper().replace({"MUGELE": "BCS", "MUG": "BCS", "BSA": "BCA"})
    df = df[df["bus"].isin(["SIN", "BCA", "BCS"])]
    cap_by_bus = df.groupby("bus")["p_nom"].sum().to_dict()
    return len(df), df["p_nom"].sum(), cap_by_bus


@st.cache_data(show_spinner=False)
def _demand_days() -> int:
    if not DEMAND_DIR.exists():
        return 0
    return len(list(DEMAND_DIR.glob("*.csv")))


n_gens, total_cap_mw, cap_by_bus = _load_gen_stats()
n_days = _demand_days()

st.markdown(
    """
<div class="hero">
  <div class="hero-title">⚡ Simulador de Despacho Económico — México</div>
  <div class="hero-sub">
    Optimización LP de costo mínimo para los 3 sistemas eléctricos del SEN.<br>
    Demanda real CENACE 2026 · Capacidad instalada PRODESEN · Solver HiGHS vía PyPSA.
  </div>
  <div class="pills">
    <span class="pill">🗺 3 sistemas: SIN · BCA · BCS</span>
    <span class="pill">📅 Datos 2026</span>
    <span class="pill">🧠 PyPSA + HiGHS</span>
    <span class="pill">⚡ 499 generadores</span>
    <span class="pill">🔋 Baterías + VoLL</span>
  </div>
</div>
    """,
    unsafe_allow_html=True,
)

col_main, col_side = st.columns([2, 1], vertical_alignment="top")

with col_main:
    st.markdown('<div class="kpi-wrap">', unsafe_allow_html=True)
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Sistemas modelados", "3 buses")
    k2.metric("Generadores", f"{n_gens:,}")
    k3.metric("Capacidad instalada", f"{total_cap_mw/1000:.0f} GW")
    k4.metric("Días de demanda disponibles", f"{n_days}")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='spacer'></div>", unsafe_allow_html=True)

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Capacidad instalada por sistema")
    sin_gw  = cap_by_bus.get("SIN", 0) / 1000
    bca_gw  = cap_by_bus.get("BCA", 0) / 1000
    bcs_gw  = cap_by_bus.get("BCS", 0) / 1000
    st.markdown(
        f"""
| Sistema | Capacidad instalada | Descripción |
|---------|-------------------|-------------|
| **SIN** | {sin_gw:.1f} GW | Sistema Interconectado Nacional — cubre la mayor parte del territorio |
| **BCA** | {bca_gw:.1f} GW | Baja California — sistema peninsular norte |
| **BCS** | {bcs_gw:.1f} GW | Baja California Sur — sistema aislado |

Los 3 buses son **independientes** (sin interconexión entre ellos en el modelo).
        """.strip()
    )
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Cómo funciona el modelo")
    st.markdown(
        """
1. **Demanda real** → datos horarios CENACE 2026 (balance por sistema)
2. **Generadores** → 499 plantas con tecnología, capacidad y costo variable (PRODESEN/CFE)
3. **Perfiles horarios** → factor de disponibilidad (p_max_pu) para cada generador
4. **Optimización LP** → PyPSA minimiza el costo total de despacho con HiGHS
5. **Resultados** → mix de generación, precio marginal nodal, curtailment y carga no servida
        """.strip()
    )
    st.markdown("</div>", unsafe_allow_html=True)

with col_side:
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Navegación")
    if st.button("📊 Demanda CENACE", use_container_width=True):
        try:
            st.switch_page("pages/1_Demanda_CENACE.py")
        except Exception:
            st.info("Usa el menú lateral para navegar.")
    st.markdown("<div class='spacer'></div>", unsafe_allow_html=True)
    if st.button("⚡ Despacho PyPSA", use_container_width=True, type="primary"):
        try:
            st.switch_page("pages/2_Despacho_PyPSA.py")
        except Exception:
            st.info("Usa el menú lateral para navegar.")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Orden de mérito (base)")
    st.markdown(
        """
| Tecnología | Costo ($/MWh) |
|---|---|
| Solar / Eólica | 0 |
| Nuclear | 5 |
| Hidro | 8 |
| Geotérmica | 10 |
| Biogás / Biomasa | 15–20 |
| Gas CCGT / CHP | 50 |
| Termoeléctrica | 65 |
| Gas OCGT | 70 |
| Diésel | 100 |
| VoLL (shedding) | 3 000 |
        """.strip()
    )
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown("---")

with st.expander("⚠️ Limitaciones del modelo", expanded=False):
    st.markdown(
        """
**Este modelo es educativo.** Simplifica deliberadamente varios aspectos de la operación real del SEN:

| Aspecto | Lo que hace el modelo | Realidad omitida |
|---|---|---|
| Red de transmisión | 3 buses sin restricciones internas | Cuellos de botella y pérdidas por línea |
| Unit commitment | Despacho continuo (LP) | Arranques/paros, tiempos mínimos, reservas girantes |
| Confiabilidad | Sin criterio N-1 | Reservas operativas y margen de reserva |
| Hidro | Perfil fijo de disponibilidad | Presupuesto diario/semanal de agua en embalse |
| Batería | Sin degradación de ciclos | Vida útil, eficiencia variable, profundidad de descarga |
| Costos variables | Valores educativos aproximados | Precios reales del MEM/CFE no son públicos |
| Perfiles de generación | Datos 2025 aplicados a 2026 | Cambios de capacidad instalada infraanuales |
| Interconexión | SIN, BCA y BCS son aislados | Interconexión SIN-BCA existe (capacidad limitada) |
| Demanda | Balance horario CENACE (sin desagregación nodal) | Flujos internos por nodo de la red |

A pesar de estas simplificaciones, el modelo captura correctamente **la lógica de mérito económico** y produce señales de precio y mezcla de generación consistentes con la teoría.
        """.strip()
    )

with st.expander("📚 Metodología y fuentes de datos", expanded=False):
    st.markdown(
        """
### Stack tecnológico

| Componente | Detalle |
|---|---|
| **PyPSA** | Optimización LP de despacho multi-período |
| **HiGHS** | Solver LP de código abierto (vía `highspy`) |
| **Streamlit** | Interfaz web interactiva |
| **pandas / numpy** | Procesamiento de datos |
| **Plotly** | Visualizaciones interactivas |

### Fuentes de datos

- **Demanda horaria 2026** — CENACE, endpoint público `SIM / obtieneBalanceMasaCarga`. Resolución horaria por sistema (SIN = suma de 7 sub-áreas, BCA, BCS).
- **Capacidad instalada** — PRODESEN 2024 (Programa de Desarrollo del Sistema Eléctrico Nacional). Tabla de centrales en operación y compromisos de capacidad al cierre de 2024.
- **Perfiles de disponibilidad** — Estimados con base en datos históricos de generación CENACE 2025. Normalizados a escala 0–1 por tecnología y hora del año.

### Supuestos clave

1. **Costos variables de referencia** — estimados educativos calibrados al orden de mérito real del SEN. Gas CCGT (~50 $/MWh) es el unit marginal típico en horas de demanda media; vapor/fuel-oil (~65 $/MWh) actúa como respaldo caro.
2. **Factores de disponibilidad** — renovables (solar, eólico): perfil horario con factor 0 en horas sin recurso; plantas térmicas y de base: factor constante 0.85–0.95.
3. **VoLL (Value of Lost Load)** — 3 000 $/MWh como costo de penalización de carga no servida; garantiza que el modelo siempre encuentre solución factible.
4. **Batería de referencia** — 500 MW / 2 000 MWh (4 h), eficiencia de ida y vuelta 92%, costo de ciclo 5 $/MWh. Representativa de proyectos BESS anunciados en México.
5. **Sin transmisión interna** — cada sistema se optimiza de forma independiente; los precios marginales nodales reflejan el costo marginal de satisfacer demanda en cada bus.

### Cómo interpretar el precio marginal

El precio marginal ($/MWh) es el **valor dual** de la restricción de balance de potencia en cada bus. Equivale al costo variable del último generador necesario para cubrir la demanda en esa hora. En horas con alta penetración renovable el precio tiende a cero; en horas pico sube al costo del unit marginal térmico o al VoLL si hay déficit.
        """.strip()
    )
