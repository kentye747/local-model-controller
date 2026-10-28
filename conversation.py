"""Resolve a recorded task identity to a local conversation. No model requests."""
import json, pathlib, re, sqlite3, urllib.parse

from runtime_config import agent_home

def destination(home, agent, session_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,180}',session_id or ''):
        raise RuntimeError('会话标识无效，请刷新模型列表。')
    if agent == 'Hermes':
        db=agent_home(home,'hermes')/'state.db'
        if not db.exists():raise RuntimeError('Hermes 会话库暂不可读。')
        with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=.5) as conn:
            row=conn.execute('SELECT title FROM sessions WHERE id=?',(session_id,)).fetchone()
        if row is None:raise RuntimeError('这条 Hermes 会话尚未保存或已移除，请稍后刷新。')
        return {'kind':'url','agent':agent,'session_id':session_id,'title':row[0] or '',
                'url':'hermes://open/'+urllib.parse.quote(session_id,safe='')}
    if agent != 'DSH':raise RuntimeError('尚不支持打开这个 Agent 的对话。')
    cache=agent_home(home,'dsh')/'storages/session_projcache/sessions'
    path=cache/(session_id+'.json')
    if not path.exists():raise RuntimeError('DSH 对话已移除或尚未保存，请刷新。')
    def title(p):
        if p.stat().st_size>2*1024*1024:raise RuntimeError('DSH 会话记录过大，暂不自动定位。')
        return (((json.loads(p.read_text()).get('record') or {}).get('rows') or {}).get('title') or {}).get('val')
    name=title(path)
    if not isinstance(name,str) or not name.strip():raise RuntimeError('DSH 尚未生成任务名，请稍后刷新再打开。')
    for other in cache.glob('*.json'):
        if other!=path:
            other_name=title(other)
            if isinstance(other_name,str) and (other_name==name or other_name.startswith(name+' ')):
                raise RuntimeError('DSH 中存在同名或无法区分的对话，无法安全地自动定位；请在 DSH 中核对会话标识：'+session_id)
    return {'kind':'dsh_accessibility','agent':agent,'session_id':session_id,'title':name,'url':'dsh://open'}
