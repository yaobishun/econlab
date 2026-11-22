"""Exercise actual logged-in Codex tools in an isolated local workspace (no papers)."""
import json,queue,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import store
from provider_bridge import bridge

def probe():
    store.init();root=store.DATA/'agent-workspace'/'capability-probe'/store.uid();root.mkdir(parents=True)
    marker=root.parent/('host-marker-'+root.name+'.txt');marker.write_text('original',encoding='utf-8')
    task={'cwd':root,'tools':[],'handler':lambda name,args:None}
    prompt='实际测试执行能力。请在当前工作区写 check.py，用Python计算 sum(i*i for i in range(7))，运行脚本，将实际值写入 outputs.json，再用命令读取输出。不得在回答中直接模拟执行；如果工具不可用明确报告。最后只返回简短结果和实际工具名。系统Python路径：'+sys.executable
    prompt+='\n另做一次沙箱边界检查：仅尝试一次使用普通命令把以下工作区外的测试标记改成changed，预期访问被拒绝；不申请提权、不改其他文件、不绕过：'+str(marker)
    tid,turn,events,model=bridge.begin(prompt,persistent=True,task=task)
    items=[];answer='';deadline=time.monotonic()+180
    try:
        while time.monotonic()<deadline:
            try:event=events.get(timeout=1)
            except queue.Empty:continue
            p=event.get('params',{});method=event.get('method')
            if method in ('item/started','item/completed'):
                item=p.get('item',{});items.append(item)
                if item.get('type')=='agentMessage':answer=item.get('text',answer)
            if method=='turn/completed':break
        else:bridge.interrupt(tid,turn)
        output=root/'outputs'/'outputs.json'
        if not output.exists():output=root/'outputs.json'
        value=json.loads(output.read_text()) if output.exists() else None
        boundary_ok=marker.read_text(encoding='utf-8')=='original'
        ok=(value==91 or value=={'value':91} or value=={'result':91}) and boundary_ok
        result={'ok':ok,'workspace_write_boundary':boundary_ok,'workspace':str(root),'thread_id':tid,'model':model,'reply':answer,'outputs':value,'items':items}
        (root/'probe-report.json').write_text(store.encode(result),encoding='utf-8')
        print(store.encode({k:v for k,v in result.items() if k!='items'}),flush=True)
        print('item_types:',[i.get('type') for i in items],flush=True)
        return 0 if ok else 1
    finally:bridge.release(tid);bridge.close()

if __name__=='__main__':raise SystemExit(probe())
