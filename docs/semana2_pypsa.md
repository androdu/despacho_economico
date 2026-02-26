# Red PyPSA y Despacho Económico — Semana 2

## Objetivo

Construir una red eléctrica simplificada con PyPSA, resolverla con HiGHS (LOPF) y obtener
el despacho óptimo por tecnología para las zonas del SIN mexicano.

---

## Descripción del Network

### Buses

Un bus por zona de demanda (columnas del parquet limpio).
Ejemplo con 3 sistemas CENACE:

| Bus | Zona |
|-----|------|
| `SIN` | Sistema Interconectado Nacional |
| `BCA` | Baja California |
| `BCS` | Baja California Sur |

### Loads

Un componente `Load` por zona, con serie temporal `p_set` tomada directamente del parquet
limpio (`DatetimeIndex` × zonas en MW).

### Generadores

Por cada zona se crean 3 generadores con supuestos de **Semana 2** (dummy):

| Tipo   | `p_nom`           | `marginal_cost` | Perfil `p_max_pu` |
|--------|-------------------|-----------------|-------------------|
| `gas`  | 1.5 × pico zonal  | 1 200 MXN/MWh   | 1.0 (constante)   |
| `solar`| 0.6 × pico zonal  | 50 MXN/MWh      | Campana 07–18 h   |
| `wind` | 0.4 × pico zonal  | 30 MXN/MWh      | 0.6 (constante)   |

> **Supuesto de capacidad**: 1.5× el pico histórico garantiza factibilidad sin importar
> la demanda. Reemplazar con capacidades reales del PRODESEN en semanas posteriores.

> **Supuesto de costos marginales**: Valores orientativos en MXN/MWh.
> El orden de mérito (wind < solar < gas) es correcto; los absolutos son ficticios.

#### Perfil solar simplificado

```
p_max_pu = 0.0   para hora < 7 o hora > 18
p_max_pu = 0.5   para hora == 7 o hora == 18
p_max_pu = 1.0   para 8 ≤ hora ≤ 17
```

### Líneas (interconexiones)

Se conectan las zonas **en cadena** (lista ordenada de buses) con parámetros dummy:

| Parámetro | Valor | Nota |
|-----------|-------|------|
| `x` | 0.0001 pu | Reactancia dummy — no afecta LOPF sin KVL |
| `r` | 0.00001 pu | Resistencia dummy |
| `s_nom` | 5 000 MW | Capacidad holgada (no limitante en Semana 2) |

> Solo se agregan líneas si hay ≥ 2 zonas. La topología real SIN/BCA/BCS
> requiere datos del CENACE (SISTRANSEL) — pendiente Semana 3+.

---

## Formulación del problema

PyPSA resuelve un **LOPF** (Linear Optimal Power Flow) sin restricciones de red
(al tener reactancias dummy y `s_nom` holgado, el flujo DC no es limitante):

```
min  Σ_t Σ_g  marginal_cost_g · p_{g,t}

s.t. Σ_g p_{g,t} = load_t          ∀ t  (balance nodal)
     0 ≤ p_{g,t} ≤ p_nom_g · p_max_pu_{g,t}  ∀ g, t
```

Solver: **HiGHS** (open-source, incluido en `highspy`).

---

## Salidas

### Archivos

| Archivo | Descripción |
|---------|-------------|
| `data_clean/pypsa_network.nc` | Red completa con resultados (NetCDF4) |

### Métricas en consola / Streamlit

| Métrica | Cómo se obtiene |
|---------|----------------|
| Costo total (MXN) | `n.objective` |
| Generación por tipo (MWh) | `n.generators_t.p.sum()` agrupado por prefijo |
| Curtailment por tipo (MWh) | `(p_nom × p_max_pu − p).clip(0).sum()` |

#### Curtailment

Energía disponible que el optimizador **no despacha** porque ya se cubre la demanda
con fuentes más baratas.

```
curtailment_{g,t} = p_nom_g · p_max_pu_{g,t} − p_{g,t}   (≥ 0)
```

Curtailment alto en solar/wind indica que la capacidad supera la demanda local
y no hay red para exportar (o la red no tiene capacidad suficiente).

---

## Gráficas clave (Streamlit — página 2_Despacho_PyPSA)

### 1. Demanda histórica por zona
Serie temporal de MW por zona. Muestra estacionalidad, picos diarios y diferencias
entre sistemas.

### 2. Despacho en el tiempo
Generación (MW) de todos los generadores en el horizonte. Permite ver:
- Cuándo entra el gas (horas nocturnas / pico sin sol).
- El perfil de solar siguiendo la campana diurna.
- La generación base de wind.

### 3. Generación total por tipo (MWh)
Barras o tabla comparando gas / solar / wind en energía total.
Indica el mix de generación del período.

---

## Cómo correr

```bash
# Requiere historical_demand.parquet generado previamente
python scripts/build_pypsa_network.py \
  --demand_parquet data_clean/demand/historical_demand.parquet \
  --out_nc data_clean/pypsa_network.nc
```

O desde Streamlit: página **Despacho PyPSA** → botón **▶ Run PyPSA**.

---

## Pendientes para Semana 3+

- [ ] Reemplazar capacidades dummy con datos reales del PRODESEN.
- [ ] Perfiles de solar/wind con datos de ERA5 o MERRA-2.
- [ ] Topología real de interconexiones (SISTRANSEL).
- [ ] Costos marginales reales por tecnología y zona.
- [ ] Restricciones de rampa (`ramp_limit_up/down`).
- [ ] Storage (BESS, hidro con embalse).

---

## Archivos relevantes

- `scripts/build_pypsa_network.py` — construcción de red + optimización
- `app/pages/2_Despacho_PyPSA.py` — visualización en Streamlit
- `data_clean/pypsa_network.nc` — red exportada (generado en runtime)
