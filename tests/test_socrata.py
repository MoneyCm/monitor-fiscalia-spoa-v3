import pytest
import requests

from src.spoa_monitor.config import DATASETS
from src.spoa_monitor.socrata import (
    DownloadError,
    MetadataSnapshot,
    SchemaChangeError,
    SocrataClient,
)


def metadata(columns):
    return MetadataSnapshot("dbdv-iihs", "Procesos", 1, 1, 1, 10, tuple(columns), tuple("text" for _ in columns), "a" * 64, {})


def test_schema_change_is_blocking():
    spec = DATASETS["procesos"]
    SocrataClient.validate_schema(spec, metadata(spec.expected_columns))
    with pytest.raises(SchemaChangeError) as error:
        SocrataClient.validate_schema(spec, metadata(spec.expected_columns[:-1] + ("columna_nueva",)))
    assert "fecha_corte_datos" in error.value.missing
    assert "columna_nueva" in error.value.extra


class _FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error

    def json(self):
        return []


def test_get_retries_transient_timeout_then_succeeds(monkeypatch):
    client = SocrataClient("", timeout=1, page_size=10, max_retries=3, backoff_base=0.5)
    calls = {"count": 0}

    def flaky(url, params=None, timeout=None):
        calls["count"] += 1
        if calls["count"] < 3:
            raise requests.ReadTimeout("read timed out")
        return _FakeResponse(200)

    monkeypatch.setattr(client.session, "get", flaky)
    monkeypatch.setattr("src.spoa_monitor.socrata.time.sleep", lambda _: None)
    response = client._get("https://www.datos.gov.co/resource/x.json")
    assert response.status_code == 200
    assert calls["count"] == 3


def test_get_does_not_retry_client_error(monkeypatch):
    client = SocrataClient("", timeout=1, page_size=10, max_retries=4, backoff_base=0.5)
    calls = {"count": 0}

    def not_found(url, params=None, timeout=None):
        calls["count"] += 1
        return _FakeResponse(404)

    monkeypatch.setattr(client.session, "get", not_found)
    monkeypatch.setattr("src.spoa_monitor.socrata.time.sleep", lambda _: None)
    with pytest.raises(DownloadError):
        client._get("https://www.datos.gov.co/resource/x.json")
    assert calls["count"] == 1


def test_get_raises_after_exhausting_retries(monkeypatch):
    client = SocrataClient("", timeout=1, page_size=10, max_retries=3, backoff_base=0.5)

    def always_timeout(url, params=None, timeout=None):
        raise requests.ReadTimeout("read timed out")

    monkeypatch.setattr(client.session, "get", always_timeout)
    monkeypatch.setattr("src.spoa_monitor.socrata.time.sleep", lambda _: None)
    with pytest.raises(DownloadError) as error:
        client._get("https://www.datos.gov.co/resource/x.json")
    assert "fuente oficial" in str(error.value)

