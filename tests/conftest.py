import os
import sys

sys.path.insert(0, '/home/hermes/apps/hermes-proxy')
os.chdir('/home/hermes/apps/hermes-proxy')
os.environ.setdefault('HERMES_PROXY_SIGNING_KEY', '9d447d6c2c7a73365f2bd9ab2328ff689d5cf65f1c9773624db21765831b3f85')
os.environ.setdefault('HERMES_PROXY_PASSWORD', 'testpass123')
os.environ.setdefault('API_SERVER_KEY', 'testkey123')

import pytest
from fastapi.testclient import TestClient
import server


@pytest.fixture
def client():
    return TestClient(server.app)
