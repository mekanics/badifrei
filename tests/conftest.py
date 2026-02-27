import pytest
import os

# Integration test marker
def pytest_configure(config):
    config.addinivalue_line("markers", "integration: marks tests requiring live DB")

@pytest.fixture
def test_db_url():
    return os.getenv("TEST_DATABASE_URL", "postgresql://badi:badi@localhost:5433/badi_test")
