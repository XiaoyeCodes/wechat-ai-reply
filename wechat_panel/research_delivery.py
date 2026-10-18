"""Delivery policy only: never rewrite the native research prompt or answer."""
import re


def wants_text(text):
    return bool(re.search(r'(不要|不用|无需|别|不需要).{0,8}pdf|(?:直接|只|用|以).{0,4}(?:文字|文本).{0,6}(?:回复|发|输出)|(?:no|without)\s+(?:a\s+)?pdf|text\s+only', text, re.I))


def analysis_request(text):
    # A PDF mention, greeting, quote lookup or definition alone is not research.
    if re.search(r'^(你好|您好|谢谢|感谢|早安|晚安|在吗)[！!。\s]*$', text.strip()):
        return False
    if re.search(r'(不要|不用|别|无需).{0,4}(分析|研究)', text):return False
    return bool(re.search(r'分析|研报|研究报告|深度研究|复盘|估值|投资策略|风险评估|前景|能不能买|可以买|值不值得|怎么看|汇总.{0,12}(市场|美股|行情)|(?:市场|美股|行情).{0,12}(汇总|总结)|(?:股市|美股|股票|债券|黄金|原油|比特币|BTC|ETF|市场).{0,12}(?:为什么|如何|怎么|怎样)|\b(analy[sz]e|analysis|valuation|market\s+report)\b', text, re.I))


def substantive_analysis(question, reply):
    """Require actual financial reasoning; failed queries/short quotes stay text."""
    plain = re.sub(r'[`#*|>]', '', reply)
    if len(plain.strip()) < 220:
        return False
    topics = re.findall(r'市场|美股|股票|股价|估值|营收|利润|盈利|收益|债券|美债|利率|通胀|流动性|现金流|仓位|波动|比特币|加密|ETF|IPO|BTC|风险|投资|金融|均线|支撑位|阻力|成交量|技术指标|汇率|美元|黄金|原油|美联储|降息|加息', plain, re.I)
    reasoning = re.findall(r'因此|意味着|由于|导致|驱动|取决于|影响|压力|支撑|风险|预期|结论|情景|趋势|相比|但是|但|因而|because|risk|outlook', plain, re.I)
    evidence = bool(re.search(r'\d[\d,.]*\s*(?:%|亿|万|美元|元|倍|bp|点)|\|[^\n]+\|\n\|\s*[-:]|\d{4}[-/]\d', reply))
    # Length by itself is never a reason to create a financial PDF.
    qualitative = len(plain) >= 450 and len(re.findall(r'因为|由于|导致|取决于|因此|意味着|相比', plain)) >= 3
    return len(topics) >= 5 and len(reasoning) >= 3 and (evidence or qualitative) and (analysis_request(question) or len(plain) >= 600)
