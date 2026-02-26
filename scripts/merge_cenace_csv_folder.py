from __future__ import annotations

import re
from pathlib import Path
import argparse
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

ZONES = {"SIN", "BCA", "BCS"}

def detect_sep(path: Path) -> str:
    sample = path.read_text(encoding="utf-8", errors="ignore")[:3000]
    return ";" if sample.count(";") >= sample.count(",") else ","

def _clean_parts(line: str, sep: str) -> list[str]:
    """Divide la línea y limpia cada celda quitando espacios y comillas."""
    return [p.strip().strip('"').strip() for p in line.split(sep)]

def find_header_line(path: Path, sep: str) -> int:
    """
    Busca la línea donde realmente empieza la tabla.
    Regla: debe contener 'Hora' y al menos una zona (SIN/BCA/BCS),
    o en fallback solo 'Hora'.
    """
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for i, line in enumerate(lines[:200]):
        parts_up = {p.upper() for p in _clean_parts(line, sep)}
        if ("HORA" in parts_up) and (len(parts_up & ZONES) >= 1):
            return i
    # fallback: encabezado con 'Hora' pero zonas en columna 'Sistema'
    for i, line in enumerate(lines[:200]):
        parts_up = {p.upper() for p in _clean_parts(line, sep)}
        if "HORA" in parts_up:
            return i
    raise ValueError(f"No pude detectar la fila de encabezados en {path.name}. Revisa las primeras líneas del archivo.")

def _date_from_filename(path: Path) -> str:
    """Extrae la fecha YYYY-MM-DD del nombre del archivo."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.stem)
    if not m:
        raise ValueError(f"No pude extraer fecha del nombre de archivo: {path.name}")
    return m.group(1)

def _read_wide_format(df: pd.DataFrame, path: Path, hora_col: str, fecha_col: str | None) -> pd.DataFrame:
    """Formato ancho: columnas son SIN/BCA/BCS directamente."""
    zones_present = [c for c in df.columns if c.upper() in ZONES]
    if not zones_present:
        raise ValueError(f"No encontré columnas SIN/BCA/BCS en {path.name}. Columnas: {list(df.columns)}")

    if fecha_col:
        ts = df[fecha_col].astype(str).str.strip() + " " + df[hora_col].astype(str).str.strip()
        dt = pd.to_datetime(ts, dayfirst=True, errors="coerce")
    else:
        fecha_str = _date_from_filename(path)
        hora_num = pd.to_numeric(df[hora_col], errors="coerce")
        dt = pd.to_datetime(fecha_str) + pd.to_timedelta(hora_num - 1, unit="h")

    out = df[zones_present].copy()
    out.columns = [c.upper() for c in zones_present]
    out.index = dt
    out = out.dropna(axis=0, how="all").sort_index()
    for z in out.columns:
        out[z] = pd.to_numeric(out[z], errors="coerce")
    return out

def _read_long_format(df: pd.DataFrame, path: Path, hora_col: str) -> pd.DataFrame:
    """
    Formato largo: columna 'Sistema' contiene BCA/BCS/SIN,
    hay múltiples áreas por sistema. Agrupa por (Sistema, Hora) sumando demanda.
    """
    sistema_col = next(c for c in df.columns if c.upper() == "SISTEMA")

    # Busca columna de demanda por balance
    demanda_col = None
    for c in df.columns:
        if "demanda" in c.lower() and "balance" in c.lower():
            demanda_col = c
            break
    if demanda_col is None:
        raise ValueError(f"No encontré columna de demanda en {path.name}. Columnas: {list(df.columns)}")

    fecha_str = _date_from_filename(path)

    df = df.copy()
    df[sistema_col] = df[sistema_col].astype(str).str.strip().str.upper()
    df[hora_col] = pd.to_numeric(df[hora_col], errors="coerce")
    df[demanda_col] = pd.to_numeric(df[demanda_col], errors="coerce")

    # Solo zonas reconocidas
    df = df[df[sistema_col].isin(ZONES)]

    # Suma demanda por sistema y hora (agrega sub-áreas)
    agg = df.groupby([sistema_col, hora_col])[demanda_col].sum().reset_index()

    # Pivot a formato ancho
    pivot = agg.pivot(index=hora_col, columns=sistema_col, values=demanda_col)
    pivot.columns.name = None

    # Índice de timestamps
    pivot.index = pd.to_datetime(fecha_str) + pd.to_timedelta(pivot.index - 1, unit="h")
    pivot.index.name = None

    for z in list(ZONES):
        if z not in pivot.columns:
            pivot[z] = float("nan")

    return pivot[sorted(ZONES)].sort_index()

def read_one_cenace_csv(path: Path) -> pd.DataFrame:
    sep = detect_sep(path)
    header_line = find_header_line(path, sep=sep)

    df = pd.read_csv(path, sep=sep, skiprows=header_line)

    # Limpia encabezados (quita espacios y comillas)
    df.columns = [str(c).strip().strip('"').strip() for c in df.columns]

    # Detecta columna hora
    hora_col = next((c for c in df.columns if c.lower() == "hora"), None)
    if hora_col is None:
        hora_col = next((c for c in df.columns if "hora" in c.lower()), None)
    if hora_col is None:
        raise ValueError(f"No encontré columna Hora en {path.name}. Columnas: {list(df.columns)}")

    # ¿Formato largo? (columna 'Sistema' con BCA/BCS/SIN en datos)
    cols_up = {c.upper() for c in df.columns}
    if "SISTEMA" in cols_up:
        return _read_long_format(df, path, hora_col)

    # Formato ancho: columnas son zonas directamente
    fecha_col = next((c for c in df.columns if c.lower() == "fecha"), None)
    if fecha_col is None:
        fecha_col = next((c for c in df.columns if "fecha" in c.lower()), None)

    return _read_wide_format(df, path, hora_col, fecha_col)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--folder", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--tz", default="America/Mexico_City")
    args = p.parse_args()

    folder = Path(args.folder)
    files = sorted(folder.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"No encontré CSV en {folder}")

    pieces = []
    for f in files:
        print("Leyendo:", f.name)
        pieces.append(read_one_cenace_csv(f))

    merged = pd.concat(pieces).sort_index()

    # timezone
    if merged.index.tz is None:
        merged.index = merged.index.tz_localize(args.tz, nonexistent="shift_forward", ambiguous="NaT")
    else:
        merged.index = merged.index.tz_convert(args.tz)
    merged = merged[~merged.index.isna()]

    # duplicados y frecuencia
    merged = merged[~merged.index.duplicated(keep="last")]
    merged = merged.asfreq("h")

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(out_path)

    print("\n✔ Archivo creado:", out_path)
    print("Rango:", merged.index.min(), "→", merged.index.max())
    print("Columnas:", merged.columns.tolist())
    print("NaNs por columna:\n", merged.isna().sum())

if __name__ == "__main__":
    main()