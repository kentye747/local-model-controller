"""Keep the entire test process away from a developer's real controller settings."""
import atexit,os,pathlib,tempfile
_profile=tempfile.TemporaryDirectory(prefix='lmc-unit-tests-')
_root=pathlib.Path(_profile.name)
(_root/'home').mkdir()
os.environ['LMC_HOME']=str(_root/'home')
os.environ['LMC_DATA_DIR']=str(_root/'data')
os.environ['LMC_DISABLE_HERMES']='1'
atexit.register(_profile.cleanup)
