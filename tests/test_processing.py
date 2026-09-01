from src.spoa_monitor.config import DATASETS
from src.spoa_monitor.processing import build_indicators, dataset_indicators, validate_and_deduplicate
from src.spoa_monitor.socrata import territorial_validation
from src.spoa_monitor.utils import normalize_text


def row(spec, entity, year=2026, month=1, delito="Hurto", **extra):
    value = {
        spec.entity_id: entity,
        spec.process_id: extra.pop("process", entity),
        spec.dane_field: "76364",
        spec.municipality_field: "Jamundí",
        spec.department_field: "Valle Del Cauca",
        spec.year_field: str(year),
        spec.month_field: str(month),
        spec.cutoff_field: "31/07/2026",
        "deli_id": extra.pop("deli_id", delito),
        "delito": delito,
        "estado": "Activo",
        "etapa": "Indagación",
    }
    value.update(extra)
    return value


def test_normalizes_jamundi_accents_case_and_spaces():
    assert normalize_text("  jamundí  ") == "JAMUNDI"
    assert normalize_text("JAMUNDI") == "JAMUNDI"


def test_territorial_filter_requires_code_name_and_valle():
    spec = DATASETS["procesos"]
    assert territorial_validation(row(spec, "p1"), spec)[0]
    wrong_code = row(spec, "p2")
    wrong_code[spec.dane_field] = "76001"
    assert territorial_validation(wrong_code, spec) == (False, "codigo_dane_no_jamundi")
    wrong_department = row(spec, "p3")
    wrong_department[spec.department_field] = "Cauca"
    assert not territorial_validation(wrong_department, spec)[0]


def test_unique_counting_keeps_distinct_crimes_but_not_duplicate_rows():
    spec = DATASETS["procesos"]
    rows = [
        row(spec, "p1", deli_id="1", delito="Hurto"),
        row(spec, "p1", deli_id="1", delito="Hurto"),
        row(spec, "p1", deli_id="2", delito="Lesiones"),
        row(spec, "p2", deli_id="1", delito="Hurto"),
    ]
    result = validate_and_deduplicate(rows, spec)
    assert result.duplicates == 1
    indicators = dataset_indicators(result.valid, spec)
    assert indicators["current_ytd"] == 2
    assert {item["crime"]: item["value"] for item in indicators["top_crimes"]} == {"Hurto": 2, "Lesiones": 1}


def test_process_victim_processed_counts_and_relationships_are_distinct():
    rows = {
        "procesos": [row(DATASETS["procesos"], "p1"), row(DATASETS["procesos"], "p2")],
        "victimas": [row(DATASETS["victimas"], "v1", process="p1"), row(DATASETS["victimas"], "v1", process="p1", deli_id="x")],
        "procesados": [row(DATASETS["procesados"], "a1", process="p1")],
    }
    indicators = build_indicators(rows, DATASETS)
    assert indicators["datasets"]["procesos"]["current_ytd"] == 2
    assert indicators["datasets"]["victimas"]["current_ytd"] == 1
    assert indicators["datasets"]["procesados"]["current_ytd"] == 1
    assert indicators["relationships"]["processes_with_both"] == 1


def test_period_comparison_uses_equivalent_months_and_handles_empty():
    spec = DATASETS["procesos"]
    rows = [row(spec, "a", 2026, 1), row(spec, "b", 2026, 2), row(spec, "c", 2025, 1), row(spec, "d", 2025, 8)]
    indicators = dataset_indicators(rows, spec)
    assert indicators["through_month"] == 2
    assert indicators["current_ytd"] == 2
    assert indicators["previous_ytd"] == 1
    empty = dataset_indicators([], spec)
    assert empty["current_ytd"] == 0 and empty["percentage_change"] is None

