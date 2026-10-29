"""Offline CLML explicit-field baseline for contract-v1. No network or file writes."""
import datetime as dt
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

L = 'http://www.legislation.gov.uk/namespaces/legislation'
M = 'http://www.legislation.gov.uk/namespaces/metadata'
DC = 'http://purl.org/dc/elements/1.1/'
DCT = 'http://purl.org/dc/terms/'
ATOM = 'http://www.w3.org/2005/Atom'
LANG = '{http://www.w3.org/XML/1998/namespace}lang'
FIELDS = {'source_sha256', 'document_id', 'provision_id', 'view_uri', 'snapshot_date', 'check_date', 'extent', 'language'}
METHODS = {'B1', 'B0_modified', 'A_root_interval', 'A_inclusive_end'}
AXES = ('snapshot_binding', 'fragment_metadata', 'known_by')
NAMES = ('identity', 'view', 'snapshot_date', 'language', 'extent', 'fragment_interval', 'conditions')

def q(namespace, name):
    return '{'+namespace+'}'+name

def aggregate(states):
    states = list(states)
    return 'MISMATCH' if 'MISMATCH' in states else ('REVIEW' if 'REVIEW' in states or not states else 'MATCH')

def date(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise ValueError('date must be YYYY-MM-DD')
    return dt.date.fromisoformat(value)

def uri(value, strip_data=False):
    if not isinstance(value, str) or re.search(r'[\s\\%]', value):
        raise ValueError('invalid URI characters')
    part = urlsplit(value)
    if part.scheme not in {'http', 'https'} or part.netloc != 'www.legislation.gov.uk':
        raise ValueError('unknown URI site or authority')
    if '?' in value or '#' in value or not part.path.startswith('/') or '//' in part.path:
        raise ValueError('query, fragment or invalid path')
    if any(x in {'.', '..'} for x in part.path.split('/')):
        raise ValueError('path traversal')
    path = part.path
    if strip_data and path.endswith('/data.xml'):
        path = path[:-9]
    return path

def extent(value):
    if not isinstance(value, str):
        return None
    bits = value.split('+')
    return set(bits) if bits and len(set(bits)) == len(bits) and set(bits) <= {'E','W','S','N.I.'} else None

def boolean(value):
    """XSD boolean lexical space; None denotes absence, never implicit false."""
    if value is None:
        return None
    normalized = value.strip(' \t\r\n')
    if normalized in {'true','1'}:
        return True
    if normalized in {'false','0'}:
        return False
    raise ValueError('unsupported xsd:boolean lexical value')

def evaluate(source_bytes, request, method='B1'):
    source_hash = hashlib.sha256(source_bytes).hexdigest() if isinstance(source_bytes, bytes) else None
    out = {'method': method, 'source_sha256': source_hash, 'subchecks': {}, 'axes': {},
           'target_location': None, 'owners': {}, 'date_roles': {}, 'input_errors': []}
    def invalid(reason):
        out['input_errors'].append(reason)
        out['axes'] = dict.fromkeys(AXES, 'INVALID_INPUT')
        out['subchecks'] = {name:{'state':'INVALID_INPUT','checks':[]} for name in NAMES}
        return out
    try:
        if method not in METHODS:
            raise ValueError('unknown method')
        if not isinstance(source_bytes, bytes) or len(source_bytes) > 5*1024*1024:
            raise ValueError('source must be bytes within 5 MiB')
        if not isinstance(request, dict) or set(request) != FIELDS or any(not isinstance(v,str) for v in request.values()):
            raise ValueError('request must have exactly the eight required string fields')
        if not re.fullmatch('[0-9a-f]{64}', request['source_sha256']):
            raise ValueError('source_sha256 must be lowercase 64hex')
        if request['source_sha256'] != source_hash:
            raise ValueError('source SHA256 mismatch')
        for key in ('document_id','provision_id','view_uri'):
            uri(request[key])
        if request['view_uri'].endswith('/data.xml'):
            raise ValueError('view_uri must omit /data.xml')
        date(request['snapshot_date']); date(request['check_date'])
        if request['extent'] not in {'E','W','S','N.I.'} or request['language'] not in {'en','cy'}:
            raise ValueError('invalid requested extent or language')
        text = source_bytes.decode('utf-8-sig')
        if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', text, re.I):
            raise ValueError('DTD and ENTITY declarations forbidden')
        declaration = re.match(r'\s*<\?xml\s+[^?]*encoding\s*=\s*[\"\']([^\"\']+)', text, re.I)
        if declaration and declaration.group(1).lower() not in {'utf-8','utf8'}:
            raise ValueError('XML declaration must specify UTF8')
        root = ET.fromstring(text)
        if root.tag != q(L,'Legislation'):
            raise ValueError('root must be CLML Legislation')
    except (ValueError, TypeError, UnicodeError, ET.ParseError) as error:
        return invalid(str(error))

    paths = {}
    parents = {}
    def index(node, path):
        paths[node] = path
        counts = {}
        for child in node:
            parents[child] = node
            counts[child.tag] = counts.get(child.tag,0)+1
            index(child,path+'/'+child.tag+'['+str(counts[child.tag])+']')
    index(root,'/'+root.tag+'[1]')
    def evidence(node, attr=None):
        return {'path': paths[node]+('/@'+attr if attr else ''),
                'raw':node.get(attr) if attr else ''.join(node.itertext())}
    def item(state, reason, refs=()):
        return {'state':state,'reason':reason,'evidence':list(refs)}
    def group(name, checks):
        out['subchecks'][name] = {'state':aggregate(x['state'] for x in checks),'checks':checks}
    def compare(node, attr, wanted, is_uri=False):
        raw = node.get(attr) if attr else ''.join(node.itertext())
        refs = [evidence(node,attr)]
        if raw is None or raw == '':
            return item('REVIEW','required field missing',refs)
        try:
            matches = uri(raw)==uri(wanted) if is_uri else raw == wanted
        except ValueError:
            return item('REVIEW','source URI outside supported syntax',refs)
        return item('MATCH' if matches else 'MISMATCH','explicit field comparison',refs)
    metadata = [n for n in root.iter(q(M,'Metadata'))]
    def meta_nodes(namespace, name):
        return [n for container in metadata for n in container.iter(q(namespace,name))]
    def unique(nodes, wanted, kind='text'):
        if len(nodes) != 1:
            return item('REVIEW','required unique field missing or duplicated',[evidence(n,'href' if kind=='self' else None) for n in nodes])
        node = nodes[0]
        if kind == 'self':
            raw = node.get('href')
            try:
                match = uri(raw,True) == uri(wanted)
            except ValueError:
                return item('REVIEW','source self URI outside supported syntax',[evidence(node,'href')])
            return item('MATCH' if match else 'MISMATCH','self URI comparison',[evidence(node,'href')])
        return compare(node,None,wanted,kind=='uri')
    def chain(node):
        values = []
        while node is not None:
            values.append(node)
            node = parents.get(node)
        return values

    p1s = list(root.iter(q(L,'P1')))
    selected = []
    invalid_p1_uris = []
    for node in p1s:
        try:
            if uri(node.get('IdURI')) == uri(request['provision_id']):
                selected.append(node)
        except ValueError:
            if node.get('IdURI'):
                invalid_p1_uris.append(node)
    target = selected[0] if len(selected)==1 else None
    related = [n for n in chain(target) if n.tag.startswith('{'+L+'}')] if target is not None else []
    descendants = [n for n in list(target.iter())[1:] if n.tag.startswith('{'+L+'}')] if target is not None else []
    alt_nodes = [n for n in related if n.get('AltVersionRefs')]
    versions = [n for n in descendants if n.tag==q(L,'Versions')]
    other_versions = [n for n in root.iter(q(L,'Versions')) if n not in versions]
    ambiguous = len(selected)>1 or bool(alt_nodes or versions)
    if ambiguous:
        locate = item('REVIEW','target multiplicity or unresolved alternative versions',
                      [evidence(n,'IdURI') for n in selected]+[evidence(n,'AltVersionRefs') for n in alt_nodes]+[evidence(n) for n in versions])
    elif target is not None:
        locate = compare(target,'IdURI',request['provision_id'],True)
    else:
        has_ids = any(n.get('IdURI') for n in p1s)
        definite_absence = has_ids and not invalid_p1_uris
        locate = item('MISMATCH' if definite_absence else 'REVIEW','requested P1 absent' if definite_absence else 'P1 identity missing or outside supported URI syntax', [evidence(n,'IdURI') for n in p1s])
    out['target_location'] = locate
    group('identity',[compare(root,'IdURI',request['document_id'],True),locate])
    usable = target is not None and not ambiguous
    unavailable = item('REVIEW','target cannot be uniquely bound under supported structure')
    self_nodes = [n for n in meta_nodes(ATOM,'link') if n.get('rel')=='self']
    group('view', [compare(target,'DocumentURI',request['view_uri'],True) if usable else unavailable,
                   unique(meta_nodes(DC,'identifier'),request['view_uri'],'uri'), unique(self_nodes,request['view_uri'],'self')])
    date_ns,date_name = (DC,'modified') if method=='B0_modified' else (DCT,'valid')
    group('snapshot_date', [item('MATCH' if uri(request['view_uri']).endswith('/'+request['snapshot_date']) else 'MISMATCH','view path snapshot suffix', [{'path':'request/view_uri','raw':request['view_uri']}]),
                            unique(meta_nodes(date_ns,date_name),request['snapshot_date'])])
    language_nodes = meta_nodes(DC,'language')
    if len(language_nodes) != 1:
        resource_language = item('REVIEW','required unique resource language missing or duplicated',[evidence(n) for n in language_nodes])
    else:
        language_raw = ''.join(language_nodes[0].itertext())
        resource_language = (compare(language_nodes[0],None,request['language']) if language_raw in {'en','cy'}
                             else item('REVIEW','unsupported resource language',[evidence(language_nodes[0])]))
    root_language_raw = root.get(LANG)
    if root_language_raw is None:
        root_language = item('MATCH','optional root xml:lang absent; recorded as null',[evidence(root,LANG)])
    elif root_language_raw not in {'en','cy'}:
        root_language = item('REVIEW','unsupported root language',[evidence(root,LANG)])
    else:
        root_language = compare(root,LANG,request['language'])
    group('language',[resource_language,root_language])

    extent_owner = next((n for n in related if n.get('RestrictExtent') is not None),None) if usable else None
    actual_extent = extent(extent_owner.get('RestrictExtent')) if extent_owner is not None else None
    out['owners']['extent'] = paths.get(extent_owner)
    refs = [evidence(extent_owner,'RestrictExtent')] if extent_owner is not None else []
    group('extent',[item('REVIEW' if actual_extent is None else ('MATCH' if request['extent'] in actual_extent else 'MISMATCH'), 'nearest explicit extent membership',refs)])

    target_owner = None
    if usable:
        if any(key in target.attrib for key in ('RestrictStartDate','RestrictEndDate')):
            target_owner = target
        else:
            parent = parents.get(target)
            if parent is not None and parent.tag == q(L,'P1group') and len([n for n in parent if n.tag==q(L,'P1')])==1:
                target_owner = parent
    owner = root if usable and method=='A_root_interval' else target_owner
    out['owners']['fragment_interval'] = paths.get(owner)
    interval = item('REVIEW','interval owner missing or target ambiguous')
    if owner is not None:
        refs = [evidence(owner,k) for k in ('RestrictStartDate','RestrictEndDate')]
        try:
            start,end = date(owner.get('RestrictStartDate')),date(owner.get('RestrictEndDate'))
            if start >= end:
                raise ValueError('start must precede end')
            point = date(request['check_date'])
            contains = start<=point and (point<=end if method=='A_inclusive_end' else point<end)
            interval = item('MATCH' if contains else 'MISMATCH','explicit interval membership; '+('inclusive end' if method=='A_inclusive_end' else 'exclusive end'),refs)
        except (ValueError,TypeError):
            interval = item('REVIEW','missing or invalid interval endpoints',refs)
    group('fragment_interval',[interval])

    limitations = []
    if not usable:
        limitations.append(unavailable)
    for node in other_versions:
        limitations.append(item('REVIEW','Versions outside target: unrelatedness not established',[evidence(node)]+[evidence(node,k) for k in node.attrib]))
    for node in descendants:
        if node.get('AltVersionRefs'):
            limitations.append(item('REVIEW','finer descendant alternative references',[evidence(node,'AltVersionRefs')]))
    for node in related+descendants:
        flags = {}
        for key in ('Match','Concurrent','BlanketAmendment'):
            try:
                flags[key] = boolean(node.get(key))
            except ValueError:
                flags[key] = None
                limitations.append(item('REVIEW','invalid explicit xsd:boolean '+key,[evidence(node,key)]))
        if flags['BlanketAmendment'] is not None:
            limitations.append(item('MATCH','BlanketAmendment retained; legal effects on other provisions are not computed',[evidence(node,'BlanketAmendment')]))
        if flags['Match'] is False:
            limitations.append(item('REVIEW','unresolved explicit Match',[evidence(node,'Match')]))
        if node.get('Status'):
            limitations.append(item('REVIEW','unresolved explicit Status',[evidence(node,'Status')]))
        if flags['Concurrent'] is True:
            mapping = {'england':'E','wales':'W','scotland':'S','ni':'N.I.'}
            segments = uri(request['view_uri']).split('/')
            zones = [mapping[segments[-2]]] if len(segments)>2 and segments[-2] in mapping else []
            resolved = usable and len(zones)==1 and actual_extent==set(zones) and zones[0]==request['extent']
            if not resolved:
                limitations.append(item('REVIEW','Concurrent not resolved to a single explicit jurisdiction',[evidence(node,'Concurrent')]))
    if usable:
        for node in descendants:
            for key in ('RestrictStartDate','RestrictEndDate','RestrictExtent','Match','Status'):
                if key not in node.attrib:
                    continue
                if key.startswith('Restrict') and key != 'RestrictExtent':
                    baseline = target_owner.get(key) if target_owner is not None else None
                elif key=='RestrictExtent':
                    baseline = extent_owner.get(key) if extent_owner is not None else None
                else:
                    nearest = next((a for a in related if key in a.attrib),None)
                    baseline = nearest.get(key) if nearest is not None else None
                value = node.get(key)
                if key=='Match':
                    nearest = next((a for a in chain(parents.get(node)) if a.tag.startswith('{'+L+'}') and key in a.attrib),None)
                    baseline = nearest.get(key) if nearest is not None else None
                    try:
                        value,baseline = boolean(value),boolean(baseline)
                    except ValueError:
                        continue  # Explicit invalid value already retained as REVIEW above.
                if value != baseline:
                    limitations.append(item('REVIEW','finer descendant restriction differs from target coverage',[evidence(node,key)]))
    for node in root.iter(q(M,'UnappliedEffect')):
        limitations.append(item('REVIEW','document-level UnappliedEffect not excluded under this contract',[evidence(node)]+[evidence(node,k) for k in node.attrib]))
    group('conditions',limitations or [item('MATCH','no represented unresolved conditions in supported structure')])
    for namespace,name,role in ((DC,'date','unknown_role'),(DC,'modified','resource_modified'),(DCT,'valid','declared_valid_raw')):
        nodes = meta_nodes(namespace,name)
        out['date_roles'][('dc:' if namespace==DC else 'dct:')+name] = {'role':role,'values':[evidence(n) for n in nodes] or None}
    version_nodes = [n for n in root.iter() if n.tag.startswith('{'+L+'}') and 'Version' in n.attrib]
    out['date_roles']['@Version'] = {'role':'legacy_fragment_version','values':[evidence(n,'Version') for n in version_nodes] or None}
    out['axes']['snapshot_binding'] = aggregate(out['subchecks'][n]['state'] for n in ('identity','view','snapshot_date','language'))
    out['axes']['fragment_metadata'] = aggregate([locate['state']]+[out['subchecks'][n]['state'] for n in ('extent','fragment_interval','conditions')])
    out['axes']['known_by'] = 'REVIEW'
    return out

def reject_duplicate_keys(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: '+key)
        result[key] = value
    return result

if __name__ == '__main__':
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('request')
    parser.add_argument('--method',choices=sorted(METHODS),default='B1')
    args = parser.parse_args()
    try:
        req = json.loads(Path(args.request).read_text(encoding='utf-8'),object_pairs_hook=reject_duplicate_keys)
        result = evaluate(Path(args.source).read_bytes(),req,args.method)
    except (ValueError,OSError) as error:
        result = {'method':args.method,'axes':dict.fromkeys(AXES,'INVALID_INPUT'),'input_errors':[str(error)]}
    print(json.dumps(result,ensure_ascii=False,indent=2))
