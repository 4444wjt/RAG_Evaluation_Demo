"""Public preparation contract tests; only constructed text, no network/models."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest
from backend.core import parent_context as tool

ROOT=Path(__file__).resolve().parents[1]
EXAMPLE=ROOT/'examples/parent_context'

def files():
    return {name:(EXAMPLE/(name+'.txt' if name=='source' else name+'.json')).read_bytes() for name in ('source','segments','retrieval','binding')}

def run(data,limit=24000):
    return tool.transform(*(data[n] for n in ('source','segments','retrieval','binding')),max_context_bytes=limit)

def rewrite(data,name,edit,rebind=False):
    value=json.loads(data[name]);edit(value);data[name]=tool.encode(value)
    if rebind:
        b=json.loads(data['binding']);b['source_sha256']=tool.sha(data['source']);b['segments_sha256']=tool.sha(data['segments']);data['binding']=tool.encode(b)

def test_exact_parent_without_question_or_answer():
    report,context=run(files())
    assert context=='第七条　甲应保存凭证。\n乙应复核凭证。'.encode()
    assert report['status']=='PREPARED_FOR_REVIEW'
    assert report['expanded_source_positions']==[2,3]
    assert report['provider_wire_verified'] is False and report['parent_completeness_verified'] is False
    assert report['human_labels'] is None and report['gate_result'] is None

def test_whole_mixed_list_review_no_partial_context():
    data=files();data['retrieval']=(EXAMPLE/'retrieval-invalid.json').read_bytes()
    report,context=run(data)
    assert context is None and report['context'] is None and report['input_resource_count']==2
    assert report['diagnostics']==[{'code':'UNKNOWN_SEGMENT_ID'}]

@pytest.mark.parametrize('name',['binding_version','source_hash','segments_hash','metadata','document','dataset','unknown_id','changed_text','empty_hits','summary','qa','partial_export','inactive','duplicate_id','bool_position','missing_header','malformed_json','duplicate_key','nonfinite','limit'])
def test_failure_modes_never_yield_context(name):
    data=files()
    if name=='binding_version':rewrite(data,'binding',lambda v:v.update(schema='future'))
    if name=='source_hash':data['source']+=b'changed'
    if name=='segments_hash':data['segments']+=b' '
    if name=='metadata':rewrite(data,'binding',lambda v:v.update(metadata_positions=[]))
    if name=='document':rewrite(data,'retrieval',lambda v:v['records'][0]['resources'][0].update(document_id='other'))
    if name=='dataset':rewrite(data,'retrieval',lambda v:v['records'][0]['resources'][0].update(dataset_id='other'))
    if name=='unknown_id':rewrite(data,'retrieval',lambda v:v['records'][0]['resources'][0].update(segment_id='missing'))
    if name=='changed_text':rewrite(data,'retrieval',lambda v:v['records'][0]['resources'][0].update(content='改写'))
    if name=='empty_hits':rewrite(data,'retrieval',lambda v:v['records'][0].update(resources=[]))
    if name=='summary':rewrite(data,'retrieval',lambda v:v['records'][0]['resources'][0].update(summary='摘要'))
    if name=='qa':rewrite(data,'segments',lambda v:v['data'][0].update(answer='答案'),True)
    if name=='partial_export':rewrite(data,'segments',lambda v:v.update(total_pages=2),True)
    if name=='inactive':rewrite(data,'segments',lambda v:v['data'][1].update(enabled=False),True)
    if name=='duplicate_id':rewrite(data,'segments',lambda v:v['data'][1].update(id=v['data'][2]['id']),True)
    if name=='bool_position':rewrite(data,'segments',lambda v:v['data'][1].update(position=True),True)
    if name=='missing_header':
        data['source']=data['source'].replace('第七条　'.encode(),b'')
        rewrite(data,'segments',lambda v:v['data'][1].update(content='甲应保存凭证。'),True)
    if name=='malformed_json':data['binding']=b'{'
    if name=='duplicate_key':data['binding']=b'{"schema":"a","schema":"b"}'
    if name=='nonfinite':data['binding']=b'{"schema":NaN}'
    report,context=run(data,1 if name=='limit' else 24000)
    assert report['status']=='REVIEW' and report['context'] is None and context is None
    assert report['diagnostics']

def test_rank_is_not_source_position_and_parent_is_deduplicated():
    data=files()
    rewrite(data,'retrieval',lambda v:v['records'][0]['resources'].append({**v['records'][0]['resources'][0],'segment_id':'s2','content':'第七条　甲应保存凭证。','position':901}))
    report,context=run(data)
    assert report['expanded_segment_ids']==['s2','s3'] and context.count('第七条'.encode())==1

def test_jointly_truncated_source_not_proven_complete():
    data=files();data['source']=data['source'].replace('\n丁应核对记录。'.encode(),b'')
    rewrite(data,'segments',lambda v:(v['data'].pop(),v.update(total=4)),True)
    report,context=run(data)
    assert context is not None and report['parent_completeness_verified'] is False

@pytest.mark.parametrize('newline_bom',[b'\xef\xbb\xbf',b'\r\n'])
def test_raw_source_hash_survives_mapping_normalization(newline_bom):
    data=files();data['source']=newline_bom+data['source'] if newline_bom==b'\xef\xbb\xbf' else data['source'].replace(b'\n',newline_bom)
    rewrite(data,'binding',lambda b:b.update(source_sha256=tool.sha(data['source'])))
    report,context=run(data)
    assert context is not None and report['inputs_sha256']['source']==tool.sha(data['source'])

def test_cli_without_site_packages_and_refuses_overwrite(tmp_path):
    command=[sys.executable,'-S','-B',str(ROOT/'scripts/parent_context.py')]
    for name in ('source','segments','retrieval','binding'):
        command+=['--'+name,str(EXAMPLE/(name+'.txt' if name=='source' else name+'.json'))]
    command+=['--out',str(tmp_path/'result')]
    first=subprocess.run(command,capture_output=True)
    assert first.returncode==0,first.stderr
    before=(tmp_path/'result/report.json').read_bytes()
    second=subprocess.run(command,capture_output=True)
    assert second.returncode==2 and (tmp_path/'result/report.json').read_bytes()==before

def test_binding_requires_explicit_declaration_and_checks_metadata(tmp_path):
    command=[sys.executable,'-S','-B',str(ROOT/'scripts/parent_context.py'),'bind','--source',str(EXAMPLE/'source.txt'),'--segments',str(EXAMPLE/'segments.json'),'--document-id','example-doc','--dataset-id','example-set','--metadata-positions','1','--out',str(tmp_path/'binding.json')]
    assert subprocess.run(command,capture_output=True).returncode==2
    assert not (tmp_path/'binding.json').exists()
    assert subprocess.run([*command,'--assert-complete'],capture_output=True).returncode==0
    assert json.loads((tmp_path/'binding.json').read_bytes())==json.loads((EXAMPLE/'binding.json').read_bytes())
