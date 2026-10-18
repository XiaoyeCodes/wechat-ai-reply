"""Own an isolated, pinned Hermes gateway without changing the user's Hermes install."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import threading
import time

import httpx
import yaml

from .ai_store import protect,unprotect

ROOT=Path(__file__).resolve().parents[1]


def approval_config(agent):
    full=agent.get('hermes_tool_policy')=='full'
    return {'mode':'off' if full else 'manual','timeout':agent.get('hermes_approval_timeout',600),
            'unattended_mode':'approve' if full else 'deny'}


class HermesRuntime:
    def __init__(self,ai):
        self.ai=ai
        self.lock=threading.Lock()
        self.processes={}

    def ensure(self,agent):
        with self.lock:return self._ensure(agent)

    def _ensure(self,agent):
        manifest=ROOT/'.runtime/hermes.json'
        if not manifest.is_file():raise ValueError('本机 Hermes 尚未安装，请运行 scripts/install-hermes.ps1')
        install=json.loads(manifest.read_text(encoding='utf-8-sig'))
        source=Path(install['source']);python=Path(install['python'])
        if not python.is_file() or not (source/'hermes').is_file():raise ValueError('Hermes 安装路径失效，请重新运行安装脚本')
        namespace=hashlib.sha256(str(self.ai.store.path.resolve()).encode()).hexdigest()[:10]
        home=Path(install['home'])/'profiles'/('relay-'+namespace+'-'+agent['id'])
        home.mkdir(parents=True,exist_ok=True)
        workspace=Path(agent.get('hermes_workspace') or home/'workspace').resolve()
        if agent.get('hermes_workspace') and not workspace.is_dir():raise ValueError('Hermes 起始目录不存在，请选择已有文件夹')
        workspace.mkdir(parents=True,exist_ok=True)
        # The bundled desktop skill was reviewed at the pinned release; it teaches
        # capture -> target by element -> verify, and contains no executable files.
        bundled=home/'skills'/'computer-use'
        if not (bundled/'SKILL.md').exists():
            bundled.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source/'skills/autonomous-ai-agents/computer-use/SKILL.md',bundled/'SKILL.md')
            shutil.copyfile(source/'LICENSE',bundled/'LICENSE')
        skills_stamp=self._sync_skills(home,agent)
        provider=self.ai.data.get('providers',agent['provider_id'],private=True)
        selected_tools=['terminal','file','browser','computer_use','skills','todo','memory','session_search','delegation','wechat_read']
        plugin_source=Path(__file__).with_name('hermes_plugin.py').read_text(encoding='utf-8')
        policy_stamp=hashlib.sha256((plugin_source+agent.get('hermes_tool_policy','native')).encode()).hexdigest()
        plugin=home/'plugins'/'wechat-permissions';plugin.mkdir(parents=True,exist_ok=True)
        (plugin/'__init__.py').write_text(plugin_source,encoding='utf-8')
        (plugin/'plugin.yaml').write_text(yaml.safe_dump({'name':'wechat-permissions','version':'1.0.0',
            'description':'WeChat operation permissions and read-only helpers','provides_hooks':['pre_tool_call'],
            'provides_tools':['relay_web_read','relay_system_info']},sort_keys=False),encoding='utf-8')
        policy_ready=home/'wechat-policy-ready'
        config={
            'model':{'provider':'custom','default':provider['model'],'base_url':provider['base_url'],'streaming':False},
            'terminal':{'backend':'local','cwd':str(workspace),'timeout':120},
            'approvals':approval_config(agent),
            'platform_toolsets':{'api_server':selected_tools},
            'agent':{'max_iterations':30},
            'browser':{'cloud_provider':'local'},
            'gateway':{'standalone':True,'api_server':{'enabled':True,'host':'127.0.0.1','max_concurrent_runs':1}},
        }
        stamp=hashlib.sha256(json.dumps([config,provider.get('secret',''),skills_stamp,policy_stamp],sort_keys=True).encode()).hexdigest()
        connection=home/'connection.json'
        saved=json.loads(connection.read_text(encoding='utf-8')) if connection.is_file() else {}
        if not saved:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            saved={'port':port,'secret':protect(secrets.token_urlsafe(36))}
        key=unprotect(saved['secret']);url=f"http://127.0.0.1:{saved['port']}"
        def ready():
            try:
                with httpx.Client(timeout=1,trust_env=False) as client:
                    r=client.get(url+'/v1/capabilities',headers={'Authorization':'Bearer '+key})
                    return r.status_code==200 and r.json().get('platform')=='hermes-agent'
            except (httpx.HTTPError,ValueError):return False
        process=self.processes.get(agent['id'])
        def policy_loaded():
            return policy_ready.is_file() and policy_ready.read_text(encoding='utf-8')==policy_stamp
        if saved.get('stamp')==stamp and ready() and policy_loaded():return url,key
        if process and process.poll() is None:
            self._terminate(process)
        elif ready():
            raise ValueError('已有 Hermes 引擎使用旧配置，请停止该引擎后再次检查连接')
        # Keep user-managed additions such as MCP servers, while Relay owns model/transport settings.
        config_path=home/'config.yaml'
        previous=yaml.safe_load(config_path.read_text(encoding='utf-8')) if config_path.is_file() else {}
        config={**(previous if isinstance(previous,dict) else {}),**config}
        plugins=config.setdefault('plugins',{})
        plugins['enabled']=list(set(plugins.get('enabled') or [])|{'wechat-permissions'})
        plugins['disabled']=[p for p in plugins.get('disabled',[]) if p!='wechat-permissions']
        config['gateway']['api_server']['port']=saved['port']
        config_path.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False),encoding='utf-8')
        saved['stamp']=stamp
        connection.write_text(json.dumps(saved),encoding='utf-8')
        # Do not inherit unrelated API keys or another assistant's auth variables.
        allowed={'PATH','PATHEXT','SYSTEMROOT','WINDIR','SYSTEMDRIVE','COMSPEC','TEMP','TMP',
                 'USERPROFILE','USERNAME','USERDOMAIN','APPDATA','LOCALAPPDATA','PROGRAMDATA',
                 'PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMW6432','HOMEDRIVE','HOMEPATH',
                 'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY'}
        env={k:v for k,v in os.environ.items() if k.upper() in allowed}
        # Git can be installed outside Program Files (for example F:\\Git).
        # Do not mistake the WindowsApps/WSL bash alias for Git Bash.
        git=shutil.which('git')
        bash=Path(git).resolve().parent.parent/'bin'/'bash.exe' if git else None
        if os.name=='nt' and bash and bash.is_file():env['HERMES_GIT_BASH_PATH']=str(bash)
        env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8',HERMES_HOME=str(home.resolve()),
            HERMES_RUNTIME_DIR=install['tools'],OPENAI_API_KEY=unprotect(provider.get('secret')),
            OPENAI_BASE_URL=provider['base_url'],API_SERVER_KEY=key,API_SERVER_ENABLED='true',
            API_SERVER_HOST='127.0.0.1',API_SERVER_PORT=str(saved['port']))
        env.update(RELAY_TOOL_POLICY=agent.get('hermes_tool_policy','native'),
            RELAY_POLICY_READY=str(policy_ready.resolve()),RELAY_POLICY_STAMP=policy_stamp)
        policy_ready.unlink(missing_ok=True)
        # Only the child receives these credentials; they are never printed or written to .env.
        with (home/'gateway.log').open('ab') as log:
            process=subprocess.Popen([str(python),str(source/'hermes'),'gateway'],cwd=str(source),env=env,
                stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        self.processes[agent['id']]=process
        for _ in range(120):
            if self.ai.stopping.is_set():self._terminate(process);raise RuntimeError('工作台正在停止')
            if ready() and policy_loaded():return url,key
            if process.poll() is not None:raise RuntimeError('Hermes 启动失败，请查看本机 Hermes gateway.log')
            time.sleep(.5)
        self._terminate(process)
        raise RuntimeError('Hermes 启动超过时限，请检查安装环境与 gateway.log')

    @staticmethod
    def _inside(home,path):
        if not path.resolve().is_relative_to(home.resolve()):raise ValueError('Hermes 技能路径越界')
        return path

    def _sync_skills(self,home,agent):
        root=self._inside(home,home/'skills'/'relay')
        root.mkdir(parents=True,exist_ok=True)
        selected={s['id']:s for s in self.ai.data.list('skills') if s['id'] in agent.get('skill_ids',[]) and s['enabled']}
        for old in root.iterdir():
            skill=selected.get(old.name)
            digest=hashlib.sha256(json.dumps(skill,sort_keys=True).encode()).hexdigest() if skill else None
            marker=old/'.relay-digest'
            if not skill or not marker.is_file() or marker.read_text(encoding='utf-8')!=digest:
                # Preserve removed skills outside the active catalog, rather than deleting user content.
                archive=self._inside(home,home/'disabled-relay-skills')
                archive.mkdir(exist_ok=True)
                self._inside(home,old).rename(self._inside(home,archive/(old.name+'-'+secrets.token_hex(4))))
        for sid,skill in selected.items():
            folder=self._inside(home,root/sid);folder.mkdir(exist_ok=True)
            content=skill['instructions']
            if not content.lstrip().startswith('---'):
                content='---\n'+yaml.safe_dump({'name':skill['name'],'description':skill['description'] or skill['name']},allow_unicode=True)+'---\n'+content
            (folder/'SKILL.md').write_text(content,encoding='utf-8')
            for file in skill['files']:
                target=self._inside(folder,folder/file['path'])
                target.parent.mkdir(parents=True,exist_ok=True);target.write_text(file['content'],encoding='utf-8')
            (folder/'.relay-digest').write_text(hashlib.sha256(json.dumps(skill,sort_keys=True).encode()).hexdigest(),encoding='utf-8')
        return hashlib.sha256(json.dumps(selected,sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _terminate(process):
        if process.poll() is not None:return
        # Only descendants of our own live Popen process; never target other Python/WeChat instances.
        import psutil
        try:
            children=psutil.Process(process.pid).children(recursive=True)
            process.terminate()
            for child in reversed(children):
                try:child.terminate()
                except psutil.Error:pass
            process.wait(timeout=3)
        except (psutil.Error,subprocess.TimeoutExpired):
            if process.poll() is None:process.kill()

    def stop(self):
        for process in list(self.processes.values()):self._terminate(process)
