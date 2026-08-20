"""Day 01 — proof that the environment and the first endpoints actually work.

Day 17 is the full testing day. These tests exist from Day 01 because a course
that adds tests on day 17 teaches that tests are optional.
"""

import pytest

pytestmark = pytest.mark.day01


class TestRoot:
    def test_root_is_discoverable(self, day01_client):
        response = day01_client.get("/")

        assert response.status_code == 200
        body = response.json()
        assert body["service"]
        assert body["endpoints"] == {"health": "/health", "books": "/books"}

    def test_response_is_json(self, day01_client):
        assert day01_client.get("/").headers["content-type"].startswith(
            "application/json"
        )


class TestHealth:
    def test_health_reports_ok(self, day01_client):
        body = day01_client.get("/health").json()

        assert body["status"] == "ok"
        assert body["version"] == "0.1.0"
        assert body["uptime_seconds"] >= 0

    def test_health_timestamp_carries_a_utc_offset(self, day01_client):
        """A naive timestamp is ambiguous: the client has to guess the zone."""
        checked_at = day01_client.get("/health").json()["checked_at"]

        assert checked_at.endswith("Z") or "+00:00" in checked_at

    def test_response_model_strips_undeclared_fields(self, day01_client):
        """`response_model` is a filter, not just documentation."""
        body = day01_client.get("/health").json()

        assert set(body) == {
            "status",
            "service",
            "version",
            "environment",
            "uptime_seconds",
            "checked_at",
        }


class TestBooks:
    def test_catalogue_is_wrapped_in_an_envelope(self, day01_client):
        body = day01_client.get("/books").json()

        assert isinstance(body, dict), "collections must not be bare JSON arrays"
        assert body["count"] == len(body["items"]) == 3

    def test_price_crosses_the_wire_as_a_string(self, day01_client):
        """JSON numbers are floats; money sent as a float loses precision."""
        first = day01_client.get("/books").json()["items"][0]

        assert isinstance(first["price"], str)

    def test_unknown_path_returns_404_as_json(self, day01_client):
        response = day01_client.get("/no-such-endpoint")

        assert response.status_code == 404
        assert response.json() == {"detail": "Not Found"}

    def test_wrong_method_returns_405(self, day01_client):
        """The path exists; the verb does not. That is 405, not 404."""
        assert day01_client.post("/books").status_code == 405


class TestOpenAPI:
    def test_schema_is_generated_from_the_type_hints(self, day01_client):
        schema = day01_client.get("/openapi.json").json()

        assert schema["info"]["version"] == "0.1.0"
        assert "/health" in schema["paths"]
        assert "HealthResponse" in schema["components"]["schemas"]

    def test_docs_are_served_in_development(self, day01_client):
        assert day01_client.get("/docs").status_code == 200


class TestConfiguration:
    def test_docs_are_disabled_in_production(self):
        """Interactive docs advertise the whole attack surface. Not in prod."""
        from fastapi.testclient import TestClient

        from tests.conftest import import_from_day

        day = "01_environment_setup_and_first_api"
        config = import_from_day(day, "shelfspace.config")
        main = import_from_day(day, "shelfspace.main")

        prod_app = main.create_app(config.Settings(environment="production"))

        with TestClient(prod_app) as client:
            assert client.get("/docs").status_code == 404
            assert client.get("/openapi.json").status_code == 404

    def test_settings_read_from_the_environment(self, monkeypatch):
        from tests.conftest import import_from_day

        config = import_from_day("01_environment_setup_and_first_api", "shelfspace.config")
        monkeypatch.setenv("SHELFSPACE_PORT", "9999")

        assert config.Settings().port == 9999

    def test_settings_reject_a_non_integer_port(self, monkeypatch):
        """Fail at startup, not on the first request that needs the value."""
        from pydantic import ValidationError

        from tests.conftest import import_from_day

        config = import_from_day("01_environment_setup_and_first_api", "shelfspace.config")
        monkeypatch.setenv("SHELFSPACE_PORT", "not-a-port")

        with pytest.raises(ValidationError):
            config.Settings()
