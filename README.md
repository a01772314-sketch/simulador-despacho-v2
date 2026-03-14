# Simulador de Despacho Económico — México
**CENACE + PyPSA + Streamlit**  
Sistemas: SIN · BCA · BCS

---

## Instalación y ejecución local

```bash
# 1. Clonar el repositorio
git clone <URL_DEL_REPO>
cd simulador-despacho

# 2. Crear entorno virtual
python -m venv venv
source venv/bin/activate        # Linux/Mac
venv\Scripts\activate           # Windows

# 3. Instalar dependencias (versiones fijadas)
pip install -r requirements.txt

# 4. Correr la aplicación
streamlit run app.py
```

La app estará disponible en `http://localhost:8501`

---

## Estructura del proyecto

```
.
├── app.py              # Interfaz Streamlit (UI, tabs, gráficas)
├── motor.py            # Motor: CENACE fetch, PyPSA dispatch, escenarios
├── requirements.txt    # Dependencias con versiones fijadas
├── README.md           # Este archivo
├── cache_cenace/       # Caché en disco (Parquet, generado automáticamente)
└── growth_assumptions.md  # Supuestos del caso 2026
```

---

## Supuestos del caso 2026 (growth_assumptions)

### Fuente base: PRODESEN 2024 (SENER), Plan de Expansión CENACE 2024–2038

| Sistema | Tecnología | 2024 (MW) | 2026 (MW) | Factor | Justificación |
|---------|-----------|-----------|-----------|--------|---------------|
| SIN     | Solar     | 8,000     | 10,400    | +30%   | Pipeline de proyectos en Sonora/Yucatán con permisos otorgados |
| SIN     | Eólica    | 7,200     | 8,640     | +20%   | Proyectos Istmo de Tehuantepec en construcción |
| BCA     | Solar     | 900       | 1,350     | +50%   | Zona Mexicali con alta irradiación y proyectos aprobados |
| BCS     | Solar     | 140       | 210       | +50%   | Programa de electrificación SENER para sistemas aislados |
| Todos   | Batería   | base      | base×2    | +100%  | Requisitos de flexibilidad CENACE + proyectos CFE |

**Supuestos conservadores:** Se excluyen proyectos sin financiamiento confirmado.  
**Costos no incluyen:** LCOE, costos de conexión, ni costos de refuerzo de red.

---

## Quality gates del proyecto

| Gate | Estado |
|------|--------|
| Reproducibilidad (instrucciones) | ✅ Este README |
| CENACE real (con trazabilidad) | ✅ API oficial + fallback documentado |
| Caché funcional | ✅ Parquet en disco, 2da corrida instantánea |
| Consistencia temporal | ✅ Detección de huecos + interpolación |
| Balance energía | ✅ PyPSA garantiza balance por construcción |
| Límites respetados | ✅ p_nom, SOC cíclico |
| Sensibilidad a costos | ✅ Sliders + comparación de escenarios |
| Resultados comprensibles | ✅ Gráficas + KPIs + precio marginal |
| Limitaciones visibles | ✅ Tab "Limitaciones" en la app |

---

## Stack tecnológico

- **Python ≥3.10**
- **PyPSA ≥0.26** con `network.optimize()` (Linopy)
- **Solver:** HiGHS (`solver_name='highs'`)
- **UI:** Streamlit ≥1.32
- **Visualización:** Plotly
- **Caché:** Parquet (pyarrow)
- **Fuente demanda:** CENACE API `ws01.cenace.gob.mx:8082`

---

## Fuentes de datos

- **Demanda:** API CENACE (SWPEND/SIM)
- **Capacidades:** PRODESEN 2024, SENER
- **Perfiles VRE:** Sintéticos basados en coordenadas (Renewables.ninja pendiente)
- **Costos marginales:** Tabla de referencia del bloque (USD/MWh, rangos SENER/literatura)

---

*Desarrollado para el módulo de Despacho Económico. No reproduce el despacho operativo de CENACE.*
