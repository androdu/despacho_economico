from __future__ import annotations
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CLEAN_DEMAND = ROOT / "data_clean" / "demand"


def load_clean_demand(name: str = "historical_demand") -> pd.DataFrame:
    path = CLEAN_DEMAND / f"{name}.parquet"
    return pd.read_parquet(path)


def list_available(suffix: str = ".parquet") -> list[str]:
    if not CLEAN_DEMAND.exists():
        return []
    return [p.stem for p in sorted(CLEAN_DEMAND.glob(f"*{suffix}"))]
