"""Report source-language keys; never auto-translate existing translations."""
import ast,json,pathlib,re
ROOT=pathlib.Path(__file__).resolve().parent.parent
STRINGS=re.compile(r'"(?:[^"\\]|\\.)*"')
CJK=re.compile('[\u3400-\u9fff]')
def key(raw):
    # Swift interpolation supports nested calls; retain each entire expression as one placeholder.
    out='';i=0
    while i<len(raw):
        if raw.startswith('\\(',i):
            depth=1;i+=2
            while i<len(raw) and depth:
                if raw[i]=='(':depth+=1
                if raw[i]==')':depth-=1
                i+=1
            out+='%@'
        else:out+=raw[i];i+=1
    return json.loads('"'+out+'"')
def sources():
    out=set()
    for p in ROOT.glob('*.swift'):
        if p.name in ('Localization.swift','Icon.swift'):continue
        for m in STRINGS.finditer(p.read_text()):
            if CJK.search(m[0]):out.add(key(m[0][1:-1]))
    for p in ROOT.glob('*.py'):
        if p.name.startswith('test_'):continue
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n,ast.JoinedStr):
                value=''.join(x.value if isinstance(x,ast.Constant) and isinstance(x.value,str) else '%@' for x in n.values)
                if CJK.search(value):out.add(value)
            elif isinstance(n,ast.Constant) and isinstance(n.value,str) and CJK.search(n.value):out.add(n.value)
    return sorted(out)
if __name__=='__main__':
    existing=json.loads((ROOT/'locales/en.json').read_text()) if (ROOT/'locales/en.json').exists() else {}
    print(json.dumps([s for s in sources() if s not in existing],ensure_ascii=False,indent=2))
