"""Bounded in-memory parent-context preparation, without persistence or model calls."""
import base64
import binascii
import json
from pathlib import Path

from fastapi import Request
from backend.api.service import EvaluationAPIError

MAX_TOTAL_BYTES=8*1024*1024
MAX_REQUEST_BYTES=12*1024*1024
FILE_NAMES={'source','segments','retrieval','binding'}

def _unique(pairs):
    value={}
    for key,item in pairs:
        if key in value: raise ValueError('duplicate field')
        value[key]=item
    return value

def _constant(_):
    raise ValueError('nonfinite value')

async def prepare_parent_context_request(request:Request)->dict:
    if request.headers.get('content-type','').split(';',1)[0].lower()!='application/json':
        raise EvaluationAPIError(415,'请以四份原始文件提交父条资料准备请求。')
    chunks=[];size=0
    async for chunk in request.stream():
        size+=len(chunk)
        if size>MAX_REQUEST_BYTES:
            raise EvaluationAPIError(413,'四份文件合计不能超过 8 MiB。')
        chunks.append(chunk)
    try:
        body=json.loads(b''.join(chunks),object_pairs_hook=_unique,parse_constant=_constant)
        if not isinstance(body,dict) or set(body)!={'schema','files','max_context_bytes'}:
            raise ValueError('fields')
        if body['schema']!='ParentContextRequestV1' or not isinstance(body['files'],dict) or set(body['files'])!=FILE_NAMES:
            raise ValueError('contract')
        limit=body['max_context_bytes']
        if type(limit) is not int or not 0<limit<=MAX_TOTAL_BYTES:raise ValueError('limit')
        files={}
        for name,value in body['files'].items():
            if not isinstance(value,str):raise ValueError('base64 type')
            raw=base64.b64decode(value,validate=True)
            if not 0<len(raw)<=MAX_TOTAL_BYTES:raise ValueError('empty or oversized file')
            files[name]=raw
        if sum(map(len,files.values()))>MAX_TOTAL_BYTES:
            raise EvaluationAPIError(413,'四份文件合计不能超过 8 MiB。')
    except (ValueError,TypeError,UnicodeError,RecursionError,binascii.Error):
        raise EvaluationAPIError(422,'文件请求不符合格式，请核对四份文件、文件编码和大小。') from None
    # Import only after an explicit preparation request; API health stays lazy.
    from backend.core.parent_context import transform,sha,HERE
    report,context=transform(files['source'],files['segments'],files['retrieval'],files['binding'],max_context_bytes=limit)
    report['implementation_sha256']={name:sha((HERE/name).read_bytes()) for name in ('parent_context.py','parent_context_mapping.py')}
    report['transport_sha256']=sha(Path(__file__).read_bytes())
    return {'schema':'ParentContextResponseV1','report':report,'context_text':None if context is None else context.decode('utf-8')}
