"""
Multi-Agent Orchestrator — LangGraph bridge for the BSB voice pipeline.
========================================================================
Routes each speech turn through the LangGraph multi-agent StateGraph.
Key fixes from audit:
  - Only routes through Supervisor on first turn (no double-LLM latency)
  - Populates agent-specific context dicts
  - Broadcasts agent_change and call_summary to dashboard
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from sqlalchemy.orm import Session
from langchain_core.messages import HumanMessage
import groq

from app.agents.graph_state import BSBGraphState
from app.agents.session_store import SessionStore
from app.core.retry import with_retry

logger = logging.getLogger("multi_agent_orchestrator")


# ── Default initial state skeleton ───────────────────────────────────────────
def _make_initial_state(
    call_id: str,
    phone_number: str,
    user_text: str,
    stage: str = "greeting",
    customer_name: Optional[str] = None,
) -> BSBGraphState:
    """Build a fresh BSBGraphState for the first turn of a new call."""
    return BSBGraphState(
        messages=[HumanMessage(content=user_text)],
        call_id=call_id,
        phone_number=phone_number,
        is_registered=False,
        active_agent="supervisor",
        next_agent="supervisor",
        stage=stage,
        should_end=False,
        customer_name=customer_name,
        customer_type=None,
        polymer_needed=None,
        grade_needed=None,
        brand_preferred=None,
        quantity_needed=None,
        factory_location=None,
        company_name=None,
        product_made=None,
        intent=None,
        freight_origin=None,
        freight_destination=None,
        freight_quantity_mt=None,
        logistics_booking_id=None,
        credit_application_id=None,
        credit_eligible=None,
        receptionist_context={},
        sales_context={},
        logistics_context={},
        nbfc_context={},
    )


class MultiAgentOrchestrator:
    """
    High-level orchestrator that:
      1. Manages ConversationSession records (DB)
      2. Invokes the LangGraph StateGraph per turn
      3. Returns (ai_response_text, should_end_call) to streams.py
      4. Broadcasts events to the dashboard
    """

    GRAPH_DB_PATH = "./conversations_graph.db"

    def __init__(self, db: Session):
        self.db = db
        self.store = SessionStore(db)
        self._graph = None
        self._last_active_agent: Optional[str] = None
        self._buyer_details_cache: dict[str, dict] = {}  # call_id → buyer details

    def set_prefetched_buyer_details(self, call_id: str, details: dict):
        """Cache buyer details fetched during greeting phase."""
        self._buyer_details_cache[call_id] = details

    def get_prefetched_buyer_details(self, call_id: str) -> Optional[dict]:
        """Retrieve and consume cached buyer details."""
        return self._buyer_details_cache.pop(call_id, None)

    def _get_graph(self):
        """Lazy-build and cache the LangGraph for this db session."""
        if self._graph is None:
            from app.agents.graph import build_graph
            self._graph = build_graph(self.db, self.GRAPH_DB_PATH)
        return self._graph

    # ── Public API ───────────────────────────────────────────────────────────

    def start_call(
        self,
        call_id: str,
        phone_number: str,
        known_name: Optional[str] = None,
    ) -> str:
        """
        Called at the start of every call.
        Creates a session record, returns the greeting text.
        """
        self.store.get_or_create(call_id, phone_number)

        if known_name:
            return (
                f"Ji Namaste {known_name} ji! "
                f"Main Mohini hun, BSB Polymers se baat kar rahi hun. "
                f"Aapko Sales se live rates chahiye, Logistics se transport ki detail chahiye, "
                f"Trade Finance se credit discuss karni hai, ya fir mujhse general inquiry karni hai?"
            )
        return (
            "Ji Namaste! Main Mohini hun, BSB Polymers se baat kar rahi hun. "
            "Aap Polymers mein deal karte ho?"
        )

    def process_message(
        self,
        call_id: str,
        user_text: str,
        phone_number: str = "",
        language_code: str = "hi-IN",
    ) -> tuple[str, bool]:
        """
        Synchronous entry point — process one user turn through LangGraph.
        KEY FIX: Only routes through supervisor on the first turn.
        On subsequent turns, sends directly to the active_agent.
        """
        import time
        t0 = time.monotonic()
        graph = self._get_graph()
        config = {"configurable": {"thread_id": call_id}}
        logger.info(f"[MultiAgentOrchestrator] Processing: '{user_text[:60]}' for {call_id}")

        session = self.store.get_or_create(call_id, phone_number)
        stage         = session.get("stage", "greeting")
        is_registered = session.get("is_registered", False)
        customer_info = session.get("customer_info", {})
        current_agent = session.get("agent_used", "supervisor")

        # FIX: Only go through supervisor on the very first turn.
        # After that, route directly to the active agent — no extra LLM call.
        if current_agent == "supervisor" or not current_agent:
            next_agent = "supervisor"
        else:
            next_agent = current_agent

        state = BSBGraphState(
            messages=[HumanMessage(content=user_text)],
            call_id=call_id,
            phone_number=phone_number or session.get("phone_number", ""),
            is_registered=is_registered,
            active_agent=current_agent,
            next_agent=next_agent,
            stage=stage,
            should_end=False,
            customer_name=customer_info.get("name"),
            customer_type=customer_info.get("customer_type"),
            polymer_needed=customer_info.get("polymer_needed"),
            grade_needed=customer_info.get("grade_needed"),
            brand_preferred=customer_info.get("brand_preferred"),
            quantity_needed=customer_info.get("quantity_needed"),
            factory_location=customer_info.get("factory_location"),
            company_name=customer_info.get("company_name"),
            product_made=customer_info.get("product_made"),
            intent=customer_info.get("intent"),
            freight_origin=customer_info.get("freight_origin"),
            freight_destination=customer_info.get("freight_destination"),
            freight_quantity_mt=customer_info.get("freight_quantity_mt"),
            logistics_booking_id=customer_info.get("logistics_booking_id"),
            credit_application_id=customer_info.get("credit_application_id"),
            credit_eligible=customer_info.get("credit_eligible"),
            receptionist_context=customer_info.get("receptionist_context", {}),
            sales_context=customer_info.get("sales_context", {}),
            logistics_context=customer_info.get("logistics_context", {}),
            nbfc_context=customer_info.get("nbfc_context", {}),
        )

        logger.info(f"[MultiAgentOrchestrator] Invoking graph: agent={next_agent} stage={stage}")

        # If buyer details were pre-fetched during greeting, inject them
        # into state so the supervisor skips its slow API call.
        prefetched = self.get_prefetched_buyer_details(call_id)
        if prefetched and next_agent == "supervisor":
            if prefetched.get("name"):
                state["customer_name"] = prefetched["name"]
            if prefetched.get("company_name"):
                state["company_name"] = prefetched["company_name"]
            if prefetched.get("location"):
                state["factory_location"] = prefetched["location"]
            if prefetched.get("last_material"):
                state["polymer_needed"] = prefetched["last_material"]
            if prefetched.get("last_brand"):
                state["brand_preferred"] = prefetched["last_brand"]
            if prefetched.get("last_code"):
                state["grade_needed"] = prefetched["last_code"]
            state["is_registered"] = prefetched.get("exists", False)
            logger.info(f"[MultiAgentOrchestrator] Injected pre-fetched buyer details")

        try:
            result_state = graph.invoke(state, config=config)
            elapsed = time.monotonic() - t0
            logger.info(f"[MultiAgentOrchestrator] Graph completed in {elapsed:.1f}s")
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error(f"[MultiAgentOrchestrator] Graph error after {elapsed:.1f}s: {e}", exc_info=True)
            return "Ji, thoda sa repeat karein please?", False

        ai_text = self._extract_ai_response(result_state)
        self._sync_session(call_id, user_text, ai_text, result_state)

        # Broadcast agent change if the agent switched
        new_agent = result_state.get("active_agent", current_agent)
        if new_agent != self._last_active_agent:
            self._last_active_agent = new_agent
            self._schedule_dashboard_broadcast({
                "type": "agent_change",
                "agent_name": new_agent,
            })

        # Broadcast call summary with collected fields
        self._schedule_call_summary_broadcast(result_state)

        should_end = result_state.get("should_end", False)
        logger.info(
            f"[MultiAgentOrchestrator] call_id={call_id} "
            f"agent={new_agent} "
            f"stage={result_state.get('stage')} "
            f"end={should_end}"
        )
        return ai_text, should_end

    @with_retry(
        max_retries=2,
        base_delay=0.5,
        retry_on=(asyncio.TimeoutError, groq.RateLimitError, groq.APIConnectionError),
        log_name="orchestrator_llm",
    )
    async def process_message_async(
        self,
        call_id: str,
        user_text: str,
        phone_number: str = "",
        language_code: str = "hi-IN",
    ) -> tuple[str, bool]:
        """Async wrapper — runs LangGraph in thread pool with timeout + retry."""
        loop = asyncio.get_running_loop()
        # 18s timeout per attempt — better to retry than go silent
        return await asyncio.wait_for(
            loop.run_in_executor(
                None,
                self.process_message,
                call_id, user_text, phone_number, language_code,
            ),
            timeout=18.0,
        )

    # ── Streaming variant (sentence-by-sentence) ────────────────────────────

    async def stream_response_sentences(
        self,
        call_id: str,
        user_text: str,
        phone_number: str = "",
        language_code: str = "hi-IN",
    ) -> AsyncGenerator[tuple[str, bool], None]:
        """
        Yields (sentence_text, is_final) pairs for streaming TTS.
        """
        import re
        _SENT_RE = re.compile(r"[.?!।]")

        ai_text, should_end = await self.process_message_async(
            call_id, user_text, phone_number, language_code
        )

        buf = ""
        for char in ai_text:
            buf += char
            if _SENT_RE.search(char):
                sentence = buf.strip()
                if sentence and any(c.isalnum() or ord(c) > 127 for c in sentence):
                    yield sentence, False
                buf = ""

        if buf.strip():
            sentence = buf.strip()
            if any(c.isalnum() or ord(c) > 127 for c in sentence):
                yield sentence, False

        yield "", should_end

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_ai_response(state: BSBGraphState) -> str:
        """Pull the last AI message content from the messages list and strip backend tags."""
        from langchain_core.messages import AIMessage
        import re
        messages = state.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                text = str(msg.content).strip()
                # Strip out any remaining tool/bracket tags e.g. [FETCH_PRICE|...]
                text = re.sub(r'\[.*?\]', '', text).strip()
                return text if text else "Ji, thoda sa repeat karein please?"
        return "Ji, thoda sa repeat karein please?"

    def _sync_session(
        self,
        call_id: str,
        user_text: str,
        ai_text: str,
        state: BSBGraphState,
    ):
        """Persist the updated graph state back to ConversationSession DB."""
        try:
            self.store.append_message(call_id, "human", user_text)
            self.store.append_message(call_id, "ai",    ai_text)

            if state.get("stage"):
                self.store.update_stage(call_id, state["stage"])
            if state.get("active_agent"):
                self.store.update_agent(call_id, state["active_agent"])

            info_fields = {
                "name":                 state.get("customer_name"),
                "customer_type":        state.get("customer_type"),
                "polymer_needed":       state.get("polymer_needed"),
                "grade_needed":         state.get("grade_needed"),
                "brand_preferred":      state.get("brand_preferred"),
                "quantity_needed":      state.get("quantity_needed"),
                "factory_location":     state.get("factory_location"),
                "company_name":         state.get("company_name"),
                "product_made":         state.get("product_made"),
                "intent":               state.get("intent"),
                "freight_origin":       state.get("freight_origin"),
                "freight_destination":  state.get("freight_destination"),
                "freight_quantity_mt":  state.get("freight_quantity_mt"),
                "logistics_booking_id": state.get("logistics_booking_id"),
                "credit_application_id": state.get("credit_application_id"),
                "credit_eligible":      state.get("credit_eligible"),
                "receptionist_context": state.get("receptionist_context", {}),
                "sales_context":        state.get("sales_context", {}),
                "logistics_context":    state.get("logistics_context", {}),
                "nbfc_context":         state.get("nbfc_context", {}),
            }
            self.store.update_customer_info(call_id, info_fields)

        except Exception as e:
            logger.error(f"[MultiAgentOrchestrator] Session sync error: {e}")

    def _schedule_dashboard_broadcast(self, message: dict):
        """Fire-and-forget broadcast to dashboard (non-blocking)."""
        try:
            from app.services.dashboard import dashboard_manager
            try:
                loop = asyncio.get_running_loop()
                asyncio.ensure_future(dashboard_manager.broadcast(message))
            except RuntimeError:
                _loop = asyncio.new_event_loop()
                try:
                    _loop.run_until_complete(dashboard_manager.broadcast(message))
                finally:
                    _loop.close()
        except Exception:
            pass

    def _schedule_call_summary_broadcast(self, state: BSBGraphState):
        """Broadcast collected call summary fields to the dashboard."""
        summary = {}
        if state.get("customer_name"):
            summary["name"] = state["customer_name"]
        if state.get("phone_number"):
            summary["phone"] = state["phone_number"]
        if state.get("factory_location"):
            summary["location"] = state["factory_location"]
        if state.get("company_name"):
            summary["company"] = state["company_name"]
        if state.get("polymer_needed"):
            summary["material"] = state["polymer_needed"]
        if state.get("active_agent"):
            summary["active_agent"] = state["active_agent"]

        if summary:
            self._schedule_dashboard_broadcast({"type": "call_summary", **summary})

    def end_call(self, call_id: str):
        """Mark session as completed when call disconnects."""
        try:
            self.store.close_session(call_id, final_stage="completed")
        except Exception as e:
            logger.error(f"[MultiAgentOrchestrator] end_call error: {e}")
