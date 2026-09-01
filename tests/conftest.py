import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def workspace_dir():
    path = ROOT / "test-artifacts" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path
