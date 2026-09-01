from datetime import datetime, timezone

from src.spoa_monitor.config import DATASETS
from src.spoa_monitor.processing import build_indicators
from src.spoa_monitor.reporting import generate_pdf, render_report_html
from src.spoa_monitor.utils import sha256_file
from tests.test_processing import row


def test_pdf_generation_and_sha256(workspace_dir):
    rows = {key: [row(spec, key + "-1")] for key, spec in DATASETS.items()}
    indicators = build_indicators(rows, DATASETS)
    dataset_manifest = {
        key: {"source_row_count": 1, "filtered_count": 1, "valid_count": 1, "discarded_count": 0, "payload_sha256": "a" * 64}
        for key in DATASETS
    }
    html = render_report_html({
        "run_id": "spoa-test-run",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "datasets": dataset_manifest,
        "indicators": indicators,
    })
    path = workspace_dir / "boletin.pdf"
    digest = generate_pdf(html, path)
    assert path.read_bytes().startswith(b"%PDF")
    assert digest == sha256_file(path) and len(digest) == 64
