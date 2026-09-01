import pytest

from src.spoa_monitor.config import DATASETS
from src.spoa_monitor.socrata import MetadataSnapshot, SchemaChangeError, SocrataClient


def metadata(columns):
    return MetadataSnapshot("dbdv-iihs", "Procesos", 1, 1, 1, 10, tuple(columns), tuple("text" for _ in columns), "a" * 64, {})


def test_schema_change_is_blocking():
    spec = DATASETS["procesos"]
    SocrataClient.validate_schema(spec, metadata(spec.expected_columns))
    with pytest.raises(SchemaChangeError) as error:
        SocrataClient.validate_schema(spec, metadata(spec.expected_columns[:-1] + ("columna_nueva",)))
    assert "fecha_corte_datos" in error.value.missing
    assert "columna_nueva" in error.value.extra

