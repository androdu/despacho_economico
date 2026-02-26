# Pipeline Histórico de Demanda — Semana 2

## Objetivo

Transformar datos de demanda en formato crudo (descarga manual, CENACE, scraping) a un
parquet limpio y listo para PyPSA.

---

## Entradas (`data_raw/demand/`)

| Formato | Columnas mínimas requeridas |
|---------|----------------------------|
| `.csv`  | `timestamp` (o alias), `zone` (o alias), `demand_mw` (o alias) |
| `.parquet` / `.pq` | ídem |

### Aliases reconocidos automáticamente

| Campo estándar | Nombres aceptados |
|----------------|-------------------|
| `timestamp`    | `fecha`, `datetime`, `timestamp`, `date_time`, `time` |
| `zone`         | `zona`, `region`, `area`, `sistema` |
| `demand_mw`    | `demanda`, `mw`, `demand_mw`, `load_mw` |

> Si el timestamp viene como índice (no como columna) y su nombre contiene "time",
> el pipeline lo detecta y lo mueve a columna automáticamente.

---

## Transformaciones y validaciones

### 1. Estandarización de columnas
- Renombra aliases → nombres canónicos.
- Falla explícitamente si faltan `timestamp`, `zone` o `demand_mw`.

### 2. Limpieza de tipos
- `timestamp` → `pd.to_datetime(errors="coerce")` — valores no parseables se convierten a `NaT`.
- `demand_mw` → `pd.to_numeric(errors="coerce")` — valores no numéricos se convierten a `NaN`.
- `zone` → `str.strip().str.upper()` — normaliza espacios y mayúsculas.
- Filas con `NaT` o `NaN` en columnas clave se eliminan.

### 3. Timezone
- **Naive → aware**: `tz_localize(tz, nonexistent="shift_forward", ambiguous="NaT")`
  - `nonexistent="shift_forward"` maneja la transición DST (hora de verano).
  - `ambiguous="NaT"` elimina la hora ambigua al retroceder el reloj.
- **Aware → aware**: `tz_convert(tz)` si ya tiene timezone.
- Zona horaria por defecto: `America/Mexico_City`.

### 4. Duplicados
- Se eliminan duplicados exactos en `(timestamp, zone)`, conservando la última ocurrencia.

### 5. Frecuencia horaria
- `pivot → wide` (index=tiempo, columns=zonas, values=MW).
- `wide.asfreq("h")` fuerza frecuencia horaria; horas faltantes quedan como `NaN`
  (no se imputan aquí — decisión del usuario aguas abajo).

---

## Salidas (`data_clean/demand/`)

| Archivo | Descripción |
|---------|-------------|
| `{name}.parquet` | Wide DataFrame: `DatetimeIndex` tz-aware × columnas = zonas (MW) |
| `{name}.csv` | Mismo contenido en CSV (opcional, `--export_csv`) |

### Ejemplo de estructura del parquet limpio

```
timestamp (index, tz=America/Mexico_City)  |  SIN   |  BCA   |  BCS
2024-01-01 00:00:00-06:00                  | 21340.0| 1850.0 | 320.0
2024-01-01 01:00:00-06:00                  | 20910.0| 1810.0 | 315.0
...
```

---

## Cómo correr

```bash
# CSV raw → parquet limpio + CSV limpio
python scripts/build_historical_demand.py \
  --input data_raw/demand/demand_raw.csv \
  --tz America/Mexico_City \
  --name historical_demand \
  --export_csv
```

**Salida esperada:**
```
OK -> data_clean/demand/historical_demand.parquet (shape=(8760, 3))
Zonas: ['BCA', 'BCS', 'SIN']
```

---

## Qué hacer con los NaN restantes

El pipeline NO imputa horas faltantes. Opciones aguas abajo:

| Estrategia | Cuándo usarla |
|------------|---------------|
| `dropna()` | Pocas horas faltantes, no importa perder snapshots |
| `interpolate("time")` | Huecos cortos (≤ 3 h), demanda suave |
| `fillna(method="ffill")` | Última observación conocida (operación en tiempo real) |

---

## Archivos relevantes

- `scripts/build_historical_demand.py` — script principal
- `scripts/utils_io.py` — helpers de I/O (parquet, CSV, dirs)
- `app/lib/demand_pipeline.py` — carga el parquet desde la app Streamlit
