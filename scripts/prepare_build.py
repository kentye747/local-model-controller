"""Remove machine-local CPython build prefixes from frozen stdlib metadata."""
import importlib,pathlib,pprint,sys,sysconfig
module=sysconfig._get_sysconfigdata_name()
data=importlib.import_module(module).build_time_vars
prefixes=sorted({str(pathlib.Path.home()),str(pathlib.Path(sys.base_prefix).resolve())},key=len,reverse=True)
def clean(value):
    if isinstance(value,str):
        for prefix in prefixes:value=value.replace(prefix,'/opt/python')
    return value
folder=pathlib.Path('build/portable-stdlib');folder.mkdir(parents=True,exist_ok=True)
(folder/(module+'.py')).write_text('# CPython build configuration data; PSF license. Machine-specific prefixes removed.\nbuild_time_vars = '+pprint.pformat({k:clean(v) for k,v in data.items()},sort_dicts=True)+'\n')
