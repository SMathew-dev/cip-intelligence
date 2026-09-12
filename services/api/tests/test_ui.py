from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_redirects_to_product_ui():
    response = client.get("/", follow_redirects=False)
    assert response.status_code in {302, 307}
    assert response.headers["location"] == "/app/"


def test_product_ui_is_served():
    response = client.get("/app/")
    assert response.status_code == 200
    assert "CIP Intelligence" in response.text
    assert "Cycle Explorer" in response.text
    assert "Historical Intelligence" in response.text
    assert "No PLC/HMI write path" in response.text
    assert "Add plant data" in response.text
    assert "/app/premium.css" in response.text
    assert "/app/historical.js" in response.text


def test_data_onboarding_ui_and_inspection_endpoint():
    script = client.get("/app/app.js")
    assert script.status_code == 200
    assert "Inspect an existing plant export" in script.text
    assert "Review suggested mappings" in script.text
    assert "Draft plant organization" in script.text
    response = client.post(
        "/v1/ingestion/inspect",
        files={"file": ("plant.csv", b"ts,CIP Return Temp [F],CIP Return Flow [gpm]\n2026-08-25T11:00:00Z,120,100\n", "text/csv")},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["filename"] == "plant.csv"
    assert payload["timestamp_candidate"]["column"] == "ts"
    assert payload["columns"][1]["mapping_candidates"][0]["concept"] == "cip.return.temperature"
    assert payload["organization_proposal"]["mode"] == "unresolved"


def test_data_inspection_rejects_oversized_csv():
    response = client.post(
        "/v1/ingestion/inspect",
        files={"file": ("large.csv", b"x" * (25 * 1024 * 1024 + 1), "text/csv")},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "CSV exceeds the 25 MB inspection limit."


def test_historical_ui_assets_are_served():
    fixture_response = client.get("/app/historical-data.json")
    script_response = client.get("/app/historical.js")

    assert fixture_response.status_code == 200
    fixture = fixture_response.json()
    assert set(fixture) == {"30", "60", "90"}
    assert fixture["90"]["simulator_only"] is True
    assert fixture["90"]["summary"]["cycles"] == 450
    assert fixture["90"]["interpretation"].startswith("Attention scores prioritize investigation only")

    assert script_response.status_code == 200
    assert "renderHistoricalIntelligence" in script_response.text
    assert "historical-data.json" in script_response.text


def test_ui_overview_fixture_is_explicitly_simulated():
    response = client.get("/v1/demo/ui/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["simulator_only"] is True
    assert payload["plant"]["control_boundary"] == "READ ONLY"
    assert len(payload["assets"]) >= 5


def test_ui_data_health_fixture_keeps_quality_visible():
    response = client.get("/v1/demo/ui/data-health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["blocked_signals"] >= 1
    assert any(sensor["status"] == "LOW" for sensor in payload["sensors"])


def test_ui_timeseries_preserves_phase_and_signal_data():
    response = client.get("/v1/demo/ui/timeseries/normal")
    assert response.status_code == 200
    payload = response.json()
    assert payload["simulator_only"] is True
    assert payload["asset"] == "HTST-01"
    assert len(payload["samples"]) > 200
    assert [p["phase"] for p in payload["phases"]] == [
        "PRE_RINSE", "CAUSTIC", "INTERMEDIATE_RINSE", "ACID", "FINAL_RINSE"
    ]
    first = payload["samples"][0]
    assert {"temperature_c", "flow_lpm", "conductivity_mscm", "pressure_bar"} <= set(first)


def test_ui_timeseries_rejects_unknown_scenario():
    response = client.get("/v1/demo/ui/timeseries/not-a-real-scenario")
    assert response.status_code == 422
