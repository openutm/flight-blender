import pytest
from tests.conftest import fastapi_auth_header, READ_SCOPE


class TestConformanceStatus:
    def test_conformance_status(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get(
            "/conformance_monitoring_ops/conformance_status",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "OK"

    def test_conformance_status_unauthenticated(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get("/conformance_monitoring_ops/conformance_status")
        assert resp.status_code == 401


class TestConformanceRecords:
    def test_get_conformance_records_missing_params(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get(
            "/conformance_monitoring_ops/get_conformance_records",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_get_conformance_records_missing_end_date(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get(
            "/conformance_monitoring_ops/get_conformance_records?start_date=2025-01-01T00:00:00Z",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400

    def test_get_conformance_records_invalid_date_format(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get(
            "/conformance_monitoring_ops/get_conformance_records?start_date=not-a-date&end_date=also-not",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400

    def test_get_conformance_records_start_after_end(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get(
            "/conformance_monitoring_ops/get_conformance_records"
            "?start_date=2025-12-31T00:00:00Z&end_date=2025-01-01T00:00:00Z",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400

    def test_get_conformance_records_valid_returns_empty_list(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get(
            "/conformance_monitoring_ops/get_conformance_records"
            "?start_date=2025-01-01T00:00:00Z&end_date=2025-12-31T23:59:59Z",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "conformance_records" in data
        assert isinstance(data["conformance_records"], list)


class TestConformanceRecordSummary:
    def test_conformance_record_summary_missing_params(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get(
            "/conformance_monitoring_ops/conformance_record_summary",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_conformance_record_summary_invalid_date_format(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get(
            "/conformance_monitoring_ops/conformance_record_summary?start_date=bad&end_date=worse",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400

    def test_conformance_record_summary_start_after_end(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get(
            "/conformance_monitoring_ops/conformance_record_summary"
            "?start_date=2025-12-31T00:00:00Z&end_date=2025-01-01T00:00:00Z",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 400

    def test_conformance_record_summary_valid_empty_db(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get(
            "/conformance_monitoring_ops/conformance_record_summary"
            "?start_date=2025-01-01T00:00:00Z&end_date=2025-12-31T23:59:59Z",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200
        summary = resp.json()["summary"]
        assert summary["total_records"] == 0
        assert summary["conforming_records"] == 0
        assert summary["non_conforming_records"] == 0
        assert summary["conformance_rate_percentage"] == 0.0

    def test_get_conformance_record_summary_alias_route(self, mounted_fastapi_client):
        resp = mounted_fastapi_client.get(
            "/conformance_monitoring_ops/get_conformance_record_summary"
            "?start_date=2025-01-01T00:00:00Z&end_date=2025-12-31T23:59:59Z",
            headers=fastapi_auth_header(READ_SCOPE),
        )
        assert resp.status_code == 200
        assert "summary" in resp.json()
