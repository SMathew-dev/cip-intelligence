from fastapi.testclient import TestClient
from app.main import app
client=TestClient(app)
def test_security_headers_and_request_id():
 r=client.get('/health'); assert r.status_code==200; assert r.headers['x-content-type-options']=='nosniff'; assert r.headers['x-frame-options']=='DENY'; assert r.headers['x-request-id']
def test_readiness():
 r=client.get('/ready'); assert r.status_code==200; assert r.json()['mode']=='read_only'
def test_connector_rejects_write_mode():
 r=client.post('/v1/connectors',json={'name':'bad-write','kind':'opcua_readonly','mode':'read_write'}); assert r.status_code==422
def test_connector_contract_is_read_only():
 import uuid
 r=client.post('/v1/connectors',json={'name':'test-'+str(uuid.uuid4()),'kind':'opcua_readonly','mode':'read_only','config':{'endpoint':'opc.tcp://example'}}); assert r.status_code==200; assert r.json()['mode']=='read_only'
def test_audit_log_available_in_demo_mode():
 client.get('/v1/demo/ui/overview'); r=client.get('/v1/admin/audit?limit=5'); assert r.status_code==200; assert len(r.json()['events'])>=1

def test_operational_store_recovers_if_runtime_directory_is_recreated(tmp_path):
    from app.production import ProductionDB
    import shutil
    db_path = tmp_path / 'runtime' / 'production.sqlite3'
    db = ProductionDB(db_path)
    db.audit(id='a1', ts='2026-08-25T00:00:00+00:00', actor='test', role='admin', method='GET', path='/x', status=200, request_id='r1')
    shutil.rmtree(db_path.parent)
    # A long-lived API process must not crash if its ephemeral runtime volume
    # is recreated. The operational schema should be restored lazily.
    db.audit(id='a2', ts='2026-08-25T00:00:01+00:00', actor='test', role='admin', method='GET', path='/y', status=200, request_id='r2')
    events = db.audits(10)
    assert len(events) == 1
    assert events[0]['id'] == 'a2'
