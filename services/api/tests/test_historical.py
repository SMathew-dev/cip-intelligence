from app.historical import historical_intelligence


def test_historical_intelligence_is_deterministic():
    first = historical_intelligence(90)
    second = historical_intelligence(90)
    assert first == second
    assert first["simulator_only"] is True
    assert first["summary"]["cycles"] == 450
    assert first["summary"]["assets"] == 5


def test_historical_intelligence_prioritizes_known_drift_patterns():
    result = historical_intelligence(90)
    ranking = {row["asset"]: row for row in result["asset_ranking"]}

    assert ranking["HTST-02"]["status"] == "ATTENTION"
    assert ranking["HTST-02"]["flow_change_lpm"] < -15
    assert ranking["HTST-02"]["duration_change_min"] > 3

    assert ranking["VAT-04"]["process_deviations"] > 0
    assert ranking["UF-01"]["data_reviews"] == 3
    assert ranking["UF-01"]["water_change_m3_per_cycle"] > 1

    assert ranking["HTST-01"]["status"] == "STABLE"
    assert ranking["SILO-07"]["status"] == "STABLE"


def test_history_windows_are_constrained_and_distinct():
    thirty = historical_intelligence(30)
    sixty = historical_intelligence(60)
    ninety = historical_intelligence(90)
    below_minimum = historical_intelligence(7)
    above_maximum = historical_intelligence(365)

    assert thirty["window_days"] == 30
    assert sixty["window_days"] == 60
    assert ninety["window_days"] == 90
    assert below_minimum["window_days"] == 30
    assert above_maximum["window_days"] == 90
    assert thirty["summary"]["cycles"] < sixty["summary"]["cycles"] < ninety["summary"]["cycles"]


def test_attention_score_is_advisory_only():
    result = historical_intelligence(90)
    assert "do not alter L2 compliance" in result["interpretation"]
    assert "authorize process changes" in result["interpretation"]
