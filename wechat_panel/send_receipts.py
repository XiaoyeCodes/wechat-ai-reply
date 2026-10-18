"""Read-only evidence for confirming a send, never a reason to send it again."""
from .snapshots import align, compare
from .wechat_text import receipt_text


def match(baseline, current, text, before_count=0):
    expected=receipt_text(text)
    if not expected:return None
    current=align(baseline,current)
    delta=compare(baseline,current)
    if delta.historical or delta.gap:return None
    old_count=max(before_count,sum(receipt_text(x['text'])==expected for x in baseline))
    if sum(receipt_text(x['text'])==expected for x in current)<=old_count:return None
    matches=[x for x in delta.items if x.get('direction')=='outgoing' and x.get('direction_verified',True)
             and x.get('kind','text')=='text' and receipt_text(x['text'])==expected]
    return matches[0] if len(matches)==1 else None
