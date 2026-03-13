# Despacho Económico MX

Simulador de despacho económico para el Sistema Eléctrico Nacional mexicano. Combina datos reales de demanda del CENACE con optimización lineal (PyPSA + HiGHS) para calcular el mix de generación de menor costo en los tres sistemas eléctricos de México: **SIN, BCA y BCS**.

## Características

- **Demanda real** — descarga automática diaria desde la API del CENACE (sistema SIN, BCA, BCS)
- **Optimización LP** — Linear Optimal Power Flow con 499 centrales reales del PRODESEN 2024
- **Perfiles horarios** — 8 760 horas de perfil de generación por tecnología y zona
- **5 escenarios predefinidos** — Base, Gas caro, Renovables gratis, Crisis BCA, Precio al carbono
- **Baterías** — modelado de almacenamiento con ciclos, SOC horario y eficiencia de round-trip
- **Emisiones CO₂** — factor por tecnología × MWh despachados (tCO₂)
- **Curvas de duración** — Price Duration Curve y Load Duration Curve por zona
- **Margen de reserva** — capacidad disponible vs. pico de demanda por bus
- **Precios sombra** — precio marginal horario por zona (señal de congestión/escasez)
- **Tests automatizados** — 15 invariantes de optimización con pytest + CI en GitHub Actions

---

## Arquitectura

```
despacho_economico/
├── app/
│   ├── Home.py                     # Landing: KPIs, limitaciones, metodología
│   ├── pages/
│   │   ├── 1_Demanda_CENACE.py     # Descarga y caché de demanda histórica
│   │   └── 2_Despacho_PyPSA.py    # Optimización y visualizaciones
│   └── lib/
│       ├── cenace_client.py        # Cliente HTTP + caché Parquet
│       ├── demand_pipeline.py      # Carga parquet limpio → DataFrame
│       └── dispatch_model.py       # Construcción de red PyPSA
│
├── scripts/
│   ├── build_historical_demand.py  # CSV raw → parquet limpio
│   └── build_pypsa_network.py      # Red PyPSA + optimización headless
│
├── data_raw/
│   └── demand/balance_2026/        # 42 CSVs diarios CENACE (ene–feb 2026)
│
├── data_clean/
│   ├── demand/                     # Parquet limpio (DatetimeIndex tz-aware)
│   └── generators/
│       ├── Centrales_gen_mx.csv    # 499 centrales: bus, carrier, p_nom, costo
│       └── Perfil_Generaciom.csv   # 8 760 × 499 perfiles horarios 2025→2026
│
├── data_cache/                     # Caché Parquet de la API live CENACE
├── tests/
│   └── test_invariants.py          # 15 tests pytest (balance, SOC, precios…)
├── docs/
│   ├── semana_1.md
│   ├── semana2_pipeline_historico.md
│   └── semana2_pypsa.md
├── .github/workflows/
│   ├── fetch_demand.yml            # Cron diario: descarga CENACE → commit
│   └── tests.yml                   # CI: pytest en cada push
└── requirements.txt
```

**Topología de red**: 3 buses aislados (SIN / BCA / BCS) — sin líneas de transmisión entre sistemas. Interconexión real pendiente para Semana 3+ (SISTRANSEL).

---

## Instalación

```bash
git clone <repo-url>
cd despacho_economico

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Dependencias principales

| Paquete | Rol |
|---------|-----|
| `pypsa` | Modelado de red eléctrica y LOPF |
| `highspy` | Solver HiGHS (open-source, incluido) |
| `linopy` | Backend de modelado lineal para PyPSA |
| `streamlit` | Interfaz web interactiva |
| `plotly` | Gráficas interactivas |
| `pandas` / `pyarrow` | Manejo de datos y Parquet |

---

## Correr la app

```bash
cd despacho_economico
source .venv/bin/activate
streamlit run app/Home.py
```

La app queda en `http://localhost:8501`.

---

## Flujo de datos

```
CENACE API ──► cenace_client.py ──► data_cache/ (Parquet)
                                         │
data_raw/balance_2026/*.csv ─────► build_historical_demand.py
                                         │
                                   data_clean/demand/*.parquet
                                         │
                         Centrales_gen_mx.csv + Perfil_Generaciom.csv
                                         │
                               dispatch_model.py (PyPSA Network)
                                         │
                              HiGHS LOPF → resultados
                                         │
                           2_Despacho_PyPSA.py (Streamlit)
```

---

## Construir el parquet de demanda histórica

```bash
python scripts/build_historical_demand.py \
  --input data_raw/demand/demand_raw.csv \
  --tz America/Mexico_City \
  --name historical_demand \
  --export_csv
```

Salida: `data_clean/demand/historical_demand.parquet`

---

## Correr la optimización headless

```bash
python scripts/build_pypsa_network.py \
  --demand_parquet data_clean/demand/historical_demand.parquet \
  --out_nc data_clean/pypsa_network.nc
```

---

## Tests

```bash
pytest tests/test_invariants.py -v
```

### Invariantes verificados (15 tests)

| Clase | Tests |
|-------|-------|
| `TestPowerBalance` | Balance nodal en red base, con batería, multi-carrier |
| `TestNonNegativeDispatch` | Generadores ≥ 0 MW, batería ≥ 0 MW |
| `TestShadowPrices` | Precio ∈ [0, VoLL], = 0 cuando solar cubre todo |
| `TestBatterySOC` | SOC ∈ [0, e_max], ciclicidad SOC (inicio ≈ fin) |
| `TestMeritOrder` | Más barato despacha más, sin shed con exceso, shed cuando escaso |
| `TestCurtailment` | Curtailment ≥ 0, aparece cuando VRE excede demanda |

---

## Escenarios disponibles

| # | Nombre | Descripción |
|---|--------|-------------|
| 1 | Base 2026 | Costos marginales del PRODESEN sin modificación |
| 2 | Gas caro (×2) | CCGT: 100 $/MWh, OCGT: 140 $/MWh |
| 3 | Renovables gratis | Solar / wind / hidro / geo / bio = 0 $/MWh |
| 4 | Crisis BCA | Gas muy caro en Baja California (+400%) |
| 5 | Precio al carbono | Sobrecosto por tonelada CO₂ en combustibles fósiles |
| 6 | Demanda +20% (crecimiento) | Escenario de crecimiento acelerado 2026 |
| 7 | PRODESEN 2026 (+capacidad) | Capacidad adicional comprometida en el PRODESEN |

---

## Fuentes de datos

| Fuente | Qué provee |
|--------|-----------|
| [CENACE](https://www.cenace.gob.mx) | Demanda horaria en tiempo real (SIN, BCA, BCS) |
| [PRODESEN 2024](https://www.gob.mx/sener/documentos/prodesen) | Capacidad instalada y costos marginales por central |
| Perfiles sintéticos 2025 | Perfiles horarios por tecnología (campana solar, factor de planta) |

---

## Limitaciones del modelo

- Sin transmisión entre zonas (SIN / BCA / BCS aislados)
- Sin unit commitment (arranque/paro de unidades)
- Sin restricciones de rampa
- Sin criterio de seguridad N-1
- Hidro modelada como generador con límite de energía diaria (sin embalse dinámico)
- Costos marginales orientativos (no precios reales de mercado CFE/MEM)
- Perfiles de generación sintéticos (no ERA5/MERRA-2)

---

## CI / CD

| Workflow | Trigger | Qué hace |
|----------|---------|----------|
| `fetch_demand.yml` | Cron 06:00 UTC diario | Descarga demanda CENACE → commit automático |
| `tests.yml` | Push / PR a `main` | `pytest tests/test_invariants.py -v` |

---

## Roadmap

- [ ] Topología real de interconexiones (SISTRANSEL)
- [ ] Perfiles solares/eólicos con ERA5
- [ ] Capacidades reales PRODESEN por zona y año
- [ ] Restricciones de rampa (`ramp_limit_up/down`)
- [ ] Storage de largo plazo (hidro con embalse)
- [ ] Análisis de sensibilidad multi-escenario automatizado
- [ ] Dashboard comparativo entre zonas

---

## Estructura del equipo / créditos

Proyecto académico — Simulación de Despacho Económico para el SEN mexicano.

Herramientas: [PyPSA](https://pypsa.org) · [HiGHS](https://highs.dev) · [Streamlit](https://streamlit.io) · [Plotly](https://plotly.com)
