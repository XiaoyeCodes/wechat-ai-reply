"""Read-only evidence for confirming a send, never a reason to send it again."""
from .snapshots import align, compare
from .wechat_text import receipt_text


def _new_items(baseline, current):
    current=align(baseline,current)
    delta=compare(baseline,current)
    if delta.historical or delta.gap:return current, []
    return current, delta.items


def _outgoing(item, kind):
    return (item.get('direction')=='outgoing' and item.get('direction_verified',True)
            and item.get('kind','text')==kind)


def match_file(baseline, current, filename, before_count=0):
    """Confirm an exact filename, never a prefix or a text-only attachment hint."""
    expected=receipt_text(filename)
    if not expected or '\n' in expected:return None
    _, added=_new_items(baseline,current)
    matches=[x for x in added if _outgoing(x,'file')
             and expected in [line.strip() for line in x['text'].splitlines()]]
    return matches[0] if len(matches)==1 else None


def match(baseline, current, text, before_count=0, kind='text'):
    if kind=='file':return match_file(baseline,current,text,before_count)
    if kind=='image':
        _, added=_new_items(baseline,current)
        matches=[x for x in added if _outgoing(x,'image')]
        return matches[0] if len(matches)==1 else None
    if kind!='text':return None
    expected=receipt_text(text)
    if not expected:return None
    current, added=_new_items(baseline,current)
    if not added:return None
    old_count=max(before_count,sum(receipt_text(x['text'])==expected for x in baseline))
    if sum(receipt_text(x['text'])==expected for x in current)<=old_count:return None
    matches=[x for x in added if _outgoing(x,'text') and receipt_text(x['text'])==expected]
    return matches[0] if len(matches)==1 else None
