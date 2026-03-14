"""
motor.py — Motor de despacho económico para México
CENACE + PyPSA (HiGHS/Linopy)
Cubre: SIN, BCA, BCS | batch + caché | load shedding | batería | shadow price
"""

import os
import json
import time
import hashlib
import logging
import numpy as np
import pandas as pd
import pypsa
import requests
from pathlib import Path
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Caché en disco ──────────────────────────────────────────────────────────

CACHE_DIR = Path("cache_cenace")
CACHE_DIR.mkdir(exist_ok=True)

def _cache_key(sistema, fecha_ini, fecha_fin):
    raw = f"{sistema}_{fecha_ini}_{fecha_fin}"
    return CACHE_DIR / f"{hashlib.md5(raw.encode()).hexdigest()}.parquet"

def _save_cache(df, sistema, fecha_ini, fecha_fin):
    try:
        df.to_parquet(_cache_key(sistema, fecha_ini, fecha_fin))
    except Exception as e:
        logger.warning(f"No se pudo guardar caché: {e}")

def _load_cache(sistema, fecha_ini, fecha_fin):
    path = _cache_key(sistema, fecha_ini, fecha_fin)
    if path.exists():
        try:
            df = pd.read_parquet(path)
            logger.info(f"Caché hit: {sistema} {fecha_ini}→{fecha_fin}")
            return df
        except Exception:
            pass
    return None

# ─── Demanda CENACE (batch ≤7 días) ──────────────────────────────────────────

SISTEMAS_MAP = {
    "SIN": "SIN",
    "BCA": "BCA",
    "BCS": "BCS",
}

API_BASE = "https://ws01.cenace.gob.mx:8082/SWPEND/SIM"

def _fetch_single_day(sistema, fecha):
    """Descarga un día del API de CENACE. Devuelve DataFrame o None."""
    y, m, d = fecha.strftime("%Y/%m/%d").split("/")
    url = f"{API_BASE}/{sistema}/MDA/{y}/{m}/{d}/{y}/{m}/{d}/JSON"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()

        def buscar(obj):
            if isinstance(obj, list) and len(obj) >= 20:
                return obj
            if isinstance(obj, dict):
                for v in obj.values():
                    r = buscar(v)
                    if r:
                        return r
            return None

        rows = buscar(data)
        if not rows:
            return None

        df = pd.DataFrame(rows)
        df.columns = [c.lower() for c in df.columns]
        col_f = next((c for c in df.columns if "/" in str(df[c].iloc[0])), None)
        col_h = next((c for c in df.columns if "hora" in c), None)
        num_cols = df.select_dtypes(include=[np.number]).columns
        col_v = next((c for c in num_cols if "hora" not in c), None)
        if col_v is None and len(num_cols) > 0:
            col_v = num_cols[-1]

        if col_f and col_h and col_v:
            df["dt"] = pd.to_datetime(df[col_f]) + pd.to_timedelta(
                pd.to_numeric(df[col_h], errors="coerce") - 1, unit="h"
            )
            df = df.dropna(subset=["dt"]).set_index("dt").sort_index()
            return df[[col_v]].rename(columns={col_v: sistema})

    except Exception as e:
        logger.warning(f"API falló para {sistema} {fecha}: {e}")
    return None


def _fallback_demand(sistema, fecha_ini, n_hours):
    """Curva de demanda típica por sistema cuando el API no responde."""
    perfiles_base = {
        "SIN": np.array([
            28000, 27000, 26500, 26000, 26000, 26500, 28000, 31000,
            34000, 36000, 38000, 40000, 41000, 42000, 41500, 41000,
            40500, 41000, 43000, 45000, 44000, 41000, 37000, 32000
        ], dtype=float),
        "BCA": np.array([
            1800, 1700, 1650, 1620, 1600, 1650, 1800, 2100,
            2400, 2600, 2800, 3000, 3100, 3200, 3150, 3000,
            2900, 2950, 3100, 3300, 3200, 2900, 2500, 2100
        ], dtype=float),
        "BCS": np.array([
            280, 260, 250, 240, 238, 245, 280, 340,
            400, 440, 470, 490, 500, 510, 505, 490,
            480, 490, 510, 530, 510, 470, 400, 320
        ], dtype=float),
    }
    base = perfiles_base.get(sistema, perfiles_base["SIN"])
    reps = (n_hours // 24) + 1
    full = np.tile(base, reps)[:n_hours]
    noise = np.random.normal(1.0, 0.02, n_hours)
    idx = pd.date_range(start=fecha_ini, periods=n_hours, freq="h")
    return pd.DataFrame({sistema: full * noise}, index=idx)


def fetch_demand(sistema, fecha_ini, fecha_fin, use_cache=True):
    """
    Descarga demanda horaria para un sistema en batch de ≤7 días.
    Devuelve DataFrame con índice datetime y columna = sistema.
    """
    if isinstance(fecha_ini, str):
        fecha_ini = pd.Timestamp(fecha_ini)
    if isinstance(fecha_fin, str):
        fecha_fin = pd.Timestamp(fecha_fin)

    # Intentar caché global primero
    if use_cache:
        cached = _load_cache(sistema, fecha_ini, fecha_fin)
        if cached is not None:
            return cached, True  # (df, from_cache)

    # Batching por días individuales
    dias = pd.date_range(start=fecha_ini, end=fecha_fin - timedelta(hours=1), freq="D")
    frames = []
    any_real = False

    for dia in dias:
        day_cached = _load_cache(sistema, dia, dia + timedelta(days=1))
        if use_cache and day_cached is not None:
            frames.append(day_cached)
            any_real = True
            continue

        df_day = None
        for intento in range(3):
            df_day = _fetch_single_day(sistema, dia)
            if df_day is not None:
                break
            time.sleep(1.5 * (intento + 1))

        if df_day is not None:
            _save_cache(df_day, sistema, dia, dia + timedelta(days=1))
            frames.append(df_day)
            any_real = True
        else:
            n = 24
            fb = _fallback_demand(sistema, dia, n)
            frames.append(fb)

    if not frames:
        n_hours = int((fecha_fin - fecha_ini).total_seconds() / 3600)
        df = _fallback_demand(sistema, fecha_ini, n_hours)
        return df, False

    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="first")]

    # Validación: detectar huecos y negativos
    expected = pd.date_range(start=df.index[0], end=df.index[-1], freq="h")
    missing = expected.difference(df.index)
    if len(missing) > 0:
        logger.warning(f"{sistema}: {len(missing)} horas faltantes — interpolando")
        df = df.reindex(expected).interpolate("linear")

    df[sistema] = df[sistema].clip(lower=0)

    _save_cache(df, sistema, fecha_ini, fecha_fin)
    return df, any_real


def fetch_all_systems(fecha_ini, fecha_fin, sistemas=("SIN", "BCA", "BCS")):
    """Descarga demanda para todos los sistemas. Devuelve dict {sys: df} y reporte."""
    results = {}
    reporte = {}
    for sys in sistemas:
        df, real = fetch_demand(sys, fecha_ini, fecha_fin)
        results[sys] = df
        reporte[sys] = {
            "filas": len(df),
            "nans": int(df.isna().sum().sum()),
            "negativos": int((df < 0).sum().sum()),
            "fuente": "CENACE (real)" if real else "Fallback sintético",
            "min_MW": float(df[sys].min()),
            "max_MW": float(df[sys].max()),
            "mean_MW": float(df[sys].mean()),
        }
    return results, reporte


# ─── Perfiles VRE ────────────────────────────────────────────────────────────

COORDENADAS_DEFAULT = {
    "SIN":  {"solar": (29.1, -110.9), "wind": (16.5, -95.0)},
    "BCA":  {"solar": (32.5, -115.5), "wind": (31.8, -116.6)},
    "BCS":  {"solar": (24.1, -110.3), "wind": (24.8, -111.9)},
}

def _solar_profile(n, lat):
    """Perfil solar sintético basado en latitud y hora del día."""
    hours = np.arange(n) % 24
    sunrise = 6
    sunset = 18 + (lat - 20) * 0.05
    solar = np.where(
        (hours >= sunrise) & (hours <= sunset),
        np.sin(np.pi * (hours - sunrise) / (sunset - sunrise)) ** 1.2,
        0.0
    )
    return np.clip(solar * 0.9, 0, 1)

def _wind_profile(n, lat):
    """Perfil eólico sintético con variación nocturna."""
    hours = np.arange(n) % 24
    base = 0.35 + 0.15 * np.sin(2 * np.pi * hours / 24 + np.pi)
    noise = np.random.normal(0, 0.05, n)
    return np.clip(base + noise, 0.05, 0.95)

def load_vre_profiles(time_index, sistemas=("SIN", "BCA", "BCS")):
    """
    Genera perfiles VRE por hora. Usa perfiles sintéticos basados en coordenadas.
    En producción, reemplazar con datos de Renewables.ninja.
    """
    n = len(time_index)
    perfiles = {}
    for sys in sistemas:
        lat_sol, _ = COORDENADAS_DEFAULT[sys]["solar"]
        lat_eo, _ = COORDENADAS_DEFAULT[sys]["wind"]
        perfiles[sys] = {
            "solar": _solar_profile(n, lat_sol),
            "wind": _wind_profile(n, lat_eo),
        }
    return perfiles


# ─── Capacidades instaladas (fuente: PRODESEN 2024, SENER) ───────────────────

# Capacidades en MW por sistema y tecnología
# Fuente: PRODESEN 2024, Balance Nacional de Energía SENER
# Caso 2026: supuestos en growth_assumptions documentados abajo

CAPACIDADES_2024 = {
    "SIN": {
        "ccgt":     28000,  # CCGT/Gas ciclo combinado
        "carbon":    5200,  # Carbón
        "fuel_oil":  3800,  # Fuel oil / térmica pesada
        "diesel":    1200,  # Diésel / Peakers
        "hidro":    12500,  # Hidroeléctrica
        "solar":     8000,  # Solar FV
        "wind":      7200,  # Eólica
        "battery":    500,  # Almacenamiento
    },
    "BCA": {
        "ccgt":      2800,
        "carbon":       0,
        "fuel_oil":   350,
        "diesel":     120,
        "hidro":        0,
        "solar":      900,
        "wind":       300,
        "battery":     80,
    },
    "BCS": {
        "ccgt":       450,
        "carbon":       0,
        "fuel_oil":   120,
        "diesel":      80,
        "hidro":        0,
        "solar":      140,
        "wind":        40,
        "battery":     30,
    },
}

# Caso 2026: +30% solar SIN, +20% eólica SIN, +50% solar BCA/BCS
# Fuente: Plan de Expansión CENACE 2024-2038, supuestos conservadores
CAPACIDADES_2026 = {
    sys: {
        **caps,
        "solar": int(caps["solar"] * (1.30 if sys == "SIN" else 1.50)),
        "wind":  int(caps["wind"]  * (1.20 if sys == "SIN" else 1.15)),
        "battery": int(caps["battery"] * 2.0),
    }
    for sys, caps in CAPACIDADES_2024.items()
}

# Costos variables de referencia (USD/MWh) — tabla del bloque
COSTOS_DEFAULT = {
    "ccgt":     45.0,
    "carbon":   60.0,
    "fuel_oil": 95.0,
    "diesel":  150.0,
    "hidro":     5.0,
    "solar":     2.0,
    "wind":      2.0,
    "battery":   1.0,
    "shedding": 5000.0,
}


# ─── Motor PyPSA ─────────────────────────────────────────────────────────────

def run_dispatch(inputs: dict, params: dict) -> dict:
    """
    Ejecuta despacho económico con PyPSA para todos los sistemas.

    inputs:
      - time_index: DatetimeIndex horario
      - systems: lista de sistemas
      - demand_MW: {sys: Series}
      - vre_pmaxpu: {sys: {solar: array, wind: array}}
      - capacity_MW: {sys: {tech: float}}

    params:
      - marginal_cost_USD_per_MWh: {tech: float}
      - voll: float (value of lost load)
      - battery_hours: float (horas de autonomía de batería)

    Devuelve dict con resultados por sistema.
    """
    try:
        time_index = inputs["time_index"]
        systems = inputs["systems"]
        costs = params.get("marginal_cost_USD_per_MWh", COSTOS_DEFAULT)
        voll = params.get("voll", COSTOS_DEFAULT["shedding"])
        bat_hours = params.get("battery_hours", 4.0)

        network = pypsa.Network()
        network.set_snapshots(time_index)

        for sys in systems:
            cap = inputs["capacity_MW"][sys]
            dem = inputs["demand_MW"][sys]
            vre = inputs["vre_pmaxpu"][sys]

            # Bus
            network.add("Bus", sys)

            # Carga
            network.add("Load", f"Load_{sys}", bus=sys, p_set=dem)

            # Generadores térmicos
            for tech in ["ccgt", "carbon", "fuel_oil", "diesel"]:
                c = cap.get(tech, 0)
                if c > 0:
                    network.add("Generator", f"{tech}_{sys}",
                        bus=sys,
                        p_nom=c,
                        marginal_cost=costs.get(tech, 100),
                        p_min_pu=0.0,
                    )

            # Hidro (energy budget diario)
            if cap.get("hidro", 0) > 0:
                hidro_daily_mwh = cap["hidro"] * 8  # 8h equivalentes/día
                n_days = max(1, len(time_index) // 24)
                hidro_budget = hidro_daily_mwh * n_days
                network.add("Generator", f"hidro_{sys}",
                    bus=sys,
                    p_nom=cap["hidro"],
                    marginal_cost=costs.get("hidro", 5),
                    p_max_pu=1.0,
                )
                # Budget energético como restricción global
                if hidro_budget > 0:
                    network.add("GlobalConstraint", f"hidro_budget_{sys}",
                        sense="<=",
                        constant=hidro_budget,
                        carrier_attribute="",
                        investment_period_weightings=None,
                    )

            # Solar
            if cap.get("solar", 0) > 0:
                solar_pu = pd.Series(vre["solar"], index=time_index)
                network.add("Generator", f"solar_{sys}",
                    bus=sys,
                    p_nom=cap["solar"],
                    marginal_cost=costs.get("solar", 2),
                    p_max_pu=solar_pu,
                    p_min_pu=0.0,
                )

            # Eólica
            if cap.get("wind", 0) > 0:
                wind_pu = pd.Series(vre["wind"], index=time_index)
                network.add("Generator", f"wind_{sys}",
                    bus=sys,
                    p_nom=cap["wind"],
                    marginal_cost=costs.get("wind", 2),
                    p_max_pu=wind_pu,
                    p_min_pu=0.0,
                )

            # Batería (StorageUnit)
            if cap.get("battery", 0) > 0:
                bat_mw = cap["battery"]
                bat_mwh = bat_mw * bat_hours
                network.add("StorageUnit", f"battery_{sys}",
                    bus=sys,
                    p_nom=bat_mw,
                    max_hours=bat_hours,
                    capital_cost=0,
                    marginal_cost=costs.get("battery", 1),
                    efficiency_store=0.92,
                    efficiency_dispatch=0.92,
                    cyclic_state_of_charge=True,
                )

            # Load shedding (generador de penalización)
            network.add("Generator", f"shedding_{sys}",
                bus=sys,
                p_nom=float(dem.max()) * 1.2,  # capacidad = demanda máx × 1.2
                marginal_cost=voll,
                p_min_pu=0.0,
            )

        # Optimización
        status = network.optimize(solver_name="highs")

        if status[0] not in ("ok", "optimal"):
            return {"metadata": {"ok": False, "error": f"Solver: {status}"}}

        # Extraer resultados
        resultados = {"metadata": {"ok": True, "status": str(status)}, "systems": {}}

        for sys in systems:
            gen_t = network.generators_t.p
            sto_t = network.storage_units_t

            dispatch = {}
            for tech in ["ccgt", "carbon", "fuel_oil", "diesel", "hidro", "solar", "wind"]:
                col = f"{tech}_{sys}"
                if col in gen_t.columns:
                    dispatch[tech] = gen_t[col]

            # Shedding
            shed_col = f"shedding_{sys}"
            shedding = gen_t[shed_col] if shed_col in gen_t.columns else pd.Series(0, index=time_index)

            # Curtailment VRE
            curtailment = {}
            cap_sys = inputs["capacity_MW"][sys]
            vre_sys = inputs["vre_pmaxpu"][sys]
            for vtech in ["solar", "wind"]:
                col = f"{vtech}_{sys}"
                if col in gen_t.columns:
                    available = pd.Series(vre_sys[vtech] * cap_sys.get(vtech, 0), index=time_index)
                    curtailment[vtech] = (available - gen_t[col]).clip(lower=0)

            # Batería
            battery_data = {}
            bat_col = f"battery_{sys}"
            if bat_col in sto_t.p.columns:
                battery_data = {
                    "charge": (-sto_t.p[bat_col].clip(upper=0)),
                    "discharge": sto_t.p[bat_col].clip(lower=0),
                    "soc": sto_t.state_of_charge[bat_col],
                }

            # Shadow price (precio marginal)
            shadow_price = pd.Series(0.0, index=time_index)
            if sys in network.buses_t.marginal_price.columns:
                shadow_price = network.buses_t.marginal_price[sys]

            # Costo total
            total_cost = float(network.objective) / len(systems)

            resultados["systems"][sys] = {
                "dispatch_MW": dispatch,
                "shedding_MW": shedding,
                "curtailment_MW": curtailment,
                "battery": battery_data,
                "shadow_price_USD_MWh": shadow_price,
                "total_cost_USD": total_cost,
                "demand_MW": inputs["demand_MW"][sys],
            }

        return resultados

    except Exception as e:
        import traceback
        logger.error(traceback.format_exc())
        return {"metadata": {"ok": False, "error": str(e)}}


# ─── Escenarios predefinidos ──────────────────────────────────────────────────

def get_scenario_params(scenario_name: str, base_costs: dict = None) -> dict:
    """Devuelve parámetros modificados para cada escenario predefinido."""
    costs = dict(COSTOS_DEFAULT if base_costs is None else base_costs)

    scenarios = {
        "base": {
            "label": "Caso base 2024",
            "description": "Capacidades y costos de referencia para 2024.",
            "costs": costs,
            "capacity_year": 2024,
            "voll": 5000,
            "battery_hours": 4,
        },
        "fuel_shock": {
            "label": "Shock de combustibles",
            "description": "Gas y diésel suben 80%. ¿Quién marca precio?",
            "costs": {**costs, "ccgt": costs["ccgt"] * 1.8, "diesel": costs["diesel"] * 1.8, "fuel_oil": costs["fuel_oil"] * 1.8},
            "capacity_year": 2024,
            "voll": 5000,
            "battery_hours": 4,
        },
        "renewables_2026": {
            "label": "Boom renovable 2026",
            "description": "Capacidad VRE al nivel esperado 2026. ¿Curtailment?",
            "costs": costs,
            "capacity_year": 2026,
            "voll": 5000,
            "battery_hours": 4,
        },
        "forced_outage": {
            "label": "Falla de planta térmica",
            "description": "CCGT pierde 40% de capacidad. Riesgo de shedding.",
            "costs": costs,
            "capacity_year": 2024,
            "voll": 5000,
            "battery_hours": 4,
            "derate_ccgt": 0.40,
        },
        "storage_value": {
            "label": "Valor del almacenamiento",
            "description": "Batería x4 horas. ¿Cuánto reduce costos?",
            "costs": costs,
            "capacity_year": 2024,
            "voll": 5000,
            "battery_hours": 8,
        },
        "scarcity_voll": {
            "label": "Escasez: VOLL alto",
            "description": "VOLL sube a $10,000. Decisiones de confiabilidad.",
            "costs": costs,
            "capacity_year": 2024,
            "voll": 10000,
            "battery_hours": 4,
        },
    }
    return scenarios.get(scenario_name, scenarios["base"])


def build_inputs_for_scenario(
    scenario_params: dict,
    demand_data: dict,
    time_index,
    sistemas: list,
    vre_profiles: dict,
) -> tuple:
    """Construye inputs y params para run_dispatch a partir de un escenario."""
    year = scenario_params.get("capacity_year", 2024)
    caps_base = CAPACIDADES_2024 if year == 2024 else CAPACIDADES_2026

    capacity = {}
    for sys in sistemas:
        cap = dict(caps_base[sys])
        if scenario_params.get("derate_ccgt"):
            cap["ccgt"] = int(cap["ccgt"] * (1 - scenario_params["derate_ccgt"]))
        capacity[sys] = cap

    inputs = {
        "time_index": time_index,
        "systems": sistemas,
        "demand_MW": {sys: demand_data[sys][sys] for sys in sistemas},
        "vre_pmaxpu": vre_profiles,
        "capacity_MW": capacity,
    }
    params = {
        "marginal_cost_USD_per_MWh": scenario_params["costs"],
        "voll": scenario_params.get("voll", 5000),
        "battery_hours": scenario_params.get("battery_hours", 4),
    }
    return inputs, params
