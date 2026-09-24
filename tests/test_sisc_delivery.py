"""Validate the companion SPOA transport change without contacting a server."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

MODULE = Path(__file__).resolve().parents[1] / 'src/spoa_monitor/sisc.py'
pytestmark = pytest.mark.skipif(not MODULE.exists(), reason='Companion monitor checkout unavailable')


def client_module():
    spec = importlib.util.spec_from_file_location('fiscalia_delivery', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_spoa_persists_evidence_before_retrying():
    module = client_module()
    client = module.SiscClient('https://example.test/api', oidc_token='test')
    with patch.object(module.Path, 'write_text') as save, patch.object(client, 'post', side_effect=[TimeoutError(), {'accepted': True}]) as send, patch.object(module.time, 'sleep'):
        assert client.heartbeat({'datasets': {}}, 'CURRENT', 'VALIDATED') == {'accepted': True}
        assert send.call_count == 2
        save.assert_called_once()
        assert 'FISCALIA_SPOA_V3' in save.call_args.args[0]


def test_spoa_keeps_report_on_permanent_error():
    module = client_module()
    client = module.SiscClient('https://example.test/api', oidc_token='test')
    with patch.object(module.Path, 'write_text') as save, patch.object(client, 'post', side_effect=HTTPError('https://example.test', 404, 'missing', {}, None)) as send:
        with pytest.raises(HTTPError):
            client.heartbeat({'datasets': {}}, 'CURRENT', 'VALIDATED')
        assert send.call_count == 1
        save.assert_called_once()
