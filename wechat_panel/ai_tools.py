"""Bounded model/tool HTTP calls and a small explicit tool registry."""
import ast
from datetime import datetime
import json
import operator
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator

from .ai_store import unprotect


class ToolUncertain(RuntimeError):
    pass


def redact(value,key):
    if not key:return value
    if isinstance(value,str):return value.replace(key,'[已隐藏密钥]')
    if isinstance(value,list):return [redact(item,key) for item in value]
    if isinstance(value,dict):return {k:redact(item,key) for k,item in value.items()}
    return value


def request_json(url, body=None, *, method='POST', headers=None, timeout=45, max_bytes=2_000_000):
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            with client.stream(method,url,headers=headers,
                    **({'params':body} if method=='GET' else {'json':body})) as response:
                if response.status_code >= 300:
                    raise RuntimeError(f'接口返回 HTTP {response.status_code}，请检查地址、密钥、模型和配额')
                content=bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content)>max_bytes:raise RuntimeError('接口响应超过大小限制')
                return json.loads(content) if content else {}
    except (httpx.HTTPError,UnicodeError,json.JSONDecodeError):
        raise RuntimeError('接口请求失败或响应不是有效 JSON，请检查网络与接口配置') from None


def complete(provider, messages, tools):
    body={'model':provider['model'],'messages':messages,'stream':False,'max_tokens':provider['max_tokens']}
    if provider.get('temperature') is not None:body['temperature']=provider['temperature']
    if urlsplit(provider['base_url']).hostname=='api.deepseek.com':
        mode='disabled' if provider.get('_structured') else provider.get('thinking','disabled')
        if mode!='default':body['thinking']={'type':mode}
    if provider.get('_structured'):
        body['response_format']={'type':'json_object'}
    if tools:body['tools']=tools
    key=unprotect(provider.get('secret'))
    headers={'Authorization':'Bearer '+key} if key else {}
    data=request_json(provider['base_url'].rstrip('/')+'/chat/completions',body,headers=headers,timeout=provider['timeout'])
    try:
        choice=data['choices'][0]
        if choice.get('finish_reason')=='length':raise RuntimeError('模型输出被长度限制截断，请调整模型设置')
        answer=choice['message']
        if not isinstance(answer,dict):raise ValueError()
        result={'role':'assistant','content':answer.get('content') or ''}
        if answer.get('tool_calls'):result['tool_calls']=answer['tool_calls']
        if not result['content'] and not result.get('tool_calls'):raise ValueError()
        if len(json.dumps(result))>150000:raise ValueError()
        # Some compatible reasoning models require this on subsequent tool turns.
        if answer.get('reasoning_content'):result['reasoning_content']=answer['reasoning_content']
        return redact(result,key),redact(data.get('usage',{}),key)
    except (KeyError,IndexError,TypeError,ValueError):
        raise RuntimeError('模型未返回可用的回答，请核对接口是否兼容 Chat Completions') from None


def calculate(expression):
    if len(expression)>300:raise ValueError('表达式过长')
    tree=ast.parse(expression,mode='eval')
    if sum(1 for _ in ast.walk(tree))>64:raise ValueError('表达式过于复杂')
    binary={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,
            ast.FloorDiv:operator.floordiv,ast.Mod:operator.mod}
    def visit(node):
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):value=node.value
        elif isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            value=visit(node.operand)*(1 if isinstance(node.op,ast.UAdd) else -1)
        elif isinstance(node,ast.BinOp) and type(node.op) in binary:value=binary[type(node.op)](visit(node.left),visit(node.right))
        else:raise ValueError('仅支持数字和四则运算，不执行代码')
        if not -1e18<=value<=1e18:raise ValueError('计算结果超出范围')
        return value
    return str(visit(tree.body))


def execute_tool(tool, arguments, knowledge_ids, ai_store):
    if not tool['enabled']:raise RuntimeError('工具已停用')
    errors=list(Draft202012Validator(tool['parameters']).iter_errors(arguments))
    if errors:raise ValueError('工具参数不符合配置的 JSON Schema')
    if tool['kind']=='builtin':
        if tool['builtin']=='clock':return datetime.now().astimezone().isoformat()
        if tool['builtin']=='calculator':return calculate(arguments['expression'])
        if tool['builtin']=='knowledge_search':return json.dumps(ai_store.search(knowledge_ids,arguments['query']),ensure_ascii=False)
        raise RuntimeError('未知内置工具')
    key=unprotect(tool.get('secret'))
    headers={tool['auth_header']:(tool['auth_scheme']+' '+key).strip()} if key else {}
    try:
        result=json.dumps(request_json(tool['url'],arguments,method=tool['method'],headers=headers,timeout=30),ensure_ascii=False)
    except Exception:
        if tool['method']=='GET':return json.dumps({'ok':False,'error':'查询接口不可用，请检查地址与服务凭据，未取得查询结果。'},ensure_ascii=False)
        # A timeout cannot prove whether an external side effect happened.
        raise ToolUncertain('HTTP 工具结果无法确认，请在外部系统核对；不会自动重复执行') from None
    return result.replace(key,'[已隐藏密钥]')[:16000] if key else result[:16000]


def function_spec(tool):
    return {'type':'function','function':{'name':tool['name'],'description':tool['description'],'parameters':tool['parameters']}}
