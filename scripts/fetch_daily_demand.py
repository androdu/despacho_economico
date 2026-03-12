"""
fetch_daily_demand.py
---------------------
Descarga la demanda del día actual (o una fecha específica) desde la API
de CENACE para los 3 sistemas (SIN, BCA, BCS) y guarda el resultado en
data_raw/demand/daily_api/demand_YYYY-MM-DD.csv

Formato de salida: snapshot (datetime), zona (SIN/BCA/BCS), demand_mw (float)
Compatible con load_demand_raw() de 2_Despacho_PyPSA.py

Uso:
    python scripts/fetch_daily_demand.py              # → hoy
    python scripts/fetch_daily_demand.py --date 2026-03-11  # → fecha específica
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data_raw" / "demand" / "daily_api"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CENACE_URL = "https://www.cenace.gob.mx/GraficaDemanda.aspx/obtieneValoresTotal"
SISTEMA_TO_GERENCIA = {"SIN": "10", "BCA": "1", "BCS": "2"}
SISTEMAS = ["SIN", "BCA", "BCS"]


def fetch_sistema(sistema: str, target_date: date, timeout: int = 30) -> pd.DataFrame:
    gerencia = SISTEMA_TO_GERENCIA[sistema]
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://www.cenace.gob.mx",
        "Referer": "https://www.cenace.gob.mx/GraficaDemanda.aspx",
        "X-Requested-With": "XMLHttpRequest",
    }
    r = requests.post(
        CENACE_URL,
        headers=headers,
        data=f'{{"gerencia":"{gerencia}"}}',
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()

    if isinstance(data, dict) and "d" in data:
        inner = data["d"]
        if isinstance(inner, str):
            inner = json.loads(inner)
        data = inner

    df = pd.DataFrame(data)

    # Renombrar columnas
    rename = {"hora": "hora", "valorDemanda": "demanda_mw"}
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    df["hora"] = pd.to_numeric(df.get("hora", pd.Series(dtype=float)), errors="coerce")
    df["demanda_mw"] = pd.to_numeric(df.get("demanda_mw", pd.Series(dtype=float)), errors="coerce")
    df = df.dropna(subset=["hora", "demanda_mw"])
    df = df.sort_values("hora").reset_index(drop=True)

    op_date = pd.Timestamp(target_date)
    df["snapshot"] = op_date + pd.to_timedelta(df["hora"].astype(int) - 1, unit="h")
    df["zona"] = sistema

    return df[["snapshot", "zona", "demand_mw"]].copy()


def fetch_day(target_date: date) -> pd.DataFrame:
    frames = []
    for s in SISTEMAS:
        try:
            df = fetch_sistema(s, target_date)
            frames.append(df)
            print(f"  ✓ {s}: {len(df)} horas descargadas")
        except Exception as e:
            print(f"  ✗ {s}: error — {e}", file=sys.stderr)
    if not frames:
        raise RuntimeError("No se pudo descargar ningún sistema.")
    return pd.concat(frames, ignore_index=True).sort_values(["zona", "snapshot"]).reset_index(drop=True)


def save(df: pd.DataFrame, target_date: date) -> Path:
    out_path = OUT_DIR / f"demand_{target_date.isoformat()}.csv"
    df.to_csv(out_path, index=False)
    return out_path


def main() -> None:
    p = argparse.ArgumentParser(description="Descarga demanda diaria CENACE")
    p.add_argument(
        "--date",
        type=str,
        default=date.today().isoformat(),
        help="Fecha en formato YYYY-MM-DD (default: hoy)",
    )
    p.add_argument("--overwrite", action="store_true", help="Sobreescribir si ya existe")
    args = p.parse_args()

    target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    out_path = OUT_DIR / f"demand_{target_date.isoformat()}.csv"

    if out_path.exists() and not args.overwrite:
        print(f"Ya existe {out_path}. Usa --overwrite para sobreescribir.")
        return

    print(f"Descargando demanda para {target_date}…")
    df = fetch_day(target_date)
    path = save(df, target_date)
    print(f"Guardado en {path} ({len(df)} filas)")


if __name__ == "__main__":
    main()
