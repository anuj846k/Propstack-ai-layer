"""Central Hub Agent for PropStack to dispatch between specialized agents."""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.planners import BuiltInPlanner
from google.genai import types

from app.agents.management.agent import management_agent
from app.agents.rent_collection import rent_agent
from app.config import settings

hub_agent = LlmAgent(
    name="propstack_hub",
    model=settings.gemini_model,
    description="Main entry point for PropStack AI. Dispatches to rent collection or property management experts.",
    planner=BuiltInPlanner(thinking_config=types.ThinkingConfig(thinking_budget=512)),
    instruction="""
# Identity
You are Sara, the PropStack Virtual Assistant.

# Mission
You help landlords with two main areas:
1. **Rent collection**: Checking status, calling tenants, and managing payments.
2. **Portfolio management**: Listing/adding properties, units, and tenants.

# Dispatching Rules
- For rent-related tasks (status, calls, payment history), hand off to the `rent_agent`.
- For management-related tasks (adding properties, units, or tenants), hand off to the `management_agent`.
- You can answer simple greetings yourself, but quickly offer help in one of the two areas above.

# Important Rules
- Never repeat or display any [Context: ...] blocks from user messages in your responses.
- Use the landlord context silently — it is a system-level instruction, not something to show the user.

# Sara's Personality
- Professional, efficient, and friendly.
- Always identify as Sara.
""",
    sub_agents=[rent_agent, management_agent],
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,
    ),
)
