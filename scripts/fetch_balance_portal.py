"""
fetch_balance_portal.py
-----------------------
Descarga CSVs oficiales de "Estimación de Demanda Real por Balance"
desde el portal CENACE:
  https://www.cenace.gob.mx/Paginas/SIM/Reportes/EstimacionDemandaReal.aspx

Los datos se publican ~2 semanas después de la fecha de operación.
Los CSVs se guardan en data_raw/demand/balance_2026/ y automáticamente
tienen prioridad sobre los estimados en daily_api/.

Uso:
    python scripts/fetch_balance_portal.py                      # últimos 30 días
    python scripts/fetch_balance_portal.py --start 2026-02-26  # desde esa fecha
    python scripts/fetch_balance_portal.py --days 60           # últimos N días
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data_raw" / "demand" / "balance_2026"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://www.cenace.gob.mx/Paginas/SIM/Reportes/EstimacionDemandaReal.aspx"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    "Referer": BASE_URL,
}


# ── Fechas ya descargadas en balance_2026/ ────────────────────────────────────

def balance_dates_on_disk() -> set[date]:
    dates: set[date] = set()
    for f in OUT_DIR.glob("*.csv"):
        try:
            with open(f, encoding="latin-1") as fh:
                for i, line in enumerate(fh):
                    if i == 7:
                        m = re.search(r"(\d{2}/\d{2}/\d{4})", line.strip().strip('"'))
                        if m:
                            dates.add(datetime.strptime(m.group(1), "%d/%m/%Y").date())
                        break
        except Exception:
            pass
    return dates


# ── Extraer campos ASP.NET del HTML ──────────────────────────────────────────

def extract_aspnet_fields(soup: BeautifulSoup) -> dict:
    fields = {}
    for name in ["__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
                 "__VIEWSTATEENCRYPTED"]:
        tag = soup.find("input", {"name": name})
        if tag:
            fields[name] = tag.get("value", "")
    return fields


# ── Descargar un día específico ───────────────────────────────────────────────

def fetch_day(target: date, session: requests.Session, timeout: int = 30) -> bytes | None:
    """
    Intenta descargar el CSV de balance para `target`.
    Devuelve los bytes del CSV, o None si no está disponible aún.
    """
    date_str = target.strftime("%d/%m/%Y")   # formato Telerik: dd/MM/yyyy

    # 1. GET inicial → obtener ViewState
    try:
        r = session.get(BASE_URL, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
    except Exception as e:
        print(f"  Error GET inicial: {e}", file=sys.stderr)
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    fields = extract_aspnet_fields(soup)

    if not fields.get("__VIEWSTATE"):
        print("  No se encontró __VIEWSTATE en la página", file=sys.stderr)
        return None

    # 2. POST para filtrar por fecha de operación
    # Nombres de controles Telerik observados en EstimacionDemandaReal.aspx
    post_data = {
        **fields,
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        # Fecha única (sección "Por Balance")
        "ctl00$MainContent$rdpFechaBalance$dateInput": date_str,
        "ctl00_MainContent_rdpFechaBalance_dateInput_ClientState":
            f'{{"enabled":true,"emptyMessage":"","validationText":"{target.strftime("%Y-%m-%d")}-00-00-00",'
            f'"valueAsString":"{target.strftime("%Y-%m-%d")}-00-00-00",'
            f'"minDateStr":"2016-01-27-00-00-00","maxDateStr":"2030-12-31-00-00-00"}}',
        "ctl00$MainContent$btnConsultaBalance": "Consultar",
    }

    try:
        r2 = session.post(BASE_URL, data=post_data, headers=HEADERS, timeout=timeout)
        r2.raise_for_status()
    except Exception as e:
        print(f"  Error POST consulta: {e}", file=sys.stderr)
        return None

    soup2 = BeautifulSoup(r2.text, "html.parser")
    fields2 = extract_aspnet_fields(soup2)

    # Verificar si hay datos (buscar tabla con datos)
    grid = soup2.find("table", {"id": re.compile(r"rgBalance", re.I)})
    if not grid:
        # Buscar cualquier tabla con filas de datos
        tables = soup2.find_all("table", class_=re.compile(r"rgMasterTable", re.I))
        if not tables:
            return None  # sin datos para esta fecha

    # 3. POST para descargar CSV (botón de exportar)
    fields2["__EVENTTARGET"] = "ctl00$MainContent$rgBalance$ctl00$ctl02$ctl00$ExportToCsvButton"
    fields2["__EVENTARGUMENT"] = ""

    # Telerik grid export button (imagen CSV)
    csv_post = {
        **fields2,
        "ctl00$MainContent$rdpFechaBalance$dateInput": date_str,
        "ctl00_MainContent_rdpFechaBalance_dateInput_ClientState":
            f'{{"enabled":true,"emptyMessage":"","validationText":"{target.strftime("%Y-%m-%d")}-00-00-00",'
            f'"valueAsString":"{target.strftime("%Y-%m-%d")}-00-00-00",'
            f'"minDateStr":"2016-01-27-00-00-00","maxDateStr":"2030-12-31-00-00-00"}}',
    }

    try:
        r3 = session.post(BASE_URL, data=csv_post, headers=HEADERS, timeout=timeout)
        r3.raise_for_status()
        content_type = r3.headers.get("Content-Type", "")
        if "text/csv" in content_type or "application/octet-stream" in content_type:
            return r3.content
        # Si responde HTML, el botón de descarga no funcionó con estos nombres
        return None
    except Exception as e:
        print(f"  Error POST descarga CSV: {e}", file=sys.stderr)
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Descarga balance CENACE del portal")
    p.add_argument("--start", default=None, help="Fecha inicial YYYY-MM-DD")
    p.add_argument("--days", type=int, default=30, help="Cuántos días hacia atrás revisar (default: 30)")
    p.add_argument("--overwrite", action="store_true", help="Re-descargar aunque ya exista")
    args = p.parse_args()

    end_date = date.today() - timedelta(days=1)
    if args.start:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    else:
        start_date = end_date - timedelta(days=args.days)

    existing = balance_dates_on_disk()

    to_fetch = []
    current = start_date
    while current <= end_date:
        if current not in existing or args.overwrite:
            to_fetch.append(current)
        current += timedelta(days=1)

    if not to_fetch:
        print("No hay fechas nuevas que descargar del portal.")
        return

    print(f"Intentando descargar {len(to_fetch)} fecha(s) del portal CENACE…")
    session = requests.Session()
    downloaded = 0
    not_available = 0

    for target in to_fetch:
        print(f"  {target}…", end=" ", flush=True)
        csv_bytes = fetch_day(target, session)

        if csv_bytes is None:
            print("no disponible aún")
            not_available += 1
        else:
            # Guardar con nombre similar al formato existente
            fname = f"Demanda Real Balance_0_v3 Dia Operacion {target.strftime('%Y-%m-%d')} auto.csv"
            out_path = OUT_DIR / fname
            out_path.write_bytes(csv_bytes)
            print(f"✓ guardado → {fname}")
            downloaded += 1

        time.sleep(1)  # cortesía al servidor

    print(f"\nResultado: {downloaded} descargados, {not_available} aún no disponibles.")


if __name__ == "__main__":
    main()
