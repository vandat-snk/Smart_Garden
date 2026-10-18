from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from backend.config import Settings
from backend.db import Base, connect
from backend.main import create_app


def pytest_addoption(parser):
    parser.addoption('--postgres', action='store_true', help='Run against isolated smart_garden_test database')


@pytest.fixture
def app(tmp_path, request):
    url = f'sqlite:///{tmp_path / "test.db"}'
    if request.config.getoption('--postgres'):
        parsed = make_url(Settings().database_url).set(database='smart_garden_test')
        assert parsed.database == 'smart_garden_test'
        url = parsed.render_as_string(hide_password=False)
    cfg = Settings(_env_file=None, database_url=url, admin_token='test-admin-'+'a'*30,
        device_key='test-device-'+'b'*30, model_dir='models/no-test-model', device_id='garden-01')
    engine, _ = connect(url)
    Base.metadata.drop_all(engine)
    engine.dispose()
    return create_app(cfg)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def admin():
    return {'Authorization': 'Bearer '+'test-admin-'+'a'*30}


@pytest.fixture
def device():
    return {'X-Device-Key': 'test-device-'+'b'*30}
