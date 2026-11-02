"""Bind active slots to native completion IDs, never to timing or an active-model guess."""
import re,socket
from history_titles import attach_titles
from runtime_config import settings

ID=re.compile(r'^[A-Za-z0-9_.:-]{1,200}$')
SESSION=re.compile(r'^[A-Za-z0-9_-]{1,180}$')

def safe(value,pattern=ID):
    return isinstance(value,str) and pattern.fullmatch(value) is not None

def current_requests(slots):
    """None means no native identity support. Empty means no current requests.

    Never retain the rest of /slots: it can contain generation parameters.
    An invalid/unlabelled active slot stays unknown even if other callers are known.
    """
    if not isinstance(slots,list) or len(slots)>128:return None
    active=[s for s in slots if isinstance(s,dict) and s.get('is_processing') is True]
    if active and not any('request_identity' in s for s in active):return None
    result=[]
    for slot in active:
        raw=slot.get('request_identity');row={}
        if isinstance(raw,dict) and safe(raw.get('completion_id')):
            row['request_id']=raw['completion_id']
            if (raw.get('origin') in ('Hermes','DSH') and safe(raw.get('session_id'),SESSION)
                    and safe(raw.get('task_id')) and safe(raw.get('host'))):
                row.update(request_origin=raw['origin'],session_id=raw['session_id'],
                           task_id=raw['task_id'],request_host=raw['host'],attribution_status='exact')
        result.append(row)
    return result

def activities(models,home):
    """Override process-based candidates only when native current-request IDs exist."""
    overrides={};unknown={};queries=[]
    for model in models:
        requests=model.get('native_requests')
        if not model['id'].startswith('hermes:') or model.get('busy') is not True or requests is None:continue
        groups={};overrides[model['id']]=[];unknown[model['id']]=False
        for row in requests:
            if not isinstance(row,dict) or row.get('attribution_status')!='exact':
                unknown[model['id']]=True;continue
            groups.setdefault(row['request_id'],[]).append(row)
        for rid,rows in groups.items():
            identities={(r['request_origin'],r['session_id'],r['task_id'],r['request_host']) for r in rows}
            if len(identities)!=1:
                unknown[model['id']]=True;continue
            row={**rows[0],'model_key':model['id']};queries.append(row)
    attach_titles(queries,home)
    local=socket.gethostname();remote=settings()['remote'];expected=remote.get('expected_hostname') if remote.get('enabled') else None
    for row in queries:
        origin=row['request_origin'];host=row['request_host'];mini=host==expected and host!=local
        agent=origin+' · mini' if mini else origin if host==local else origin+' · '+host
        overrides[row['model_key']].append(dict(agent=agent,session_id=row['session_id'],
            title=row.get('task_title') or row['session_id'],phase='模型请求中',
            source='llama.cpp 原生当前请求标识',confidence='live',
            host='mini' if mini else host,request_host=host,request_id=row['request_id'],
            task_id=row['task_id'],can_open=host==local and bool(row.get('task_title'))))
    return overrides,unknown
