"""Remote MCP tools: discover real schemas, keep credentials on the server."""
import asyncio
from datetime import timedelta
import hashlib
import json
import logging
import re
from urllib.parse import urlencode, quote

import httpx
from .ai_models import Tool
from .ai_store import unprotect
from .ai_tools import ToolUncertain


AMAP_READ = {'maps_geo','maps_regeocode','maps_weather','maps_text_search',
    'maps_around_search','maps_search_detail','maps_distance','maps_direction_walking',
    'maps_direction_driving','maps_direction_bicycling','maps_direction_transit_integrated',
    'maps_schema_personal_map','maps_schema_navi','maps_schema_take_taxi'}


class _Redact(logging.Filter):
    def __init__(self,key):
        super().__init__();self.values=[key,quote(key,safe='')] if key else []

    def filter(self,record):
        message=record.getMessage()
        for key in self.values:message=message.replace(key,'[REDACTED]')
        record.msg=message;record.args=()
        if record.exc_info:
            formatted=logging.Formatter().formatException(record.exc_info)
            for key in self.values:formatted=formatted.replace(key,'[REDACTED]')
            record.exc_text=formatted;record.exc_info=None
        return True


def connection_args(connection):
    if not connection['enabled']:raise ValueError('MCP 连接已停用')
    if not connection['url']:raise ValueError('尚未配置官方 MCP 接入地址，请先提供服务商接口资料')
    key=unprotect(connection.get('secret'))
    if connection['auth']!='none' and not key:raise ValueError('尚未配置 MCP 密钥；高德请使用 Web 服务 Key')
    headers={};url=connection['url']
    if key:
        if connection['auth']=='query':url+='?'+urlencode({connection['auth_name']:key})
        elif connection['auth']=='bearer':headers['Authorization']='Bearer '+key
        elif connection['auth']=='header':headers[connection['auth_name']]=key
    return url,headers,key


async def exchange(connection,name=None,arguments=None):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    url,headers,key=connection_args(connection)
    # The SDK/httpx can log full request URLs. Never log the query credential.
    filt=_Redact(key)
    names={'httpx','httpcore','mcp.client.streamable_http','mcp.shared.session'}
    names.update(n for n in list(logging.Logger.manager.loggerDict) if n.startswith(('httpcore.','mcp.')))
    loggers=[logging.getLogger(n) for n in names]
    for logger in loggers:logger.addFilter(filt)
    try:
        async with asyncio.timeout(connection['timeout']):
            async with httpx.AsyncClient(headers=headers,timeout=connection['timeout'],follow_redirects=False) as client:
                async with streamable_http_client(url,http_client=client) as (read,write,_):
                    async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=connection['timeout'])) as session:
                        await session.initialize()
                        if name:
                            value=(await session.call_tool(name,arguments or {})).model_dump(mode='json',exclude_none=True)
                        else:
                            items=[];cursor=None
                            for _ in range(10):
                                page=await session.list_tools(cursor=cursor)
                                items.extend(t.model_dump(mode='json',exclude_none=True) for t in page.tools)
                                cursor=page.nextCursor
                                if not cursor:break
                            if cursor or len(items)>100:raise ValueError('远程工具数量超过限制')
                            value={'tools':items}
        encoded=json.dumps(value,ensure_ascii=False)
        if len(encoded)>250000:raise ValueError('MCP 响应超过大小限制')
        if key:encoded=encoded.replace(key,'[REDACTED]').replace(quote(key,safe=''),'[REDACTED]')
        return json.loads(encoded)
    except Exception:
        # ExceptionGroup/HTTP errors may contain the full credential-bearing URL.
        raise RuntimeError('MCP 请求未完成，请检查接入地址、Key、服务额度和网络；未自动重试') from None
    finally:
        for logger in loggers:logger.removeFilter(filt)


def request(connection,name=None,arguments=None):
    return asyncio.run(exchange(connection,name,arguments))


def discover(connection):
    result=request(connection)
    rows=[]
    for remote in result.get('tools',[]):
        name=remote.get('name','')
        if not isinstance(name,str) or not name or len(name)>200:continue
        if connection['profile']=='amap' and name=='maps_ip_location':continue
        schema=remote.get('inputSchema',{})
        # Validate the same schema that will be exposed to the model and executed.
        Tool(name='validate_schema',description='校验',kind='mcp',connection_id=connection['id'],
             remote_name=name,parameters=schema)
        rows.append({'name':name,'description':str(remote.get('description') or name)[:1200],
                     'parameters':schema,'approval':'auto' if connection['profile']=='amap' and name in AMAP_READ else 'confirm'})
    return rows


def import_tools(data,connection,rows,names):
    if not names or len(names)>40:raise ValueError('请选择 1 至 40 个工具')
    byname={row['name']:row for row in rows}
    if any(name not in byname for name in names):raise ValueError('只能导入本次发现的工具')
    existing={t.get('remote_name'):t for t in data.list('tools') if t.get('connection_id')==connection['id']}
    result=[]
    for name in dict.fromkeys(names):
        remote=byname[name];old=existing.get(name)
        alias=re.sub(r'[^A-Za-z0-9_]','_',name)[:40]
        suffix=hashlib.sha256((connection['id']+name).encode()).hexdigest()[:10]
        value={'name':old['name'] if old else 'mcp_'+alias+'_'+suffix,
               'description':remote['description'],'kind':'mcp','connection_id':connection['id'],
               'remote_name':name,'parameters':remote['parameters'],
               'approval':old['approval'] if old else remote['approval'],'enabled':old['enabled'] if old else True}
        result.append(data.save('tools',old['id'] if old else None,value))
    return result


def execute(data,tool,args):
    connection=data.get('mcp',tool['connection_id'],private=True)
    # Configuration errors are definite non-execution, even for a writing tool.
    try:connection_args(connection)
    except ValueError as exc:return json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False)
    readonly=connection['profile']=='amap' and tool['remote_name'] in AMAP_READ
    try:result=request(connection,tool['remote_name'],args)
    except RuntimeError as exc:
        if not readonly:raise ToolUncertain('MCP 外部操作结果无法确认，请到服务商核对；不会自动重复执行') from None
        return json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False)
    return json.dumps({'ok':not result.get('isError',False),'result':result},ensure_ascii=False)
