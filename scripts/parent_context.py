"""Public standard-library entrypoint: prepare frozen parent context or make a binding."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.core import parent_context as tool

def bind(argv):
    parser=argparse.ArgumentParser(description='创建显式父条声明；它不证明原文在法律全文中完整。')
    for name in ('source','segments','document-id','dataset-id','out'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--metadata-positions',default='',help='独立标题/来源行的位置，逗号分隔；无此类行时留空')
    parser.add_argument('--assert-complete',action='store_true',required=True)
    args=parser.parse_args(argv)
    target=Path(args.out)
    if target.exists():print('OUTPUT_EXISTS_NO_OVERWRITE',file=sys.stderr);return 2
    try:
        raw={}
        for name in ('source','segments'):
            with Path(getattr(args,name)).open('rb') as stream:raw[name]=stream.read(tool.MAX_INPUT+1)
            tool.require(len(raw[name])<=tool.MAX_INPUT,'INPUT_TOO_LARGE')
        tool.require(tool.identifier(args.document_id) and tool.identifier(args.dataset_id),'INVALID_DOCUMENT_IDENTITY')
        positions=[] if not args.metadata_positions else [int(s) for s in args.metadata_positions.split(',')]
        tool.require(all(p>0 for p in positions) and len(positions)==len(set(positions)),'INVALID_METADATA_DECLARATION')
        exported=tool.parse(raw['segments'])
        mapping,_=tool.source_mapping(raw['source'],exported['data'])
        actual=sorted(v['position'] for v in mapping.values() if v['kind']=='standalone_metadata')
        tool.require(sorted(positions)==actual,'UNCLASSIFIED_SOURCE_LINES')
        tool.require(all(row.get('document_id')==args.document_id for row in exported['data']),'SEGMENT_DOCUMENT_MISMATCH')
        value={'schema':'ParentContextBindingV1','source_format':'blank-line-articles-v1',
            'structure_assertion':'operator-declared-complete-parent-blocks','source_sha256':tool.sha(raw['source']),
            'segments_sha256':tool.sha(raw['segments']),'document_id':args.document_id,'dataset_id':args.dataset_id,'metadata_positions':positions}
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream:stream.write(tool.encode(value))
    except (OSError,ValueError,KeyError,TypeError,IndexError):
        print('BINDING_NOT_CREATED_CHECK_SOURCE_AND_DECLARATION',file=sys.stderr);return 2
    print('BINDING_CREATED_NOT_COMPLETENESS_PROOF');return 0

if __name__=='__main__':
    sys.exit(bind(sys.argv[2:]) if sys.argv[1:2]==['bind'] else tool.main())
