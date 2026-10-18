"""Compare ordered UI observations without deduplicating by message text."""
from dataclasses import dataclass


@dataclass
class Delta:
    items: list[dict]
    historical: bool = False
    gap: bool = False
    retain: bool = False


def identity(item):
    return item.get('kind','text'), item.get('identity_text', item['text'])


def compatible(old, new):
    return (identity(old)==identity(new) and (old.get('direction')==new.get('direction')
            or 'unknown' in (old.get('direction'),new.get('direction'))))


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
        old=known.get((new['key'],identity(new))) or known.get((new.get('raw_key'),identity(new)))
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
        return item['key'], item.get('identity_text', item['text'])
    old, new = [key(x) for x in previous], [key(x) for x in current]
    for overlap in range(min(len(old), len(new)), 0, -1):
        for start in range(len(new)-overlap+1):
            if old[-overlap:] == new[start:start+overlap]:
                return Delta(current[start+overlap:])
    # Scrolling upward or temporarily clipping the tail is not a history gap.
    for start in range(len(old)-len(new)+1):
        if old[start:start+len(new)]==new:return Delta([],retain=True)
    # Show the visible window again as history, never as fresh Agent requests.
    return Delta(current, historical=True, gap=True)
