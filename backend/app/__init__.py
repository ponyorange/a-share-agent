"""Share Data Explorer backend package."""

from .env import load_env

# Load .env before proxy / provider init
load_env()

from .proxy_fix import apply_network_fixes  # noqa: E402

# Must run before any akshare / requests traffic
apply_network_fixes()
