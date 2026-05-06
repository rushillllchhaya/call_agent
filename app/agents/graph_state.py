"""
LangGraph State Definition for the BSB Multi-Agent System.
Shared state dict that flows through every node in the graph.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional
from typing_extensions import TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


# ── Agent names (used in routing edges) ──────────────────────────────────────
AgentName = Literal[
    "supervisor",
    "receptionist_agent",
    "sales_agent",
    "logistics_agent",
    "nbfc_agent",
    "__end__",
]


class BSBGraphState(TypedDict):
    """
    The single shared state that every LangGraph node reads and writes.

    `messages` uses the `add_messages` reducer so each node appends (not replaces).
    All other fields are plain overwrite.
    """

    # ── LangChain message history ─────────────────────────────────────────────
    # add_messages reducer: new messages are APPENDED, not overwritten.
    messages: Annotated[list[BaseMessage], add_messages]

    # ── Call metadata ─────────────────────────────────────────────────────────
    call_id:       str          # Unique identifier for this call
    phone_number:  str          # Caller's phone number
    is_registered: bool         # True = known buyer/seller; False = new caller

    # ── Routing ───────────────────────────────────────────────────────────────
    active_agent:  AgentName    # Which agent is currently handling the turn
    next_agent:    AgentName    # Where the supervisor or current agent routes next

    # ── Conversation progress ─────────────────────────────────────────────────
    stage:         str          # Current stage name (greeting, qualification, …)
    should_end:    bool         # True = hang up the call

    # Barge-in status
    interrupted: bool

    # ── Collected customer data ────────────────────────────────────────────────
    customer_name:     Optional[str]
    customer_type:     Optional[str]   # 'manufacturer' | 'trader'
    polymer_needed:    Optional[str]
    grade_needed:      Optional[str]
    brand_preferred:   Optional[str]
    quantity_needed:   Optional[str]
    factory_location:  Optional[str]
    company_name:      Optional[str]
    product_made:      Optional[str]
    intent:            Optional[str]   # 'buy' | 'sell'

    # ── Sub-agent specific ────────────────────────────────────────────────────
    # Logistics
    freight_origin:      Optional[str]
    freight_destination: Optional[str]
    freight_quantity_mt: Optional[float]
    logistics_booking_id: Optional[str]

    # Credit / NBFC
    credit_application_id: Optional[str]
    credit_eligible:       Optional[bool]

    # ── Agent Specific Context Storage (Small Data Storage) ───────────────────
    receptionist_context: dict
    sales_context: dict
    logistics_context: dict
    nbfc_context: dict
