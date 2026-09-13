import json
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_research_validation_assets_are_exposed() -> None:
    page = client.get('/app/')
    script = client.get('/app/research-validation.js')
    style = client.get('/app/research.css')
    catalog = client.get('/app/research-benchmarks.json')

    assert page.status_code == 200
    assert 'Research Validation' in page.text
    assert '/app/research-validation.js' in page.text
    assert '/app/research.css' in page.text
    assert script.status_code == 200
    assert style.status_code == 200
    assert catalog.status_code == 200


def test_benchmark_catalog_does_not_claim_unrun_research_validation() -> None:
    response = client.get('/app/research-benchmarks.json')
    payload = response.json()

    assert payload['version'] == '1.0'
    assert len(payload['benchmarks']) >= 4
    published = [b for b in payload['benchmarks'] if b['benchmark_type'].startswith('published')]
    assert published
    assert all('awaiting' in b['validation_state'] for b in published)
    assert not any(b['validation_state'].startswith('validated') for b in published)


def test_uht_benchmark_preserves_sensor_and_resource_boundaries() -> None:
    payload = client.get('/app/research-benchmarks.json').json()
    uht = next(b for b in payload['benchmarks'] if b['id'] == 'alvarez-2010-uht')

    assert 'conductivity' in uht['signals_reported']
    assert 'turbidity' in uht['signals_reported']
    assert '5 s during launch and cleaning' in uht['sampling']
    assert any('about half' in finding for finding in uht['published_findings'])
    assert any('withhold microbiological-cleanliness claims' in item for item in uht['what_cip_intelligence_should_test'])


def test_mbr_is_labeled_as_ingestion_not_dairy_cip_validation() -> None:
    payload = client.get('/app/research-benchmarks.json').json()
    mbr = next(b for b in payload['benchmarks'] if b['id'] == 'external-mbr-2017')

    assert mbr['benchmark_type'] == 'external-ingestion benchmark'
    assert 'not a dairy-CIP validation benchmark' in mbr['validation_state']
