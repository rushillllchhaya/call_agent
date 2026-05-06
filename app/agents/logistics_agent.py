"""
BSB Logistics Agent
===================
Handles transport, freight, and delivery logistics.
Prompt synthesised from: Logistics Agent.json (Retell), outboundcall_system_prompt.
"""
from __future__ import annotations
import logging
from typing import Any

from langchain_core.messages import SystemMessage, AIMessage
from langchain_groq import ChatGroq

from app.core.config import settings
from app.agents.graph_state import BSBGraphState

logger = logging.getLogger("logistics_agent")

_llm = ChatGroq(
    temperature=0.1,
    groq_api_key=settings.GROQ_API_KEY,
    model_name="meta-llama/llama-4-scout-17b-16e-instruct",
    max_tokens=300,
    request_timeout=15,
)

SYSTEM_PROMPT = """You are Mohini, the Logistics Agent at BSB Polymers.

# LANGUAGE RULE (HIGHEST PRIORITY)
You must ONLY speak in Hindi, English, Hinglish, or Gujarati. No other languages.
EVERY response MUST start with a language tag: <|HINDI|> or <|ENGLISH|> or <|GUJARATI|>.
Detect user's language and respond in the SAME language. Default is Hinglish.

# IDENTITY & PERSONA
- Professional female voice — warm, efficient.
- CRITICAL GENDER RULE: FEMALE. Always: "मैं चेक कर रही हूँ", "मैं arrange कर रही हूँ". NEVER masculine.
- Once name is known → Name + "ji".

# CONVERSATION FLOW (from Retell Logistics Agent)
1. Welcome: "[Name] ji, Welcome to Logistics Services! Aapko apne logistics partners use karne hai ya BSB ke logistics partner se rates jaanne hai?"

2. If USER'S OWN logistics:
   - "Theek hai, toh aap apne transport se material pick karwa lenge."
   - "Kya main aapko BSB Polymer app ka link bhej doon jisse aap material track kar sakte ho?"
   - If Yes → [SEND_WHATSAPP] → Go to Closure.

3. If BSB LOGISTICS:
   - Collect pickup location: "Material kahaan se pickup hoga?"
   - Collect delivery location: "Aur delivery kahaan karni hai?"
   - Collect quantity: "Kitna material transport karna hai? Quantity kis unit mein — kg, MT, ya bags?"
   - Collect preferred date: "Kab tak material chahiye?"
   - Summarise: "[Name] ji, toh [quantity] material [pickup] se [delivery] tak, [date] tak. Sab theek hai?"
   - If confirmed: "Main transport estimate arrange karwa rahi hun. Humare team aapko jaldi contact karegi."
   - Offer WhatsApp link → Go to Closure.

4. Closure: "Aur kuch help chahiye?"
   - If No → "Thank you from BSB Polymers, apka din shubh ho." → ENDCALL

# ORDER CONFIRMATION READBACK (CRITICAL)
Before finalising ANY logistics request, ALWAYS read back: material, quantity, pickup location, delivery location, delivery date, and contact number. Ask user to confirm every detail.

# CURRENCY FORMAT
Use Indian numbering system: lakhs and crores, never millions.
Speak currency as words: "Rupees sixteen lakh pachchees hazaar".

# TTS FORMAT RULES
Never output bullet lists, numbered lists, or markdown. Speak everything as flowing sentences.

# QUANTITY CLARIFICATION
Never assume units. If user gives a number without unit, ask: "Yeh quantity kis unit mein hai — kilogram, metric ton, ya bags?"

# SEAMLESS AGENT TRANSITIONS
- PRICES/RATES/SALES/ORDER → Say "Main aapko sales team se connect karti hu." → [TRANSFER_SALES]
- FINANCE/CREDIT/LOAN → Say "Main aapko trade finance team se connect karti hu." → [TRANSFER_NBFC]
- General → [TRANSFER_RECEPTIONIST]

# ENDCALL RULE
ENDCALL must be LAST output. No words after it.

# SMART BEHAVIOR
No response 5-10s: "Sir, are you there?" Audio unclear: "Sorry sir, aapki awaaz clear nahi aa rahi. Can you repeat?" Stuck after 3 attempts: "Main aapko humari team se callback karwati hoon." → ENDCALL
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
    if state.get("polymer_needed"):
        parts.append(f"Material: {state['polymer_needed']}")
    if state.get("freight_origin"):
        parts.append(f"Pickup: {state['freight_origin']}")
    if state.get("freight_destination"):
        parts.append(f"Delivery: {state['freight_destination']}")
    if state.get("freight_quantity_mt"):
        parts.append(f"Transport Qty: {state['freight_quantity_mt']} MT")
    if not parts:
        return ""
    return "\n[KNOWN CUSTOMER CONTEXT — DO NOT ask for these again]\n" + "\n".join(parts)


def make_logistics_agent(db=None):
    def logistics_node(state: BSBGraphState) -> dict[str, Any]:
        messages = state.get("messages", [])
        ctx = _build_context(state)
        full_prompt = SYSTEM_PROMPT + ctx

        system_msg = SystemMessage(content=full_prompt)
        response = _llm.invoke([system_msg] + messages)
        reply = response.content

        next_agent = "logistics_agent"
        if "[TRANSFER_SALES]" in reply:
            next_agent = "sales_agent"
            reply = reply.replace("[TRANSFER_SALES]", "").strip()
        elif "[TRANSFER_NBFC]" in reply:
            next_agent = "nbfc_agent"
            reply = reply.replace("[TRANSFER_NBFC]", "").strip()
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
                    logger.error(f"[LogisticsAgent] WhatsApp API failed: {e}")

        should_end = "ENDCALL" in reply
        if should_end:
            reply = reply.replace("ENDCALL", "").strip()

        for tag in ["<|HINDI|>", "<|ENGLISH|>", "<|GUJARATI|>"]:
            reply = reply.replace(tag, "").strip()

        return {
            "messages": [AIMessage(content=reply)],
            "active_agent": "logistics_agent",
            "next_agent": next_agent,
            "should_end": should_end,
        }

    return logistics_node
