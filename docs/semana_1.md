# Semana 1 — Setup + Framing

## Qué se logró
- Entorno local con `venv`.
- App Streamlit base con navegación.
- Cliente CENACE operativo para el día en curso (SIN/BCA/BCS) usando el endpoint `obtieneValoresTotal`.
- Caché en disco por día y sistema.
- Visualización (tabla + gráfica) y descarga CSV desde la app.

## Arquitectura (alto nivel)
- Streamlit:
  - `app/Home.py` (home)
  - `app/pages/1_Demanda_CENACE.py` (UI de demanda)
- Lógica:
  - `app/lib/cenace_client.py` contiene `fetch_demand()`:
    - arma headers/payload
    - hace POST a CENACE
    - normaliza columnas (`hora`, `demanda_mw`, `generacion_mw`, `pronostico_mw`)
    - agrega `fecha` y `timestamp`
    - guarda/lee caché `.parquet` en `data_cache/`

## Limitación identificada
El endpoint usado (`obtieneValoresTotal`) solo entrega la jornada en curso (no permite rangos históricos).
Se planifica incorporar un dataset histórico (“golden week”) o un endpoint alterno para cumplir batching ≤ 7 días y validaciones DST.

## Próximos pasos (Semana 2)
- Implementar modo “Rango histórico”.
- Batching automático (≤ 7 días).
- Validación temporal: huecos/duplicados (DST).
- Export canónico a `data_clean/` para alimentar PyPSA.