"""One source of role/style instructions when a complete prompt is supplied."""


def primary_prompt(agent):
    return bool(agent.get('prompt', '').strip())
