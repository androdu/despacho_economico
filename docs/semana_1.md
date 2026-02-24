# Semana 1 – Integración de Demanda CENACE

## 1. Objetivo

Implementar un cliente en Python que permita consultar datos reales de demanda eléctrica del CENACE y visualizarlos en una aplicación Streamlit, estableciendo la base para un simulador de despacho económico.

---

## 2. Arquitectura del Proyecto

Estructura implementada:

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
├── requirements.txt
└── README.md

Separación clara entre:
- Lógica de negocio (cenace_client.py)
- Interfaz (Streamlit)
- Persistencia local (cache)

---

## 3. Cliente CENACE

Se implementó un cliente HTTP que:

- Consume el WebMethod:
  https://www.cenace.gob.mx/GraficaDemanda.aspx/obtieneValoresTotal
- Utiliza headers compatibles con el endpoint ASP.NET
- Permite consultar:
  - SIN
  - BCA
  - BCS

---

## 4. Sistema de Caché

Se implementó almacenamiento local en formato Parquet para:

- Evitar consultas repetidas
- Mejorar tiempos de respuesta
- Permitir funcionamiento incluso si CENACE está temporalmente fuera de servicio

Directorio utilizado:
data_cache/

---

## 5. Interfaz

Se desarrolló una aplicación Streamlit que:

- Permite seleccionar sistema eléctrico
- Permite seleccionar rango de fechas
- Visualiza demanda horaria
- Muestra tabla exportable

---

## 6. Validación

Se verificó que:

- Los datos no son simulados
- La información corresponde a datos reales publicados por CENACE
- La app responde tanto con caché como con consulta directa

---

## 7. Resultado

Semana 1 establece una base funcional y profesional para la construcción del modelo de despacho económico utilizando PyPSA en fases posteriores.
## 8. Diagrama Conceptual

Usuario → Streamlit UI → cenace_client.py → CENACE API  
                              ↓  
                         data_cache (Parquet)