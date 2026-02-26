# app/lib/dispatch_model.py
import pandas as pd

def run_dispatch(
    demand: pd.Series,
    techs: pd.DataFrame,
) -> dict:
    """
    Placeholder: aquí iremos armando PyPSA.
    demand: serie de MW por hora (index 1..24 o timestamps)
    techs: tabla de tecnologías (nombre, bus, p_nom, marginal_cost, etc.)
    """
    return {
        "status": "todo",
        "objective": None,
        "dispatch": None,
    }