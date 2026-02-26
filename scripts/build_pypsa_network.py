from __future__ import annotations

import argparse
import sys
from pathlib import Path
import pandas as pd
import pypsa

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
CLEAN_DEMAND = ROOT / "data_clean" / "demand"


def build_network(demand: pd.DataFrame) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(demand.index)

    zones = list(demand.columns)

    # Buses
    for z in zones:
        n.add("Bus", z)

    # Loads (time series)
    for z in zones:
        n.add("Load", f"load_{z}", bus=z)
    n.loads_t.p_set = demand.copy()

    # Generadores "dummy" por zona (ajústalos a tu caso)
    # Costos marginales ejemplo: gas caro, solar/wind barato
    # p_nom = capacidad instalada (MW) - ejemplo: 1.5x pico de demanda por zona para factibilidad
    peak = demand.max()
    for z in zones:
        cap = float(1.5 * peak[z]) if pd.notna(peak[z]) else 1000.0

        # Gas (dispatchable)
        n.add(
            "Generator",
            f"gas_{z}",
            bus=z,
            p_nom=cap,
            marginal_cost=1200.0,  # MXN/MWh (ejemplo)
            efficiency=0.5,
        )

        # Solar (limitado por perfil)
        n.add(
            "Generator",
            f"solar_{z}",
            bus=z,
            p_nom=0.6 * cap,
            marginal_cost=50.0,
        )

        # Wind
        n.add(
            "Generator",
            f"wind_{z}",
            bus=z,
            p_nom=0.4 * cap,
            marginal_cost=30.0,
        )

    # Perfiles de disponibilidad (p_max_pu) super simples (luego los reemplazas por datos reales)
    # solar: campana diurna; wind: constante suave
    idx = demand.index
    hour = idx.tz_convert("America/Mexico_City").hour if idx.tz is not None else idx.hour

    solar_profile = pd.Series(0.0, index=idx)
    solar_profile[(hour >= 7) & (hour <= 18)] = 1.0
    solar_profile[(hour == 7) | (hour == 18)] = 0.5

    wind_profile = pd.Series(0.6, index=idx)  # constante por ahora

    for z in zones:
        n.generators_t.p_max_pu[f"solar_{z}"] = solar_profile.values
        n.generators_t.p_max_pu[f"wind_{z}"] = wind_profile.values
        n.generators_t.p_max_pu[f"gas_{z}"] = 1.0

    # Líneas entre zonas (opcional): conecta en cadena con capacidad grande
    # Ajusta topología real después (SIN/BCA/BCS)
    if len(zones) >= 2:
        for a, b in zip(zones[:-1], zones[1:]):
            n.add(
                "Line",
                f"line_{a}_{b}",
                bus0=a,
                bus1=b,
                x=0.0001,     # reactancia dummy
                r=0.00001,
                s_nom=5000.0,  # MW capacidad (dummy)
            )

    return n


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--demand_parquet", type=str, required=True)
    p.add_argument("--out_nc", type=str, default=str(ROOT / "data_clean" / "pypsa_network.nc"))
    args = p.parse_args()

    demand = pd.read_parquet(args.demand_parquet)

    if not isinstance(demand.index, pd.DatetimeIndex):
        raise ValueError("La demanda debe venir con DatetimeIndex.")
    # PyPSA no acepta índices con zona horaria — eliminar tz manteniendo la hora local
    if demand.index.tz is not None:
        demand.index = demand.index.tz_localize(None)
    if demand.isna().all().all():
        raise ValueError("La demanda está vacía (todo NaN).")

    n = build_network(demand)

    # Si hay NaN en demanda, PyPSA puede fallar: eliminar snapshots incompletos
    if n.loads_t.p_set.isna().any().any():
        good = ~n.loads_t.p_set.isna().any(axis=1)
        n = n[good]

    status, cond = n.optimize(solver_name="highs")
    print("Optimize status:", status, cond)

    out_path = Path(args.out_nc)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n.export_to_netcdf(out_path)
    print("OK ->", out_path)

    print("Costo total (objective):", float(n.objective))
    print("Generación total por tipo (MWh):")
    gen = n.generators_t.p.sum()
    print(gen.groupby(gen.index.str.split("_").str[0]).sum())

    # Curtailment: energía disponible pero no despachada
    avail = (
        n.generators.p_nom
        * n.generators_t.p_max_pu.reindex(columns=n.generators.index, fill_value=1.0)
    )
    curtailment = (avail - n.generators_t.p).clip(lower=0).sum()
    print("Curtailment por tipo (MWh):")
    print(curtailment.groupby(curtailment.index.str.split("_").str[0]).sum())


if __name__ == "__main__":
    main()
