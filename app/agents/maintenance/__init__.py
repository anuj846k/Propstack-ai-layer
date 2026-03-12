from .triage_agent import triage_agent as maintenance_agent
from .voice_dispatch_agent import vendor_dispatch_agent

root_agent = maintenance_agent

__all__ = ["maintenance_agent", "vendor_dispatch_agent", "root_agent"]
