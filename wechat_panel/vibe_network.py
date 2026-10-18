"""Network compatibility for the isolated Vibe worker, never a global proxy change."""


def configure_ccxt_network():
    # CCXT defaults requests_trust_env to False. Unlike the other upstream
    # requests-based loaders, it therefore ignores both conventional proxy
    # variables and Windows Internet Settings. This breaks public symbol
    # resolution even when the same exchange is reachable by other tools.
    # Only the short-lived worker imports this module's optional dependency.
    try:
        import ccxt
    except ImportError:
        return
    ccxt.Exchange.requests_trust_env = True
