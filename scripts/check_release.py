"""Check the source publication boundary before staging or publishing."""
import pathlib,re,subprocess,sys
ROOT=pathlib.Path(__file__).resolve().parent.parent
PATTERNS={
    'private key':r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'GitHub token':r'\b(?:gh[pousr]_[A-Za-z0-9]{24,}|github_pat_[A-Za-z0-9_]{24,})',
    'personal absolute path':r'\x2fUsers\x2f(?!Shared(?:\x2f|\b))[^\s\x2f"\x27]+\x2f',
    'private LAN address':r'\b(?:192\.168\.[0-9]+\.[0-9]+|10\.[0-9]+\.[0-9]+\.[0-9]+)\b',
}
ALLOWED_SUFFIXES={'.py','.mjs','.swift','.md','.json','.txt','.yml','.yaml','.svg','.png'}
def check():
    files=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    errors=[]
    for name in filter(None,files):
        p=ROOT/name
        if p.suffix not in ALLOWED_SUFFIXES and p.name not in ('LICENSE','.gitignore','build.sh'):
            errors.append(name+': unexpected publication file type');continue
        if p.suffix=='.png':continue
        text=p.read_text()
        for label,pattern in PATTERNS.items():
            if re.search(pattern,text):errors.append(name+': '+label)
        if any(part.startswith(('review','__pycache__','.venv')) for part in p.relative_to(ROOT).parts):errors.append(name+': runtime or review artifact')
    if not files or files==['']:errors.append('No staged/tracked source files to check')
    if errors:raise SystemExit('\n'.join(errors))
    print('PASS: source publication boundary, '+str(sum(bool(x) for x in files))+' files')
if __name__=='__main__':check()
