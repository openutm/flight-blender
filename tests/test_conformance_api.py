import pytest
from tests.conftest import auth_header, READ_SCOPE


@pytest.mark.django_db
class TestConformanceStatus:
    def test_conformance_status(self, client):
        resp = client.get(
            "/conformance_monitoring_ops/conformance_status",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "OK"


@pytest.mark.django_db
class TestConformanceRecords:
    def test_get_conformance_records_missing_params(self, client):
        # View uses request.parameters (non-standard) → Django raises AttributeError → 500
        resp = client.get(
            "/conformance_monitoring_ops/get_conformance_records",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 500

    def test_conformance_record_summary_missing_params(self, client):
        resp = client.get(
            "/conformance_monitoring_ops/conformance_record_summary",
            **auth_header(READ_SCOPE),
        )
        assert resp.status_code == 500
