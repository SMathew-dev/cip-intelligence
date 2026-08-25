from app.intelligence.data_quality import detect_flatline


def test_partial_flatline_is_detected() -> None:
    values = [400 + i * 0.1 for i in range(10)] + [402.0] * 20 + [405 + i * 0.1 for i in range(10)]
    issues = detect_flatline(values, min_consecutive_points=12)
    assert issues
    assert issues[0].code == "FLATLINE"
    assert issues[0].evidence["consecutive_samples"] == 20

def test_in_memory_simulator_matches_csv_fixture(tmp_path):
    """M11's faster validation path must use the exact simulator values exposed by CSV fixtures."""
    import csv
    from app.simulator import generate_cycle, generate_cycle_rows
    path = tmp_path / 'cycle.csv'
    expected = generate_cycle_rows('normal', seed=909, asset='HTST-01')
    generate_cycle(path, 'normal', seed=909, asset='HTST-01')
    with path.open() as f:
        actual = list(csv.DictReader(f))
    assert len(actual) == len(expected)
    assert actual[0]['timestamp'] == expected[0]['timestamp']
    assert float(actual[50]['return_flow_lpm']) == expected[50]['return_flow_lpm']
    assert float(actual[-1]['return_conductivity_mscm']) == expected[-1]['return_conductivity_mscm']
