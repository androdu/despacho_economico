"""
fill_missing_demand.py
----------------------
Genera datos ESTIMADOS de demanda para fechas sin datos oficiales,
usando el promedio de los mismos días de la semana del historial
de balance_2026.

Guarda en: data_raw/demand/daily_api/demand_YYYY-MM-DD.csv
           (mismo formato que fetch_daily_demand.py)

Los datos oficiales (balance_2026/) tienen prioridad sobre estos
estimados en la app (load_demand_raw() deduplica favoreciendo balance_2026).

Uso:
    python scripts/fill_missing_demand.py
    python scripts/fill_missing_demand.py --start 2026-02-26 --end 2026-03-10
    python scripts/fill_missing_demand.py --overwrite   # sobrescribe estimados existentes
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BALANCE_DIR = ROOT / "data_raw" / "demand" / "balance_2026"
API_DIR = ROOT / "data_raw" / "demand" / "daily_api"
API_DIR.mkdir(parents=True, exist_ok=True)

SISTEMAS = ["SIN", "BCA", "BCS"]


# ── Cargar histórico oficial ──────────────────────────────────────────────────

def load_balance_history() -> pd.DataFrame:
    """Carga todos los CSVs de balance_2026 en un DataFrame unificado."""
    frames = []
    for f in sorted(BALANCE_DIR.glob("*.csv")):
        try:
            with open(f, encoding="latin-1") as fh:
                header_lines = [fh.readline() for _ in range(8)]
            m = re.search(r"(\d{2}/\d{2}/\d{4})", header_lines[7].strip().strip('"'))
            if not m:
                continue
            op_date = pd.to_datetime(m.group(1), format="%d/%m/%Y")
            df = pd.read_csv(f, skiprows=8, header=0, encoding="latin-1")
            df.columns = [c.strip().strip('"').strip() for c in df.columns]
            col_s = "Sistema"
            col_h = "Hora"
            col_d = "Estimacion de Demanda por Balance (MWh)"
            if not {col_s, col_h, col_d}.issubset(df.columns):
                continue
            df = df[[col_s, col_h, col_d]].copy()
            df.columns = ["zona", "hora", "demand_mw"]
            df["zona"] = df["zona"].astype(str).str.strip().str.strip('"').str.upper()
            df["hora"] = pd.to_numeric(df["hora"], errors="coerce")
            df["demand_mw"] = pd.to_numeric(
                df["demand_mw"].astype(str).str.strip().str.replace(",", ""), errors="coerce"
            )
            df = df.dropna(subset=["hora", "demand_mw"])
            df["snapshot"] = op_date + pd.to_timedelta(df["hora"].astype(int) - 1, unit="h")
            df["zona"] = df["zona"].replace({"BSA": "BCA"})
            frames.append(df[["snapshot", "zona", "demand_mw"]])
        except Exception as e:
            print(f"  Saltando {f.name}: {e}", file=sys.stderr)
    if not frames:
        raise RuntimeError(f"No se encontraron CSVs en {BALANCE_DIR}")
    return pd.concat(frames, ignore_index=True)


# ── Detectar fechas faltantes ─────────────────────────────────────────────────

def dates_without_data(start: date, end: date) -> list[date]:
    """Fechas en [start, end] sin CSV en balance_2026 NI en daily_api."""
    # Datas disponibles en balance oficial
    balance_dates: set[date] = set()
    for f in BALANCE_DIR.glob("*.csv"):
        try:
            with open(f, encoding="latin-1") as fh:
                for i, line in enumerate(fh):
                    if i == 7:
                        m = re.search(r"(\d{2}/\d{2}/\d{4})", line.strip().strip('"'))
                        if m:
                            balance_dates.add(
                                datetime.strptime(m.group(1), "%d/%m/%Y").date()
                            )
                        break
        except Exception:
            pass

    # Datas ya en daily_api
    api_dates: set[date] = set()
    for f in API_DIR.glob("demand_*.csv"):
        try:
            d = datetime.strptime(f.stem.replace("demand_", ""), "%Y-%m-%d").date()
            api_dates.add(d)
        except Exception:
            pass

    missing = []
    current = start
    while current <= end:
        if current not in balance_dates and current not in api_dates:
            missing.append(current)
        current += timedelta(days=1)
    return missing


# ── Estimar demanda por interpolación (promedio mismo día de semana) ──────────

def estimate_day(target: date, history: pd.DataFrame) -> pd.DataFrame:
    """Promedia horas del mismo día de la semana del historial."""
    weekday = target.weekday()  # 0=lunes … 6=domingo
    hist_same_weekday = history[
        history["snapshot"].dt.weekday == weekday
    ].copy()

    if hist_same_weekday.empty:
        # Fallback: promedio de todo el historial
        hist_same_weekday = history.copy()

    hist_same_weekday["hora"] = hist_same_weekday["snapshot"].dt.hour

    avg = (
        hist_same_weekday.groupby(["zona", "hora"])["demand_mw"]
        .mean()
        .reset_index()
    )

    rows = []
    op_date = pd.Timestamp(target)
    for _, row in avg.iterrows():
        rows.append({
            "snapshot": op_date + pd.to_timedelta(int(row["hora"]), unit="h"),
            "zona": row["zona"],
            "demand_mw": round(row["demand_mw"], 2),
        })
    return pd.DataFrame(rows).sort_values(["zona", "snapshot"]).reset_index(drop=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Rellena fechas sin demanda con estimados")
    p.add_argument("--start", default="2026-02-26", help="Fecha inicial (YYYY-MM-DD)")
    p.add_argument(
        "--end",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="Fecha final (YYYY-MM-DD, default: ayer)",
    )
    p.add_argument("--overwrite", action="store_true", help="Sobreescribir estimados existentes")
    args = p.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()

    print("Cargando historial oficial de balance_2026…")
    history = load_balance_history()
    print(f"  {len(history)} registros cargados de {history['snapshot'].dt.date.nunique()} días")

    missing = dates_without_data(start, end)
    if args.overwrite:
        # Con --overwrite, procesar todas las fechas del rango sin datos oficiales
        balance_dates: set[date] = set()
        for f in BALANCE_DIR.glob("*.csv"):
            try:
                with open(f, encoding="latin-1") as fh:
                    for i, line in enumerate(fh):
                        if i == 7:
                            m = re.search(r"(\d{2}/\d{2}/\d{4})", line.strip().strip('"'))
                            if m:
                                balance_dates.add(
                                    datetime.strptime(m.group(1), "%d/%m/%Y").date()
                                )
                            break
            except Exception:
                pass
        missing = []
        current = start
        while current <= end:
            if current not in balance_dates:
                missing.append(current)
            current += timedelta(days=1)

    if not missing:
        print("No hay fechas faltantes. Todo está al día.")
        return

    print(f"\nFechas a estimar ({len(missing)}):")
    for d in missing:
        out_path = API_DIR / f"demand_{d.isoformat()}.csv"
        df = estimate_day(d, history)
        df.to_csv(out_path, index=False)
        zones = df["zona"].unique().tolist()
        print(f"  ✓ {d}  ({', '.join(zones)})  → {out_path.name}  [ESTIMADO]")

    print(f"\nListo. {len(missing)} días estimados guardados en {API_DIR}")


if __name__ == "__main__":
    main()
