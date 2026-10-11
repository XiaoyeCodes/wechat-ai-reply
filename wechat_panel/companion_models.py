"""Explicit capabilities shared by companion agents and ordinary workflows."""
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Literal


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')


class Persona(Strict):
    enabled: bool=False
    name: str=Field(default='阿慧',max_length=40)
    age: int=Field(default=24,ge=18,le=100)
    occupation: str=Field(default='设计师',max_length=100)
    personality: str=Field(default='温柔，有一点小傲娇，可盐可甜，有自己的想法。',max_length=3000)
    appearance: str=Field(default='可爱的成年女性，保持固定人物特征。',max_length=2000)
    relationship: str=Field(default='恋人',max_length=80)
    speaking_style: str=Field(default='甜而自然、温柔俏皮，像熟悉的人发微信。短句为主，简单接话一句就好，复杂事情再展开；不固定三段，不总以问题结尾。语气词、轻轻撒娇和 emoji 偶尔用，不刻意卖萌。先接住情绪，不说教，不生硬罗列记忆。',max_length=4000)
    natural_rhythm: bool=True
    emoji_probability: float=Field(default=.25,ge=0,le=1)
    boundaries: str=Field(default='尊重现实中的朋友、家人和生活，不制造愧疚、不以离开相威胁、不诱导消费。被认真问到身份时承认是 AI，不冒充真人。',max_length=4000)


class MemoryOptions(Strict):
    enabled: bool=False
    service_id: str=Field(default='',max_length=100)
    recent_messages: int=Field(default=20,ge=2,le=60)
    retrieve_limit: int=Field(default=6,ge=1,le=20)
    retention_days: int=Field(default=180,ge=1,le=3650)
    auto_extract: bool=True
    store_sensitive: bool=False


class CareOptions(Strict):
    enabled: bool=False
    timezone: str='Asia/Shanghai'
    quiet_start: str=Field(default='23:00',pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    quiet_end: str=Field(default='08:00',pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    daily_limit: int=Field(default=3,ge=1,le=12)
    min_gap_minutes: int=Field(default=120,ge=5,le=1440)

    @field_validator('timezone')
    @classmethod
    def zone(cls,value):
        try:ZoneInfo(value)
        except Exception:raise ValueError('无效时区') from None
        return value


class MediaOptions(Strict):
    tts_service_id: str=Field(default='',max_length=100)
    voice_id: str=Field(default='',max_length=160)
    voice_speed: float=Field(default=1,ge=.5,le=2)
    auto_voice: bool=False
    vision_provider_id: str=Field(default='',max_length=100)
    understand_images: bool=False
    image_service_id: str=Field(default='',max_length=100)
    reference_media_id: str=Field(default='',max_length=100)
    lora: str=Field(default='',max_length=300)
    lora_scale: float=Field(default=.8,ge=0,le=2)
    image_prompt: str=Field(default='',max_length=3000)


class SafetyOptions(Strict):
    emotion_enabled: bool=True
    crisis_detection: bool=True
    require_adult: bool=True
    observability_service_id: str=Field(default='',max_length=100)
    export_content: bool=False


class Companion(Strict):
    persona: Persona=Field(default_factory=Persona)
    memory: MemoryOptions=Field(default_factory=MemoryOptions)
    care: CareOptions=Field(default_factory=CareOptions)
    media: MediaOptions=Field(default_factory=MediaOptions)
    safety: SafetyOptions=Field(default_factory=SafetyOptions)
    search_service_id: str=Field(default='',max_length=100)
    weather_enabled: bool=False


class PersonProfile(Strict):
    display_name: str=Field(default='',max_length=80)
    age_status: Literal['unknown','adult','minor']='unknown'
    city: str=Field(default='',max_length=120)
    birthday: str=Field(default='',max_length=20)
    relationship_start: str=Field(default='',max_length=10)
    closeness: Literal['初识','熟悉','亲密']='初识'
    preferred_name: str=Field(default='',max_length=80)
    care_consent: bool=False
    memory_consent: bool=True

    @field_validator('relationship_start')
    @classmethod
    def day(cls,value):
        if value:datetime.strptime(value,'%Y-%m-%d')
        return value


class MemoryEntry(Strict):
    category: Literal['fact','preference','event','worry','summary']='fact'
    key: str=Field(default='',max_length=100)
    content: str=Field(min_length=1,max_length=3000)
    expires_at: str=Field(default='',max_length=50)
    pinned: bool=False


class Schedule(Strict):
    agent_id: str=Field(min_length=1,max_length=100)
    contact_id: str=Field(min_length=1,max_length=100)
    title: str=Field(min_length=1,max_length=100)
    instruction: str=Field(min_length=1,max_length=2000)
    kind: Literal['once','cron']='once'
    at: str=Field(default='',max_length=50)
    cron: str=Field(default='',max_length=80)
    timezone: str='Asia/Shanghai'
    enabled: bool=False
    delivery: Literal['draft','auto']='draft'

    @model_validator(mode='after')
    def valid(self):
        CareOptions.zone(self.timezone)
        if self.kind=='once':
            try:
                date=datetime.fromisoformat(self.at)
                if date.tzinfo is None:self.at=date.replace(tzinfo=ZoneInfo(self.timezone)).isoformat()
            except ValueError:raise ValueError('请填写有效执行时间') from None
        else:
            from croniter import croniter
            if len(self.cron.split())!=5 or not croniter.is_valid(self.cron):
                raise ValueError('Cron 必须包含有效的分、时、日、月、星期五段')
        return self
