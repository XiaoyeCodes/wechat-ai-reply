"""Validated, provider-independent AI configuration."""
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .companion_models import Companion
from .vibe_catalog import DEFAULT_TOOLS, DEFAULT_SKILLS


class Config(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=80)


def endpoint(value):
    url = urlsplit(value)
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError('请填写完整 HTTP/HTTPS 地址，不包含账号、密码或片段')
    return value.rstrip('/')


class Provider(Config):
    base_url: str = Field(min_length=1, max_length=1000)
    model: str = Field(min_length=1, max_length=160)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_secret: bool = False
    temperature: float | None = Field(default=None, ge=0, le=2)
    thinking: Literal['default','enabled','disabled'] = 'disabled'
    max_tokens: int = Field(default=1000, ge=64, le=8000)
    timeout: int = Field(default=45, ge=5, le=120)
    _url = field_validator('base_url')(endpoint)


class Document(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=300000)


class Knowledge(Config):
    description: str = Field(default='', max_length=300)
    documents: list[Document] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def size(self):
        if sum(len(d.content) for d in self.documents) > 1000000:
            raise ValueError('单个知识库最多保存 100 万字')
        return self


class SkillFile(BaseModel):
    model_config=ConfigDict(extra='forbid')
    path: str=Field(min_length=1,max_length=240)
    content: str=Field(max_length=150000)

    @field_validator('path')
    @classmethod
    def relative_path(cls,value):
        from pathlib import PurePosixPath
        value=value.replace('\\','/')
        if value.startswith('/') or ':' in value or '..' in PurePosixPath(value).parts:
            raise ValueError('技能附件只能使用包内相对路径')
        if value.lower().endswith(('.env','.pem','.key')):raise ValueError('不要导入密钥文件')
        return value


class Skill(Config):
    description: str=Field(default='',max_length=1200)
    instructions: str=Field(min_length=1,max_length=50000)
    files: list[SkillFile]=Field(default_factory=list,max_length=40)
    enabled: bool=True

    @model_validator(mode='after')
    def limit(self):
        if sum(len(f.content) for f in self.files)>600000:raise ValueError('技能附件总量最多 60 万字')
        if len({f.path for f in self.files})!=len(self.files):raise ValueError('技能附件路径重复')
        return self


class Tool(Config):
    description: str = Field(min_length=1, max_length=1200)
    kind: Literal['builtin', 'http', 'mcp'] = 'http'
    connection_id: str = Field(default='', max_length=100)
    remote_name: str = Field(default='', max_length=200)
    capability: Literal['general','amap','mindmap'] = 'general'
    builtin: Literal['clock', 'calculator', 'knowledge_search','web_search','read_web','news_collect','financial_daily','weather',
                     'music_search','places_search','movie_search','memory_search','remember','forget',
                     'set_reminder','generate_image','speak','use_skill','read_skill_file',
                     'platform_search','platform_read','platform_browse','platform_transcribe'] | None = None
    approval: Literal['auto', 'confirm'] = 'confirm'
    url: str = Field(default='', max_length=1000)
    method: Literal['GET', 'POST'] = 'POST'
    api_key: str | None = Field(default=None, max_length=4096)
    clear_secret: bool = False
    auth_header: str = Field(default='Authorization', pattern=r'^[A-Za-z0-9-]{1,80}$')
    auth_scheme: str = Field(default='Bearer', pattern=r'^[A-Za-z0-9 -]{0,40}$')
    parameters: dict = Field(default_factory=lambda:{'type':'object','properties':{},'additionalProperties':False})
    enabled: bool = True

    @field_validator('name')
    @classmethod
    def function_name(cls, value):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', value):
            raise ValueError('工具名称请使用英文字母、数字或下划线，且不能以数字开头')
        return value

    @model_validator(mode='after')
    def valid(self):
        from jsonschema import Draft202012Validator
        if self.kind == 'http' and (self.enabled or self.url):
            self.url = endpoint(self.url)
        elif self.kind == 'mcp':
            if not self.connection_id or not self.remote_name:
                raise ValueError('MCP 工具需要连接和远程工具名称，请先发现并导入工具')
        elif self.kind=='builtin' and not self.builtin:
            raise ValueError('请选择内置工具')
        if self.parameters.get('type') != 'object':
            raise ValueError('工具参数 JSON Schema 的 type 必须为 object')
        def references(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == '$ref' and (not isinstance(item,str) or not item.startswith('#')):
                        raise ValueError('工具参数不支持外部 Schema 引用')
                    references(item)
            elif isinstance(value,list):
                for item in value: references(item)
        references(self.parameters)
        try:
            Draft202012Validator.check_schema(self.parameters)
        except Exception:
            raise ValueError('工具参数 JSON Schema 无效') from None
        return self


class Step(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['knowledge', 'model', 'tool', 'reply']
    instruction: str = Field(default='', max_length=8000)
    tool_id: str = Field(default='', max_length=100)
    arguments: dict = Field(default_factory=dict)


class Agent(Config):
    description: str = Field(default='', max_length=300)
    kind: Literal['workflow', 'external', 'hermes', 'vibe'] = 'workflow'
    enabled: bool = True
    provider_id: str = Field(default='', max_length=100)
    style_id: str = Field(default='', max_length=100)
    temperature: float | None = Field(default=None, ge=0, le=2)
    knowledge_ids: list[str] = Field(default_factory=list, max_length=30)
    knowledge_mode: Literal['on_demand','always','off']='on_demand'
    web_policy: Literal['auto','model','off']='auto'
    source_policy: Literal['auto','requested','always']='auto'
    skill_ids: list[str]=Field(default_factory=list,max_length=20)
    tool_ids: list[str] = Field(default_factory=list, max_length=64)
    prompt: str = Field(default='', max_length=16000)
    max_tool_rounds: int = Field(default=4, ge=1, le=10)
    steps: list[Step] = Field(default_factory=lambda:[Step(type='knowledge'),Step(type='model'),Step(type='reply')], min_length=1, max_length=12)
    url: str = Field(default='', max_length=1000)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_secret: bool = False
    timeout: int = Field(default=60, ge=5, le=120)
    hermes_managed: bool = False
    hermes_workspace: str = Field(default='', max_length=1000)
    hermes_contact_id: str = Field(default='', max_length=100)
    hermes_task_approval: Literal['confirm', 'auto'] = 'confirm'
    hermes_tool_policy: Literal['native', 'sensitive', 'all', 'ai_review', 'full'] = 'native'
    hermes_approval_scope: Literal['task', 'operation'] = 'task'
    hermes_approval_channel: Literal['panel', 'wechat'] = 'panel'
    hermes_approval_timeout: int = Field(default=600, ge=60, le=1800)
    hermes_chat_routing: bool = True
    hermes_task_timeout: int = Field(default=900, ge=30, le=3600)
    vibe_tools: list[str] = Field(default_factory=lambda: DEFAULT_TOOLS.copy(), max_length=30)
    vibe_skills: list[str] = Field(default_factory=lambda: DEFAULT_SKILLS.copy(), max_length=30)
    vibe_max_iterations: int = Field(default=12, ge=2, le=50)
    vibe_engine: Literal['github', 'sparkflow'] = 'github'
    vibe_task_timeout: int | None = Field(default=600, ge=30, le=1800)
    companion: Companion = Field(default_factory=Companion)

    @model_validator(mode='after')
    def valid(self):
        from .vibe_catalog import TOOLS, SKILLS
        self.style_id = ''  # Accept legacy clients, but never retain a reply-style binding.
        if self.kind == 'vibe' and self.vibe_engine == 'sparkflow':
            # SessionService owns these settings; legacy forms must not override them.
            self.vibe_max_iterations = 50
            self.vibe_task_timeout = None
            self.vibe_tools = []
            self.vibe_skills = []
            self.prompt = ''
            self.style_id = ''
        elif self.kind == 'vibe':
            if self.vibe_max_iterations > 30 or self.vibe_task_timeout is None:
                raise ValueError('GitHub 引擎最多 30 轮，且需要任务时限')
        if set(self.vibe_tools) - TOOLS.keys() or set(self.vibe_skills) - SKILLS.keys():
            raise ValueError('Vibe-Trading 包含未适配的工具或技能')
        if self.kind == 'vibe':
            if not self.provider_id:raise ValueError('Vibe-Trading 需要选择模型连接')
            identity_tools={'get_stock_profile','get_stock_news','get_fundamentals','get_financial_statements',
                            'get_market_data','get_options_chain','technical_indicators','etf_holdings'}
            if identity_tools.intersection(self.vibe_tools) and 'search_symbol' not in self.vibe_tools:
                raise ValueError('证券查询工具需要同时勾选“证券代码搜索”，用于核对标的身份')
            if self.tool_ids or self.skill_ids or self.knowledge_ids:
                raise ValueError('Vibe-Trading 请使用专属技能和工具选项')
        elif self.kind == 'hermes':
            if self.hermes_tool_policy!='native' and not self.hermes_managed:
                raise ValueError('细粒度操作权限需要使用本机托管 Hermes 引擎')
            if self.hermes_managed:
                if not self.provider_id:raise ValueError('本机 Hermes 需要选择模型连接')
            else:
                self.url = endpoint(self.url)
                if urlsplit(self.url).query:raise ValueError('Hermes 地址不能包含查询参数')
            if self.hermes_tool_policy=='full':self.hermes_task_approval='auto'
        elif self.kind == 'external':
            self.url = endpoint(self.url)
        else:
            if not self.provider_id:
                raise ValueError('请选择模型连接')
            if self.steps[-1].type != 'reply' or any(s.type=='reply' for s in self.steps[:-1]):
                raise ValueError('流程最后一步必须为回复，且只能有一个回复步骤')
            if not any(s.type in ('model','tool') for s in self.steps):
                raise ValueError('流程至少包含一个模型或工具步骤')
            if any(s.type=='tool' and s.tool_id not in self.tool_ids for s in self.steps):
                raise ValueError('固定工具步骤必须选择 Agent 已启用的工具')
        return self


class AISettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False
    debounce_seconds: float = Field(default=2, ge=0.3, le=10)
    context_messages: int = Field(default=20, ge=2, le=60)


class Service(Config):
    category: Literal['memory','search','tts','image','observability']
    driver: Literal['qdrant','mem0','memory_http','agent_reach','exa','tavily','deepseek','elevenlabs','minimax',
                    'openai_speech','tts_http','openai_image','image_http','codex_image','langfuse','langsmith','trace_http']
    url: str=Field(default='',max_length=1000)
    api_key: str | None=Field(default=None,max_length=4096)
    clear_secret: bool=False
    model: str=Field(default='',max_length=160)
    voice_id: str=Field(default='',max_length=160)
    public_key: str=Field(default='',max_length=200)
    collection: str=Field(default='relay_memories',pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    embedding_provider_id: str=Field(default='',max_length=100)
    enabled: bool=True
    timeout: int=Field(default=45,ge=5,le=900)

    @model_validator(mode='after')
    def validate_service(self):
        drivers={'memory':{'qdrant','mem0','memory_http'},'search':{'agent_reach','exa','tavily','deepseek'},
                 'tts':{'elevenlabs','minimax','openai_speech','tts_http'},
                 'image':{'openai_image','image_http','codex_image'},'observability':{'langfuse','langsmith','trace_http'}}
        if self.driver not in drivers[self.category]:raise ValueError('服务类别与协议不匹配')
        if self.driver=='codex_image':
            if self.api_key:raise ValueError('Codex 生图使用本机 ChatGPT 登录，无需填写 API Key')
            self.url='';self.model='';self.clear_secret=True
            if 'timeout' not in self.model_fields_set:self.timeout=300
        elif self.timeout>120:raise ValueError('此服务的超时不能超过 120 秒')
        if self.driver=='deepseek':
            if self.url.rstrip('/') not in ('','https://api.deepseek.com','https://api.deepseek.com/v1','https://api.deepseek.com/v1/tools'):
                raise ValueError('DeepSeek Search / Fetch 请使用官方地址 https://api.deepseek.com')
            self.url='https://api.deepseek.com'
        if self.driver not in ('agent_reach','codex_image'):self.url=endpoint(self.url)
        if self.driver=='qdrant' and not self.embedding_provider_id:raise ValueError('Qdrant 需要配置 Embedding 模型连接')
        return self


class MCPConnection(Config):
    profile: Literal['amap','treemind','custom'] = 'custom'
    url: str = Field(default='',max_length=1000)
    api_key: str | None = Field(default=None,max_length=4096)
    clear_secret: bool = False
    enabled: bool = True
    timeout: int = Field(default=30,ge=5,le=90)
    auth: Literal['none','bearer','header','query'] = 'bearer'
    auth_name: str = Field(default='Authorization',pattern=r'^[A-Za-z0-9_-]{1,80}$')

    @model_validator(mode='after')
    def valid(self):
        if self.profile=='amap':
            self.url='https://mcp.amap.com/mcp'
            self.auth='query';self.auth_name='key'
        if self.url:
            self.url=endpoint(self.url)
            # Credentials belong in the encrypted field, never in public URLs.
            if urlsplit(self.url).query:
                raise ValueError('MCP 地址不要带查询参数，密钥请填写到 API Key 并选择认证方式')
        return self


CONFIGS = {'providers':Provider,'knowledge':Knowledge,'tools':Tool,'agents':Agent,'services':Service,'skills':Skill,'mcp':MCPConnection}
