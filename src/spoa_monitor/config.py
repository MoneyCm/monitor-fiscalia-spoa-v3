from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple


OFFICIAL_STATS_URL = "https://www.fiscalia.gov.co/gestion/estadisticas/"
SOCRATA_DOMAIN = "https://www.datos.gov.co"
JAMUNDI_DANE = "76364"
JAMUNDI_NAME = "JAMUNDI"
VALLE_NAME = "VALLE DEL CAUCA"
METHODOLOGY_NOTE = (
    "Fuente: Fiscalía General de la Nación, Sistema Penal Oral Acusatorio —SPOA—, "
    "Datos Abiertos V3. La información es estadística, pública e informativa, está "
    "sujeta a actualización y no corresponde a decisiones judiciales definitivas. "
    "Los registros SPOA constituyen una fuente judicial complementaria y no son "
    "directamente equivalentes a los registros de criminalidad de la Policía Nacional, "
    "Medicina Legal, Comisarías de Familia o Inspecciones de Policía."
)


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    dataset_id: str
    title: str
    about_url: str
    entity_id: str
    process_id: str
    municipality_field: str
    dane_field: str
    department_field: str
    year_field: str
    month_field: str
    cutoff_field: str
    expected_columns: Tuple[str, ...]


PROCESOS_COLUMNS = (
    "proceso_anonimizado", "proceso_conexado", "proceso_con_conexidad", "es_ruptura",
    "estado", "etapa", "ley", "tipo_noticia", "procedimiento_abreviado", "pais_hecho",
    "departamento_hecho", "municipio_hecho", "cod_dane_hecho", "dir_hecho_rural_urbana",
    "seccional", "a_o_hecho", "mes_hecho", "trimestre_hecho", "a_o_creacion_proceso",
    "mes_creacion_proceso", "trimestre_creacion", "tipo_delito", "deli_id", "delito",
    "titulo_delito", "capitulo_delito", "grado_delito", "delito_querellable",
    "fecha_corte_datos",
)
VICTIMAS_COLUMNS = (
    "id_victima_anonimizado", "proceso_anonimizado", "deli_id", "delito", "titulo_delito",
    "capitulo_delito", "grado_delito", "occiso_homi_femi", "caracterizacion_homicidio_femi",
    "modalidad_homicidio_femi", "proceso_conexado", "proceso_con_conexidad", "es_ruptura",
    "estado", "etapa", "tipo_noticia", "procedimiento_abreviado", "seccional",
    "proceso_anonimizado_origen", "departamento_hecho_origen", "municipio_hecho_origen",
    "cod_dane_hecho_origen", "a_o_hecho_origen", "mes_hecho_origen", "trimestre_hecho_origen",
    "a_o_creacion_origen", "mes_creacion_origen", "trimestre_creacion_origen", "sexo",
    "aplica_lgbti", "identidad_genero_orient_sexual", "grupo_etario", "victima_nna",
    "pais_nacimiento", "nivel_educativo", "etnia", "comunidad_indigena", "religioso",
    "periodista", "profesor", "ddhh", "discapacidad", "fecha_actualizacion_datos",
)
PROCESADOS_COLUMNS = (
    "id_procesado_anonimizado", "proceso_anonimizado", "deli_id", "delito", "titulo_delito",
    "capitulo_delito", "grado_delito", "proceso_conexado", "proceso_con_conexidad", "es_ruptura",
    "estado", "etapa", "tipo_noticia", "procedimiento_abreviado", "seccional",
    "proceso_anonimizado_origen", "departamento_hecho_origen", "municipio_hecho_origen",
    "cod_dane_hecho_origen", "a_o_hecho_origen", "mes_hecho_origen", "trimestre_hecho_origen",
    "a_o_creacion_origen", "mes_creacion_origen", "trimestre_creacion_origen", "sexo",
    "aplica_lgbti", "identidad_genero_orient_sexual", "grupo_etario", "pais_nacimiento",
    "nivel_educativo", "etnia", "comunidad_indigena", "religioso", "periodista", "profesor",
    "ddhh", "discapacidad", "categoria_procesado", "tipo_sentencia", "fecha_actualizacion_datos",
)

DATASETS: Dict[str, DatasetSpec] = {
    "procesos": DatasetSpec(
        "procesos", "dbdv-iihs", "Procesos Fiscalía V3",
        "https://www.datos.gov.co/Justicia-y-Derecho/Procesos-Fiscal-a-V3/dbdv-iihs/about_data",
        "proceso_anonimizado", "proceso_anonimizado", "municipio_hecho", "cod_dane_hecho",
        "departamento_hecho", "a_o_hecho", "mes_hecho", "fecha_corte_datos", PROCESOS_COLUMNS,
    ),
    "victimas": DatasetSpec(
        "victimas", "hr73-zqjf", "Víctimas Fiscalía V3",
        "https://www.datos.gov.co/Justicia-y-Derecho/V-ctimas-Fiscal-a-V3/hr73-zqjf/about_data",
        "id_victima_anonimizado", "proceso_anonimizado", "municipio_hecho_origen",
        "cod_dane_hecho_origen", "departamento_hecho_origen", "a_o_hecho_origen",
        "mes_hecho_origen", "fecha_actualizacion_datos", VICTIMAS_COLUMNS,
    ),
    "procesados": DatasetSpec(
        "procesados", "piva-db2c", "Procesados Fiscalía V3",
        "https://www.datos.gov.co/Justicia-y-Derecho/Procesados-Fiscal-a-V3/piva-db2c/about_data",
        "id_procesado_anonimizado", "proceso_anonimizado", "municipio_hecho_origen",
        "cod_dane_hecho_origen", "departamento_hecho_origen", "a_o_hecho_origen",
        "mes_hecho_origen", "fecha_actualizacion_datos", PROCESADOS_COLUMNS,
    ),
}


ALERT_THRESHOLDS = {
    "minimum_absolute_increase": 3,
    "minimum_percentage_increase": 20.0,
    "minimum_current_count": 5,
    "high_process_without_processed_ratio": 0.35,
    "critical_patterns": (
        "HOMICID", "FEMINICID", "VIOLENCIA INTRAFAMILIAR", "EXTORS", "SECUESTRO",
    ),
}


CRIME_GROUPS = {
    "Homicidio y feminicidio": ("HOMICID", "FEMINICID"),
    "Violencia intrafamiliar": ("VIOLENCIA INTRAFAMILIAR",),
    "Delitos sexuales": ("SEXUAL", "ACCESO CARNAL", "ACTO SEXUAL", "PORNOGRAF"),
    "Hurto": ("HURTO",),
    "Extorsión": ("EXTORS" ,),
    "Lesiones personales": ("LESION",),
    "Estupefacientes": ("ESTUPEFACIENT",),
    "Delitos con armas": ("ARMA DE FUEGO", "PORTE DE ARMAS", "FABRICACION TRAFICO PORTE ARMAS"),
}


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    data_dir: Path
    output_dir: Path
    socrata_app_token: str
    timeout_seconds: int
    page_size: int
    sisc_api_url: str
    sisc_monitor_key: str
    sisc_sync_enabled: bool
    dashboard_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            state_dir=Path(os.getenv("SPOA_STATE_DIR", "state")),
            data_dir=Path(os.getenv("SPOA_DATA_DIR", "data")),
            output_dir=Path(os.getenv("SPOA_OUTPUT_DIR", "output")),
            socrata_app_token=os.getenv("SOCRATA_APP_TOKEN", "").strip(),
            timeout_seconds=int(os.getenv("SOCRATA_TIMEOUT_SECONDS", "60")),
            page_size=max(1, min(int(os.getenv("SOCRATA_PAGE_SIZE", "50000")), 50000)),
            sisc_api_url=os.getenv("SISC_API_URL", "https://sisc-backend.onrender.com/api").rstrip("/"),
            sisc_monitor_key=os.getenv("SISC_SOURCE_MONITOR_KEY", "").strip(),
            sisc_sync_enabled=os.getenv("SISC_SYNC_ENABLED", "true").lower() in {"1", "true", "yes"},
            dashboard_url=os.getenv("SISC_DASHBOARD_URL", "https://sisc-frontend.onrender.com").strip(),
        )

