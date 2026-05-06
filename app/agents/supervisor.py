"""
Supervisor Node — Entry point for the LangGraph graph.

Responsibilities:
  1. At call START: call Buyer Details API to seed state with caller history.
  2. Route initially to receptionist_agent.
  3. Mid-call: if invoked again, route based on last active agent.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_groq import ChatGroq

from app.core.config import settings
from app.agents.graph_state import BSBGraphState

logger = logging.getLogger("supervisor")

# Larger LLM for supervisor-level routing decisions (only used on first turn)
_llm = ChatGroq(
    temperature=0,
    groq_api_key=settings.GROQ_API_KEY,
    model_name="llama-3.3-70b-versatile",
    max_tokens=100,
    request_timeout=15,
)


def supervisor_node(state: BSBGraphState, db=None) -> dict[str, Any]:
    """
    LangGraph node: Supervisor.
    On first turn: fetches Buyer Details API → seeds state → routes to receptionist.
    On subsequent turns: should NOT be called (orchestrator routes directly to active agent).
    """
    from app.services.bsb_api_client import sync_fetch_buyer_details
    from app.agents.session_store import SessionStore

    phone    = state.get("phone_number", "")
    call_id  = state.get("call_id", "")

    caller_profile: dict = {}

    # Check if buyer details were pre-fetched during greeting
    already_has_data = bool(state.get("customer_name") or state.get("is_registered"))

    if phone and not already_has_data:
        try:
            # ── Buyer Details API (called at start of every call) ─────────────
            caller_profile = sync_fetch_buyer_details(phone)
            is_registered  = caller_profile.get("exists", False)

            if db:
                store = SessionStore(db)
                store.get_or_create(call_id, phone)
                store.update_registration(call_id, is_registered)
                store.update_agent(call_id, "supervisor")

            logger.info(
                f"[Supervisor] Buyer Details fetched for {phone}: "
                f"exists={is_registered}, name={caller_profile.get('name')}"
            )
        except Exception as e:
            logger.error(f"[Supervisor] Buyer Details API failed: {e}")
    elif already_has_data:
        logger.info(f"[Supervisor] Using pre-fetched buyer details for {phone}")

    updates: dict[str, Any] = {
        "is_registered": caller_profile.get("exists", state.get("is_registered", False)),
        "next_agent":    "receptionist_agent",
        "active_agent":  "supervisor",
        "stage":         "greeting",
        "should_end":    False,
    }

    # Seed shared state with whatever the API returned
    if caller_profile.get("name"):
        updates["customer_name"] = caller_profile["name"]
    if caller_profile.get("company_name"):
        updates["company_name"] = caller_profile["company_name"]
    if caller_profile.get("location"):
        updates["factory_location"] = caller_profile["location"]
    if caller_profile.get("last_material"):
        updates["polymer_needed"] = caller_profile["last_material"]
    if caller_profile.get("last_brand"):
        updates["brand_preferred"] = caller_profile["last_brand"]
    if caller_profile.get("last_code"):
        updates["grade_needed"] = caller_profile["last_code"]

    logger.info(f"[Supervisor] → routing to receptionist_agent for {phone}")
    return updates
