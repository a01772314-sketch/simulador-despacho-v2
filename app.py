"""
app.py — Simulador de Despacho Económico para México
CENACE + PyPSA + Streamlit
SIN | BCA | BCS
"""

import os
import sys
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
from pathlib import Path

ruta_actual = os.path.dirname(os.path.abspath(__file__))
if ruta_actual not in sys.path:
    sys.path.insert(0, ruta_actual)

try:
    from motor import (
        fetch_all_systems,
        load_vre_profiles,
        run_dispatch,
        get_scenario_params,
        build_inputs_for_scenario,
        COSTOS_DEFAULT,
        CAPACIDADES_2024,
        CAPACIDADES_2026,
    )
except ImportError as e:
    st.error(f"❌ No se encontró motor.py: {e}")
    st.stop()

# ─── Configuración ────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Despacho Económico México",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Paleta de tecnologías
TECH_COLORS = {
    "ccgt":     "#2196F3",
    "carbon":   "#616161",
    "fuel_oil": "#FF7043",
    "diesel":   "#FF5722",
    "hidro":    "#26C6DA",
    "solar":    "#FDD835",
    "wind":     "#66BB6A",
    "battery":  "#AB47BC",
    "shedding": "#F44336",
}
TECH_LABELS = {
    "ccgt":     "CCGT / Gas",
    "carbon":   "Carbón",
    "fuel_oil": "Fuel Oil",
    "diesel":   "Diésel / Peaker",
    "hidro":    "Hidroeléctrica",
    "solar":    "Solar FV",
    "wind":     "Eólica",
    "battery":  "Batería (descarga)",
    "shedding": "Load Shedding",
}

SISTEMAS = ["SIN", "BCA", "BCS"]
SISTEMAS_NOMBRES = {
    "SIN": "Sistema Interconectado Nacional",
    "BCA": "Baja California",
    "BCS": "Baja California Sur",
}

# ─── CSS ─────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', sans-serif;
}
.stMetric { background: #f8f9fa; border-radius: 8px; padding: 12px; border-left: 3px solid #1565C0; }
.metric-ok  { border-left-color: #2E7D32; }
.metric-warn{ border-left-color: #E65100; }
.scenario-card {
    background: linear-gradient(135deg, #E3F2FD 0%, #F3E5F5 100%);
    border-radius: 10px; padding: 14px 16px; margin: 6px 0;
    border: 1px solid #BBDEFB; cursor: pointer;
}
.kpi-row { display: flex; gap: 12px; flex-wrap: wrap; margin: 12px 0; }
.kpi-box {
    background: white; border-radius: 8px; padding: 12px 16px;
    border: 1px solid #E0E0E0; flex: 1; min-width: 140px;
    font-family: 'IBM Plex Mono', monospace;
}
.kpi-label { font-size: 11px; color: #757575; text-transform: uppercase; letter-spacing: 0.08em; }
.kpi-value { font-size: 22px; font-weight: 600; color: #1A237E; margin-top: 4px; }
.tag-real  { background: #E8F5E9; color: #2E7D32; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
.tag-cache { background: #FFF3E0; color: #E65100; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
.tag-fallback { background: #FCE4EC; color: #C62828; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
h1 { font-weight: 600 !important; }
</style>
""", unsafe_allow_html=True)

# ─── Header ───────────────────────────────────────────────────────────────────

st.markdown("""
# ⚡ Simulador de Despacho Económico — México
**CENACE · PyPSA · HiGHS** &nbsp;|&nbsp; SIN · BCA · BCS
""")

# ─── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## ⚙️ Configuración")

    # Fechas
    st.markdown("### 📅 Período de análisis")
    f_inicio = st.date_input("Fecha inicio", datetime(2024, 10, 15))
    n_dias = st.selectbox("Duración", [1, 3, 7], index=0, format_func=lambda x: f"{x} día{'s' if x>1 else ''}")
    f_fin = f_inicio + timedelta(days=n_dias)

    # Sistemas
    st.markdown("### 🗺️ Sistemas")
    sistemas_sel = st.multiselect(
        "Sistemas a simular",
        SISTEMAS,
        default=["SIN", "BCA", "BCS"],
        format_func=lambda s: f"{s} — {SISTEMAS_NOMBRES[s]}"
    )
    if not sistemas_sel:
        st.warning("Selecciona al menos un sistema.")
        st.stop()

    # Escenarios
    st.markdown("### 🎬 Escenario")
    scenario_options = {
        "base":           "🔵 Caso base 2024",
        "fuel_shock":     "🔴 Shock de combustibles",
        "renewables_2026":"🟢 Boom renovable 2026",
        "forced_outage":  "🟠 Falla térmica (outage)",
        "storage_value":  "🟣 Valor del almacenamiento",
        "scarcity_voll":  "⚫ Escasez / VOLL alto",
    }
    scenario_key = st.selectbox(
        "Preset de escenario",
        list(scenario_options.keys()),
        format_func=lambda k: scenario_options[k]
    )

    # Costos personalizados
    st.markdown("### 💲 Costos marginales (USD/MWh)")
    st.caption("Ajusta para ver sensibilidad. Los escenarios usan estos valores como base.")

    c_ccgt     = st.slider("CCGT / Gas",         10, 120,  int(COSTOS_DEFAULT["ccgt"]))
    c_carbon   = st.slider("Carbón",             20, 150,  int(COSTOS_DEFAULT["carbon"]))
    c_fuel_oil = st.slider("Fuel Oil",           40, 200,  int(COSTOS_DEFAULT["fuel_oil"]))
    c_diesel   = st.slider("Diésel / Peaker",    60, 300,  int(COSTOS_DEFAULT["diesel"]))
    c_hidro    = st.slider("Hidroeléctrica",      0,  30,   int(COSTOS_DEFAULT["hidro"]))
    c_solar    = st.slider("Solar FV",            0,  20,   int(COSTOS_DEFAULT["solar"]))
    c_wind     = st.slider("Eólica",              0,  20,   int(COSTOS_DEFAULT["wind"]))
    c_voll     = st.slider("VOLL (shedding)",  1000, 12000, int(COSTOS_DEFAULT["shedding"]), step=500)

    custom_costs = {
        "ccgt": c_ccgt, "carbon": c_carbon, "fuel_oil": c_fuel_oil,
        "diesel": c_diesel, "hidro": c_hidro, "solar": c_solar,
        "wind": c_wind, "battery": 1.0, "shedding": c_voll,
    }

    st.markdown("### 🔋 Batería")
    bat_hours = st.slider("Horas de autonomía", 1, 12, 4)

    usar_cache = st.checkbox("Usar caché en disco", value=True)
    st.markdown("---")
    correr = st.button("▶ CORRER SIMULACIÓN", type="primary", use_container_width=True)

# ─── Tabs principales ─────────────────────────────────────────────────────────

tab_sim, tab_escenarios, tab_datos, tab_limite = st.tabs([
    "📊 Simulación", "🎬 Escenarios", "📦 Datos CENACE", "⚠️ Limitaciones"
])

# ─── TAB: Simulación ──────────────────────────────────────────────────────────

with tab_sim:
    if not correr:
        st.info("👈 Configura los parámetros en la barra lateral y presiona **CORRER SIMULACIÓN**.")
        st.markdown("""
        **¿Qué hace este simulador?**
        - Descarga demanda horaria real del CENACE (con caché en disco)
        - Modela SIN, BCA y BCS como buses independientes sin interconexión
        - Resuelve el despacho económico óptimo con PyPSA + HiGHS
        - Muestra: generación por tecnología, curtailment, shedding, SOC batería y precio marginal
        """)
        st.stop()

    # ── 1. Demanda CENACE ──────────────────────────────────────────────────
    with st.spinner("📡 Descargando demanda de CENACE..."):
        demand_data, reporte = fetch_all_systems(f_inicio, f_fin, sistemas=sistemas_sel)

    # Mostrar estado de datos
    cols_rep = st.columns(len(sistemas_sel))
    for i, sys in enumerate(sistemas_sel):
        rep = reporte[sys]
        with cols_rep[i]:
            fuente = rep["fuente"]
            tag_class = "tag-real" if "real" in fuente else "tag-fallback"
            st.markdown(f"""
            <div style="padding:10px;background:#fafafa;border-radius:8px;border:1px solid #eee">
            <b>{sys}</b> &nbsp; <span class="{tag_class}">{fuente}</span><br>
            <small>Horas: {rep['filas']} | NaNs: {rep['nans']} | Neg: {rep['negativos']}</small><br>
            <small>Dem: {rep['min_MW']:,.0f}–{rep['max_MW']:,.0f} MW (μ={rep['mean_MW']:,.0f})</small>
            </div>""", unsafe_allow_html=True)

    # ── 2. Perfiles VRE ────────────────────────────────────────────────────
    df_ref = demand_data[sistemas_sel[0]]
    time_index = df_ref.index
    vre_profiles = load_vre_profiles(time_index, sistemas=sistemas_sel)

    # ── 3. Escenario y capacidades ─────────────────────────────────────────
    sc_params = get_scenario_params(scenario_key, base_costs=custom_costs)
    inputs, params = build_inputs_for_scenario(
        sc_params, demand_data, time_index, sistemas_sel, vre_profiles
    )
    params["battery_hours"] = bat_hours
    params["voll"] = c_voll  # siempre usar el slider de VOLL

    st.markdown(f"""
    <div class="scenario-card">
    <b>{scenario_options[scenario_key]}</b><br>
    <span style="font-size:13px;color:#555">{sc_params['description']}</span>
    </div>
    """, unsafe_allow_html=True)

    # ── 4. Optimización ────────────────────────────────────────────────────
    with st.spinner("⚙️ Optimizando con PyPSA + HiGHS..."):
        resultados = run_dispatch(inputs, params)

    if not resultados["metadata"]["ok"]:
        st.error(f"❌ Error en optimización: {resultados['metadata'].get('error')}")
        st.stop()

    st.success("✅ Despacho calculado exitosamente")

    # ── 5. Resultados por sistema ───────────────────────────────────────────
    for sys in sistemas_sel:
        res = resultados["systems"][sys]
        st.markdown(f"---\n### 🏭 {sys} — {SISTEMAS_NOMBRES[sys]}")

        # KPIs
        total_dem   = float(res["demand_MW"].sum())
        total_shed  = float(res["shedding_MW"].sum())
        total_cost  = res["total_cost_USD"]
        avg_price   = float(res["shadow_price_USD_MWh"].mean())
        curtail_mwh = sum(float(v.sum()) for v in res["curtailment_MW"].values())

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Demanda total", f"{total_dem:,.0f} MWh")
        c2.metric("Costo operativo", f"${total_cost:,.0f} USD")
        c3.metric("Precio marg. promedio", f"${avg_price:.1f}/MWh")
        c4.metric("Load shedding", f"{total_shed:,.1f} MWh",
                  delta="⚠️ Shedding" if total_shed > 0 else "✅ Sin shedding",
                  delta_color="inverse" if total_shed > 0 else "off")
        c5.metric("Curtailment VRE", f"{curtail_mwh:,.1f} MWh")

        # Gráfica de despacho apilado
        fig = go.Figure()

        for tech, series in res["dispatch_MW"].items():
            if series.sum() > 0:
                fig.add_trace(go.Scatter(
                    x=time_index, y=series.values,
                    name=TECH_LABELS.get(tech, tech),
                    stackgroup="gen",
                    fillcolor=TECH_COLORS.get(tech, "#999"),
                    line=dict(width=0, color=TECH_COLORS.get(tech, "#999")),
                    hovertemplate="%{y:.0f} MW"
                ))

        # Batería (descarga)
        if res["battery"] and "discharge" in res["battery"]:
            dch = res["battery"]["discharge"]
            if float(dch.sum()) > 0:
                fig.add_trace(go.Scatter(
                    x=time_index, y=dch.values,
                    name="Batería (descarga)",
                    stackgroup="gen",
                    fillcolor=TECH_COLORS["battery"],
                    line=dict(width=0, color=TECH_COLORS["battery"]),
                ))

        # Shedding
        if float(res["shedding_MW"].sum()) > 0:
            fig.add_trace(go.Scatter(
                x=time_index, y=res["shedding_MW"].values,
                name="Load Shedding",
                stackgroup="gen",
                fillcolor=TECH_COLORS["shedding"],
                line=dict(width=0, color=TECH_COLORS["shedding"]),
            ))

        # Demanda (línea)
        fig.add_trace(go.Scatter(
            x=time_index, y=res["demand_MW"].values,
            name="Demanda",
            line=dict(color="#1A237E", width=2, dash="dot"),
            mode="lines",
        ))

        fig.update_layout(
            title=f"Despacho por tecnología — {sys}",
            xaxis_title="Tiempo", yaxis_title="MW",
            hovermode="x unified",
            legend=dict(orientation="h", y=-0.2),
            height=380,
            margin=dict(t=50, b=60),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Curtailment
        if any(v.sum() > 0 for v in res["curtailment_MW"].values()):
            fig_c = go.Figure()
            for vtech, series in res["curtailment_MW"].items():
                if series.sum() > 0:
                    fig_c.add_trace(go.Bar(
                        x=time_index, y=series.values,
                        name=f"Curtailment {TECH_LABELS.get(vtech, vtech)}",
                        marker_color=TECH_COLORS.get(vtech, "#ccc"),
                    ))
            fig_c.update_layout(
                title=f"Curtailment VRE — {sys}", height=250,
                xaxis_title="Tiempo", yaxis_title="MW",
                barmode="stack", margin=dict(t=40, b=40),
            )
            st.plotly_chart(fig_c, use_container_width=True)

        # SOC batería
        if res["battery"] and "soc" in res["battery"]:
            soc = res["battery"]["soc"]
            fig_s = go.Figure()
            fig_s.add_trace(go.Scatter(
                x=time_index, y=soc.values,
                fill="tozeroy", line=dict(color=TECH_COLORS["battery"], width=1.5),
                name="SOC Batería"
            ))
            fig_s.update_layout(
                title=f"Estado de carga batería (SOC) — {sys}", height=220,
                yaxis_title="MWh", xaxis_title="Tiempo",
                margin=dict(t=40, b=40),
            )
            st.plotly_chart(fig_s, use_container_width=True)

        # Precio marginal
        sp = res["shadow_price_USD_MWh"]
        fig_p = go.Figure()
        fig_p.add_trace(go.Scatter(
            x=time_index, y=sp.values,
            line=dict(color="#1565C0", width=2),
            fill="tozeroy", fillcolor="rgba(21,101,192,0.1)",
            name="Precio marginal"
        ))
        fig_p.update_layout(
            title=f"Precio marginal (shadow price) — {sys}",
            yaxis_title="USD/MWh", xaxis_title="Tiempo",
            height=220, margin=dict(t=40, b=40),
        )
        st.plotly_chart(fig_p, use_container_width=True)

        # Explicación del precio marginal
        tech_marginal = "Térmica (CCGT)" if avg_price > 30 else "Renovables/Hidro"
        with st.expander(f"📖 ¿Qué representa el precio marginal de {sys}?"):
            st.markdown(f"""
            El **precio marginal** (shadow price) es el costo de suministrar **1 MWh adicional** 
            en ese sistema en cada hora. Refleja el costo del generador **marginal** (el último 
            en ser despachado para cubrir la demanda).

            - Precio promedio: **${avg_price:.1f} USD/MWh**
            - Tecnología marginal estimada: **{tech_marginal}**
            - Cuándo es 0: hay exceso de renovables baratas (posible curtailment)
            - Cuándo es muy alto: hay escasez y se activa shedding (VOLL = ${c_voll:,})

            **Simplificaciones que sesgan el precio:** No modelamos costos de arranque (unit 
            commitment), restricciones de red, ni reservas. El precio real del MDA/MTR de CENACE 
            incluye estos factores.
            """)

    # Export de resultados
    st.markdown("---")
    st.markdown("### 💾 Exportar resultados")
    export_rows = []
    for sys in sistemas_sel:
        res = resultados["systems"][sys]
        for t in time_index:
            row = {"sistema": sys, "timestamp": t,
                   "demanda_MW": res["demand_MW"].get(t, 0),
                   "shedding_MW": res["shedding_MW"].get(t, 0),
                   "precio_marginal_USD_MWh": res["shadow_price_USD_MWh"].get(t, 0)}
            for tech, s in res["dispatch_MW"].items():
                row[f"gen_{tech}_MW"] = s.get(t, 0)
            export_rows.append(row)

    df_export = pd.DataFrame(export_rows)
    st.download_button(
        "⬇️ Descargar CSV de resultados",
        df_export.to_csv(index=False).encode(),
        file_name=f"despacho_{scenario_key}_{f_inicio}.csv",
        mime="text/csv"
    )


# ─── TAB: Escenarios ──────────────────────────────────────────────────────────

with tab_escenarios:
    st.markdown("## 🎬 Los 5 escenarios predefinidos")
    st.caption("Cada escenario activa un cambio concreto y produce una lección sobre el sistema eléctrico.")

    escenarios_info = [
        {
            "key": "fuel_shock",
            "icono": "🔴",
            "titulo": "Shock de combustibles",
            "cambio": "Costos marginales de gas, diésel y fuel oil ×1.8",
            "resultado": "El precio marginal sube bruscamente. Las térmicas siguen despachando si no hay alternativa.",
            "leccion": "La sensibilidad del precio a combustibles depende de si hay renovables disponibles. En BCS y BCA, la escasez de alternativas hace el sistema muy vulnerable.",
        },
        {
            "key": "renewables_2026",
            "icono": "🟢",
            "titulo": "Boom renovable 2026",
            "cambio": "+30% solar SIN, +50% solar/eólica BCA-BCS vs 2024",
            "resultado": "Curtailment aumenta en horas solares. El precio marginal colapsa a 0 en mediodía.",
            "leccion": "Más MW renovable no garantiza menor costo si no hay flexibilidad. El curtailment implica inversión desperdiciada.",
        },
        {
            "key": "forced_outage",
            "icono": "🟠",
            "titulo": "Falla térmica (outage)",
            "cambio": "CCGT pierde 40% de su capacidad instalada",
            "resultado": "En horas pico puede aparecer load shedding. El precio marginal se dispara hacia el VOLL.",
            "leccion": "La confiabilidad tiene un costo implícito. El VOLL revela cuánto estamos dispuestos a pagar para evitar cortes.",
        },
        {
            "key": "storage_value",
            "icono": "🟣",
            "titulo": "Valor del almacenamiento",
            "cambio": "Batería con 8h de autonomía (vs 4h base)",
            "resultado": "La batería carga en horas de exceso solar y descarga en pico nocturno, reduciendo shedding y costo.",
            "leccion": "El valor del almacenamiento depende del spread de precios. A mayor diferencia pico/valle, mayor beneficio.",
        },
        {
            "key": "scarcity_voll",
            "icono": "⚫",
            "titulo": "Escasez: VOLL $10,000",
            "cambio": "Value of Lost Load sube de $5,000 a $10,000 USD/MWh",
            "resultado": "El optimizador despacha más térmicas costosas para evitar cortes. El costo total sube, el shedding baja.",
            "leccion": "El VOLL es una política implícita de confiabilidad. VOLL alto = priorizar servicio sobre costo.",
        },
    ]

    for sc in escenarios_info:
        with st.expander(f"{sc['icono']} **{sc['titulo']}**", expanded=False):
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                st.markdown(f"**🔧 Cambio**\n\n{sc['cambio']}")
            with col_b:
                st.markdown(f"**📊 Resultado esperado**\n\n{sc['resultado']}")
            with col_c:
                st.markdown(f"**💡 Lección**\n\n{sc['leccion']}")
            st.caption(f"Para activar: selecciona '{scenario_options.get(sc['key'], sc['key'])}' en la barra lateral y corre la simulación.")


# ─── TAB: Datos CENACE ────────────────────────────────────────────────────────

with tab_datos:
    st.markdown("## 📦 Arquitectura de datos CENACE")

    st.markdown("""
    ### Pipeline de demanda
    El módulo descarga datos del API oficial de CENACE en **lotes diarios** (batching), 
    los guarda en **caché en disco** (formato Parquet) y aplica validaciones de calidad.

    ```
    API CENACE → batch por día → validar → caché disco → Series horaria
                                    ↓ (si falla)
                              fallback sintético
    ```

    **URL base:** `https://ws01.cenace.gob.mx:8082/SWPEND/SIM/{SISTEMA}/MDA/{Y}/{M}/{D}/{Y}/{M}/{D}/JSON`

    ### Validaciones aplicadas
    - Detección de huecos temporales → interpolación lineal
    - Eliminación de duplicados (DST)
    - Clipeo de negativos a 0
    - Reporte de cobertura (NaNs, gaps, rango)

    ### Caché
    - Directorio: `cache_cenace/` (Parquet por consulta)
    - Segunda corrida: sin llamadas al API, instantánea
    - Hash MD5 por (sistema, fecha_ini, fecha_fin)
    """)

    st.markdown("### 📊 Capacidades instaladas por sistema (fuente: PRODESEN 2024, SENER)")

    rows = []
    for sys in SISTEMAS:
        for tech, mw in CAPACIDADES_2024[sys].items():
            rows.append({"Sistema": sys, "Tecnología": TECH_LABELS.get(tech, tech),
                         "2024 (MW)": mw, "2026 esperado (MW)": CAPACIDADES_2026[sys].get(tech, mw)})
    df_cap = pd.DataFrame(rows)
    st.dataframe(df_cap, use_container_width=True, hide_index=True)

    st.caption("Supuestos 2026: +30% solar SIN, +50% solar BCA/BCS, +20% eólica SIN, +15% eólica BCA/BCS, batería x2. Fuente: Plan de Expansión CENACE 2024–2038.")

    st.markdown("### 💲 Tabla de costos de referencia (USD/MWh)")
    df_costos = pd.DataFrame([
        {"Tecnología": TECH_LABELS.get(t, t), "Rango (USD/MWh)": r, "Valor default": v}
        for t, v, r in [
            ("ccgt",     45,  "30–60"),
            ("carbon",   60,  "50–70"),
            ("fuel_oil", 95,  "80–120"),
            ("diesel",  150,  "120–180"),
            ("hidro",    5,   "0–10"),
            ("solar",    2,   "0–5"),
            ("wind",     2,   "0–5"),
            ("battery",  1,   "0–2"),
            ("shedding",5000, "2,000–10,000"),
        ]
    ])
    st.dataframe(df_costos, use_container_width=True, hide_index=True)


# ─── TAB: Limitaciones ────────────────────────────────────────────────────────

with tab_limite:
    st.markdown("## ⚠️ Limitaciones del modelo")
    st.markdown("""
    Este simulador es una herramienta **educativa** que simplifica el despacho real de CENACE.
    Es coherente, verificable y defendible — pero **no** replica el despacho operativo.
    """)

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("""
        ### ❌ Qué NO modela este simulador

        **Red eléctrica**
        - No hay flujos de potencia entre nodos (SIN, BCA, BCS son buses independientes)
        - No hay restricciones de transmisión interna ni líneas
        - No hay análisis de contingencias (criterio N-1)

        **Generación**
        - No hay unit commitment (arranques/paradas con costos fijos)
        - No modelamos costos de rampa ni tiempos mínimos de operación
        - Hidro sin modelo de embalse (SOC de agua), solo budget diario
        - No hay reservas rodantes ni no-rodantes
        - No hay modelado de despacho por contrato o energía firmada

        **Datos**
        - Perfiles VRE son sintéticos (Renewables.ninja como fuente real pendiente)
        - Capacidades agregadas por tecnología, no por planta
        - No modelamos DST correctamente en zonas con horario de verano

        **Baterías**
        - Sin costos de cycling ni degradación
        - Sin límites de profundidad de descarga
        - Un solo banco de baterías por sistema

        **Mercado**
        - No replica el MDA, MTR ni el MCC de CENACE
        - No modelamos contratos de cobertura ni precios spot reales
        """)

    with col2:
        st.markdown("""
        ### ✅ Qué SÍ garantiza este simulador

        **Optimización real**
        - Despacho económico por minimización de costos (PyPSA + HiGHS)
        - Balance energético: generación = demanda + shedding en cada hora
        - Límites de capacidad respetados (p_nom)
        - SOC de batería consistente (cíclico)

        **Datos reales**
        - Demanda horaria de CENACE (API oficial, con fallback documentado)
        - Capacidades basadas en PRODESEN 2024 (fuentes trazables)
        - Costos en rangos del bloque (USD/MWh, documentados)

        **Sensibilidad verificable**
        - Subir costo de una tecnología reduce su despacho
        - VOLL alto reduce shedding a costa de mayor costo total
        - Más VRE genera curtailment visible

        **Transparencia**
        - Esta sección de limitaciones
        - Fuentes documentadas en tab "Datos CENACE"
        - Supuestos del caso 2026 explícitos

        ---
        ### 🔧 Stack tecnológico
        - **Python** · Streamlit · PyPSA ≥0.26 (Linopy)
        - **Solver:** HiGHS (sin configuración extra)
        - **Datos:** CENACE API + Parquet cache
        - **UI:** Plotly, Streamlit widgets
        - **Fuentes:** PRODESEN 2024, SENER, CENACE API
        """)

    st.markdown("""
    ---
    > *"Todos los modelos son incorrectos, pero algunos son útiles."* — George Box

    Este simulador es útil para entender sensibilidades, escenarios y la lógica del despacho económico.
    Para decisiones operativas o de inversión, se requiere un modelo de mayor fidelidad.
    """)
