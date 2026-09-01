from pathlib import Path

from src.spoa_monitor.config import DATASETS, Settings
from src.spoa_monitor.pipeline import MonitorPipeline
from src.spoa_monitor.socrata import MetadataSnapshot
from tests.test_processing import row


class FakeClient:
    def __init__(self, rows=None, metadata_hash="m" * 64, bad_schema=False):
        self.rows = rows or {key: [row(spec, key + "-1")] for key, spec in DATASETS.items()}
        self.metadata_hash = metadata_hash
        self.bad_schema = bad_schema

    def metadata(self, spec):
        columns = spec.expected_columns[:-1] if self.bad_schema else spec.expected_columns
        return MetadataSnapshot(spec.dataset_id, spec.title, 1, 1, 1, 100, columns, tuple("text" for _ in columns), self.metadata_hash, {})

    def validate_schema(self, spec, metadata):
        from src.spoa_monitor.socrata import SocrataClient
        return SocrataClient.validate_schema(spec, metadata)

    def iter_jamundi(self, spec, limit=None):
        yield from self.rows[spec.key][:limit]


class BrokenDownloadClient(FakeClient):
    def iter_jamundi(self, spec, limit=None):
        raise RuntimeError("descarga oficial no disponible")


def settings(tmp_path):
    return Settings(
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "output",
        socrata_app_token="",
        timeout_seconds=1,
        page_size=100,
        sisc_api_url="http://invalid.local/api",
        sisc_monitor_key="",
        sisc_sync_enabled=False,
        dashboard_url="https://sisc.example",
    )


def test_pipeline_is_idempotent_and_does_not_publish_without_update(workspace_dir):
    config = settings(workspace_dir)
    first = MonitorPipeline(config, FakeClient()).run(dry_run=True, sync_sisc=False)
    assert first["status"] == "COMPLETED"
    assert set(first["updated_datasets"]) == set(DATASETS)
    assert Path(first["bulletin_path"]).exists()

    second = MonitorPipeline(config, FakeClient()).run(dry_run=True, sync_sisc=False)
    assert second["status"] == "COMPLETED"
    assert second["updated_datasets"] == []
    assert second["no_change"] is True
    assert second["email_sent"] is False


def test_force_run_rebuilds_without_update_and_remains_dry(workspace_dir):
    config = settings(workspace_dir)
    MonitorPipeline(config, FakeClient()).run(dry_run=True, sync_sisc=False)
    forced = MonitorPipeline(config, FakeClient()).run(force_run=True, dry_run=True, sync_sisc=False)
    assert forced["status"] == "COMPLETED"
    assert forced["updated_datasets"] == []
    assert forced["email_sent"] is False
    assert Path(forced["bulletin_path"]).exists()


def test_metadata_only_change_downloads_but_does_not_publish_same_payload(workspace_dir):
    config = settings(workspace_dir)
    MonitorPipeline(config, FakeClient(metadata_hash="a" * 64)).run(dry_run=True, sync_sisc=False)
    result = MonitorPipeline(config, FakeClient(metadata_hash="b" * 64)).run(dry_run=True, sync_sisc=False)
    assert result["status"] == "COMPLETED"
    assert result["updated_datasets"] == []
    assert result["no_change"] is True


def test_download_error_is_recorded_and_does_not_generate_products(workspace_dir):
    result = MonitorPipeline(settings(workspace_dir), BrokenDownloadClient()).run(dry_run=True, sync_sisc=False)
    assert result["status"] == "FAILED"
    assert "descarga oficial no disponible" in result["warnings"][0]
    assert "bulletin_path" not in result


def test_schema_alert_prevents_bulletin_and_state_replacement(workspace_dir):
    result = MonitorPipeline(settings(workspace_dir), FakeClient(bad_schema=True)).run(dry_run=True, sync_sisc=False)
    assert result["status"] == "SCHEMA_ALERT"
    assert "bulletin_path" not in result
    assert result["warnings"]


def test_download_or_mail_error_is_recorded_without_secret(monkeypatch, workspace_dir):
    config = settings(workspace_dir)
    monkeypatch.setenv("SMTP_PASSWORD", "TOP_SECRET_PASSWORD")
    monkeypatch.setattr("src.spoa_monitor.pipeline.send_report", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fallo SMTP")))
    result = MonitorPipeline(config, FakeClient()).run(dry_run=False, sync_sisc=False)
    assert result["status"] == "FAILED"
    assert "TOP_SECRET_PASSWORD" not in str(result)
    assert "fallo SMTP" in result["warnings"][0]
