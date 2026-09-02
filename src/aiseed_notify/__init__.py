"""Config-driven Discord notifications driven by an MCP agent.

The supported integration surface is the `notify` CLI, and the way a service joins is by
serving MCP and getting an entry in the registry. These names are exported so the package
is coherent to import, and so callers have one exception to catch.
"""

from .agent import run as run_agent
from .config import load
from .errors import ConfigError, DeliveryError, NotifyError
from .mcp_client import Registry, Tool, discover, load_registry

__all__ = [
    "load",
    "load_registry",
    "discover",
    "run_agent",
    "Registry",
    "Tool",
    "NotifyError",
    "ConfigError",
    "DeliveryError",
]
