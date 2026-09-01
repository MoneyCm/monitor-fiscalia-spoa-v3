from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .config import ALERT_THRESHOLDS, CRIME_GROUPS, DatasetSpec
from .socrata import territorial_validation
from .utils import normalize_text, parse_date, parse_int, unique_nonempty


@dataclass
class ValidationResult:
    valid: List[dict]
    discarded: List[dict]
    reasons: Dict[str, int]
    duplicates: int


def validate_and_deduplicate(rows: Iterable[dict], spec: DatasetSpec) -> ValidationResult:
    valid: List[dict] = []
    discarded: List[dict] = []
    reasons: Counter[str] = Counter()
    seen: set[tuple[str, str, str]] = set()
    duplicates = 0
    for row in rows:
        ok, reason = territorial_validation(row, spec)
        entity = str(row.get(spec.entity_id) or "").strip()
        process = str(row.get(spec.process_id) or "").strip()
        if not ok:
            reasons[reason] += 1
            discarded.append(row)
            continue
        if not entity:
            reasons["identificador_anonimizado_vacio"] += 1
            discarded.append(row)
            continue
        # Conserva delitos distintos de una entidad, elimina únicamente filas repetidas.
        key = (entity, process, str(row.get("deli_id") or row.get("delito") or "").strip())
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        valid.append(row)
    return ValidationResult(valid, discarded, dict(reasons), duplicates)


def _rows_period(rows: Sequence[dict], spec: DatasetSpec, year: int, through_month: int) -> List[dict]:
    return [
        row for row in rows
        if parse_int(row.get(spec.year_field)) == year
        and (parse_int(row.get(spec.month_field)) or 0) <= through_month
    ]


def _unique_count(rows: Iterable[dict], field: str) -> int:
    return len(unique_nonempty(rows, field))


def _distribution(rows: Iterable[dict], entity_id: str, field: str, limit: int = 12) -> List[dict]:
    buckets: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        entity = str(row.get(entity_id) or "").strip()
        if entity:
            buckets[str(row.get(field) or "Sin información").strip()].add(entity)
    return [
        {"label": label, "value": len(values)}
        for label, values in sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))[:limit]
    ]


def _crime_counts(rows: Iterable[dict], entity_id: str) -> Dict[str, int]:
    buckets: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        entity = str(row.get(entity_id) or "").strip()
        crime = str(row.get("delito") or "Sin información").strip()
        if entity:
            buckets[crime].add(entity)
    return {crime: len(values) for crime, values in buckets.items()}


def _grouped_crimes(rows: Iterable[dict], entity_id: str) -> Dict[str, int]:
    buckets: dict[str, set[str]] = {name: set() for name in CRIME_GROUPS}
    for row in rows:
        entity = str(row.get(entity_id) or "").strip()
        haystack = normalize_text(" ".join(str(row.get(field) or "") for field in ("delito", "titulo_delito", "capitulo_delito")))
        for name, patterns in CRIME_GROUPS.items():
            if entity and any(normalize_text(pattern) in haystack for pattern in patterns):
                buckets[name].add(entity)
    return {name: len(values) for name, values in buckets.items()}


def dataset_indicators(rows: Sequence[dict], spec: DatasetSpec) -> Dict[str, Any]:
    years = sorted({year for row in rows if (year := parse_int(row.get(spec.year_field)))})
    current_year = max(years) if years else date.today().year
    current_months = [
        parse_int(row.get(spec.month_field)) or 0
        for row in rows if parse_int(row.get(spec.year_field)) == current_year
    ]
    through_month = max(current_months) if current_months else 12
    current = _rows_period(rows, spec, current_year, through_month)
    previous = _rows_period(rows, spec, current_year - 1, through_month)
    current_total = _unique_count(current, spec.entity_id)
    previous_total = _unique_count(previous, spec.entity_id)
    absolute = current_total - previous_total
    percentage = None if previous_total == 0 else round(absolute * 100.0 / previous_total, 1)

    monthly = []
    for month in range(1, through_month + 1):
        current_month_rows = [row for row in current if parse_int(row.get(spec.month_field)) == month]
        previous_month_rows = [row for row in previous if parse_int(row.get(spec.month_field)) == month]
        monthly.append(
            {
                "month": month,
                "current": _unique_count(current_month_rows, spec.entity_id),
                "previous": _unique_count(previous_month_rows, spec.entity_id),
            }
        )

    crimes_current = _crime_counts(current, spec.entity_id)
    crimes_previous = _crime_counts(previous, spec.entity_id)
    crime_changes = [
        {
            "crime": crime,
            "current": crimes_current.get(crime, 0),
            "previous": crimes_previous.get(crime, 0),
            "absolute": crimes_current.get(crime, 0) - crimes_previous.get(crime, 0),
        }
        for crime in set(crimes_current) | set(crimes_previous)
    ]
    crime_changes.sort(key=lambda item: (-abs(item["absolute"]), item["crime"]))
    cutoffs = [parsed for row in rows if (parsed := parse_date(row.get(spec.cutoff_field)))]
    result: Dict[str, Any] = {
        "dataset_id": spec.dataset_id,
        "entity": spec.key,
        "cutoff_date": max(cutoffs).isoformat() if cutoffs else None,
        "overall_unique": _unique_count(rows, spec.entity_id),
        "unique_processes": _unique_count(rows, spec.process_id),
        "current_year": current_year,
        "through_month": through_month,
        "current_ytd": current_total,
        "previous_ytd": previous_total,
        "absolute_change": absolute,
        "percentage_change": percentage,
        "monthly": monthly,
        "top_crimes": [
            {"crime": crime, "value": count}
            for crime, count in sorted(crimes_current.items(), key=lambda item: (-item[1], item[0]))[:10]
        ],
        "crime_changes": crime_changes[:12],
        "crime_groups": _grouped_crimes(current, spec.entity_id),
        "state_distribution": _distribution(current, spec.entity_id, "estado"),
        "stage_distribution": _distribution(current, spec.entity_id, "etapa"),
    }
    if spec.key in {"victimas", "procesados"}:
        result["sex_distribution"] = _distribution(current, spec.entity_id, "sexo")
        result["age_distribution"] = _distribution(current, spec.entity_id, "grupo_etario")
        result["ethnicity_distribution"] = _distribution(current, spec.entity_id, "etnia")
        result["education_distribution"] = _distribution(current, spec.entity_id, "nivel_educativo")
        result["disability_distribution"] = _distribution(current, spec.entity_id, "discapacidad")
        result["gender_identity_distribution"] = _distribution(
            current, spec.entity_id, "identidad_genero_orient_sexual"
        )
    if spec.key == "victimas":
        result["nna_distribution"] = _distribution(current, spec.entity_id, "victima_nna")
        result["homicide_femicide_deaths"] = _distribution(current, spec.entity_id, "occiso_homi_femi")
    if spec.key == "procesados":
        result["processed_category_distribution"] = _distribution(current, spec.entity_id, "categoria_procesado")
        result["sentence_distribution"] = _distribution(current, spec.entity_id, "tipo_sentencia")
    return result


def build_indicators(rows_by_dataset: Mapping[str, Sequence[dict]], specs: Mapping[str, DatasetSpec]) -> Dict[str, Any]:
    datasets = {key: dataset_indicators(rows_by_dataset[key], specs[key]) for key in specs}
    process_sets = {key: unique_nonempty(rows_by_dataset[key], specs[key].process_id) for key in specs}
    relationships = {
        "processes_with_victims": len(process_sets["procesos"] & process_sets["victimas"]),
        "processes_with_processed": len(process_sets["procesos"] & process_sets["procesados"]),
        "processes_with_both": len(process_sets["procesos"] & process_sets["victimas"] & process_sets["procesados"]),
        "victim_processes_without_processed": len(process_sets["victimas"] - process_sets["procesados"]),
    }
    alerts: List[dict] = []
    for key, item in datasets.items():
        for change in item["crime_changes"]:
            previous = change["previous"]
            percentage = None if not previous else change["absolute"] * 100.0 / previous
            if (
                change["absolute"] >= ALERT_THRESHOLDS["minimum_absolute_increase"]
                and change["current"] >= ALERT_THRESHOLDS["minimum_current_count"]
                and (percentage is None or percentage >= ALERT_THRESHOLDS["minimum_percentage_increase"])
            ):
                alerts.append({"dataset": key, **change, "percentage": None if percentage is None else round(percentage, 1)})
    alerts.sort(key=lambda item: (-item["absolute"], item["dataset"], item["crime"]))
    cutoffs = [parse_date(item.get("cutoff_date")) for item in datasets.values()]
    return {
        "datasets": datasets,
        "relationships": relationships,
        "alerts": alerts[:12],
        "cutoff_date": max(item for item in cutoffs if item).isoformat() if any(cutoffs) else None,
        "thresholds": ALERT_THRESHOLDS,
    }

