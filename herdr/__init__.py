"""Herdr Multi-Agent Workflow Platform.

Core Python package providing workflow engine, agent routing, pane allocation,
multi-project registry, and self-healing topology services.
"""

from . import workflow
from . import agent_router
from . import pane_pool
from . import projects
from . import topology
from . import preflight
from . import deep_preflight
from . import kernel

__all__ = [
    "workflow",
    "agent_router",
    "pane_pool",
    "projects",
    "topology",
    "preflight",
    "deep_preflight",
    "kernel",
]

# Provide backwards-compatible module aliases so legacy flat-file imports
# (e.g., `import herdr_workflow` or `from herdr_workflow import ...`) continue to work seamlessly.
import sys

for _alias, _module in [
    ("herdr_workflow", workflow),
    ("herdr_agent_router", agent_router),
    ("herdr_pane_pool", pane_pool),
    ("herdr_projects", projects),
    ("herdr_topology", topology),
    ("herdr_preflight", preflight),
    ("herdr_deep_preflight", deep_preflight),
    ("herdr_kernel", kernel),
]:
    sys.modules.setdefault(_alias, _module)
