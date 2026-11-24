"""Host-selected formal commands through Codex OS sandbox, without model inference."""
import os
from pathlib import Path
import store

class Command:
    def __init__(self,root):
        from codex_bridge import agent_bridge
        self.client=agent_bridge;self.root=Path(root).resolve();self.id='econlab-'+store.uid();self.stopped=False
        if not self.root.is_relative_to(store.DATA/'runs'):raise ValueError('Formal sandbox must use an assigned run directory')

    def cancel(self):
        self.stopped=True
        try:self.client.rpc('command/exec/terminate',{'processId':self.id},timeout=10)
        except Exception:pass

    def execute(self,command,env,timeout):
        if self.stopped:raise ValueError('Task cancelled')
        # Explicitly unset sensitive inherited values; command/exec env is a merge.
        overrides={key:None for key in os.environ if any(t in key.upper() for t in ('API_KEY','TOKEN','SECRET','PASSWORD'))}
        overrides.update(env)
        cache=self.root/'.runtime-cache';cache.mkdir(exist_ok=True)
        overrides.update({key:str(cache) for key in ('TEMP','TMP','TMPDIR','MPLCONFIGDIR','XDG_CACHE_HOME','PYTHONPYCACHEPREFIX')})
        return self.client.rpc('command/exec',{'command':[str(x) for x in command],'processId':self.id,'cwd':str(self.root),
            'env':overrides,'timeoutMs':timeout*1000,
            'sandboxPolicy':{'type':'workspaceWrite','writableRoots':[str(self.root)],'networkAccess':False,'excludeTmpdirEnvVar':True,'excludeSlashTmp':True}},timeout=timeout+30)
