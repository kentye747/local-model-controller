"""User-owned paths and optional integrations; no machine-specific defaults."""
import copy, json, os, pathlib, shutil

HOME = pathlib.Path(os.environ.get('LMC_HOME', str(pathlib.Path.home()))).expanduser()
DATA_DIR = pathlib.Path(os.environ.get('LMC_DATA_DIR', str(HOME/'Library/Application Support/Local Model Controller'))).expanduser()
DEFAULTS = {
    'paths': {'hermes_app':'/Applications/Hermes.app', 'hermes_home':str(HOME/'.hermes'),
              'dsh_app':'/Applications/DeepSeek Harness.app', 'dsh_source':str(HOME/'deepseek-harness'),
              'dsh_home':str(HOME/'.dsh'), 'node':shutil.which('node') or str(HOME/'.local/bin/node'),
              'lms':str(HOME/'.lmstudio/bin/lms') if (HOME/'.lmstudio/bin/lms').exists() else '/Applications/LM Studio.app/Contents/Resources/app/.webpack/lms'},
    'tensorfold': {'enabled':False, 'command':str(HOME/'.local/bin/tensorfold'), 'model_id':'local-tensorfold',
                   'name':'TensorFold', 'key':'tensorfold:default', 'base_url':'http://127.0.0.1:18080/v1',
                   'context':65536, 'estimated_bytes':0, 'vision':False},
    'llama': {'base_url':'', 'api_key_env':'LMC_LLAMA_API_KEY', 'context':65536},
    'remote': {'enabled':False, 'ssh_host':'inference-host', 'expected_hostname':'inference-host.local',
               'python':'python3', 'probe_path':'.local/share/local-model-controller/probe.py', 'gateway_sha256':''},
    'history_timezone':''
}

def merge(base,override):
    for key,value in override.items():
        if isinstance(value,dict) and isinstance(base.get(key),dict):merge(base[key],value)
        else:base[key]=value
    return base

def settings():
    result=copy.deepcopy(DEFAULTS)
    path=DATA_DIR/'settings.json'
    if path.exists():
        data=json.loads(path.read_text())
        if not isinstance(data,dict):raise ValueError('settings.json must contain a JSON object')
        merge(result,data)
    for name in ('paths','tensorfold','llama','remote'):
        if not isinstance(result[name],dict):raise ValueError(name+' must be a JSON object')
    for name in ('enabled',):
        for group in ('tensorfold','remote'):
            if type(result[group][name]) is not bool:raise ValueError(group+'.enabled must be true or false')
    remote=result['remote']
    if remote['enabled']:
        import re
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]*',remote['ssh_host']):raise ValueError('Invalid SSH host alias')
        for key in ('python','probe_path'):
            if not isinstance(remote[key],str) or not remote[key] or remote[key].startswith('-') or '\n' in remote[key]:raise ValueError('Invalid remote '+key)
    for name,value in result['paths'].items():
        if not isinstance(value,str):raise ValueError('paths.'+name+' must be a path string')
        result['paths'][name]=str(pathlib.Path(value).expanduser())
    return result

def agent_home(home,agent):
    """Keep temporary test homes isolated while honoring explicitly configured agent roots."""
    key={'hermes':'hermes_home','dsh':'dsh_home'}[agent]
    configured=pathlib.Path(settings()['paths'][key])
    default=pathlib.Path(DEFAULTS['paths'][key])
    return pathlib.Path(home)/('.hermes' if agent=='hermes' else '.dsh') if configured==default else configured
