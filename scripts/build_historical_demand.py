from __future__ import annotations

import argparse
import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils_io import write_parquet, write_csv, ensure_dir

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data_raw" / "demand"
CLEAN_DIR = ROOT / "data_clean" / "demand"

def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    # intenta mapear nombres comunes a un estándar
    colmap = {}
    for c in df.columns:
        cl = c.strip().lower()
        if cl in {"fecha", "datetime", "timestamp", "date_time", "time"}:
            colmap[c] = "timestamp"
        elif cl in {"zona", "region", "area", "sistema"}:
            colmap[c] = "zone"
        elif cl in {"demanda", "mw", "demand_mw", "load_mw"}:
            colmap[c] = "demand_mw"
    df = df.rename(columns=colmap)

    # Si viene con el timestamp como índice
    if "timestamp" not in df.columns and df.index.name and "time" in df.index.name.lower():
        df = df.reset_index().rename(columns={df.index.name: "timestamp"})

    required = {"timestamp", "zone", "demand_mw"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas requeridas {missing}. Columnas actuales: {list(df.columns)}")

    return df[["timestamp", "zone", "demand_mw"]].copy()

def _clean(df: pd.DataFrame, tz: str) -> pd.DataFrame:
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp", "zone", "demand_mw"])

    df["zone"] = df["zone"].astype(str).str.strip().str.upper()
    df["demand_mw"] = pd.to_numeric(df["demand_mw"], errors="coerce")
    df = df.dropna(subset=["demand_mw"])

    # Timezone: trabajamos en tz local y guardamos en tz-aware
    # Si viene naive -> localize; si viene aware -> convert
    if df["timestamp"].dt.tz is None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(tz, nonexistent="shift_forward", ambiguous="NaT")
    else:
        df["timestamp"] = df["timestamp"].dt.tz_convert(tz)

    df = df.dropna(subset=["timestamp"])

    # Quitar duplicados exactos
    df = df.drop_duplicates(subset=["timestamp", "zone"], keep="last")

    # Pivot a formato PyPSA-friendly: index=time, columns=zones, values=MW
    wide = df.pivot(index="timestamp", columns="zone", values="demand_mw").sort_index()

    # Forzar frecuencia horaria (sin inventar si faltan: dejamos NaN y luego decides imputación)
    wide = wide.asfreq("h")

    return wide

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=str, required=True, help="Ruta a CSV o Parquet raw")
    p.add_argument("--tz", type=str, default="America/Mexico_City")
    p.add_argument("--name", type=str, default="historical_demand")
    p.add_argument("--export_csv", action="store_true")
    args = p.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        raise FileNotFoundError(in_path)

    if in_path.suffix.lower() == ".csv":
        df = pd.read_csv(in_path)
    elif in_path.suffix.lower() in {".parquet", ".pq"}:
        df = pd.read_parquet(in_path)
    else:
        raise ValueError("Input debe ser .csv o .parquet")

    df = _standardize_columns(df)
    wide = _clean(df, tz=args.tz)

    out_parquet = CLEAN_DIR / f"{args.name}.parquet"
    write_parquet(wide, out_parquet)

    if args.export_csv:
        out_csv = CLEAN_DIR / f"{args.name}.csv"
        write_csv(wide, out_csv)

    print(f"OK -> {out_parquet} (shape={wide.shape})")
    print("Zonas:", list(wide.columns))

if __name__ == "__main__":
    ensure_dir(RAW_DIR)
    ensure_dir(CLEAN_DIR)
    main()