"""Compare ordered UI observations without deduplicating by message text."""
from dataclasses import dataclass


@dataclass
class Delta:
    items: list[dict]
    historical: bool = False
    gap: bool = False
    retain: bool = False


def identity(item):
    text=item.get('identity_text', item['text'])
    if item.get('sender_verified') and item.get('sender'):
        text='[group-sender:'+item['sender']+']\0'+text
    return item.get('kind','text'), text


def compatible(old, new):
    return (identity(old)==identity(new) and (old.get('direction')==new.get('direction')
            or 'unknown' in (old.get('direction'),new.get('direction'))))


def sender_enrichment(old, new):
    """A later native read can enrich the same visibly unchanged message."""
    return (not old.get('sender_verified') and new.get('sender_verified')
            and new.get('sender_source') == 'uia_selection'
            and old.get('native_key') and old['native_key'] == new.get('native_key')
            and old.get('sender_header') and old['sender_header'] == new.get('sender_header')
            and old['text'] == new['text'] and old.get('kind','text') == new.get('kind','text')
            and (old.get('direction') == new.get('direction') or 'unknown' in (old.get('direction'),new.get('direction'))))


def align(previous, current):
    """Keep durable identities across Qt recycling, using ordered unique context.

    A single repeated 'hello' is never enough to join recreated controls. Exact
    raw IDs still require unchanged content. Two distinct message anchors are
    required for content alignment; source directions are never guessed.
    """
    if not previous or not current:return [dict(x) for x in current]
    result=[dict(x) for x in current]
    known={}
    for old in previous:
        for key in (old['key'],old.get('raw_key',old['key'])):known[(key,identity(old))]=old
    matched={}
    for j,new in enumerate(current):
        enriched=next((old for old in previous if sender_enrichment(old,new)),None)
        if enriched:
            matched[j]=enriched
            continue
        old=known.get((new['key'],identity(new))) or known.get((new.get('raw_key'),identity(new)))
        if old is None:
            # Identity evidence may become visible after the body. Only the
            # same exact UI row/content may inherit or enrich that evidence.
            old=next((x for x in previous
                if x['key'] in (new['key'],new.get('raw_key'))
                and x.get('kind','text')==new.get('kind','text')
                and x.get('identity_text',x['text'])==new.get('identity_text',new['text'])
                and (not x.get('sender_verified') or not new.get('sender_verified')
                     or x.get('sender')==new.get('sender'))),None)
            if old and old.get('direction')!=new.get('direction') and 'unknown' not in (old.get('direction'),new.get('direction')):old=None
            if old:matched[j]=old
        if old and compatible(old,new):matched[j]=old
    # Longest contiguous ordered block, independent of transient UIA runtime IDs.
    runs=[];row=[0]*(len(current)+1)
    for i,old in enumerate(previous):
        next_row=[0]
        for j,new in enumerate(current):
            length=row[j]+1 if compatible(old,new) else 0
            next_row.append(length)
            if length>=2:runs.append((length,i-length+1,j-length+1))
        row=next_row
    for length,i,j in sorted(runs,reverse=True):
        block=previous[i:i+length]
        signatures=[identity(x) for x in block]
        messages={identity(x) for x in block if x.get('kind','text')!='system'}
        if len(messages)<2 or sum(len(x[1]) for x in messages)<24:continue
        # Reject ambiguous repeated sequences and contradictions with exact anchors.
        if sum([identity(x) for x in previous[k:k+length]]==signatures for k in range(len(previous)-length+1))!=1:continue
        if sum([identity(x) for x in current[k:k+length]]==signatures for k in range(len(current)-length+1))!=1:continue
        if any(j+n in matched and matched[j+n]['key']!=old['key'] for n,old in enumerate(block)):continue
        for n,old in enumerate(block):matched[j+n]=old
        break
    for j,old in matched.items():
        new=result[j]
        if new['key']!=old['key']:
            new['raw_key']=new.get('raw_key',new['key']);new['key']=old['key']
        if new.get('direction')=='unknown' and old.get('direction_verified'):
            new['direction']=old['direction'];new['direction_verified']=True
        if old.get('sender_verified') and not new.get('sender_verified'):
            for field in ('sender','sender_verified','mention_me','mention_verified','mention_source'):
                if field in old:new[field]=old[field]
    return result


def compare(previous: list[dict] | None, current: list[dict]) -> Delta:
    if previous is None:
        return Delta(current, historical=True)
    if not current:
        return Delta([], gap=bool(previous))
    if not previous:
        return Delta(current)
    # A message's UI identity AND content are used; two identical texts stay distinct.
    def key(item):
        # Avatar visibility can change when rows are clipped. UI identity and
        # content still identify the same message, even if direction is unknown.
        return item['key'], identity(item)
    effective=[]
    for item in previous:
        enriched=next((x for x in current if x['key']==item['key'] and sender_enrichment(item,x)),None)
        if enriched:
            effective.append(dict(item,sender=enriched['sender'],sender_verified=True,
                                  identity_text=enriched.get('identity_text',enriched['text'])))
            continue
        visible=next((x for x in current if x['key']==item['key']
            and x.get('kind','text')==item.get('kind','text')
            and x.get('identity_text',x['text'])==item.get('identity_text',item['text'])),None)
        effective.append(dict(item,sender=visible.get('sender'),sender_verified=True)
            if visible and visible.get('sender_verified') and not item.get('sender_verified') else item)
    old, new = [key(x) for x in effective], [key(x) for x in current]
    for overlap in range(min(len(old), len(new)), 0, -1):
        for start in range(len(new)-overlap+1):
            if old[-overlap:] == new[start:start+overlap]:
                return Delta(current[start+overlap:])
    # Scrolling upward or temporarily clipping the tail is not a history gap.
    for start in range(len(old)-len(new)+1):
        if old[start:start+len(new)]==new:return Delta([],retain=True)
    # Show the visible window again as history, never as fresh Agent requests.
    return Delta(current, historical=True, gap=True)
