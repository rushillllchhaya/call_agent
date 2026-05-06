"""
CRM Tools — LangChain @tool wrappers for caller lookup,
lead upsert, and conversation session management.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from langchain_core.tools import tool

logger = logging.getLogger("crm_tools")


def make_crm_tools(db):
    """
    Factory: returns CRM tools that close over the SQLAlchemy `db` session.
    """
    from app.services.bsb_api_client import CallAgentService
    from app.agents.session_store import SessionStore

    svc = CallAgentService(db)
    store = SessionStore(db)

    @tool
    def check_caller_registration(phone_number: str) -> str:
        """
        Check whether the caller is a registered BSB user (buyer/seller) or a
        new/unknown caller. Must be called at the START of every call.

        Args:
            phone_number: Caller's mobile number (10-digit or with country code).

        Returns:
            JSON with keys: exists (bool), new_user (bool), name, company_name,
            location, last_material, last_quantity, status, source.
        """
        result = svc.check_caller(phone_number)
        return json.dumps(result, ensure_ascii=False, default=str)

    @tool
    def save_lead_info(
        call_id: str,
        name: str = "",
        phone: str = "",
        company_name: str = "",
        location: str = "",
        material: str = "",
        quantity: str = "",
        quoted_price: float = 0.0,
        status: str = "New",
    ) -> str:
        """
        Save or update a lead's qualification information collected during the call.
        Use this whenever you collect a new piece of information from the caller.

        Args:
            call_id:      Unique identifier for this call session.
            name:         Caller's name.
            phone:        Caller's mobile number.
            company_name: Caller's company name (if mentioned).
            location:     Factory/delivery city or state.
            material:     Polymer type + grade, e.g. 'PVC SG-5'.
            quantity:     Required quantity, e.g. '25 MT'.
            quoted_price: Price quoted during call (0 if not quoted).
            status:       Lead status: 'New', 'Qualified', 'Converted'.

        Returns:
            JSON confirmation with lead_id and storage details.
        """
        result = svc.save_lead(
            name=name, phone=phone, company_name=company_name,
            location=location, material=material, quantity=quantity,
            quoted_price=quoted_price if quoted_price else None,
            status=status,
        )
        # Also update session store
        store.update_customer_info(call_id, {
            "name": name, "location": location,
            "material": material, "quantity": quantity,
        })
        return json.dumps(result, ensure_ascii=False)

    @tool
    def get_session_history(call_id: str) -> str:
        """
        Retrieve the conversation history and customer info for an ongoing call
        from the session database.

        Args:
            call_id: Unique call session identifier.

        Returns:
            JSON with keys: call_id, phone_number, stage, history (list),
            customer_info (dict), agent_used, is_registered.
        """
        session = store.get_session(call_id)
        if session:
            return json.dumps(session, ensure_ascii=False, default=str)
        return json.dumps({"error": "Session not found", "call_id": call_id})

    return [check_caller_registration, save_lead_info, get_session_history]
