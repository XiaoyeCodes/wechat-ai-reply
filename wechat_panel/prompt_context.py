"""Resolve a small prompt-variable allowlist and persist daily role context."""
from datetime import datetime
import hashlib
import json
import re
import threading
from zoneinfo import ZoneInfo
from .prompt_policy import primary_prompt


VARIABLE = re.compile(r'{{\s*(user_name|current_time|weather)\s*}}')
UNKNOWN_WEATHER = '未知（未取得可靠天气信息，不编造；需要时可自然询问用户）'


def local_now(timezone):
    return datetime.now(ZoneInfo(timezone))


def uses_variables(agent):
    return bool(VARIABLE.search(agent.get('prompt', '')))


def mood_choices(agent):
    if not primary_prompt(agent):return ()
    match=re.search(r'【今日(?:心情|状态)[^\n]*】\s*\n([^\n]+)',agent.get('prompt',''))
    if not match:return ()
    choices=tuple(s.strip() for s in match[1].split('/') if s.strip())
    return choices if 2<=len(choices)<=16 and all(len(s)<=50 for s in choices) else ()


def render(template, variables):
    # Single pass only: user values cannot introduce further template expansion.
    return VARIABLE.sub(lambda match: str(variables.get(match[1], {
        'user_name': '对方', 'current_time': '未知（不编造时间）',
        'weather': UNKNOWN_WEATHER}[match[1]])), template)


class PromptContext:
    def __init__(self, runtime):
        self.runtime = runtime
        self.lock = threading.RLock()

    def prepare(self, run, state, config, profile):
        now = local_now(config['care']['timezone'])
        day = now.date().isoformat()
        scope = state['scope']
        city = profile.get('city', '').strip()
        moods = mood_choices(state['snapshot']['agent'])
        # Date and timezone isolate the daily cache; an explicit city/settings
        # change also refreshes it. No weather request is made during startup.
        key = hashlib.sha256(json.dumps([day, config['care']['timezone'], city,
                                        config['weather_enabled'],moods]).encode()).hexdigest()
        with self.lock:
            daily = self.runtime.memory.daily_context(scope, key)
            if daily is None:
                weather = UNKNOWN_WEATHER
                if config['weather_enabled'] and city:
                    try:
                        weather = self.runtime.services.weather(city) or UNKNOWN_WEATHER
                    except Exception:
                        weather = '未知（天气查询暂不可用，不编造；今天不自动重复查询）'
                seed = hashlib.sha256((scope + ':' + day).encode()).digest()
                daily = {'date': day, 'updated_at': now.isoformat(), 'weather': weather}
                if moods:daily['persona_mood']=moods[int.from_bytes(seed[:4], 'big') % len(moods)]
                self.runtime.memory.save_daily_context(scope, key, daily)
                self.runtime.ai.data.trace(run['id'], '每日陪伴上下文', {
                    'date': day, 'weather_available': isinstance(weather, dict),
                    'detail': '当天首次回复更新；同一会话当天复用，当前时间实时注入'})
        state['prompt_variables'] = self.variables(run, config, profile, daily, now)
        return daily

    def variables(self, run, config, profile, daily=None, now=None):
        now = now or local_now(config['care']['timezone'])
        contact = self.runtime.ai.store.contact(run['contact_id']) if run.get('contact_id') else {}
        user_name = profile.get('preferred_name') or profile.get('display_name') or contact.get('nickname') or contact.get('lookup') or '对方'
        # Quote the name as data; neither missing names nor weather are invented.
        user_name = json.dumps(str(user_name).strip()[:120] or '对方', ensure_ascii=False)
        weather = daily['weather'] if daily else UNKNOWN_WEATHER
        weather_text = json.dumps(weather, ensure_ascii=False) if isinstance(weather, (dict, list)) else weather
        variables = {'user_name': user_name,
                     'current_time': now.strftime('%Y-%m-%d %H:%M:%S') + '（' + config['care']['timezone'] + '）',
                     'weather': str(weather_text) + ('；信息记录于 ' + daily['updated_at'] if daily else '')}
        return variables
