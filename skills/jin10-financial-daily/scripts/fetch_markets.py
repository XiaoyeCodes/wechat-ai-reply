"""Source-backed market snapshot. No secrets, scraping model, or generated prices."""
import argparse
import os
import concurrent.futures
import json
import math
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(os.environ.get('JIN10_DAILY_WORKSPACE', Path.cwd())).resolve()
CST = timezone(timedelta(hours=8))
SPECS = [
    dict(key='nasdaq',name='纳斯达克综合',unit='点',em='100.NDX',sina='gb_ixic',identity='纳斯达克',bounds=(100,100000),url='https://quote.eastmoney.com/gb/zsNDX.html'),
    dict(key='sp500',name='标普500',unit='点',em='100.SPX',sina='gb_inx',identity='标普',bounds=(100,50000),url='https://quote.eastmoney.com/gb/zsSPX.html'),
    dict(key='mags',name='MAGS',unit='美元',em='107.MAGS',sina='gb_mags',identity='MAGS',bounds=(.01,10000),url='https://quote.eastmoney.com/us/MAGS.html'),
    dict(key='shanghai',name='上证指数',unit='点',em='1.000001',sina='sh000001',identity='上证',bounds=(100,50000),url='https://quote.eastmoney.com/zs000001.html'),
    dict(key='hsi',name='恒生指数',unit='点',em='100.HSI',sina='rt_hkHSI',identity='恒生',bounds=(100,100000),url='https://quote.eastmoney.com/q/100.hsi.html'),
    dict(key='btc',name='比特币现货',unit='美元',bounds=(100,10000000),url='https://www.kraken.com/prices/bitcoin'),
    dict(key='gold',name='现货黄金',unit='美元/盎司',em='122.XAU',sina='hf_XAU',identity='黄金',bounds=(10,100000),url='https://quote.eastmoney.com/forex/XAU.html'),
    dict(key='brent',name='布伦特原油',unit='美元/桶',em='112.B00Y',sina='hf_OIL',identity='布伦特',bounds=(.01,1000),url='https://quote.eastmoney.com/globalfuture/B00Y.html'),
    dict(key='us10y',name='10年期美债收益率',unit='%',em='171.US10Y',identity='美国10年期国债收益率',bounds=(.001,30),url='https://quote.eastmoney.com/stock/171.US10Y.html'),
]
LOCAL = threading.local()


def now():
    return datetime.now(CST)


def session():
    if not hasattr(LOCAL,'session'):
        s=requests.Session()
        s.headers.update({'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn/','Cache-Control':'no-cache'})
        retry=Retry(total=2,connect=2,read=1,backoff_factor=.3,status_forcelist=[429,500,502,503,504],allowed_methods=['GET'])
        s.mount('https://',HTTPAdapter(max_retries=retry))
        LOCAL.session=s
    return LOCAL.session


def get(url,**kwargs):
    response=session().get(url,timeout=(4,9),**kwargs)
    response.raise_for_status()
    return response


def stamp(epoch):
    return datetime.fromtimestamp(float(epoch),CST).isoformat()


def make_quote(spec,price,previous,asof,source,url,reported_pct=None,time_kind='quote'):
    price,previous=float(price),float(previous)
    if not all(math.isfinite(v) and v>0 for v in (price,previous)):
        raise ValueError('Nonpositive or nonfinite price/reference')
    if not all(spec['bounds'][0] <= v <= spec['bounds'][1] for v in (price,previous)):
        raise ValueError('Price scale outside instrument bounds')
    pct=(price/previous-1)*100
    if abs(pct)>60:
        raise ValueError('Extreme daily move requires manual verification')
    if reported_pct is not None and abs(pct-float(reported_pct))>.04:
        raise ValueError('Reported percent disagrees with price and reference')
    quote_time=datetime.fromisoformat(asof)
    age=(now()-quote_time).total_seconds()
    if age < -300:
        raise ValueError('Quote timestamp is in the future')
    max_age=600 if spec['key']=='btc' else 96*3600 if spec['key'] in ('gold','brent','us10y') else 10*86400
    if age>max_age:
        raise ValueError('Quote timestamp is stale')
    basis='前收盘'
    if spec['key']=='brent':
        basis='前结算；近月连续期货'
    if spec['key']=='btc':
        basis='UTC 00:00开盘；BTC/USD现货'
    row=dict(key=spec['key'],name=spec['name'],unit=spec['unit'],price=price,reference=previous,change_pct=pct,change_bp=(price-previous)*100 if spec['key']=='us10y' else None,asof=asof,time_kind=time_kind,source=source,source_url=url,basis=basis,status='ok',retrieved_at=now().isoformat())
    if spec['key']!='btc' and age>4*86400:
        row['status']='older-session'
    return row


def parse_em(spec,payload):
    d=payload.get('data')
    if payload.get('rc')!=0 or not isinstance(d,dict):
        raise ValueError('Eastmoney returned no instrument')
    if str(d.get('f57','')).upper()!=spec['em'].split('.',1)[1].upper():
        raise ValueError('Eastmoney security code mismatch')
    if spec['key']=='nasdaq' and ('100' in d.get('f58','') or '纳斯达克' not in d.get('f58','')):
        raise ValueError('Nasdaq Composite cannot be replaced by Nasdaq 100')
    if spec['key']=='mags':
        if 'Magnificent Seven' not in d.get('f58','') and 'MAGS' not in d.get('f58','').upper():
            raise ValueError('MAGS ETF identity mismatch')
    elif spec['identity'] not in d.get('f58',''):
        raise ValueError('Eastmoney instrument name mismatch')
    decimals=int(d['f59'])
    if not 0<=decimals<=6:
        raise ValueError('Invalid Eastmoney decimal scale')
    return make_quote(spec,float(d['f43'])/10**decimals,float(d['f60'])/10**decimals,stamp(d['f86']),'东方财富',spec['url'],float(d['f170'])/100)


def fetch_em(spec,rawdir,mirror=False):
    if mirror:
        url='https://push2.eastmoney.com/api/qt/ulist.np/get'
        params={'secids':spec['em'],'fields':'f2,f3,f4,f12,f13,f14,f18,f124','_':str(time.time_ns())}
        r=get(url,params=params)
        j=r.json()
        (rawdir/(spec['key']+'-eastmoney-mirror.json')).write_text(json.dumps(j,ensure_ascii=False),encoding='utf-8')
        rows=j.get('data',{}).get('diff',[])
        d=next(x for x in rows if x.get('f12')==spec['em'].split('.',1)[1])
        if d.get('f13')!=171 or spec['identity'] not in d.get('f14',''):
            raise ValueError('Bond mirror identity mismatch')
        # Bond prices use four decimals. The list field f152 is NOT its price scale.
        scale=4
        return make_quote(spec,float(d['f2'])/10**scale,float(d['f18'])/10**scale,stamp(d['f124']),'东方财富',spec['url'],float(d['f3'])/100)
    url='https://push2.eastmoney.com/api/qt/stock/get'
    params={'secid':spec['em'],'fields':'f43,f57,f58,f59,f60,f86,f169,f170','_':str(time.time_ns())}
    r=get(url,params=params)
    j=r.json()
    (rawdir/(spec['key']+'-eastmoney.json')).write_text(json.dumps(j,ensure_ascii=False),encoding='utf-8')
    return parse_em(spec,j)


def fetch_sina(rawdir):
    symbols=','.join(s['sina'] for s in SPECS if s.get('sina'))
    r=get('https://hq.sinajs.cn/list='+symbols)
    text=r.content.decode('gb18030')
    (rawdir/'sina-quotes.txt').write_text(text,encoding='utf-8')
    return {key:value.split(',') for key,value in re.findall(r'var hq_str_([^=]+)="([^"]*)";',text)}


def parse_sina(spec,data):
    a=data.get(spec['sina'],[])
    symbol=spec['sina']
    if len(a)<10:
        raise ValueError('Sina quote empty')
    if symbol.startswith('gb_'):
        if spec['identity'] not in a[0]:
            raise ValueError('Sina US instrument mismatch')
        price,prev,at,pct=a[1],a[26],a[3],a[2]
        url='https://stock.finance.sina.com.cn/usstock/quotes/'+symbol[3:]+'.html'
    elif symbol=='sh000001':
        if '上证' not in a[0]:
            raise ValueError('Sina Shanghai instrument mismatch')
        price,prev,at,pct=a[3],a[2],a[30]+' '+a[31],None
        url='https://finance.sina.com.cn/realstock/company/sh000001/nc.shtml'
    elif symbol=='rt_hkHSI':
        if a[0]!='HSI':
            raise ValueError('Sina HSI instrument mismatch')
        price,prev,at,pct=a[6],a[3],a[17].replace('/','-')+' '+a[18],a[8]
        url='https://stock.finance.sina.com.cn/hkstock/quotes/HSI.html'
    else:
        if spec['identity'] not in a[13] or ('期货' in a[13] and spec['key']=='gold'):
            raise ValueError('Sina commodity instrument mismatch')
        price,prev,at,pct=a[0],a[7],a[12]+' '+a[6],None
        url='https://finance.sina.com.cn/money/future/hf.html?symbol='+symbol[3:]
    asof=datetime.fromisoformat(at).replace(tzinfo=CST).isoformat()
    return make_quote(spec,price,prev,asof,'新浪财经',url,pct)


def fetch_btc(rawdir,coinbase=False):
    spec=next(s for s in SPECS if s['key']=='btc')
    if coinbase:
        r=get('https://api.exchange.coinbase.com/products/BTC-USD/stats')
        stats=r.json()
        (rawdir/'btc-coinbase.json').write_text(json.dumps(stats),encoding='utf-8')
        row=make_quote(spec,stats['last'],stats['open'],now().isoformat(),'Coinbase','https://www.coinbase.com/price/bitcoin',time_kind='retrieved')
        row['basis']='24小时；BTC/USD现货'
        row['change_period']='24h'
        return row
    r=get('https://api.kraken.com/0/public/Ticker',params={'pair':'XBTUSD'})
    j=r.json()
    (rawdir/'btc-kraken.json').write_text(json.dumps(j),encoding='utf-8')
    if j.get('error'):
        raise ValueError('Kraken returned API error')
    d=j['result']['XXBTZUSD']
    return make_quote(spec,d['c'][0],d['o'],now().isoformat(),'Kraken',spec['url'],time_kind='retrieved')


def choose_quote(spec,primary,secondary):
    if primary and secondary:
        a=datetime.fromisoformat(primary['asof'])
        b=datetime.fromisoformat(secondary['asof'])
        deviation=abs(primary['price']/secondary['price']-1)*100
        # Compare the same instrument only at sufficiently aligned timestamps.
        if abs((a-b).total_seconds())<=300 and deviation>.5:
            raise ValueError(f'Cross-source price conflict {deviation:.3f}%')
        primary['cross_check']={'source':secondary['source'],'asof':secondary['asof'],'price':secondary['price'],'deviation_pct':deviation}
    if primary:
        return primary
    if secondary:
        secondary['fallback_used']=True
        return secondary
    return None


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--date',required=True)
    args=ap.parse_args()
    datetime.strptime(args.date,'%Y-%m-%d')
    outdir=ROOT/'source'/args.date/'market-raw'
    outdir.mkdir(parents=True,exist_ok=True)
    fetched={}
    errors=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures={pool.submit(fetch_em,s,outdir):s['key'] for s in SPECS if s.get('em')}
        futures[pool.submit(fetch_sina,outdir)]='sina'
        futures[pool.submit(fetch_btc,outdir,coinbase=True)]='btc'
        for f in concurrent.futures.as_completed(futures):
            key=futures[f]
            try:
                fetched[key]=f.result()
            except Exception as e:
                errors.append({'key':key,'stage':'primary','error':str(e)})
    rows=[]
    cache_path=ROOT/'market-cache.json'
    cache=json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
    for spec in SPECS:
        key=spec['key']
        primary=fetched.get(key)
        secondary=None
        try:
            if spec.get('sina'):
                secondary=parse_sina(spec,fetched.get('sina',{}))
            elif key=='btc':
                # Independent spot-price check. Kraken's change period is UTC-day,
                # and is explicitly labeled if this fallback becomes necessary.
                secondary=fetch_btc(outdir,coinbase=False)
            elif not primary and key=='us10y':
                secondary=fetch_em(spec,outdir,mirror=True)
        except Exception as e:
            errors.append({'key':key,'stage':'secondary','error':str(e)})
        try:
            row=choose_quote(spec,primary,secondary)
        except ValueError as e:
            row=None
            errors.append({'key':key,'stage':'cross-check','error':str(e)})
        if row is None:
            old=cache.get(key)
            if old:
                try:
                    row=make_quote(spec,old['price'],old['reference'],old['asof'],old['source'],old['source_url'],time_kind=old.get('time_kind','quote'))
                    row['status']='cached'
                    row['basis']=old['basis']
                    if 'change_period' in old:
                        row['change_period']=old['change_period']
                except (ValueError,KeyError):
                    row=None
            if row is None:
                row={'key':key,'name':spec['name'],'unit':spec['unit'],'price':None,'change_pct':None,'change_bp':None,'source':'','source_url':spec['url'],'asof':None,'status':'missing','basis':'待核实'}
        if row['status']=='ok':
            cache[key]=row
        rows.append(row)
    result={'edition_date':args.date,'generated_at':now().isoformat(),'timezone':'Asia/Shanghai','rows':rows,'available_count':sum(r['price'] is not None for r in rows),'errors':errors,'methodology':{'price':'采集时可取得的最新有效报价；各市场时点不同，逐行标注北京时间。','change':'股票、股指、黄金相对前收；布伦特近月连续期货相对前结；BTC/USD现货优先使用24小时涨跌幅，Kraken备用源只能提供UTC日内时逐行明确标注。','us10y':'显示收益率、收益率相对涨跌幅及基点变化；不是债券价格涨跌。','missing':'主备源重试仍失败时，只使用标明原报价时间的有效缓存；超期或无缓存显示待核实，绝不填0或编造。'}}
    (ROOT/'source'/args.date/'markets.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    cache_path.write_text(json.dumps(cache,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'available_count':result['available_count'],'total':len(rows),'rows':[{k:r.get(k) for k in ['name','price','change_pct','change_bp','source','asof','status','fallback_used']} for r in rows],'errors':errors},ensure_ascii=False))


if __name__=='__main__':
    main()
