"""Request-boundary and lazy API integration checks for context preparation."""
import base64
import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest
from backend.api.app import create_app

EXAMPLE=Path(__file__).resolve().parents[1]/'examples/parent_context'

# Windows needs an internal socketpair to construct an event loop. Construct it
# during collection, before the existing offline guard; all app requests below
# run under that guard using an in-memory ASGI transport, without TCP.
_LOOP=asyncio.new_event_loop()

class TestClient:
    __test__=False
    def __init__(self,app):self.app=app
    def post(self,url,**kwargs):
        async def invoke():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://testserver') as client:
                return await client.post(url,**kwargs)
        return _LOOP.run_until_complete(invoke())

def teardown_module():_LOOP.close()

def body():
    return {'schema':'ParentContextRequestV1','files':{name:base64.b64encode((EXAMPLE/(name+'.txt' if name=='source' else name+'.json')).read_bytes()).decode() for name in ('source','segments','retrieval','binding')},'max_context_bytes':24000}

def test_actual_api_matches_canonical_and_writes_nothing(tmp_path):
    client=TestClient(create_app(project_root=tmp_path))
    result=client.post('/contexts/parent/prepare',json=body())
    assert result.status_code==200
    r=result.json();assert r['schema']=='ParentContextResponseV1'
    assert r['report']['status']=='PREPARED_FOR_REVIEW'
    assert r['context_text']=='第七条　甲应保存凭证。\n乙应复核凭证。'
    assert list(tmp_path.iterdir())==[]
    assert r['report']['gate_result'] is None and r['report']['human_labels'] is None

def test_business_review_stays_http_200_without_partial_context():
    data=body();data['files']['retrieval']=base64.b64encode((EXAMPLE/'retrieval-invalid.json').read_bytes()).decode()
    r=TestClient(create_app()).post('/contexts/parent/prepare',json=data)
    assert r.status_code==200 and r.json()['context_text'] is None
    assert r.json()['report']['status']=='REVIEW'

@pytest.mark.parametrize('case',['version','extra','missing','bool_limit','zero_limit','string_limit','bad_base64','non_string','empty_file','duplicate_json','malformed_json'])
def test_invalid_transport_is_rejected_without_echo(case):
    data=body()
    if case=='version':data['schema']='future'
    if case=='extra':data['path']='private-path-marker'
    if case=='missing':del data['files']['binding']
    if case=='bool_limit':data['max_context_bytes']=True
    if case=='zero_limit':data['max_context_bytes']=0
    if case=='string_limit':data['max_context_bytes']='24000'
    if case=='bad_base64':data['files']['source']='private-path-marker!'
    if case=='non_string':data['files']['source']=42
    if case=='empty_file':data['files']['source']=''
    content='{"schema":"a","schema":"private-path-marker"}' if case=='duplicate_json' else '{' if case=='malformed_json' else json.dumps(data)
    r=TestClient(create_app()).post('/contexts/parent/prepare',content=content,headers={'content-type':'application/json'})
    assert r.status_code==422 and 'private-path-marker' not in r.text

def test_content_type_and_public_read_only(monkeypatch):
    client=TestClient(create_app())
    assert client.post('/contexts/parent/prepare',content='x').status_code==415
    monkeypatch.setenv('VERISCOPE_READ_ONLY','1')
    assert client.post('/contexts/parent/prepare',json=body()).status_code==423

def test_actual_request_size_limit(monkeypatch):
    from backend.api import parent_context
    monkeypatch.setattr(parent_context,'MAX_REQUEST_BYTES',50)
    assert TestClient(create_app()).post('/contexts/parent/prepare',json=body()).status_code==413

def test_decoded_total_limit(monkeypatch):
    from backend.api import parent_context
    data=body();data['max_context_bytes']=1
    monkeypatch.setattr(parent_context,'MAX_TOTAL_BYTES',500)
    # Each individual file is <=500 except segments; size rejection must not leak it.
    for name in data['files']:data['files'][name]=base64.b64encode(b'X'*200).decode()
    assert TestClient(create_app()).post('/contexts/parent/prepare',json=data).status_code==413
