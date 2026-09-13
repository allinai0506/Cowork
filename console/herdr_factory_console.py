#!/usr/bin/env python3
import json, os, re, shutil, subprocess, sys, threading, time, urllib.parse, uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME=Path.home(); HERDR_ROOT=Path(os.environ.get('HERDR_ROOT', str(HOME/'herdr'))); ROOT=HOME/'.herdr-controller'
sys.path.insert(0, str(HERDR_ROOT))
from herdr import workflow as herdr_workflow
from herdr import projects as herdr_projects
from herdr.agent_binary import resolve_agent_binary
PROJECTS_FILE=ROOT/'projects.json'; WORKFLOWS_FILE=ROOT/'workflows.json'; TASKS_FILE=ROOT/'tasks.json'; POOLS_FILE=ROOT/'agent-pools.json'; SLOTS_FILE=ROOT/'pane-slots.json'; LOG_DIR=ROOT/'logs'
HOST='127.0.0.1'; PORT=int(os.environ.get('HERDR_CONSOLE_PORT','8765'))
PRODUCT_NAME='共事工厂'; PRODUCT_TAGLINE='本地 AI 软件工厂'
HERDR_TASK=HERDR_ROOT/'bin'/'herdr-task'
RUN_JOBS={}
RUN_JOBS_LOCK=threading.Lock()
STAGES=[('requirements','需求分析'),('plan','计划'),('implementation','实现'),('test','测试'),('review','评审'),('wrapup','收尾')]
ACTIVE={'pending','dispatched','working','blocked','agent_done','rework','completed','committed','integrated','cleanup_ready'}
AGENTS=['opencode','codex','claude','qodercli','agy','pi']

def load_json(path,default):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:return default

def save_json(path,data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+'.tmp'); tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); tmp.replace(path)

def run(cmd,timeout=20,check=False):
    try:r=subprocess.run(cmd,text=True,capture_output=True,timeout=timeout)
    except subprocess.TimeoutExpired as e: raise RuntimeError('命令超时: '+' '.join(cmd)) from e
    if check and r.returncode!=0: raise RuntimeError(r.stderr.strip() or r.stdout.strip() or '命令失败')
    return r

def run_json(cmd,timeout=20):
    r=run(cmd,timeout,True)
    try:return json.loads(r.stdout)
    except Exception as e: raise RuntimeError('无法解析 Herdr JSON') from e

def ops_center(workflow_id=None,include_tasks=False):
    cmd=[str(HERDR_TASK),'ops-center']
    if workflow_id:cmd += ['--workflow-id',workflow_id]
    if include_tasks:cmd.append('--include-tasks')
    data=run_json(cmd,20)
    subjects={wid:(w.get('requirement_subject') or herdr_projects.requirement_subject(w.get('requirement',''))) for wid,w in workflows().items()}
    for c in data.get('workflow_cards') or []:
        s=subjects.get(c.get('workflow_id'))
        if s:c['workflow_label']=s
    return data

def projects():return list(load_json(PROJECTS_FILE,{'projects':{}}).get('projects',{}).values())
def workflows():return load_json(WORKFLOWS_FILE,{'workflows':{}}).get('workflows',{})
def tasks():return load_json(TASKS_FILE,{'tasks':[]}).get('tasks',[])
def project_by_id(pid):return next((p for p in projects() if p.get('project_id')==pid),None)
def project_for_workflow(wid):
    w=workflows().get(wid); return (project_by_id(w.get('project_id')) if w else None) or w

def _with_subject(w):
    w=dict(w)
    if not w.get('requirement_subject'):
        w['requirement_subject']=herdr_projects.requirement_subject(w.get('requirement',''))
    return w

def workflows_for_project(pid):
    out=[]
    for wid,w in workflows().items():
        if w.get('project_id')==pid: out.append({'workflow_id':wid,**_with_subject(w)})
    return sorted(out,key=lambda x:x['workflow_id'],reverse=True)

def tasks_for_workflow(wid):return [t for t in tasks() if t.get('workflow_id')==wid]

def stage_summary(ts,key):
    xs=[t for t in ts if t.get('stage')==key]
    if not xs:return {'key':key,'count':0,'status':'waiting','tasks':[]}
    live=[t for t in xs if t.get('status')!='superseded' and not t.get('superseded_by')]
    if not live: st='superseded'
    else:
        ss=[t.get('status','unknown') for t in live]
        if all(s=='cleaned' for s in ss): st='cleaned'
        elif any(s=='failed' for s in ss): st='failed'
        elif any(s=='blocked' for s in ss): st='blocked'
        elif any(s in {'working','dispatched','pending','rework','agent_done'} for s in ss): st='working'
        elif all(s in {'completed','committed','integrated','cleanup_ready','cleaned'} for s in ss): st='finalizing'
        else: st='mixed'
    return {'key':key,'count':len(live),'status':st,'tasks':xs}

def panes(workspace):
    try:
        d=run_json(['herdr','pane','list','--workspace',workspace]).get('result',{})
        return d.get('panes',[]) if isinstance(d,dict) else d if isinstance(d,list) else []
    except Exception:return []

def tabs(workspace):
    try:
        d=run_json(['herdr','tab','list','--workspace',workspace]).get('result',{})
        return d.get('tabs',[]) if isinstance(d,dict) else d if isinstance(d,list) else []
    except Exception:return []

def agent_runtime(pane):
    if not pane:return None
    r=run(['herdr','agent','get',pane],8)
    if r.returncode!=0:return None
    try:return json.loads(r.stdout)['result']['agent']
    except Exception:return None

def pool(pid):return load_json(POOLS_FILE,{'projects':{}}).get('projects',{}).get(pid,{})

AUTH_HINTS = {
    'codex': [HOME / '.codex' / 'auth.json'],
    'claude': [HOME / '.claude.json'],
    'pi': [HOME / '.pi' / 'agent' / 'auth.json'],
    'opencode': [HOME / '.config' / 'opencode'],
    'qodercli': [HOME / '.qoder-cn'],
    'agy': [HOME / '.agy'],
}

IN_FLIGHT_STATUSES = {'pending', 'dispatched', 'working', 'blocked', 'agent_done', 'rework'}

def agent_loads(pid):
    d={a:0 for a in AGENTS}
    for t in tasks():
        if t.get('project_id')==pid and t.get('status') in IN_FLIGHT_STATUSES and t.get('agent'):d[t['agent']]=d.get(t['agent'],0)+1
    return d

def preflight(p):
    po=pool(p.get('project_id')); allowed=po.get('allowed_agents',AGENTS); disabled=set(po.get('disabled_agents',[])); loads=agent_loads(p.get('project_id'))
    out=[]
    for a in allowed:
        b=resolve_agent_binary(a)
        hs=AUTH_HINTS.get(a,[])
        if hs:
            auth_state='present' if any(h.exists() for h in hs) else 'missing'
        else:
            auth_state='unknown'
        out.append({'agent':a,'installed':bool(b),'binary':b,'disabled':a in disabled,'load':loads.get(a,0),'auth_hint':auth_state,'status':'disabled' if a in disabled else 'ready' if b else 'missing'})
    return out

def deep_preflight(p):
    script = HERDR_ROOT / "bin" / "herdr-deep-preflight"
    if not script.exists():
        script = HERDR_ROOT / "herdr" / "deep_preflight.py"
    if not script.exists():
        raise RuntimeError(f"Deep Preflight 未安装: {script}")

    r = run([
        str(script),
        "--project-id", p.get("project_id"),
        "--deep",
        "--json",
    ], 180)

    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip() or "Deep Preflight 执行失败")

    try:
        return json.loads(r.stdout)
    except Exception as e:
        raise RuntimeError("Deep Preflight JSON 解析失败") from e


def slots(p):
    cfg=load_json(Path(p.get('workflow_file','')),{}) ; stage_by_tab={s.get('tab_id'):s for s in cfg.get('stages',[])}; anchors={s.get('anchor_pane_id') for s in cfg.get('stages',[])}; binds=load_json(SLOTS_FILE,{'panes':{}}).get('panes',{}); claimed={t.get('pane_id'):t.get('task_id') for t in tasks() if t.get('pane_id')}; out=[]
    for x in panes(p.get('workspace_id')):
        pid=x.get('pane_id'); tid=x.get('tab_id')
        if not pid or tid not in stage_by_tab or pid in anchors:continue
        rt=agent_runtime(pid); s=stage_by_tab[tid]
        out.append({'pane_id':pid,'tab_id':tid,'stage':s.get('key'),'stage_label':s.get('label'),'bound_agent':binds.get(pid,{}).get('agent','auto'),'claimed_by':claimed.get(pid),'live_agent':rt.get('agent') if rt else None,'agent_status':rt.get('agent_status') if rt else None})
    return out

def service_status():
    uid=os.getuid(); names=['com.user.herdr-controller','com.user.herdr-notifier','com.user.herdr-sentinel','com.user.herdr-factory-console']; out={}
    for n in names:
        r=run(['launchctl','print',f'gui/{uid}/{n}'],5); out[n]='running' if r.returncode==0 and 'state = running' in r.stdout else 'stopped'
    return out


def herdr_workspaces():
    """Herdr is the source of truth for Spaces/Workspaces."""
    try:
        d = run_json(['herdr', 'workspace', 'list'], 10).get('result', {})
        return d.get('workspaces', []) if isinstance(d, dict) else []
    except Exception:
        return []

def infer_space_root(workspace_id):
    """Infer a project root, ignoring task clones/sandboxes."""
    xs = panes(workspace_id)
    if not xs:
        return None

    # Strongest signal: coordinator pane.
    for x in xs:
        if x.get('label') == '总指挥':
            cwd = x.get('cwd') or x.get('foreground_cwd')
            if cwd:
                return cwd

    registered_roots = {p.get('project_root') for p in projects() if p.get('project_root')}

    # If a pane sits exactly at a registered root, prefer it.
    for x in xs:
        cwd = x.get('cwd') or x.get('foreground_cwd')
        if cwd in registered_roots:
            return cwd

    # Otherwise infer only from non-task/non-sandbox panes.
    candidates = []
    for x in xs:
        cwd = x.get('cwd') or x.get('foreground_cwd')
        if not cwd:
            continue
        if '/.herdr-controller/clones/' in cwd:
            continue
        if '/.nexusarchive-sandboxes/' in cwd:
            continue
        candidates.append(cwd)

    if not candidates:
        return None

    from collections import Counter
    return Counter(candidates).most_common(1)[0][0]

def spaces():
    """Join live Herdr Spaces with the Factory project registry."""
    ps = projects()
    by_workspace = {p.get('workspace_id'): p for p in ps if p.get('workspace_id')}
    out = []

    for ws in herdr_workspaces():
        wid = ws.get('workspace_id')
        p = by_workspace.get(wid)
        root = p.get('project_root') if p else infer_space_root(wid)

        if p:
            relation = 'current_factory'
        else:
            same_project = next((x for x in ps if root and x.get('project_root') == root), None)
            if same_project:
                p = same_project
                relation = 'historical'
            else:
                relation = 'unregistered'

        item = dict(ws)
        item.update({
            'relation': relation,
            'project_root': root,
            'project_id': p.get('project_id') if p else None,
            'project_name': p.get('project_name') if p else None,
            'factory_workspace_id': p.get('workspace_id') if p else None,
        })
        out.append(item)

    rank = {'current_factory': 0, 'historical': 1, 'unregistered': 2}
    return sorted(out, key=lambda x: (
        rank.get(x.get('relation'), 9),
        x.get('number', 9999),
        x.get('workspace_id', ''),
    ))

def overview():
    ts = tasks()
    alerts = [
        {
            'task_id': t.get('task_id'),
            'workflow_id': t.get('workflow_id'),
            'project_id': t.get('project_id'),
            'project_name': t.get('project_name'),
            'status': t.get('status'),
            'agent': t.get('agent'),
            'pane_id': t.get('pane_id'),
            'reason': (
                t.get('failure_reason')
                or t.get('blocked_reason')
                or t.get('sentinel_reason')
                or ''
            ),
        }
        for t in ts
        if t.get('status') in {'blocked', 'failed'}
    ]
    ss = spaces()
    return {
        'projects': projects(),
        'spaces': ss,
        'space_count': len(ss),
        'active_workflows': len({
            t.get('workflow_id')
            for t in ts
            if t.get('status') in ACTIVE
        }),
        'active_agents': sum(
            1 for t in ts
            if t.get('status') in {'working', 'blocked', 'dispatched'}
        ),
        'alerts': alerts[-50:],
        'services': service_status(),
    }

def project_detail(pid):
    p=project_by_id(pid)
    if not p:raise RuntimeError('项目不存在')
    ws=workflows_for_project(pid)
    return {'project':p,'workflows':ws,'latest_workflow_id':ws[0]['workflow_id'] if ws else None,'tabs':tabs(p.get('workspace_id')),'panes':panes(p.get('workspace_id')),'slots':slots(p),'agents':preflight(p)}

def workflow_detail(wid):
    w=workflows().get(wid)
    if not w:raise RuntimeError('Workflow 不存在')
    p=project_for_workflow(wid); ts=tasks_for_workflow(wid); ss=[]
    for k,l in STAGES:
        x=stage_summary(ts,k); x['label']=l; ss.append(x)
    return {'workflow':{'workflow_id':wid,**_with_subject(w)},'project':p,'stages':ss,'tasks':ts,'coordinator':agent_runtime(w.get('coordinator_pane_id')),'candidate_branch':w.get('candidate_branch'),'agent_override':w.get('agent_override','auto')}

def task_detail(tid):
    t=next((x for x in tasks() if x.get('task_id')==tid),None)
    if not t:raise RuntimeError('Task 不存在')
    return {'task':t,'runtime':agent_runtime(t.get('pane_id'))}

def read_pane(pid):
    r=run(['herdr','pane','read',pid,'--source','visible'],12)
    if r.returncode!=0:raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return r.stdout

def run_workflow(root,req,agent='auto',template='software-development-v1'):
    r=run([str(HERDR_ROOT/'bin'/'herdr-factory'),'run','--project',root,'--agent',agent or 'auto','--template',template or 'software-development-v1',req],600)
    if r.returncode!=0:raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return r.stdout.strip()

TEMPLATE_NAME_RE=re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')

def _is_builtin_path(path):
    return Path(path).resolve().parent==herdr_workflow.BUNDLED_TEMPLATES_DIR.resolve()

def templates_summary():
    out=[]
    for name,info in sorted(herdr_workflow.list_templates().items()):
        out.append({'id':name,**info,'is_builtin':_is_builtin_path(info['path'])})
    return {'templates':out}

def template_detail(tid):
    info=herdr_workflow.list_templates().get(tid)
    if not info:raise RuntimeError('模板不存在: '+tid)
    wf=herdr_workflow.load_template(tid)
    nodes=[{'id':n.get('id'),'label':n.get('label') or n.get('id'),'node_type':n.get('node_type','agent'),'depends_on':n.get('depends_on',[]),'purpose':n.get('purpose','')} for n in wf.get('nodes',[])]
    return {'template':{'id':tid,**info},'nodes':nodes,'is_builtin':_is_builtin_path(info['path']),'yaml':Path(info['path']).read_text(encoding='utf-8')}

def save_template(name,content):
    name=(name or '').strip()
    if not TEMPLATE_NAME_RE.match(name):raise RuntimeError('模板名只能用小写字母/数字/-/_，且以字母或数字开头')
    info=herdr_workflow.list_templates().get(name)
    if info and _is_builtin_path(info['path']):raise RuntimeError('内置模板只读，请换一个名字保存为自定义模板')
    import yaml
    text=(content or '').replace('\r\n','\n')
    if not text.strip():raise RuntimeError('模板内容不能为空')
    try:data=yaml.safe_load(text)
    except yaml.YAMLError as e:raise RuntimeError('YAML 解析失败:\n'+str(e))
    if not isinstance(data,dict):raise RuntimeError('模板顶层必须是 YAML 映射')
    if data.get('name') and data.get('name')!=name:raise RuntimeError(f"YAML 中的 name '{data.get('name')}' 与模板名 '{name}' 不一致")
    nodes=data.get('nodes')
    if not isinstance(nodes,list) or not nodes:raise RuntimeError('模板必须包含非空 nodes 列表')
    for n in nodes:
        if not isinstance(n,dict) or not n.get('id'):raise RuntimeError('每个节点都必须是包含 id 的映射')
    try:herdr_workflow.validate_workflow_dag(nodes)
    except ValueError as e:raise RuntimeError(str(e))
    path=herdr_workflow.USER_TEMPLATES_DIR/f'{name}.yaml'; path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(text,encoding='utf-8')
    return {'name':name,'path':str(path),'node_count':len(nodes)}

def _run_workflow_job(job_id,root,req,agent,template):
    try:
        output=run_workflow(root,req,agent,template)
        with RUN_JOBS_LOCK:
            RUN_JOBS[job_id].update({'status':'succeeded','output':output,'finished_at':time.time()})
    except Exception as e:
        with RUN_JOBS_LOCK:
            RUN_JOBS[job_id].update({'status':'failed','error':str(e),'finished_at':time.time()})

def start_workflow_job(root,req,agent='auto',template='software-development-v1'):
    if not root or not req:
        raise RuntimeError('项目目录和自然语言需求不能为空')
    job_id=uuid.uuid4().hex[:12]
    with RUN_JOBS_LOCK:
        RUN_JOBS[job_id]={'job_id':job_id,'status':'running','started_at':time.time()}
    threading.Thread(target=_run_workflow_job,args=(job_id,root,req,agent,template),daemon=True).start()
    return RUN_JOBS[job_id].copy()

def workflow_job_status(job_id):
    with RUN_JOBS_LOCK:
        job=RUN_JOBS.get(job_id)
        if not job:raise RuntimeError('启动任务不存在或已过期')
        return job.copy()

def set_agent_override(wid,agent):
    d=load_json(WORKFLOWS_FILE,{'workflows':{}}); w=d.get('workflows',{}).get(wid)
    if not w:raise RuntimeError('Workflow 不存在')
    w['agent_override']=agent or 'auto'; save_json(WORKFLOWS_FILE,d); return {'workflow_id':wid,'agent_override':w['agent_override']}

def bind_slot(pid,agent):
    d=load_json(SLOTS_FILE,{'panes':{}}); d.setdefault('panes',{})[pid]={'agent':agent or 'auto'}; save_json(SLOTS_FILE,d); return {'pane_id':pid,'agent':agent or 'auto'}

def ask_coordinator(tid):
    t=task_detail(tid)['task']; wid=t.get('workflow_id'); w=workflows().get(wid,{}); c=w.get('coordinator_pane_id') or t.get('coordinator_pane_id')
    msg=f'''HERDR_FACTORY_CONSOLE_ACTION\n\nworkflow_id: {wid}\ntask_id: {tid}\nstatus: {t.get('status')}\nstage: {t.get('stage')}\nagent: {t.get('agent')}\npane_id: {t.get('pane_id')}\n\n用户在{PRODUCT_NAME}控制台点击“让总指挥处理”。\n请检查 Task Registry、Pane、Agent Runtime 和验收标准；可恢复则安全恢复，需要人工决策则明确说明。不得删除任何已注册 Task 的 Pane / Tab / Clone。不得处理其他 Workflow。'''
    r=run(['herdr','agent','prompt',c,msg,'--wait','--timeout','600000'],620)
    if r.returncode!=0:raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return {'ok':True}

def _blocked_verdict_tasks(wid):
    return [t for t in tasks_for_workflow(wid)
            if t.get('status')!='superseded' and t.get('stage_verdict')=='blocked']

def manual_advance(wid):
    d=workflow_detail(wid); done=None; nxt=None
    bl=_blocked_verdict_tasks(wid)
    if bl:raise RuntimeError('存在 blocked 验收结论('+', '.join(t['task_id'] for t in bl)+'),禁止手工推进;请先走 fix-loop(修复→重测→复审)或作废过期结论')
    for i,s in enumerate(d['stages']):
        if s['status'] in {'cleaned','finalizing'}:
            done=s['key']; nxt=d['stages'][i+1]['key'] if i+1<len(d['stages']) else None
        else:break
    if not done or not nxt:raise RuntimeError('当前没有可手工推进的下一阶段')
    w=d['workflow']; p=d['project']; c=w.get('coordinator_pane_id')
    msg=f'''HERDR_FACTORY_CONSOLE_STAGE_ADVANCE\n\nworkflow_id: {wid}\nproject_name: {p.get('project_name')}\nproject_root: {p.get('project_root')}\ncompleted_stage: {done}\nnext_stage: {nxt}\nbase_branch: {w.get('base_branch',p.get('base_branch',''))}\n\n用户点击“进入下一阶段”。请先检查门禁；满足后用 ~/herdr-task.py launch 创建 {nxt} Task，参数必须包含 --workflow-id {wid} --stage {nxt} --source {p.get('project_root')} --agent auto。优先复用 Persistent Pane；不要删除 Tab、Pane、Clone。'''
    r=run(['herdr','agent','prompt',c,msg,'--wait','--timeout','600000'],620)
    if r.returncode!=0:raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return {'completed_stage':done,'next_stage':nxt}

def create_candidate(wid):
    allw=load_json(WORKFLOWS_FILE,{'workflows':{}}); w=allw.get('workflows',{}).get(wid)
    if not w:raise RuntimeError('Workflow 不存在')
    bl=_blocked_verdict_tasks(wid)
    if bl:raise RuntimeError('存在 blocked 验收结论('+', '.join(t['task_id'] for t in bl)+'),拒绝创建候选分支;请先完成 fix-loop 闭环或显式处理阻断,避免把未修复的交付合入候选分支')
    p=project_for_workflow(wid); repo=p.get('project_root'); base=w.get('original_base_branch') or p.get('base_branch'); cand=w.get('candidate_branch') or f'herdr/workflow-{wid}'
    tracked=run(['git','-C',repo,'status','--porcelain','--untracked-files=no'],check=True).stdout.strip()
    if tracked:raise RuntimeError('主仓库存在 tracked 修改，拒绝创建候选分支:\n'+tracked)
    impl=[t for t in tasks_for_workflow(wid) if t.get('stage')=='implementation' and t.get('integration_branch')]
    if not impl:raise RuntimeError('没有可汇总的 implementation Integration Branch')
    current=run(['git','-C',repo,'branch','--show-current'],check=True).stdout.strip(); exists=run(['git','-C',repo,'show-ref','--verify','--quiet',f'refs/heads/{cand}']).returncode==0
    try:
        if not exists:run(['git','-C',repo,'branch',cand,base],check=True)
        run(['git','-C',repo,'switch',cand],check=True)
        for t in impl:
            b=t['integration_branch']; m=run(['git','-C',repo,'merge','--no-edit',b],120)
            if m.returncode!=0:
                run(['git','-C',repo,'merge','--abort'],20); raise RuntimeError(f'合并 {b} 失败:\n'+(m.stderr.strip() or m.stdout.strip()))
    finally:run(['git','-C',repo,'switch',current],30)
    w['original_base_branch']=base; w['candidate_branch']=cand; w['base_branch']=cand; save_json(WORKFLOWS_FILE,allw); return {'workflow_id':wid,'candidate_branch':cand,'merged':[t['integration_branch'] for t in impl]}

def tail_log(kind='controller',n=180):
    mp={'controller':LOG_DIR/'controller.out.log','controller_err':LOG_DIR/'controller.err.log','notifier':LOG_DIR/'notifier.out.log','sentinel':LOG_DIR/'sentinel.out.log'}; p=mp.get(kind)
    if not p or not p.exists():return ''
    return '\n'.join(p.read_text(errors='ignore').splitlines()[-max(10,min(n,1000)):])

HTML_TEMPLATE='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>__PRODUCT_NAME__</title><style>
:root{--bg:#0b0f14;--panel:#121821;--card:#17202b;--line:#293342;--text:#edf2f7;--muted:#8fa0b5;--accent:#67a4ff;--good:#42c58a;--warn:#f3b950;--bad:#f36b6b}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif}button,input,select,textarea{font:inherit}button{cursor:pointer}.shell{display:grid;grid-template-columns:250px minmax(0,1fr);min-height:100vh}.sidebar{border-right:1px solid var(--line);background:#0f141b;padding:16px;position:sticky;top:0;height:100vh;overflow:auto}.brand{font-size:20px;font-weight:750}.sub{color:var(--muted);font-size:12px;margin:4px 0 16px}.project{width:100%;text-align:left;background:transparent;border:1px solid var(--line);color:var(--text);border-radius:12px;padding:16px;margin-bottom:8px}.project.active{border-color:var(--accent);background:#14243a}.project small{display:block;color:var(--muted);margin-top:4px}.main{padding:24px;min-width:0}.top{display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:16px}.title{font-size:22px;font-weight:760}.muted{color:var(--muted)}.actions{display:flex;gap:8px;flex-wrap:wrap}.btn{border:1px solid var(--line);background:var(--card);color:var(--text);border-radius:10px;padding:8px 16px}.btn.primary{background:var(--accent);color:#06111f;border-color:var(--accent);font-weight:700}.actions .btn.primary{margin-left:auto}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-bottom:16px}.metric,.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px}.metric{padding:16px}.metric b{font-size:22px;display:block;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-variant-numeric:tabular-nums}.metric span{font-size:12px;color:var(--muted)}.stages{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));gap:8px;overflow:auto;margin-bottom:16px}.stage{min-width:130px;padding:16px;background:var(--panel);border:1px solid var(--line);border-radius:14px}.stage strong{display:block;margin-bottom:8px}.badge{font-size:12px;border-radius:999px;padding:4px 8px;display:inline-block;border:1px solid var(--line)}.badge.cleaned{color:var(--good)}.badge.working,.badge.finalizing{color:var(--warn)}.badge.failed,.badge.blocked{color:var(--bad)}.badge.waiting{color:var(--muted)}.badge.superseded{color:var(--muted)}.badge.in_progress{color:var(--warn)}.grid{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(280px,.8fr);gap:16px}.panel{overflow:hidden}.panel h3{font-size:14px;margin:0;padding:16px;border-bottom:1px solid var(--line)}.task{padding:16px;border-bottom:1px solid var(--line);display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center}.task-name{font-weight:650}.task-id{color:#6f8197;font-size:11px;margin-top:4px}.task-meta{color:var(--muted);font-size:12px;margin-top:4px}.task-actions{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}.mini{padding:4px 8px;border-radius:8px;border:1px solid var(--line);background:#101720;color:var(--text);font-size:12px}.agent-row,.slot-row,.alert-row{padding:8px 16px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:8px;align-items:center}.dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:8px;background:var(--muted)}.dot.ready{background:var(--good)}.dot.working{background:var(--warn)}.dot.disabled,.dot.failed{background:var(--bad)}.section-gap{margin-top:16px}.empty{padding:16px;color:var(--muted);font-size:13px}pre{margin:0;white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;line-height:1.5}.modal{position:fixed;inset:0;background:rgba(0,0,0,.58);display:none;align-items:center;justify-content:center;padding:16px;z-index:50}.modal.open{display:flex}.modal-card{width:min(920px,100%);max-height:86vh;overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:16px}.modal-head{display:flex;justify-content:space-between;padding:16px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--panel)}.modal-body{padding:16px}.close{background:transparent;color:var(--text);border:0;font-size:22px}.form{display:grid;gap:8px}.form label{font-size:12px;color:var(--muted)}.form input,.form select,.form textarea{width:100%;background:#0d131a;color:var(--text);border:1px solid var(--line);border-radius:9px;padding:8px}.form textarea{min-height:120px}.toast{position:fixed;right:24px;bottom:24px;background:#111923;border:1px solid var(--line);padding:8px 16px;border-radius:12px;display:none;max-width:420px;z-index:60}.toast.show{display:block}.danger-text{color:var(--bad)}.good-text{color:var(--good)}.wf-subject{font-size:16px;font-weight:700;line-height:1.35}.wf-sub{font-size:12px;margin-top:4px}.wf-switcher{display:flex;align-items:center;gap:8px;margin:0 0 16px}.wf-switcher label{font-size:12px;color:var(--muted)}.wf-switcher select{background:#0d131a;color:var(--text);border:1px solid var(--line);border-radius:9px;padding:8px;max-width:520px}@media(max-width:1000px){.shell{grid-template-columns:1fr}.sidebar{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line)}.projects{display:flex;gap:8px;overflow:auto}.project{min-width:180px}.grid{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}}
</style></head><body><div class="shell"><aside class="sidebar"><div class="brand">__PRODUCT_NAME__</div><div class="sub">__PRODUCT_TAGLINE__ · 控制台</div><div id="projects" class="projects"></div><button class="btn" style="width:100%;margin-top:8px" onclick="refreshAll()">刷新</button></aside><main class="main"><div class="top"><div><div class="title" id="projectTitle">选择项目</div><div id="workflowTitle"><div class="wf-subject" id="workflowSubject">—</div><div class="muted wf-sub" id="workflowSub"></div></div></div><div class="actions"><button class="btn factory-action" onclick="showLogs()">查看日志</button><button class="btn factory-action" onclick="advanceStage()">进入下一阶段</button><button class="btn factory-action" onclick="createCandidate()">创建候选分支</button><button class="btn factory-action" onclick="showAgentOverride()">指定执行者</button><button class="btn factory-action" onclick="runPreflight()">执行者自检</button><button class="btn factory-action" onclick="showTemplateLibrary()">模板库</button><button class="btn primary factory-action" onclick="showNewWorkflow()">＋ 新需求</button></div></div><div class="metrics"><div class="metric"><b id="mProjects">0</b><span>Herdr 空间</span></div><div class="metric"><b id="mWorkflows">0</b><span>活跃 Workflow</span></div><div class="metric"><b id="mAgents">0</b><span>活跃执行者</span></div><div class="metric"><b id="mAlerts">0</b><span>需要关注</span></div></div><div id="stages" class="stages"></div><div id="workflowSwitcher" class="wf-switcher" style="display:none"></div><div class="grid"><section class="panel"><h3>执行者与任务实时看板</h3><div id="tasks"></div></section><section><div class="panel"><h3>执行者阵容</h3><div id="agents"></div></div><div class="panel section-gap"><h3>常驻智能体工位</h3><div id="slots"></div></div><div class="panel section-gap"><h3>告警中心</h3><div id="alerts"></div></div></section></div></main></div><div id="modal" class="modal"><div class="modal-card"><div class="modal-head"><strong id="modalTitle">详情</strong><button class="close" onclick="closeModal()">×</button></div><div id="modalBody" class="modal-body"></div></div></div><div id="toast" class="toast"></div><script>
let state={overview:null,project:null,workflow:null,ops:null,projectId:null,workflowId:null,spaceId:null,space:null,opsMode:false};
const VIEW_KEY='herdrConsoleView';
function saveViewState(){try{localStorage.setItem(VIEW_KEY,JSON.stringify({opsMode:state.opsMode,spaceId:state.spaceId,workflowId:state.workflowId}))}catch(e){}}
function loadViewState(){try{return JSON.parse(localStorage.getItem(VIEW_KEY)||'null')}catch(e){return null}}
async function waitForWorkflowJob(jobId){
  try{
    for(let i=1;i<=180;i++){
      const job=await api('/api/run/status?id='+encodeURIComponent(jobId));
      if(job.status==='succeeded')return job;
      if(job.status==='failed')throw new Error(job.error||'Workflow 启动失败');
      const wait=document.getElementById('runWaitStatus');
      if(wait)wait.textContent='深度体检与启动中… '+i+'s（深度体检约 1–2 分钟，请勿重复创建）';
      await new Promise(resolve=>setTimeout(resolve,1000));
    }
    throw new Error('Workflow 启动超时，请到运维驾驶舱查看状态');
  }finally{
    const wait=document.getElementById('runWaitStatus');
    if(wait)wait.textContent='';
  }
}
async function submitNewWorkflowAsync(){
  const button=[...document.querySelectorAll('#modal button')].find(x=>x.textContent.includes('启动 Workflow'));
  const q=document.getElementById('newRequirement')?.value.trim();
  const a=document.getElementById('newAgent')?.value;
  const t=document.getElementById('newTemplate')?.value||'software-development-v1';
  if(!q)return toast('请输入需求',true);
  if(button){button.disabled=true;button.textContent='启动中…'}
  try{
    toast('正在创建 Workflow…');
    const job=await api('/api/run',{method:'POST',body:JSON.stringify({project_root:state.project.project.project_root,requirement:q,agent:a,template:t})});
    const result=await waitForWorkflowJob(job.job_id);
    const m=/(?:^|\n)WORKFLOW_ID=(\S+)/.exec(result.output||'');
    if(m){state.workflowId=m[1];saveViewState()}
    closeModal();await refreshAll();
    toast('Workflow 已启动：'+(m?m[1]:(result.output||'已提交')));
  }catch(e){
    if(button){button.disabled=false;button.textContent='重试启动 Workflow'}
    const body=document.getElementById('modalBody');
    if(body){let error=body.querySelector('.run-error');if(!error){error=document.createElement('div');error.className='run-error danger-text';body.prepend(error)}error.textContent='启动失败：'+e.message}
    toast('Workflow 启动失败：'+e.message,true);
  }
}
document.addEventListener('click',e=>{if(e.target.matches('button')&&e.target.textContent.includes('启动 Workflow')){e.preventDefault();e.stopImmediatePropagation();submitNewWorkflowAsync()}},true);
function syncOpsUi(){
  const actions=document.querySelector('.actions');
  if(!actions)return;
  let b=document.getElementById('opsButton');
  if(!b){b=document.createElement('button');b.id='opsButton';actions.prepend(b)}
  b.textContent=state.opsMode?'← 返回工厂':'进入运维驾驶舱';
  b.className=state.opsMode?'btn primary':'btn';
  b.onclick=state.opsMode?exitOpsCenter:showOpsCenter;
  document.querySelectorAll('.factory-action').forEach(button=>{button.hidden=state.opsMode})
}

async function api(p,o={}){
  const r=await fetch(p,{headers:{'Content-Type':'application/json'},...o});
  const d=await r.json();
  if(!r.ok||d.ok===false)throw new Error(d.error||('HTTP '+r.status));
  return d.data??d
}
function toast(m,b=false){
  const e=document.getElementById('toast');
  e.textContent=m;
  e.className='toast show '+(b?'danger-text':'good-text');
  setTimeout(()=>e.classList.remove('show'),4200)
}
function esc(s){
  return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))
}
function humanStatus(s){
  return ({
    waiting:'等待',pending:'待派发',dispatched:'已派发',working:'运行中',
    blocked:'已阻塞',agent_done:'执行者已完成',completed:'待收尾',
    committed:'已提交',integrated:'已集成',cleanup_ready:'待归档',
    cleaned:'已完成',failed:'失败',rework:'返工中',
    superseded:'已取代',in_progress:'运行中',empty:'无任务',
    finalizing:'收尾中',mixed:'处理中',active:'运行中'
  })[s]||s||'未知'
}
function badge(s){return `<span class="badge ${esc(s)}">${esc(humanStatus(s))}</span>`}

function relationText(s){
  return s.relation==='current_factory'?'当前工厂':
         s.relation==='historical'?'历史空间':'未注册'
}
function relationClass(s){
  return s.relation==='current_factory'?'good-text':
         s.relation==='historical'?'muted':'danger-text'
}
function taskSequence(t){
  const m=String(t.task_id||'').match(/-(?:req|plan|impl|test|fix|rev|wrap)-([0-9]+)$/i);
  return m?parseInt(m[1],10):null
}
function taskKind(t){
  const id=String(t.task_id||'').toLowerCase();
  const st=String(t.stage||'').toLowerCase();
  if(id.includes('-fix-'))return '修复';
  if(id.includes('-test-')){
    const n=taskSequence(t);
    return n&&n>1?'回归测试':'测试'
  }
  if(id.includes('-req-')||st==='requirements')return '需求分析';
  if(id.includes('-plan-')||st==='plan')return '计划';
  if(id.includes('-impl-')||st==='implementation')return '实现';
  if(id.includes('-rev-')||st==='review')return '评审';
  if(id.includes('-wrap-')||st==='wrapup')return '收尾';
  return t.stage_label||t.stage||'任务'
}
function taskDisplayName(t){
  const n=taskSequence(t);
  return n?`${taskKind(t)} #${n}`:taskKind(t)
}
function visibleAlerts(){
  const all=(state.overview&&state.overview.alerts)||[];
  if(!state.projectId)return [];
  let rows=all.filter(a=>a.project_id===state.projectId);
  if(state.workflowId){
    const exact=rows.filter(a=>a.workflow_id===state.workflowId);
    if(exact.length)rows=exact
  }
  return rows
}

function formatElapsed(seconds){
  if(seconds==null)return '—';
  const n=Math.max(0,Math.round(seconds));
  if(n<60)return n+'s';
  const m=Math.floor(n/60),s=n%60;
  if(m<60)return m+'m '+String(s).padStart(2,'0')+'s';
  return Math.floor(m/60)+'h '+String(m%60).padStart(2,'0')+'m';
}
function opsHealthClass(s){return ['BLOCKED','FAILED','STALE'].includes(s)?'danger-text':s==='BUSY'?'warn-text':'good-text'}
function healthLabel(s){
  return ({BUSY:'忙碌',IDLE:'空闲',BLOCKED:'阻塞',FAILED:'失败',STALE:'失联',UNKNOWN:'未知'})[s]||s||'未知'
}
function agentStatusLabel(s){
  return ({ready:'可用',disabled:'已禁用',missing:'未安装',working:'运行中'})[s]||s||'未知'
}
function authHintLabel(s){
  return ({present:'已配置',missing:'未配置',unknown:'未知'})[s]||s||'未知'
}
async function fetchTemplates(){
  const d=await api('/api/templates');
  state.templates=d.templates||[];
  return state.templates
}
async function showTemplateLibrary(){
  openModal('工作流模板库','<div class="empty">正在加载模板…</div>');
  try{
    const ts=await fetchTemplates();
    const cards=ts.length?ts.map(t=>`
      <div class="task">
        <div>
          <div class="task-name">${esc(t.label||t.id)} <span class="badge ${t.is_builtin?'waiting':'good-text'}">${t.is_builtin?'内置':'自定义'}</span></div>
          <div class="task-id">${esc(t.id)} · v${esc(t.version)} · ${t.node_count} 节点</div>
          <div class="task-meta">${esc(t.description||'')}</div>
        </div>
        <div class="task-actions">
          <button class="mini" onclick="showTemplateDAG('${esc(t.id)}')">节点依赖</button>
          <button class="mini" onclick="showTemplateEditor('${esc(t.id)}')">${t.is_builtin?'查看 YAML':'编辑'}</button>
        </div>
      </div>`).join(''):'<div class="empty">暂无模板</div>';
    openModal('工作流模板库',`<div class="muted" style="margin-bottom:8px">模板定义 Workflow 的节点与 DAG 依赖。自定义模板保存到 ~/.herdr-controller/templates/，对新启动的 Workflow 即时生效，不影响已运行的 Workflow。</div>${cards}<div style="margin-top:8px"><button class="btn primary" onclick="showTemplateEditor()">＋ 新建模板</button></div>`)
  }catch(e){toast(e.message,true)}
}
async function showTemplateDAG(id){
  try{
    const d=await api('/api/template?id='+encodeURIComponent(id));
    const rows=(d.nodes||[]).map(n=>`
      <div class="agent-row">
        <div>
          <div class="task-name">${esc(n.label||n.id)} <span class="task-id">${esc(n.id)} · ${esc(n.node_type||'agent')}</span></div>
          <div class="task-meta">${esc(n.purpose||'')}</div>
        </div>
        <div class="task-meta">${(n.depends_on||[]).length?'← '+(n.depends_on||[]).map(esc).join('、'):'起始节点'}</div>
      </div>`).join('');
    openModal('节点依赖 · '+((d.template&&d.template.label)||id),rows||'<div class="empty">无节点</div>')
  }catch(e){toast(e.message,true)}
}
const TEMPLATE_SCAFFOLD=`name: my-workflow
label: 我的工作流
version: "1.0"
description: 在这里描述业务流程

nodes:
  - id: analyze
    label: 分析
    node_type: agent
    purpose: 第一步做什么

  - id: deliver
    label: 交付
    node_type: agent
    depends_on: [analyze]
    purpose: 汇总并产出结果
`;
async function showTemplateEditor(id){
  const info=(state.templates||[]).find(t=>t.id===id);
  const builtin=!!(info&&info.is_builtin);
  let yaml=TEMPLATE_SCAFFOLD;
  if(id){
    try{
      const d=await api('/api/template?id='+encodeURIComponent(id));
      yaml=d.yaml
    }catch(e){return toast(e.message,true)}
  }
  openModal(id?(builtin?'内置模板（只读）':'编辑模板 · '+id):'新建模板',`
    <div class="form">
      <label>模板名（小写字母/数字/-/_，作为启动时的 --template 参数${id?'，不可修改':''}）</label>
      <input id="tplName" value="${esc(id||'')}" ${id?'disabled':''}>
      <label>YAML 定义</label>
      <textarea id="tplYaml" style="min-height:320px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace" ${builtin?'disabled':''}>${esc(yaml)}</textarea>
      <div class="muted">保存时服务端会做 DAG 校验（未知依赖 / 循环依赖会被拒绝）。内置模板只读；自定义模板保存后可在“新建需求”中选用。</div>
      ${builtin?'':`<button class="btn primary" onclick="saveTemplate()">保存模板</button>`}
    </div>`)
}
async function saveTemplate(){
  const name=document.getElementById('tplName').value.trim();
  const yaml=document.getElementById('tplYaml').value;
  if(!name)return toast('请填写模板名',true);
  try{
    const d=await api('/api/template',{method:'POST',body:JSON.stringify({name:name,yaml:yaml})});
    closeModal();
    await fetchTemplates();
    toast('模板已保存：'+d.name+'（'+d.node_count+' 节点）')
  }catch(e){toast(e.message,true)}
}
async function populateTemplateSelect(){
  try{
    const ts=await fetchTemplates();
    const sel=document.getElementById('newTemplate');
    if(!sel||!ts.length)return;
    sel.innerHTML=ts.map(t=>`<option value="${esc(t.id)}"${t.id==='software-development-v1'?' selected':''}>${esc(t.id)} · ${esc(t.label||'')}（${t.node_count} 节点）</option>`).join('')
  }catch(e){}
}
function showOpsCenter(){
  state.opsMode=true;
  saveViewState();
  syncOpsUi();
  document.getElementById('projectTitle').textContent='运维驾驶舱';
  document.getElementById('workflowSubject').textContent='四层运维视图 · 每 10 分钟自动刷新';
  document.getElementById('workflowSub').textContent='';
  document.getElementById('workflowSwitcher').style.display='none';
  document.getElementById('stages').innerHTML='';
  document.getElementById('tasks').innerHTML='<div class="empty">正在加载运维数据…</div>';
  loadOpsCenter()
}
async function exitOpsCenter(){
  state.opsMode=false;
  saveViewState();
  syncOpsUi();
  await refreshAll()
}
async function loadOpsCenter(){
  try{
    const ops=await api('/api/ops-center?include_tasks=1');
    if(!state.opsMode)return;
    state.ops=ops;
    renderOpsCenter()
  }catch(e){if(state.opsMode)toast(e.message,true)}
}
function renderOpsCenter(){
  const d=state.ops||{},b=d.boss||{},cards=d.workflow_cards||[],fleet=d.agent_fleet||[],anoms=d.anomalies||[];
  document.getElementById('mProjects').textContent=b.running_workflows||0;
  document.getElementById('mWorkflows').textContent=b.working_agents||0;
  document.getElementById('mAgents').textContent=b.attention_required||0;
  document.getElementById('mAlerts').textContent=anoms.length;
  document.getElementById('projects').innerHTML='<button class="project active" onclick="showOpsCenter()"><strong>运维驾驶舱</strong><small>老板视角 · 执行者舰队 · 异常中心</small></button>';
  document.getElementById('tasks').innerHTML=`<div class="ops-section"><h3>Workflow / 工作流节点</h3>${cards.length?cards.map(c=>`<div class="ops-card"><div class="ops-card-head"><strong>${esc(c.workflow_label||c.workflow_id)}</strong><span>${formatElapsed(c.runtime_seconds)}</span></div><div class="task-meta">${esc(c.workflow_id)} · ${c.tasks.active} 运行 · ${c.tasks.completed} 完成 · ${c.tasks.blocked} 阻塞 · ${c.tasks.failed} 失败</div><div class="ops-nodes">${c.nodes.map(n=>`<span class="badge ${esc(n.status)}">${esc(n.node_label)} · ${esc(humanStatus(n.status))}</span>`).join('')}</div></div>`).join(''):'<div class="empty">暂无运行中的 Workflow</div>'}</div><div class="ops-section"><h3>执行者舰队</h3>${fleet.map(a=>`<div class="fleet-row"><strong>${esc(a.agent)}</strong><span class="${opsHealthClass(a.health)}">${esc(healthLabel(a.health))}</span><span>${esc(a.current_task||'—')}</span><span>负载 ${a.load}</span><span>${formatElapsed(a.runtime_seconds)}</span><span>${esc(a.last_result)}</span></div>`).join('')}</div>`;
  document.querySelectorAll('.ops-card').forEach((el,i)=>{el.style.cursor='pointer';el.onclick=()=>openWorkflowFromOps(cards[i].workflow_id)});
  document.getElementById('agents').innerHTML='<div class="empty">运行时状态、任务注册表状态和最后事件已合并到执行者舰队。</div>';
  document.getElementById('slots').innerHTML='<div class="empty">点击现有 Workflow 页面可进入工位 / 任务 详情。</div>';
  document.getElementById('alerts').innerHTML=anoms.length?anoms.map(a=>`<div class="alert-row"><div><strong>${esc(a.kind)}</strong><div class="task-meta">${esc(a.task_id||'')} · ${esc(a.agent||'')} · ${esc(a.last_event||'')}</div></div><button class="mini" onclick="showOpsAnomaly(${JSON.stringify(a).replaceAll('"','&quot;')})">处理</button></div>`).join(''):'<div class="empty">暂无异常</div>';
}
function showOpsAnomaly(a){openModal(a.kind,`<div class="task-meta">${esc(a.task_id||'')} · ${esc(a.agent||'')} · ${esc(a.node||'')}</div><div class="task-meta" style="margin:8px 0">最后事件：${esc(a.last_event||'—')}</div><div class="actions">${(a.actions||[]).map(x=>`<button class="mini" onclick="toast('建议操作：${esc(x)}')">${esc(x)}</button>`).join('')}</div><pre style="margin-top:16px">${esc(JSON.stringify(a.links||{},null,2))}</pre>`)}
async function openWorkflowFromOps(id){
  state.opsMode=false;
  syncOpsUi();
  state.workflowId=id;
  saveViewState();
  try{
    await loadWorkflow(id);
    const project=(state.workflow&&state.workflow.project)||{};
    if(project.project_id)state.projectId=project.project_id;
    if(project.workspace_id)state.spaceId=project.workspace_id;
    await refreshAll()
  }catch(e){
    toast(e.message,true);
    state.opsMode=true;
    saveViewState();
    syncOpsUi();
    await loadOpsCenter()
  }
}

async function refreshAll(){
  try{
    syncOpsUi();
    if(state.opsMode){await loadOpsCenter();return}
    state.overview=await api('/api/overview');
    if(state.opsMode){await loadOpsCenter();return}
    const ss=state.overview.spaces||[];

    if(!state.spaceId||!ss.some(s=>s.workspace_id===state.spaceId)){
      const preferred=ss.find(s=>s.relation==='current_factory')||ss[0];
      state.spaceId=preferred?preferred.workspace_id:null
    }

    renderOverview();
    if(state.spaceId)await selectSpace(state.spaceId,false)
  }catch(e){toast(e.message,true)}
}

function renderOverview(){
  const o=state.overview;
  const ss=o.spaces||[];
  const alerts=visibleAlerts();

  document.getElementById('mProjects').textContent=ss.length;
  document.getElementById('mWorkflows').textContent=o.active_workflows;
  document.getElementById('mAgents').textContent=o.active_agents;
  document.getElementById('mAlerts').textContent=alerts.length;

  document.getElementById('projects').innerHTML=ss.length?ss.map(s=>`
    <button class="project ${s.workspace_id===state.spaceId?'active':''}" onclick="selectSpace('${esc(s.workspace_id)}')">
      <div style="display:flex;justify-content:space-between;gap:8px;align-items:center">
        <strong>${esc(s.label||s.workspace_id)}</strong>
        <span class="${relationClass(s)}" style="font-size:11px">${esc(relationText(s))}</span>
      </div>
      <small>${esc(s.workspace_id)} · ${s.tab_count||0} 个工作流节点 · ${s.pane_count||0} 个智能体工位</small>
      <small>${esc(s.project_root||'未识别项目目录')}</small>
    </button>`).join(''):'<div class="empty">Herdr 当前没有空间</div>';

  document.getElementById('alerts').innerHTML=alerts.length?alerts.map(a=>`
    <div class="alert-row">
      <div>
        <div class="task-name">${esc(taskDisplayName(a))}</div>
        <div class="task-id">${esc(a.task_id||'')}</div>
        <div class="task-meta">${esc(a.agent||'')} · ${esc(a.pane_id||'')}</div>
      </div>
      ${badge(a.status)}
    </div>`).join(''):'<div class="empty">当前项目 / Workflow 暂无告警</div>'
}

async function selectSpace(workspaceId,rer=true){
  const s=(state.overview.spaces||[]).find(x=>x.workspace_id===workspaceId);
  if(!s)return;

  state.spaceId=workspaceId;
  state.space=s;

  if(s.relation==='current_factory'&&s.project_id){
    state.projectId=s.project_id;
    await loadProject(s.project_id,false);
    document.getElementById('projectTitle').textContent=s.label||s.project_name||s.workspace_id;
  }else{
    state.project=null;
    state.projectId=s.project_id||null;
    state.workflow=null;
    state.workflowId=null;

    document.getElementById('projectTitle').textContent=s.label||s.workspace_id;
    document.getElementById('workflowSubject').textContent=relationText(s);
    document.getElementById('workflowSub').textContent=
      s.workspace_id+
      (s.factory_workspace_id?` · 当前工厂空间：${s.factory_workspace_id}`:'');
    document.getElementById('workflowSwitcher').style.display='none';

    document.getElementById('stages').innerHTML='';
    document.getElementById('tasks').innerHTML=`
      <div class="empty">
        <div style="font-weight:650;margin-bottom:8px">${esc(relationText(s))}</div>
        <div>项目目录：${esc(s.project_root||'未识别')}</div>
        <div style="margin-top:4px">${s.tab_count||0} 个工作流节点 · ${s.pane_count||0} 个智能体工位</div>
        ${s.factory_workspace_id?`<div style="margin-top:8px">该项目当前工厂空间：${esc(s.factory_workspace_id)}</div>`:''}
        <div style="margin-top:8px">该空间仅展示，不参与当前自动 Workflow 调度。</div>
      </div>`;
    document.getElementById('agents').innerHTML='<div class="empty">历史/未注册空间不参与当前工厂执行者阵容。</div>';
    document.getElementById('slots').innerHTML='<div class="empty">只有当前工厂空间才显示可调度的常驻智能体工位。</div>';
  }

  if(rer)renderOverview();
  saveViewState()
}

async function loadProject(id,rer=true){state.projectId=id;state.project=await api('/api/project?id='+encodeURIComponent(id));if(rer&&state.overview)renderOverview();document.getElementById('projectTitle').textContent=state.project.project.project_name;const w=state.project.workflows;if(!state.workflowId||!w.some(x=>x.workflow_id===state.workflowId))state.workflowId=state.project.latest_workflow_id;renderAgents();renderSlots();renderWorkflowSwitcher();if(state.workflowId)await loadWorkflow(state.workflowId);else clearWorkflow()}
function workflowSubject(w){return (w&&w.requirement_subject)||''}
function renderWorkflowHead(w){
  const subj=workflowSubject(w);
  document.getElementById('workflowSubject').textContent=subj||w.workflow_id;
  const a=state.workflow.agent_override||'auto';
  const parts=[];
  if(subj)parts.push('Workflow '+w.workflow_id);
  parts.push('执行者 '+(a==='auto'?'自动分配':a));
  if(w.candidate_branch)parts.push('候选分支 '+w.candidate_branch);
  document.getElementById('workflowSub').textContent=parts.join(' · ')
}
function renderWorkflowSwitcher(){
  const box=document.getElementById('workflowSwitcher'),ws=(state.project&&state.project.workflows)||[];
  const sig=state.workflowId+'#'+ws.map(x=>x.workflow_id+':'+(workflowSubject(x)||x.workflow_id)).join('|');
  if(box.dataset.sig===sig){box.style.display=ws.length<2?'none':'flex';return}
  box.dataset.sig=sig;
  if(ws.length<2){box.style.display='none';box.innerHTML='';return}
  box.style.display='flex';
  box.innerHTML='<label>Workflow</label><select onchange="state.workflowId=this.value;loadWorkflow(this.value)">'+ws.map(x=>`<option value="${esc(x.workflow_id)}"${x.workflow_id===state.workflowId?' selected':''}>${esc(workflowSubject(x)||x.workflow_id)}</option>`).join('')+'</select>'
}
async function loadWorkflow(id){state.workflowId=id;state.workflow=await api('/api/workflow?id='+encodeURIComponent(id));const w=state.workflow.workflow;saveViewState();renderWorkflowHead(w);renderStages();renderTasks()}
function clearWorkflow(){state.workflow=null;state.workflowId=null;saveViewState();document.getElementById('workflowSubject').textContent='暂无 Workflow';document.getElementById('workflowSub').textContent='';document.getElementById('stages').innerHTML='';document.getElementById('tasks').innerHTML='<div class="empty">暂无任务</div>'}function renderStages(){
  document.getElementById('stages').innerHTML=state.workflow.stages.map((s,i)=>`
    <div class="stage">
      <strong>${i+1}. ${esc(s.label)}</strong>
      ${badge(s.status)}
      <div class="task-meta">${s.count} 个任务</div>
    </div>`).join('')
}
function renderTasks(){
  const e=document.getElementById('tasks'),ts=state.workflow.tasks;
  if(!ts.length){e.innerHTML='<div class="empty">暂无任务</div>';return}
  e.innerHTML=ts.map(t=>`
    <div class="task">
      <div>
        <div class="task-name">${esc(taskDisplayName(t))}</div>
        <div class="task-id">任务 ID：${esc(t.task_id)}</div>
        <div class="task-meta">${esc(t.agent||'未分配执行者')} · ${esc(t.pane_id||'未分配工位')} · ${esc(t.pane_source||'')}</div>
      </div>
      <div class="task-actions">
        ${badge(t.status)}
        <button class="mini" onclick="showTask('${esc(t.task_id)}')">详情</button>
        <button class="mini" onclick="showPane('${esc(t.pane_id||'')}')">工位</button>
        <button class="mini" onclick="askCoordinator('${esc(t.task_id)}')">让总指挥处理</button>
      </div>
    </div>`).join('')
}
function renderAgents(){const rs=state.project.agents||[];document.getElementById('agents').innerHTML=rs.length?rs.map(a=>`<div class="agent-row"><span><i class="dot ${esc(a.status)}"></i>${esc(a.agent)}</span><span class="muted">${esc(agentStatusLabel(a.status))} · 负载 ${a.load} · 认证 ${esc(authHintLabel(a.auth_hint))}</span></div>`).join(''):'<div class="empty">暂无执行者信息</div>'}function renderSlots(){const rs=state.project.slots||[];document.getElementById('slots').innerHTML=rs.length?rs.map(s=>`<div class="slot-row"><div><div>${esc(s.pane_id)} · ${esc(s.stage_label)}</div><div class="task-meta">绑定 ${esc(s.bound_agent)} · 运行时 ${esc(s.live_agent||'空闲')} · ${esc(s.claimed_by?'被任务占用':'未占用')}</div></div><button class="mini" onclick="bindSlotPrompt('${esc(s.pane_id)}')">绑定</button></div>`).join(''):'<div class="empty">暂无用户预建智能体工位</div>'}
function openModal(t,h){document.getElementById('modalTitle').textContent=t;document.getElementById('modalBody').innerHTML=h;document.getElementById('modal').classList.add('open')}function closeModal(){document.getElementById('modal').classList.remove('open')}function showNewWorkflow(){if(!state.project||!state.space||state.space.relation!=='current_factory')return toast('请先选择当前工厂空间',true);openModal('新建需求',`<div class="form"><label>项目</label><input value="${esc(state.project.project.project_name)}" disabled><label>工作流模板</label><select id="newTemplate"><option value="software-development-v1">software-development-v1（默认软件开发）</option></select><label>执行者策略</label><select id="newAgent"><option value="auto">auto（Router 自动）</option>${['opencode','codex','claude','qodercli','agy','pi'].map(a=>`<option>${a}</option>`).join('')}</select><label>自然语言需求</label><textarea id="newRequirement"></textarea><button class="btn primary" onclick="submitNewWorkflow()">启动 Workflow</button><div id="runWaitStatus" class="muted" style="min-height:16px;font-size:12px"></div></div>`);populateTemplateSelect()}async function submitNewWorkflow(){const q=document.getElementById('newRequirement').value.trim(),a=document.getElementById('newAgent').value,t=document.getElementById('newTemplate').value;if(!q)return toast('请输入需求',true);try{toast('正在启动 Workflow…');await api('/api/run',{method:'POST',body:JSON.stringify({project_root:state.project.project.project_root,requirement:q,agent:a,template:t})});closeModal();await refreshAll();toast('Workflow 已启动')}catch(e){toast(e.message,true)}}async function runPreflight(){
  if(!state.projectId)return toast('当前空间不参与工厂调度',true);
  try{
    toast('正在执行深度自检…');
    const d=await api('/api/deep-preflight?id='+encodeURIComponent(state.projectId));
    const rows=d.agents||[];

    const statusLabel=s=>({
      READY:'就绪',
      DISABLED:'已禁用',
      UNKNOWN:'未知',
      MISSING:'未安装',
      WARN:'警告',
      TOKEN_EXHAUSTED:'额度耗尽',
      AUTH_REQUIRED:'需要认证',
      TRUST_REQUIRED:'需要信任',
      UPDATE_BLOCKED:'更新受阻',
      TIMEOUT:'超时',
      ERROR:'错误'
    })[s]||s;

    const hard=new Set([
      'TOKEN_EXHAUSTED','AUTH_REQUIRED','TRUST_REQUIRED',
      'UPDATE_BLOCKED','TIMEOUT','ERROR','MISSING'
    ]);

    const html='<div class="muted" style="margin-bottom:8px">'
      +'真实最小调用仅用于已确认安全非交互模式的执行者；'
      +'UNKNOWN 表示尚未配置安全适配器，不代表不可用。'
      +'</div><div>'
      +rows.map(a=>{
        const deep=a.deep||{};
        const final=a.final_status||a.shallow_status||'UNKNOWN';
        const cls=final==='READY'?'good-text':(hard.has(final)?'danger-text':'muted');
        const note=deep.note||a.version||a.binary||'';
        const adapter=deep.adapter?(' · '+esc(deep.adapter)):'';
        return '<div class="agent-row">'
          +'<div><div><strong>'+esc(a.agent)+'</strong> '
          +'<span class="'+cls+'">'+esc(statusLabel(final))+'</span></div>'
          +'<div class="task-meta">'+esc(note)+adapter+'</div></div>'
          +'<div class="task-meta">'+esc(authHintLabel(a.auth_hint||'unknown'))+'</div>'
          +'</div>';
      }).join('')
      +'</div>';

    openModal('执行者深度自检',html);
    toast('深度自检完成');
  }catch(e){
    toast(e.message,true);
  }
}
function showAgentOverride(){if(!state.workflowId)return toast('当前没有 Workflow',true);const cur=state.workflow.agent_override||'auto';openModal('指定后续任务执行者',`<div class="form"><select id="overrideAgent">${['auto','opencode','codex','claude','qodercli','agy','pi'].map(a=>`<option ${a===cur?'selected':''}>${a}</option>`).join('')}</select><button class="btn primary" onclick="saveAgentOverride()">保存</button><div class="muted">只影响后续新建任务。</div></div>`)}async function saveAgentOverride(){try{await api('/api/workflow/agent',{method:'POST',body:JSON.stringify({workflow_id:state.workflowId,agent:document.getElementById('overrideAgent').value})});closeModal();await loadWorkflow(state.workflowId);toast('执行者策略已更新')}catch(e){toast(e.message,true)}}async function showTask(id){try{const d=await api('/api/task?id='+encodeURIComponent(id));openModal(id,`<pre>${esc(JSON.stringify(d,null,2))}</pre>`)}catch(e){toast(e.message,true)}}async function showPane(id){if(!id)return toast('没有工位',true);try{const d=await api('/api/pane/read?id='+encodeURIComponent(id));openModal('工位 '+id,`<pre>${esc(d.output)}</pre>`)}catch(e){toast(e.message,true)}}async function askCoordinator(id){try{toast('正在通知总指挥…');await api('/api/task/coordinator',{method:'POST',body:JSON.stringify({task_id:id})});toast('总指挥已处理/接收')}catch(e){toast(e.message,true)}}async function createCandidate(){if(!confirm('创建/更新 Workflow 候选分支？不会直接合入 main。'))return;try{toast('正在构建候选分支…');const d=await api('/api/workflow/candidate',{method:'POST',body:JSON.stringify({workflow_id:state.workflowId})});await loadWorkflow(state.workflowId);toast('候选分支：'+d.candidate_branch)}catch(e){toast(e.message,true)}}async function advanceStage(){if(!confirm('让总指挥检查门禁并尝试进入下一阶段？'))return;try{toast('正在请求阶段推进…');await api('/api/workflow/advance',{method:'POST',body:JSON.stringify({workflow_id:state.workflowId})});toast('阶段推进请求已完成')}catch(e){toast(e.message,true)}}async function showLogs(){try{const d=await api('/api/logs?kind=controller');openModal('控制器日志',`<pre>${esc(d.output)}</pre>`)}catch(e){toast(e.message,true)}}async function bindSlotPrompt(p){const a=prompt('绑定执行者','auto');if(!a)return;try{await api('/api/slot/bind',{method:'POST',body:JSON.stringify({pane_id:p,agent:a})});await loadProject(state.projectId,false);toast('工位已绑定 '+a)}catch(e){toast(e.message,true)}}setInterval(()=>{if(!document.hidden)refreshAll()},600000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshAll()});(function(){const v=loadViewState();if(!v)return;state.opsMode=!!v.opsMode;state.spaceId=v.spaceId||null;state.workflowId=v.workflowId||null})();state.opsMode?showOpsCenter():refreshAll();
</script></body></html>'''
HTML=HTML_TEMPLATE.replace('__PRODUCT_NAME__',PRODUCT_NAME).replace('__PRODUCT_TAGLINE__',PRODUCT_TAGLINE)

class Handler(BaseHTTPRequestHandler):
    server_version='HerdrFactoryConsole/1.2'
    def log_message(self,fmt,*args):print(f'[{datetime.now().isoformat(timespec="seconds")}] '+(fmt%args),flush=True)
    def send_json(self,status,data=None,error=None):
        p={'ok':error is None};
        if data is not None:p['data']=data
        if error is not None:p['error']=str(error)
        raw=json.dumps(p,ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(raw)
    def send_html(self,text):
        raw=text.encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(raw)
    def query(self):return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
    def body(self):
        n=int(self.headers.get('Content-Length','0') or 0); raw=self.rfile.read(n) if n else b'{}'; return json.loads(raw.decode() or '{}')
    def do_GET(self):
        p=urllib.parse.urlparse(self.path).path
        try:
            if p=='/':return self.send_html(HTML)
            if p=='/api/overview':return self.send_json(200,overview())
            if p=='/api/ops-center':return self.send_json(200,ops_center(self.query().get('workflow_id',[''])[0] or None,self.query().get('include_tasks',[''])[0]=='1'))
            if p=='/api/templates':return self.send_json(200,templates_summary())
            if p=='/api/template':return self.send_json(200,template_detail(self.query().get('id',[''])[0]))
            if p=='/api/run/status':return self.send_json(200,workflow_job_status(self.query().get('id',[''])[0]))
            if p=='/api/project':return self.send_json(200,project_detail(self.query().get('id',[''])[0]))
            if p=='/api/workflow':return self.send_json(200,workflow_detail(self.query().get('id',[''])[0]))
            if p=='/api/task':return self.send_json(200,task_detail(self.query().get('id',[''])[0]))
            if p=='/api/pane/read':return self.send_json(200,{'output':read_pane(self.query().get('id',[''])[0])})
            if p=='/api/deep-preflight':
                x=project_by_id(self.query().get('id',[''])[0])
                if not x:
                    raise RuntimeError('项目不存在')
                return self.send_json(200,deep_preflight(x))
            if p=='/api/preflight':
                x=project_by_id(self.query().get('id',[''])[0]);
                if not x:raise RuntimeError('项目不存在')
                return self.send_json(200,preflight(x))
            if p=='/api/logs':return self.send_json(200,{'output':tail_log(self.query().get('kind',['controller'])[0])})
            return self.send_json(404,error='Not Found')
        except Exception as e:
            self.log_message('GET %s failed: %s', self.path, e)
            return self.send_json(500,error=e)
    def do_POST(self):
        p=urllib.parse.urlparse(self.path).path
        try:
            b=self.body()
            if p=='/api/run':return self.send_json(202,start_workflow_job(str(Path(b.get('project_root','')).expanduser().resolve()),str(b.get('requirement','')).strip(),str(b.get('agent') or 'auto'),str(b.get('template') or 'software-development-v1')))
            if p=='/api/template':return self.send_json(200,save_template(str(b.get('name') or ''),str(b.get('yaml') or '')))
            if p=='/api/workflow/agent':return self.send_json(200,set_agent_override(str(b['workflow_id']),str(b.get('agent') or 'auto')))
            if p=='/api/workflow/candidate':return self.send_json(200,create_candidate(str(b['workflow_id'])))
            if p=='/api/workflow/advance':return self.send_json(200,manual_advance(str(b['workflow_id'])))
            if p=='/api/task/coordinator':return self.send_json(200,ask_coordinator(str(b['task_id'])))
            if p=='/api/slot/bind':return self.send_json(200,bind_slot(str(b['pane_id']),str(b.get('agent') or 'auto')))
            return self.send_json(404,error='Not Found')
        except Exception as e:
            self.log_message('POST %s failed: %s', self.path, e)
            return self.send_json(500,error=e)

def main():
    ROOT.mkdir(parents=True,exist_ok=True); print(f'{PRODUCT_NAME}控制台: http://{HOST}:{PORT}',flush=True); ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
if __name__=='__main__':main()
