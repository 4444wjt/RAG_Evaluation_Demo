"""Offline context preparation; no network, scoring, answers, or gate execution."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from .parent_context_mapping import source_mapping, expand_parent_context

MAX_INPUT = 8 * 1024 * 1024
DEFAULT_OUTPUT = 24000
HERE = Path(__file__).resolve().parent
LIMITS = [
    '仅准备操作者提供的资料；未验证Dify当前索引或最终供应商请求。',
    '父条完整性及正文/元数据边界来自操作者声明，未作法律语义认证。',
    '不核验回答、不计算评分、不作门禁PASS/FAIL；转换成功仍需复核。',
    '字节上限不是token上限；未证明上下文不会被下游裁剪。',
]

class Rejected(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)

def require(condition, code):
    if not condition:
        raise Rejected(code)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')

def parse(raw):
    def pairs(rows):
        out = {}
        for key, value in rows:
            require(key not in out, 'DUPLICATE_JSON_FIELD')
            out[key] = value
        return out
    def constant(_):
        raise Rejected('NON_FINITE_JSON')
    require(isinstance(raw, bytes) and len(raw) <= MAX_INPUT, 'INPUT_TOO_LARGE')
    try:
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=pairs, parse_constant=constant)
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except Rejected:
        raise
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise Rejected('INVALID_JSON') from None
    require(isinstance(value, dict), 'OBJECT_REQUIRED')
    return value

def identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 512 and all(ord(c) >= 32 for c in value)

def transform(source_raw, segments_raw, retrieval_raw, binding_raw, *, max_context_bytes=DEFAULT_OUTPUT):
    """No question/answer/expected address arguments. Returns report and bytes or None."""
    report = {'schema': 'ParentContextPreparationV1', 'status': 'REVIEW',
              'diagnostics': [], 'context': None, 'provider_wire_verified': False,
              'online_index_verified': False, 'parent_completeness_verified': False,
              'human_labels': None, 'gate_result': None, 'limitations': LIMITS,
              'inputs_sha256': {k: sha(v) for k, v in (
                  ('source', source_raw), ('segments', segments_raw), ('retrieval', retrieval_raw), ('binding', binding_raw))}}
    try:
        require(type(max_context_bytes) is int and 0 < max_context_bytes <= MAX_INPUT, 'INVALID_CONTEXT_LIMIT')
        require(len(source_raw) <= MAX_INPUT, 'INPUT_TOO_LARGE')
        binding = parse(binding_raw)
        require(set(binding) == {'schema', 'source_format', 'structure_assertion', 'source_sha256',
                'segments_sha256', 'document_id', 'dataset_id', 'metadata_positions'}, 'BINDING_FIELDS_UNSUPPORTED')
        require(binding['schema'] == 'ParentContextBindingV1', 'BINDING_VERSION_UNSUPPORTED')
        require(binding['source_format'] == 'blank-line-articles-v1', 'SOURCE_FORMAT_UNSUPPORTED')
        require(binding['structure_assertion'] == 'operator-declared-complete-parent-blocks', 'STRUCTURE_DECLARATION_REQUIRED')
        for field in ('source_sha256', 'segments_sha256'):
            require(isinstance(binding[field], str) and re.fullmatch('[a-f0-9]{64}', binding[field]), 'INVALID_BINDING_HASH')
        require(binding['source_sha256'] == sha(source_raw), 'SOURCE_HASH_MISMATCH')
        require(binding['segments_sha256'] == sha(segments_raw), 'SEGMENTS_HASH_MISMATCH')
        require(identifier(binding['document_id']) and identifier(binding['dataset_id']), 'INVALID_DOCUMENT_IDENTITY')
        try:
            source = source_raw.decode('utf-8-sig')
        except UnicodeError:
            raise Rejected('SOURCE_NOT_UTF8') from None
        require('\r' not in source.replace('\r\n', ''), 'SOURCE_NEWLINE_UNSUPPORTED')
        require('\x00' not in source, 'SOURCE_CONTROL_CHARACTER')
        report['source_normalization'] = {
            'utf8_bom_removed_for_mapping': source_raw.startswith(b'\xef\xbb\xbf'),
            'crlf_to_lf_for_mapping': '\r\n' in source,
            'raw_file_sha256_retained': True, 'text_or_punctuation_rewritten': False}
        exported = parse(segments_raw)
        require(exported.get('page') == 1 and type(exported.get('page')) is int
                and type(exported.get('total_pages')) is int and exported['total_pages'] == 1,
                'INCOMPLETE_SEGMENT_EXPORT')
        segments = exported.get('data')
        require(isinstance(segments, list) and 0 < len(segments) <= 10000, 'INVALID_SEGMENTS')
        require(type(exported.get('total')) is int and exported['total'] == len(segments), 'INCOMPLETE_SEGMENT_EXPORT')
        for row in segments:
            require(isinstance(row, dict), 'INVALID_SEGMENT')
            require(identifier(row.get('id')) and type(row.get('position')) is int and row['position'] > 0,
                    'INVALID_SEGMENT_ID_OR_POSITION')
            require(row.get('document_id') == binding['document_id'], 'SEGMENT_DOCUMENT_MISMATCH')
            require(isinstance(row.get('content'), str) and bool(row['content'].strip()), 'INVALID_SEGMENT_CONTENT')
            require(row.get('enabled') is True and row.get('status') == 'completed', 'SEGMENT_NOT_ACTIVE')
            require(row.get('answer') in (None, '') and row.get('summary') in (None, ''), 'QA_OR_SUMMARY_UNSUPPORTED')
        try:
            mapping, groups = source_mapping(source_raw, segments)
        except (ValueError, IndexError, KeyError, TypeError):
            raise Rejected('SOURCE_SEGMENTS_OR_BLOCKS_MISMATCH') from None
        declared = binding['metadata_positions']
        require(isinstance(declared, list) and all(type(p) is int and p > 0 for p in declared)
                and len(set(declared)) == len(declared), 'INVALID_METADATA_DECLARATION')
        actual = [row['position'] for row in mapping.values() if row['kind'] == 'standalone_metadata']
        require(sorted(declared) == sorted(actual), 'UNCLASSIFIED_SOURCE_LINES')
        labels = [row['article_label_explicit'] for row in mapping.values()
                  if row['kind'] == 'article' and groups[row['group']][0] == row['id']]
        require(bool(labels) and len(labels) == len(set(labels)), 'MISSING_OR_DUPLICATE_PARENT_HEADER')
        retrieval = parse(retrieval_raw)
        require(retrieval.get('schema') == 'CitationResourcesV1', 'RETRIEVAL_FORMAT_UNSUPPORTED')
        records = retrieval.get('records')
        require(isinstance(records, list) and len(records) == 1 and isinstance(records[0], dict), 'SINGLE_RECORD_REQUIRED')
        record = records[0]
        require(identifier(record.get('message_id')) and identifier(record.get('conversation_id')), 'RETRIEVAL_IDENTITY_MISSING')
        resources = record.get('resources')
        require(isinstance(resources, list) and 0 < len(resources) <= 20, 'EMPTY_OR_TOO_MANY_RESOURCES')
        report['input_resource_count'] = len(resources)
        ids = []
        for row in resources:
            require(isinstance(row, dict) and identifier(row.get('segment_id')), 'INVALID_RESOURCE')
            require(row.get('document_id') == binding['document_id'] and row.get('dataset_id') == binding['dataset_id'],
                    'RESOURCE_DOCUMENT_OR_DATASET_MISMATCH')
            sid = row['segment_id']
            require(sid in mapping, 'UNKNOWN_SEGMENT_ID')
            require(row.get('content') == mapping[sid]['content'], 'RESOURCE_TEXT_MISMATCH')
            require(row.get('summary') in (None, '') and row.get('answer') in (None, ''), 'QA_OR_SUMMARY_UNSUPPORTED')
            # Resource position is retrieval rank. Never use it as source position.
            ids.append(sid)
        expanded_ids, context = expand_parent_context(ids, mapping, groups)
        raw = context.encode('utf-8')
        require(len(raw) <= max_context_bytes, 'CONTEXT_LIMIT_EXCEEDED')
        report.update(status='PREPARED_FOR_REVIEW',
                      document_id=binding['document_id'], dataset_id=binding['dataset_id'],
                      message_id=record['message_id'], conversation_id=record['conversation_id'],
                      context={'file': 'context.txt', 'sha256': sha(raw), 'utf8_bytes': len(raw)},
                      retrieved_segment_ids=ids, expanded_segment_ids=expanded_ids,
                      expanded_source_positions=[mapping[sid]['position'] for sid in expanded_ids],
                      source_segment_count=len(segments), metadata_positions=declared,
                      ordering='first parent hit; source order inside parent; standalone metadata preserved',
                      source_content_bytes_unchanged=True, join='LF without final LF')
        return report, raw
    except Rejected as error:
        report['diagnostics'] = [{'code': error.code}]
        return report, None

def main(argv=None):
    parser = argparse.ArgumentParser(description='将已导出的检索段展开为父条资料；不调用模型或门禁。')
    for name in ('source', 'segments', 'retrieval', 'binding', 'out'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--max-context-bytes', type=int, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    destination = Path(args.out)
    if destination.exists():
        print('OUTPUT_EXISTS_NO_OVERWRITE', file=sys.stderr)
        return 2
    paths = {name: Path(getattr(args, name)) for name in ('source', 'segments', 'retrieval', 'binding')}
    try:
        data = {}
        for name, path in paths.items():
            with path.open('rb') as stream:
                data[name] = stream.read(MAX_INPUT + 1)
            require(len(data[name]) <= MAX_INPUT, 'INPUT_TOO_LARGE')
        report, context = transform(data['source'], data['segments'], data['retrieval'], data['binding'],
                                    max_context_bytes=args.max_context_bytes)
        for name, path in paths.items():
            require(path.read_bytes() == data[name], 'INPUT_CHANGED_DURING_PREPARATION')
    except (Rejected, OSError) as error:
        report = {'schema': 'ParentContextPreparationV1', 'status': 'REVIEW', 'context': None,
                  'diagnostics': [{'code': error.code if isinstance(error, Rejected) else 'INPUT_IO_FAILURE'}],
                  'limitations': LIMITS, 'gate_result': None}
        context = None
    report['implementation_sha256'] = {name: sha((HERE / name).read_bytes())
                                        for name in ('parent_context.py', 'parent_context_mapping.py')}
    try:
        destination.mkdir(parents=True, exist_ok=False)
        if context is not None:
            with (destination / 'context.txt').open('xb') as stream:
                stream.write(context)
        with (destination / 'report.json').open('xb') as stream:
            stream.write(encode(report))
    except OSError:
        print('OUTPUT_IO_FAILURE_NO_READY_REPORT', file=sys.stderr)
        return 2
    print(json.dumps({'status': report['status'], 'diagnostics': report['diagnostics']}, ensure_ascii=False))
    return 0 if context is not None else 2

if __name__ == '__main__':
    sys.exit(main())
