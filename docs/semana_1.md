# Semana 1 – Integración de Demanda CENACE

## 1. Objetivo
Implementar un cliente en Python que permita consultar datos reales de demanda eléctrica del CENACE y visualizarlos en una aplicación Streamlit, estableciendo la base para un simulador de despacho económico.

---

## 2. User Journey (Flujo del Usuario)
1. El usuario ingresa a la aplicación Streamlit.
2. Selecciona el sistema eléctrico: SIN, BCA o BCS.
3. Presiona “Descargar”.
4. La app consulta la API oficial de CENACE o utiliza caché local si está disponible.
5. Se muestran:
   - Reporte de calidad de datos
   - Tabla horaria
   - Gráfica de demanda/generación/pronóstico
6. El usuario puede descargar el CSV.

---

## 3. Arquitectura del Proyecto

despacho_economico/
│
├── app/
│   ├── Home.py
│   ├── pages/
│   │   └── 1_Demanda_CENACE.py
│   └── lib/
│       └── cenace_client.py
│
├── data_cache/
├── docs/
│   └── semana_1.md
├── .streamlit/
│   └── config.toml
├── requirements.txt
└── README.md

Separación clara entre:
- Lógica de negocio (cenace_client.py)
- Interfaz (Streamlit)
- Persistencia local (Parquet)

---

## 4. Cliente CENACE
Se implementó un cliente HTTP que:
- Consume el WebMethod: https://www.cenace.gob.mx/GraficaDemanda.aspx/obtieneValoresTotal
- Utiliza headers compatibles con el endpoint ASP.NET
- Permite consultar SIN, BCA y BCS
- Convierte valores a numéricos y genera timestamp horario

Nota: los datos no son simulados (si CENACE cae, el cliente puede regresar ceros solo si se habilita el modo mock).

---

## 5. Sistema de Caché
Se implementó almacenamiento local en formato Parquet para:
- Evitar consultas repetidas
- Mejorar tiempos de respuesta
- Permitir funcionamiento si CENACE está temporalmente fuera de servicio

Directorio: data_cache/

---

## 6. Validación y Calidad de Datos
Se revisa:
- Columnas esperadas
- Rango horario 1–24
- Horas faltantes
- Duplicados
- Conteo de NaN en columnas clave

---

## 7. Supuestos (Scope actual)
- Se modela cada sistema como entidad independiente.
- Se trabaja con la jornada actual (limitación del endpoint consultado).
- No se modelan restricciones de transmisión.
- No se modelan rampas ni unit commitment.
- No se ejecuta optimización todavía.

---

## 8. Not Doing Yet (No implementado todavía)
- Despacho económico con optimización (PyPSA).
- Modelado de red eléctrica y transmisión.
- Análisis multi-día / histórico.
- Integración de tecnologías (renovables, baterías, costos marginales).

---

## 9. Cómo correr local

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app/Home.py

---

## 10. App desplegada
Pega aquí tu link de Streamlit Cloud:
-https://4oh5guu4tjacdu9qus82ys.streamlit.app/Demanda_CENACE

---

## 11. Resultado
Semana 1 deja una base modular, reproducible y lista para expandirse con PyPSA en las siguientes semanas.