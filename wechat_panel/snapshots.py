"""Compare ordered UI observations without deduplicating by message text."""
from dataclasses import dataclass


@dataclass
class Delta:
    items: list[dict]
    historical: bool = False
    gap: bool = False


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
        return item['key'], item['text']
    old, new = [key(x) for x in previous], [key(x) for x in current]
    for overlap in range(min(len(old), len(new)), 0, -1):
        if old[-overlap:] == new[:overlap]:
            return Delta(current[overlap:])
    # Show the visible window again as history, never as fresh Agent requests.
    return Delta(current, historical=True, gap=True)
