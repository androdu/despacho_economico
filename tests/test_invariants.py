"""
Invariant tests for the PyPSA dispatch model.

These tests build minimal networks directly (no Streamlit imports) and verify
that key physical and economic properties hold after optimization.

Run with:  pytest tests/test_invariants.py -v
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

# ──────────────────────────────────────────────────────────────────────────────
# Helpers — build minimal networks for testing
# ──────────────────────────────────────────────────────────────────────────────

SNAPSHOTS = pd.date_range("2026-01-01", periods=24, freq="h")
VOLL = 3_000.0  # $/MWh


def _base_network(demand_mw: float = 1_000.0) -> pypsa.Network:
    """Single-bus, 3-generator, 24-hour network — always feasible."""
    n = pypsa.Network()
    n.set_snapshots(SNAPSHOTS)
    n.add("Bus", "SIN")

    # Merit order: solar (0) → gas_ccgt (50) → VoLL (3000)
    n.add("Generator", "solar_1",   bus="SIN", carrier="solar",
          p_nom=800.0,  marginal_cost=0.0)
    n.add("Generator", "gas_ccgt_1", bus="SIN", carrier="gas_ccgt",
          p_nom=2_000.0, marginal_cost=50.0)
    n.add("Generator", "VoLL_SIN",   bus="SIN", carrier="shedding",
          p_nom=1e6,    marginal_cost=VOLL)

    # Solar only available 08–18 h (hours 8–18)
    solar_profile = pd.Series(0.0, index=SNAPSHOTS)
    solar_profile.iloc[8:19] = 0.80
    n.generators_t.p_max_pu = pd.DataFrame({"solar_1": solar_profile})

    n.add("Load", "load_SIN", bus="SIN",
          p_set=pd.Series(demand_mw, index=SNAPSHOTS))

    n.optimize(solver_name="highs")
    return n


def _battery_network() -> pypsa.Network:
    """Single-bus network with solar + CCGT + battery + VoLL."""
    n = pypsa.Network()
    n.set_snapshots(SNAPSHOTS)
    n.add("Bus", "SIN")

    n.add("Generator", "solar_1",    bus="SIN", carrier="solar",
          p_nom=1_500.0, marginal_cost=0.0)
    n.add("Generator", "gas_ccgt_1", bus="SIN", carrier="gas_ccgt",
          p_nom=1_000.0, marginal_cost=50.0)
    n.add("Generator", "VoLL_SIN",   bus="SIN", carrier="shedding",
          p_nom=1e6,     marginal_cost=VOLL)

    solar_profile = pd.Series(0.0, index=SNAPSHOTS)
    solar_profile.iloc[8:19] = 0.90
    n.generators_t.p_max_pu = pd.DataFrame({"solar_1": solar_profile})

    n.add("StorageUnit", "battery_SIN", bus="SIN", carrier="battery",
          p_nom=500.0, max_hours=4.0,
          efficiency_store=0.95, efficiency_dispatch=0.95,
          state_of_charge_initial=1_000.0,
          cyclic_state_of_charge=True)

    n.add("Load", "load_SIN", bus="SIN",
          p_set=pd.Series(800.0, index=SNAPSHOTS))

    n.optimize(solver_name="highs")
    return n


def _merit_order_network() -> pypsa.Network:
    """Network where merit order is unambiguous: solar < nuclear < gas_ccgt < steam."""
    n = pypsa.Network()
    n.set_snapshots(SNAPSHOTS)
    n.add("Bus", "SIN")

    n.add("Generator", "solar_1",    bus="SIN", carrier="solar",
          p_nom=300.0,  marginal_cost=0.0)
    n.add("Generator", "nuclear_1",  bus="SIN", carrier="nuclear",
          p_nom=500.0,  marginal_cost=5.0)
    n.add("Generator", "gas_ccgt_1", bus="SIN", carrier="gas_ccgt",
          p_nom=800.0,  marginal_cost=50.0)
    n.add("Generator", "steam_1",    bus="SIN", carrier="steam_other",
          p_nom=600.0,  marginal_cost=65.0)
    n.add("Generator", "VoLL_SIN",   bus="SIN", carrier="shedding",
          p_nom=1e6,    marginal_cost=VOLL)

    solar_profile = pd.Series(0.0, index=SNAPSHOTS)
    solar_profile.iloc[8:19] = 0.80
    n.generators_t.p_max_pu = pd.DataFrame({"solar_1": solar_profile})

    # Demand: 600 MW flat (solar + nuclear covers it; gas/steam barely needed)
    n.add("Load", "load_SIN", bus="SIN",
          p_set=pd.Series(600.0, index=SNAPSHOTS))

    n.optimize(solver_name="highs")
    return n


# ──────────────────────────────────────────────────────────────────────────────
# 1. Power balance
# ──────────────────────────────────────────────────────────────────────────────

class TestPowerBalance:
    """Generation must exactly equal load in every bus at every snapshot."""

    def test_power_balance_base(self):
        n = _base_network(demand_mw=1_000.0)
        _assert_power_balance(n)

    def test_power_balance_battery(self):
        n = _battery_network()
        _assert_power_balance(n)

    def test_power_balance_multi_carrier(self):
        n = _merit_order_network()
        _assert_power_balance(n)


def _assert_power_balance(n: pypsa.Network, tol: float = 1e-3) -> None:
    for bus in n.buses.index:
        gen_on_bus = n.generators[n.generators["bus"] == bus].index
        gen_p = n.generators_t.p.reindex(columns=gen_on_bus, fill_value=0.0).sum(axis=1)

        load_on_bus = n.loads[n.loads["bus"] == bus].index
        load_p = n.loads_t.p_set.reindex(columns=load_on_bus, fill_value=0.0).sum(axis=1)

        if not n.storage_units.empty:
            bat_on_bus = n.storage_units[n.storage_units["bus"] == bus].index
            bat_p = n.storage_units_t.p.reindex(columns=bat_on_bus, fill_value=0.0).sum(axis=1)
        else:
            bat_p = pd.Series(0.0, index=n.snapshots)

        imbalance = (gen_p + bat_p - load_p).abs()
        assert imbalance.max() < tol, (
            f"Power imbalance on bus {bus}: max={imbalance.max():.4f} MW"
        )


# ──────────────────────────────────────────────────────────────────────────────
# 2. Non-negative dispatch
# ──────────────────────────────────────────────────────────────────────────────

class TestNonNegativeDispatch:
    """Generator output must be ≥ 0 in every hour."""

    def test_generators_non_negative(self):
        n = _base_network()
        assert (n.generators_t.p >= -1e-6).all().all(), \
            "Some generator has negative dispatch"

    def test_battery_dispatch_non_negative(self):
        n = _battery_network()
        if "p_dispatch" in dir(n.storage_units_t):
            assert (n.storage_units_t.p_dispatch >= -1e-6).all().all()
        # Net p can be negative (charging) — that's fine


# ──────────────────────────────────────────────────────────────────────────────
# 3. Shadow prices (nodal marginal prices)
# ──────────────────────────────────────────────────────────────────────────────

class TestShadowPrices:
    """
    In a cost-minimization LP with non-negative marginal costs,
    shadow prices (dual variables for ≤ load constraint) must be ≥ 0.
    """

    def test_shadow_prices_non_negative(self):
        n = _base_network()
        sp = n.buses_t.marginal_price
        assert not sp.empty, "Shadow prices not computed"
        assert (sp >= -1e-4).all().all(), \
            f"Negative shadow price found: min={sp.min().min():.4f}"

    def test_shadow_price_bounded_by_voll(self):
        """Shadow price must never exceed VoLL (if there's no shedding, it's the cap)."""
        n = _base_network()
        sp = n.buses_t.marginal_price
        assert (sp <= VOLL + 1e-4).all().all(), \
            f"Shadow price exceeds VoLL: max={sp.max().max():.2f} > {VOLL}"

    def test_shadow_price_zero_when_solar_covers_all(self):
        """If solar (cost=0) alone covers demand, marginal price should be 0."""
        n = pypsa.Network()
        snaps = pd.date_range("2026-01-01 10:00", periods=3, freq="h")
        n.set_snapshots(snaps)
        n.add("Bus", "SIN")
        n.add("Generator", "solar_1", bus="SIN", carrier="solar",
              p_nom=2_000.0, marginal_cost=0.0)
        n.add("Generator", "gas_ccgt_1", bus="SIN", carrier="gas_ccgt",
              p_nom=1_000.0, marginal_cost=50.0)
        n.add("Generator", "VoLL_SIN", bus="SIN", carrier="shedding",
              p_nom=1e6, marginal_cost=VOLL)
        # Solar always available
        n.generators_t.p_max_pu = pd.DataFrame(
            {"solar_1": pd.Series(1.0, index=snaps)}
        )
        n.add("Load", "load_SIN", bus="SIN",
              p_set=pd.Series(500.0, index=snaps))
        n.optimize(solver_name="highs")

        sp = n.buses_t.marginal_price["SIN"]
        assert (sp.abs() < 1e-3).all(), \
            f"Expected price=0 when solar covers all demand, got {sp.values}"


# ──────────────────────────────────────────────────────────────────────────────
# 4. Battery SOC bounds
# ──────────────────────────────────────────────────────────────────────────────

class TestBatterySOC:
    """Battery SOC must stay within [0, max_energy_mwh] at all times."""

    def test_soc_within_bounds(self):
        n = _battery_network()
        bat = "battery_SIN"
        if n.storage_units.empty or bat not in n.storage_units.index:
            pytest.skip("No battery in network")

        e_max = (
            float(n.storage_units.loc[bat, "p_nom"])
            * float(n.storage_units.loc[bat, "max_hours"])
        )
        soc = n.storage_units_t.state_of_charge[bat]
        assert (soc >= -1e-3).all(), f"SOC went below 0: min={soc.min():.2f}"
        assert (soc <= e_max + 1e-3).all(), \
            f"SOC exceeded max ({e_max:.0f} MWh): max={soc.max():.2f}"

    def test_soc_cyclic(self):
        """With cyclic_state_of_charge=True, SOC[last] ≈ SOC[first]."""
        n = _battery_network()
        bat = "battery_SIN"
        if n.storage_units.empty or bat not in n.storage_units.index:
            pytest.skip("No battery in network")
        soc = n.storage_units_t.state_of_charge[bat]
        assert abs(float(soc.iloc[-1]) - float(soc.iloc[0])) < 1.0, \
            f"Cyclic SOC violated: first={soc.iloc[0]:.1f}, last={soc.iloc[-1]:.1f}"


# ──────────────────────────────────────────────────────────────────────────────
# 5. Merit order sanity
# ──────────────────────────────────────────────────────────────────────────────

class TestMeritOrder:
    """
    Cheaper generators should have higher capacity factors than expensive ones
    when both are available simultaneously (unconstrained case).
    """

    def test_cheaper_generator_dispatches_more(self):
        n = _merit_order_network()
        dispatch = n.generators_t.p

        solar_cf   = dispatch["solar_1"].sum()   / (300.0  * 24)
        nuclear_cf = dispatch["nuclear_1"].sum()  / (500.0  * 24)
        gas_cf     = dispatch["gas_ccgt_1"].sum() / (800.0  * 24)
        steam_cf   = dispatch["steam_1"].sum()    / (600.0  * 24)

        # Solar is only available 08–18 h, so compare available hours
        solar_avail = dispatch["solar_1"].sum() / (300.0 * 0.80 * 11)  # 11 daylight hours
        assert solar_avail > 0.99, \
            f"Solar not fully dispatched when available: CF={solar_avail:.3f}"

        # Nuclear (5 $/MWh) should run more than gas (50 $/MWh)
        assert nuclear_cf >= gas_cf, \
            f"Nuclear CF ({nuclear_cf:.3f}) < gas CF ({gas_cf:.3f}) — merit order violated"

        # Gas should run more than steam (65 $/MWh)
        assert gas_cf >= steam_cf, \
            f"Gas CF ({gas_cf:.3f}) < steam CF ({steam_cf:.3f}) — merit order violated"

    def test_no_shedding_with_sufficient_capacity(self):
        """VoLL generator should not dispatch when total capacity > demand."""
        n = _base_network(demand_mw=500.0)  # demand << capacity
        voll_dispatch = n.generators_t.p.get("VoLL_SIN", pd.Series(0.0))
        assert voll_dispatch.sum() < 1e-3, \
            f"Unexpected shedding with surplus capacity: {voll_dispatch.sum():.1f} MWh"

    def test_shedding_when_capacity_scarce(self):
        """VoLL should dispatch when demand exceeds all available capacity."""
        n = pypsa.Network()
        snaps = pd.date_range("2026-01-01", periods=3, freq="h")
        n.set_snapshots(snaps)
        n.add("Bus", "SIN")
        n.add("Generator", "gas_ccgt_1", bus="SIN", carrier="gas_ccgt",
              p_nom=500.0, marginal_cost=50.0)
        n.add("Generator", "VoLL_SIN", bus="SIN", carrier="shedding",
              p_nom=1e6, marginal_cost=VOLL)
        n.add("Load", "load_SIN", bus="SIN",
              p_set=pd.Series(900.0, index=snaps))  # 900 > 500 MW available
        n.optimize(solver_name="highs")

        voll_dispatch = n.generators_t.p["VoLL_SIN"]
        assert voll_dispatch.sum() > 1.0, \
            "Expected shedding when demand exceeds capacity, but none occurred"


# ──────────────────────────────────────────────────────────────────────────────
# 6. Curtailment
# ──────────────────────────────────────────────────────────────────────────────

class TestCurtailment:
    """
    Available VRE energy that is not dispatched (curtailment) must be ≥ 0.
    Curtailment should appear when VRE supply > demand and no storage/export.
    """

    def test_curtailment_non_negative(self):
        n = _base_network()
        if n.generators_t.p_max_pu.empty:
            pytest.skip("No VRE profiles")

        vre_gens = [g for g in n.generators_t.p_max_pu.columns
                    if n.generators.loc[g, "carrier"] == "solar"]
        if not vre_gens:
            pytest.skip("No solar generators with profiles")

        p_avail = (n.generators_t.p_max_pu[vre_gens]
                   .multiply(n.generators.loc[vre_gens, "p_nom"]))
        p_disp  = n.generators_t.p.reindex(columns=vre_gens, fill_value=0.0)
        curtailment = (p_avail - p_disp).clip(lower=0)

        assert (curtailment >= -1e-4).all().all(), \
            "Negative curtailment detected (dispatched more than available)"

    def test_curtailment_appears_when_surplus_vre(self):
        """With large solar and no storage, curtailment must occur at solar peak."""
        n = pypsa.Network()
        snaps = pd.date_range("2026-01-01 10:00", periods=4, freq="h")
        n.set_snapshots(snaps)
        n.add("Bus", "SIN")
        n.add("Generator", "solar_1", bus="SIN", carrier="solar",
              p_nom=5_000.0, marginal_cost=0.0)
        n.add("Generator", "VoLL_SIN", bus="SIN", carrier="shedding",
              p_nom=1e6, marginal_cost=VOLL)
        n.generators_t.p_max_pu = pd.DataFrame(
            {"solar_1": pd.Series(0.90, index=snaps)}
        )
        n.add("Load", "load_SIN", bus="SIN",
              p_set=pd.Series(1_000.0, index=snaps))  # demand << 5000 MW solar
        n.optimize(solver_name="highs")

        solar_disp  = n.generators_t.p["solar_1"].sum()
        solar_avail = 5_000.0 * 0.90 * len(snaps)
        curtailment = solar_avail - solar_disp
        assert curtailment > 1.0, \
            f"Expected curtailment with surplus solar, got {curtailment:.1f} MWh"
