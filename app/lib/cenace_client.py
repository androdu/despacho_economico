from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import requests


CENACE_URL = "https://www.cenace.gob.mx/GraficaDemanda.aspx/obtieneValoresTotal"

# Mapeo de sistema a gerencia (número que espera la API de CENACE)
SISTEMA_TO_GERENCIA = {
    "SIN": "10",
    "BCA": "1",
    "BCS": "2",
}

# Cache en disco (raíz del repo /data_cache)
REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)


@dataclass
class FetchResult:
    df: pd.DataFrame
    from_cache: bool
    batches: int


def _cache_path(system: str) -> Path:
    today = date.today().isoformat()
    key = hashlib.md5(f"{system}|{today}".encode()).hexdigest()
    return CACHE_DIR / f"demanda_{system}_{key}.parquet"


def fetch_demand(
    system: str = "SIN",
    use_cache: bool = True,
    timeout: int = 30,
    allow_mock_on_error: bool = True,
) -> FetchResult:
    """
    Descarga la demanda del día actual desde CENACE.
    La API obtieneValoresTotal solo devuelve datos del día en curso.
    - system: "SIN", "BCA" o "BCS"
    """
    gerencia = SISTEMA_TO_GERENCIA.get(system, "10")

    cache_file = _cache_path(system)
    if use_cache and cache_file.exists():
        df = pd.read_parquet(cache_file)
        return FetchResult(df=df, from_cache=True, batches=0)

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://www.cenace.gob.mx",
        "Referer": "https://www.cenace.gob.mx/GraficaDemanda.aspx",
        "X-Requested-With": "XMLHttpRequest",
    }

    try:
        r = requests.post(
            CENACE_URL,
            headers=headers,
            data=f'{{"gerencia":"{gerencia}"}}',
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()

        # La respuesta viene en data["d"] como string JSON
        if isinstance(data, dict) and "d" in data:
            inner = data["d"]
            if isinstance(inner, str):
                inner = json.loads(inner)
            data = inner

        df = pd.DataFrame(data)

        # Renombrar columnas al esquema canónico
        rename = {
            "hora": "hora",
            "valorDemanda": "demanda_mw",
            "valorGeneracion": "generacion_mw",
            "valorPronostico": "pronostico_mw",
        }
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

        # Convertir a numérico (CENACE envía strings)
        for col in ["demanda_mw", "generacion_mw", "pronostico_mw"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Ordenar por hora + timestamp
        if "hora" in df.columns:
            df["hora"] = pd.to_numeric(df["hora"], errors="coerce")
            df = df.dropna(subset=["hora"]).sort_values("hora").reset_index(drop=True)

            # Crear timestamp del día actual + hora (para graficar / PyPSA)
            df["fecha"] = date.today().isoformat()
            df["timestamp"] = pd.to_datetime(df["fecha"]) + pd.to_timedelta(df["hora"] - 1, unit="h")
        else:
            # Por si cambia el schema de CENACE
            df["fecha"] = date.today().isoformat()
    except Exception:
        if allow_mock_on_error:
            df = pd.DataFrame({
                "hora": range(1, 25),
                "demanda_mw": [0.0] * 24,
                "generacion_mw": [0.0] * 24,
                "pronostico_mw": [0.0] * 24,
            })
            df["fecha"] = date.today().isoformat()
            df["timestamp"] = pd.to_datetime(df["fecha"]) + pd.to_timedelta(df["hora"] - 1, unit="h")
        else:
            raise

    if use_cache:
        try:
            df.to_parquet(cache_file, index=False)
        except Exception:
            pass

    return FetchResult(df=df, from_cache=False, batches=1)