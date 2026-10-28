"""Inspect frozen Python code independently of source-file checks."""
import pathlib,sys,types
from PyInstaller.archive.readers import CArchiveReader
app=pathlib.Path(sys.argv[1]);exe=app/'Contents/Resources/Backend/LMCBackend'
archive=CArchiveReader(str(exe));name=next(n for n in archive.toc if n.endswith('.pyz'))
pyz=archive.open_embedded_archive(name);issues=[]
def check(code,module):
    if not isinstance(code,types.CodeType):return
    if '\x2fUsers\x2f' in code.co_filename:issues.append(module+': absolute source filename')
    for value in code.co_consts:
        if isinstance(value,types.CodeType):check(value,module)
        elif isinstance(value,str) and str(pathlib.Path.home())+'/' in value:issues.append(module+': local home in constant')
for module in pyz.toc:
    check(pyz.extract(module),module)
    if module.startswith(('hermes_cli','gateway','agent.')):issues.append(module+': unexpected vendored agent code')
if issues:raise SystemExit('\n'.join(sorted(set(issues))))
print('PASS: frozen Python archive; no personal paths or vendored agents')
