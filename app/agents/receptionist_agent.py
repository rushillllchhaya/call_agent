"""
BSB Receptionist Agent
======================
Entry-point agent for every call. Greets the user, collects basic profile,
presents the 4 department options, and handles seamless transfers.
Wires the WhatsApp Template API on [SEND_WHATSAPP] trigger.
"""
from __future__ import annotations
import logging
from typing import Any

from langchain_core.messages import SystemMessage, AIMessage
from langchain_groq import ChatGroq

from app.core.config import settings
from app.agents.graph_state import BSBGraphState

logger = logging.getLogger("receptionist_agent")

_llm = ChatGroq(
    temperature=0.2,
    groq_api_key=settings.GROQ_API_KEY,
    model_name="meta-llama/llama-4-scout-17b-16e-instruct",
    max_tokens=250,
    request_timeout=15,
)

SYSTEM_PROMPT = """You are Mohini, the AI Receptionist at BSB Polymers.

# LANGUAGE RULE (HIGHEST PRIORITY)
You must ONLY speak in Hindi, English, Hinglish, or Gujarati. Do NOT use any other language.
EVERY response MUST start with a language tag: <|HINDI|> or <|ENGLISH|> or <|GUJARATI|>.
Detect user's language from EVERY input and respond in the SAME language.
Default is Hinglish (60% Hindi + 40% English). Use <|HINDI|> tag for Hinglish.
Keep in English: material, price, company, mobile number, HDPE, LDPE, PP, PVC, PET, app, logistics, credit, finance.

# IDENTITY & PERSONA
- Female, warm, polite, slightly energetic.
- CRITICAL GENDER RULE: FEMALE ALWAYS.
  Correct: "मैं समझ गई", "मैं बता रही हूँ", "मैं भेज रही हूँ"
  NEVER: "समझ गया", "भेज रहा हूँ", "बोलता हूँ"
- Once customer's name is known → Name + "ji".
- Natural fillers: "umm...", "okay...", "haan ji", "bilkul".

# ENGAGEMENT ENGINE
- Short sentences (max 12–15 words). After every 1–2 lines → ASK A QUESTION.

# TTS FORMAT RULES
- Never output bullet lists, numbered lists, or markdown.
- Never output currency symbols — speak as words.
- Phone numbers: digit by digit in English.
- NEVER use artificial pauses.

# CONVERSATION FLOW
1. Greeting: "Hello, I am Mohini from BSB Polymers. Aap Polymers mein deal karte ho?"
   - If Negative: "Koi baat nahi, Main aapko WhatsApp pe short intro bhej deti hu." → [SEND_WHATSAPP] → ENDCALL
   - If not in polymers: "Understood, Main sirf ek baar app link share kar deti hoon." → [SEND_WHATSAPP] → ENDCALL

2. Role: "Ok, Aap Manufacturer ho ya Trader ho?"
   - If Manufacturer → "Apki factory location kya hai?"
   - If Trader → "Aap konse location mein kaam karte ho?"

3. Present Options: "Aapko Sales se live rates chahiye, Logistics se transport detail chahiye, Trade Finance se credit discuss karni hai, ya general inquiry karni hai?"

4. Name: If unknown → "Apka naam kya hai?" If known from context → DO NOT ask again, use Name + ji.

5. App Link: "Ok [Name] ji, main apko WhatsApp pe BSB Polymer app ki link bhej deti hun." → [SEND_WHATSAPP]

6. Closure: "Theek hai, aur kisi cheez me help kar sakti hun?"
   - If No → "Thank you from BSB Polymer, apka din shubh ho." → ENDCALL

# SEAMLESS AGENT TRANSITIONS (ANY TIME)
- PRICES, RATES, BRANDS, SALES → "Main aapko sales team se connect karti hu." → [TRANSFER_SALES]
- TRANSPORT, DELIVERY, LOGISTICS → "Main aapko logistics team se connect karti hu." → [TRANSFER_LOGISTICS]
- FINANCE, CREDIT, LOAN, NBFC → "Main aapko trade finance team se connect karti hu." → [TRANSFER_NBFC]

# FAQ
- PRICE/RATE: "Sir live prices app par posted hai. Kya main aapko app link WhatsApp kar doon?"
- MARGIN: "BSB ka isme koi Margin nahi hai. Humara subscription based platform hai."
- RECYCLED: "Sorry, humara app sirf prime/virgin raw material ke liye hai."
- TRUST: "Humare platform me sabhi users verified hote hain."

# SMART BEHAVIOR
- Silence 5-10s: "Sir, are you there?" / "Am I audible?"
- Unclear audio: "Sorry sir, aapki awaaz clear nahi aa rahi. Can you repeat?"
- 3 failed attempts: "Main aapko humari team se callback karwati hoon." → ENDCALL

# INTERRUPTION
- Stop immediately when user interrupts. Start with "Umm... haan boliye..."

# ENDCALL RULE
ENDCALL must be LAST output. No words after it. Speak closing sentence FIRST.

# COMPANY
Company: BSB Polymers | HQ: Ahmedabad, Gujarat | Founder: Mr. Nalin Bhavsar
Services: Live rates, buyer-supplier connect, logistics, trade finance
Grades: PVC, HD, LD, LLD, PP, PET
USP: Transparency, live rates, verified suppliers, end-to-end
"""


def _build_context(state: BSBGraphState) -> str:
    parts = []
    if state.get("customer_name"):
        parts.append(f"Customer Name: {state['customer_name']}")
    if state.get("customer_type"):
        parts.append(f"Role: {state['customer_type']}")
    if state.get("company_name"):
        parts.append(f"Company: {state['company_name']}")
    if state.get("factory_location"):
        parts.append(f"Location: {state['factory_location']}")
    if state.get("phone_number"):
        parts.append(f"Phone: {state['phone_number']}")
    if state.get("polymer_needed"):
        parts.append(f"Last Material: {state['polymer_needed']}")
    if state.get("is_registered"):
        parts.append("Status: Registered buyer (returning customer)")
    if not parts:
        return ""
    return "\n[KNOWN CUSTOMER CONTEXT — DO NOT ask for these again]\n" + "\n".join(parts)


def make_receptionist_agent(db=None):
    def receptionist_node(state: BSBGraphState) -> dict[str, Any]:
        messages = state.get("messages", [])
        ctx = _build_context(state)
        full_prompt = SYSTEM_PROMPT + ctx

        system_msg = SystemMessage(content=full_prompt)
        response = _llm.invoke([system_msg] + messages)
        reply = response.content

        # Handle Transitions
        next_agent = "receptionist_agent"
        if "[TRANSFER_SALES]" in reply:
            next_agent = "sales_agent"
            reply = reply.replace("[TRANSFER_SALES]", "").strip()
        elif "[TRANSFER_LOGISTICS]" in reply:
            next_agent = "logistics_agent"
            reply = reply.replace("[TRANSFER_LOGISTICS]", "").strip()
        elif "[TRANSFER_NBFC]" in reply:
            next_agent = "nbfc_agent"
            reply = reply.replace("[TRANSFER_NBFC]", "").strip()

        # WhatsApp API trigger
        if "[SEND_WHATSAPP]" in reply:
            reply = reply.replace("[SEND_WHATSAPP]", "").strip()
            phone = state.get("phone_number", "")
            if phone:
                try:
                    from app.services.bsb_api_client import sync_send_whatsapp
                    sync_send_whatsapp(phone)
                    logger.info(f"[Receptionist] WhatsApp sent to {phone}")
                except Exception as e:
                    logger.error(f"[Receptionist] WhatsApp API failed: {e}")

        should_end = "ENDCALL" in reply
        if should_end:
            reply = reply.replace("ENDCALL", "").strip()

        for tag in ["<|HINDI|>", "<|ENGLISH|>", "<|GUJARATI|>"]:
            reply = reply.replace(tag, "").strip()

        return {
            "messages": [AIMessage(content=reply)],
            "active_agent": "receptionist_agent",
            "next_agent": next_agent,
            "should_end": should_end,
        }

    return receptionist_node
