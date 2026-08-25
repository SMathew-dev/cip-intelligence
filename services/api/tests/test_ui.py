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
    assert "No PLC/HMI write path" in response.text


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
