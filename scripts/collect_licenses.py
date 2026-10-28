"""Include original license notices for frozen Python dependencies."""
import importlib.metadata,pathlib,shutil,sys
out=pathlib.Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
for name in ('psutil','PyYAML','websocket-client','pyinstaller'):
    dist=importlib.metadata.distribution(name)
    for file in dist.files or []:
        if any(x in pathlib.Path(str(file)).name.lower() for x in ('license','copying','notice')):
            source=pathlib.Path(dist.locate_file(file))
            if source.is_file():
                dest=out/name/pathlib.Path(str(file)).name;dest.parent.mkdir(exist_ok=True);shutil.copy2(source,dest)
# python-build-standalone and standard Python installations keep their distribution notice nearby.
base=pathlib.Path(sys.base_prefix)
candidates=list(base.glob('**/LICENSE*'))+list(base.glob('**/COPYING*'))
for source in candidates:
    if 'site-packages' in source.parts or not source.is_file():continue
    dest=out/'Python'/source.relative_to(base);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
