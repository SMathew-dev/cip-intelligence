from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_semantic_discovery_ui_asset_is_loaded() -> None:
    page = client.get("/app/")
    script = client.get("/app/discovery-ui.js")

    assert page.status_code == 200
    assert "/app/discovery-ui.js" in page.text
    assert script.status_code == 200
    assert "SEMANTIC DISCOVERY" in script.text
    assert "Process context needed" in script.text
    assert "will not invent equipment identity" in script.text


def test_inspection_api_exposes_generic_measurement_evidence() -> None:
    response = client.post(
        "/v1/ingestion/inspect",
        files={
            "file": (
                "external.csv",
                b"Real Time,Temperature (C)\n2017-06-20 07:57:00,21.2\n2017-06-20 07:57:01,21.3\n",
                "text/csv",
            )
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["timestamp_candidate"]["column"] == "Real Time"
    assert payload["timestamp_candidate"]["value_supported"] is True
    temperature = next(c for c in payload["columns"] if c["source_column"] == "Temperature (C)")
    assert temperature["measurement_candidate"]["measurement_type"] == "temperature"
    assert temperature["mapping_candidates"] == []
