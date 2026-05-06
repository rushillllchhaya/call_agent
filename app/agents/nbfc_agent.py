"""
BSB NBFC / Trade Finance Agent
===============================
Handles trade finance, buyer credit, and NBFC-related queries.
Prompt synthesised from: NBFC Finance Agent.json (Retell), outboundcall_system_prompt.
"""
from __future__ import annotations
import logging
from typing import Any

from langchain_core.messages import SystemMessage, AIMessage
from langchain_groq import ChatGroq

from app.core.config import settings
from app.agents.graph_state import BSBGraphState

logger = logging.getLogger("nbfc_agent")

_llm = ChatGroq(
    temperature=0.1,
    groq_api_key=settings.GROQ_API_KEY,
    model_name="meta-llama/llama-4-scout-17b-16e-instruct",
    max_tokens=300,
    request_timeout=15,
)

SYSTEM_PROMPT = """You are Mohini, the Trade Finance (NBFC) Agent at BSB Polymers.

# LANGUAGE RULE (HIGHEST PRIORITY)
You must ONLY speak in Hindi, English, Hinglish, or Gujarati. No other languages.
EVERY response MUST start with a language tag: <|HINDI|> or <|ENGLISH|> or <|GUJARATI|>.
Detect user's language and respond in the SAME language. Default is Hinglish.

# IDENTITY & PERSONA
- Professional female voice — warm, empathetic.
- CRITICAL GENDER RULE: FEMALE. Always: "मैं चेक कर रही हूँ", "मैं arrange कर रही हूँ". NEVER masculine.
- Once name is known → Name + "ji".

# CONVERSATION FLOW (from Retell NBFC Finance Agent)
1. Welcome: "[Name] ji, welcome to Trade Finance. Kya aap aapki requirements bata sakte ho?"

2. If user wants Trader Finance / Buy on Credit / Buyer Credit:
   - Explain briefly: "BSB Polymers ke through aapko trade finance facility milti hai jisme aap credit pe material purchase kar sakte ho."
   - Ask about their requirement: "Aapko kitne amount ki credit facility chahiye?"
   - Ask about material: "Kis material ke liye credit chahiye?"

3. Divert to App/Website:
   - "The further information and steps for trade finance will be on our platform. Please fill the form and your application will start quickly."
   - "Kya main aapko BSB Polymer App ka link WhatsApp pe bhej doon jisse aap form fill kar sakte ho?"
   - If Yes → [SEND_WHATSAPP]

4. Follow-up: "Aur kuch help chahiye?"
   - If user has other needs → respond accordingly.
   - If No → Go to Closure.

5. Closure: "Thank you from BSB Polymers, apka din shubh ho." → ENDCALL

# CURRENCY FORMAT
Use Indian numbering: lakhs and crores. Speak currency as words.

# TTS FORMAT RULES
Never output bullet lists, numbered lists, or markdown. Speak everything as flowing sentences.

# SEAMLESS AGENT TRANSITIONS
- PRICES/RATES/SALES/ORDER → Say "Main aapko sales team se connect karti hu." → [TRANSFER_SALES]
- TRANSPORT/DELIVERY/LOGISTICS → Say "Main aapko logistics team se connect karti hu." → [TRANSFER_LOGISTICS]
- General → [TRANSFER_RECEPTIONIST]

# ENDCALL RULE
ENDCALL must be LAST output. No words after it.

# SMART BEHAVIOR
No response 5-10s: "Sir, are you there?" Audio unclear: "Sorry sir, aapki awaaz clear nahi aa rahi." Stuck after 3 attempts: "Main aapko humari team se callback karwati hoon." → ENDCALL
"""


def _build_context(state: BSBGraphState) -> str:
    """Build dynamic context from shared state."""
    parts = []
    if state.get("customer_name"):
        parts.append(f"Customer Name: {state['customer_name']}")
    if state.get("company_name"):
        parts.append(f"Company: {state['company_name']}")
    if state.get("factory_location"):
        parts.append(f"Location: {state['factory_location']}")
    if state.get("phone_number"):
        parts.append(f"Phone: {state['phone_number']}")
    if state.get("credit_application_id"):
        parts.append(f"Credit Application: {state['credit_application_id']}")
    if not parts:
        return ""
    return "\n[KNOWN CUSTOMER CONTEXT — DO NOT ask for these again]\n" + "\n".join(parts)


def make_nbfc_agent(db=None):
    def nbfc_node(state: BSBGraphState) -> dict[str, Any]:
        messages = state.get("messages", [])
        ctx = _build_context(state)
        full_prompt = SYSTEM_PROMPT + ctx

        system_msg = SystemMessage(content=full_prompt)
        response = _llm.invoke([system_msg] + messages)
        reply = response.content

        next_agent = "nbfc_agent"
        if "[TRANSFER_SALES]" in reply:
            next_agent = "sales_agent"
            reply = reply.replace("[TRANSFER_SALES]", "").strip()
        elif "[TRANSFER_LOGISTICS]" in reply:
            next_agent = "logistics_agent"
            reply = reply.replace("[TRANSFER_LOGISTICS]", "").strip()
        elif "[TRANSFER_RECEPTIONIST]" in reply:
            next_agent = "receptionist_agent"
            reply = reply.replace("[TRANSFER_RECEPTIONIST]", "").strip()

        # WhatsApp API trigger
        if "[SEND_WHATSAPP]" in reply:
            reply = reply.replace("[SEND_WHATSAPP]", "").strip()
            phone = state.get("phone_number", "")
            if phone:
                try:
                    from app.services.bsb_api_client import sync_send_whatsapp
                    sync_send_whatsapp(phone)
                except Exception as e:
                    logger.error(f"[NBFCAgent] WhatsApp API failed: {e}")

        should_end = "ENDCALL" in reply
        if should_end:
            reply = reply.replace("ENDCALL", "").strip()

        for tag in ["<|HINDI|>", "<|ENGLISH|>", "<|GUJARATI|>"]:
            reply = reply.replace(tag, "").strip()

        return {
            "messages": [AIMessage(content=reply)],
            "active_agent": "nbfc_agent",
            "next_agent": next_agent,
            "should_end": should_end,
        }

    return nbfc_node
