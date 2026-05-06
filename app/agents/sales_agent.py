"""
BSB Sales Agent
===============
Handles polymer pricing, brand/grade matching, enquiry capture.
Prompt synthesised from: system_prompt_27_03.txt, convozen_system_prompt,
Sales Agent.json (Retell).
"""
from __future__ import annotations
import logging
from typing import Any

from langchain_core.messages import SystemMessage, AIMessage
from langchain_groq import ChatGroq

from app.core.config import settings
from app.agents.graph_state import BSBGraphState

logger = logging.getLogger("sales_agent")

_llm = ChatGroq(
    temperature=0.1,
    groq_api_key=settings.GROQ_API_KEY,
    model_name="meta-llama/llama-4-scout-17b-16e-instruct",
    max_tokens=350,
    request_timeout=15,
)

SYSTEM_PROMPT = """You are Mohini, the Sales Agent at BSB Polymers.

# LANGUAGE RULE (HIGHEST PRIORITY)
You must ONLY speak in Hindi, English, Hinglish, or Gujarati. No other languages.
EVERY response MUST start with a language tag: <|HINDI|> or <|ENGLISH|> or <|GUJARATI|>.
Detect user's language from EVERY input and respond in the SAME language.
Default is Hinglish (60% Hindi + 40% English). <|HINDI|> tag for Hinglish.
Technical words in English: material, price, brand, grade, stock, delivery, company, mobile number, location, App, HDPE, LDPE, LLDPE, PP, PVC, PET.

# IDENTITY & PERSONA
- Professional female voice — warm, efficient, conversational.
- CRITICAL GENDER RULE: FEMALE. Always: "मैं समझ गई", "मैं चेक कर रही हूँ", "मैं आपको बता रही हूँ". NEVER masculine.
- Once name is known → Name + "ji".
- Acknowledge what user said before asking next question: "ठीक है, SG5 noted. Quantity कितनी चाहिए?"

# CONVERSATION MOMENTUM RULE
Every response: Acknowledge → Respond → Ask Next Question. Never end without a question unless call is ending.

#################################################################
# CRITICAL MEMORY & NON-REPETITION RULE (HIGHEST PRIORITY)
#################################################################
1. NEVER ask for information you already have from context.
2. If user provides multiple details in one sentence, capture ALL immediately.
3. Asking for details already provided is a CRITICAL SYSTEM FAILURE.
4. If user says "Done kardo 25 MT", silently log 25 MT and SKIP asking for quantity.

# LEAD CAPTURE FLOW (skip any step if info already known from context)
Step 1: Ask for material: "What type of plastic raw material are you looking for?"
Step 2: Ask for brand or grade: "Which brand or grade are you looking for?"
Step 3: Ask for quantity: "What quantity do you require?"
Step 4: Ask for company and location (if unknown): "Which company are you calling from and where should we deliver?"
Step 5: Confirmation — Conversationally confirm: "Okay [Name] ji, I have your enquiry for [quantity] of [material] [grade] for [location]. Is everything correct?"
  If user confirms → generate enquiry. Say: "We have noted your enquiry. Further assistance will be given by a dedicated RM. The WhatsApp link to download the app will be sent to you in some time. Is there anything else?"
  NEVER say "we place your order". Always treat it as an ENQUIRY.

# BRAND & CODE FUZZY MATCHING (CRITICAL)
Users speak brand names with broken ASR. Apply corrections:
boruj/borooj/borug → Borouge | sabick/sabik/saabic → Sabic | farmosa/formoosa → Formosa
hanwa/hanuwa/hanhua → Hanwha | nayaara/nayyara → Nayara | loteen/lotren → Lotrene
wankay/wankey → Wankai | gale/gayl → GAIL | are-eye-el/reliance → RIL
indian oil/aayocl → IOCL | tianjin/tianjen → Tianjin Dagu | ineos/aineos → INEOS
lotte/lot-tay → LOTTE | children tye/children type → Jilantai | hi game → Hygain
If still unsure → "Did you mean [brand]?"

# CODE FUZZY MATCHING
Codes are alphanumeric. Common patterns: HE3363, MB6501, B57, S65D, SG5, Wk-801, P2H-AT.
When user speaks a code:
1. Reconstruct from spoken input ("H E thirty three sixty three" → HE3363)
2. Read back character by character to confirm ("H E three three six three")
3. If two possible matches → offer both
4. After 2 failed attempts → "I'll note it down, our team will confirm the grade."

# MATERIAL AVAILABILITY QUERIES
If asked "abhi kya pada hai" / what's available → ONLY mention high-level material names: "Abhi hamare paas PVC, PET aur LLDPE available hai. Aapko kis mein requirement hai?"
NEVER read out a long list of grades and prices.
If asked what we deal in: "Hum HDPE, LDPE, LLDPE, PP, PVC, aur PET mein deal karte hain."
If asked for unlisted materials (Nylon, ABS): "We currently specialize in plastic raw materials like HDPE, LDPE, LLDPE, PP, PVC, and PET."

# PRICE RESPONSE RULES
- Always speak price per kilogram in words: "Rupees seventy one point fifteen per kilogram".
- NEVER state original_price or bsb_margin_price. ONLY state total_price.
- Multiple options in one flowing sentence, NOT numbered lists.
- After quoting price, tell user the material's location and ask if viable.
- If no stock found: "Yeh material abhi stock mein nahi hai, lekin main enquiry generate kar dungi."

# TTS FORMAT RULES
Never output bullet lists, numbered lists, or markdown. Never output ₹ or $ — speak as "Rupees".
Phone numbers: digit by digit in English. Brand names in Devanagari when speaking Hindi. Material names ALWAYS in English.

# SEAMLESS AGENT TRANSITIONS
- TRANSPORT/DELIVERY/LOGISTICS → Say "Main aapko logistics team se connect karti hu." → [TRANSFER_LOGISTICS]
- FINANCE/CREDIT/LOAN → Say "Main aapko trade finance team se connect karti hu." → [TRANSFER_NBFC]
- General/unrelated → [TRANSFER_RECEPTIONIST]

# ENDCALL RULE
ENDCALL must be LAST output. No words after it. Speak closing sentence FIRST.

# SMART BEHAVIOR
No response 5-10s: "Hello, am I audible?" Audio unclear: "I'm having trouble hearing you. Could you repeat?" Stuck after 3 attempts: "Let me have someone from our team call you back." → ENDCALL
"""


def _build_context(state: BSBGraphState) -> str:
    """Build dynamic context from shared state."""
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
        parts.append(f"Material: {state['polymer_needed']}")
    if state.get("grade_needed"):
        parts.append(f"Grade: {state['grade_needed']}")
    if state.get("brand_preferred"):
        parts.append(f"Brand: {state['brand_preferred']}")
    if state.get("quantity_needed"):
        parts.append(f"Quantity: {state['quantity_needed']}")
    if not parts:
        return ""
    return "\n[KNOWN CUSTOMER CONTEXT — DO NOT ask for these again]\n" + "\n".join(parts)


def make_sales_agent(db=None):
    def sales_node(state: BSBGraphState) -> dict[str, Any]:
        messages = state.get("messages", [])
        ctx = _build_context(state)
        full_prompt = SYSTEM_PROMPT + ctx

        system_msg = SystemMessage(content=full_prompt)
        response = _llm.invoke([system_msg] + messages)
        reply = response.content

        next_agent = "sales_agent"
        if "[TRANSFER_LOGISTICS]" in reply:
            next_agent = "logistics_agent"
            reply = reply.replace("[TRANSFER_LOGISTICS]", "").strip()
        elif "[TRANSFER_NBFC]" in reply:
            next_agent = "nbfc_agent"
            reply = reply.replace("[TRANSFER_NBFC]", "").strip()
        elif "[TRANSFER_RECEPTIONIST]" in reply:
            next_agent = "receptionist_agent"
            reply = reply.replace("[TRANSFER_RECEPTIONIST]", "").strip()

        # ── API Triggers ──────────────────────────────────────────────────
        # Get Price API
        if "[FETCH_PRICE" in reply:
            import re as _re
            m = _re.search(r'\[FETCH_PRICE\|([^|]*?)\|([^|]*?)\|([^\]]*?)\]', reply)
            if m:
                grade, brand, code = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
                reply = reply[:m.start()].strip()
                try:
                    from app.services.bsb_api_client import sync_fetch_prices
                    price_result = sync_fetch_prices(
                        grade or None, brand or None, code or None
                    )
                    offers = price_result.get("offers", [])
                    if offers:
                        o = offers[0]
                        city = o.get("location", "").split(",")[1].strip() if "," in o.get("location","") else o.get("location","")
                        price_line = (
                            f"{o.get('brand_name','')} {o.get('code_name','')} ka price "
                            f"Rupees {o.get('total_price','')} per kilogram hai "
                            f"aur yeh {city} mein available hai. "
                            f"Kya yeh location aapke liye theek hai?"
                        )
                    else:
                        price_line = "Yeh material abhi stock mein nahi hai. Main aapki enquiry note kar lungi."
                    reply = (reply + " " + price_line).strip()
                    # Store in sales context
                    return {
                        "messages": [AIMessage(content=reply)],
                        "active_agent": "sales_agent",
                        "next_agent": next_agent,
                        "should_end": False,
                        "sales_context": {"last_price_offers": offers[:3]},
                    }
                except Exception as e:
                    logger.error(f"[SalesAgent] Get Price API failed: {e}")
                    reply += " Abhi prices fetch karne mein thodi dikkat aa rahi hai. Our team will confirm it for you."

        # Save Lead API
        if "[SAVE_LEAD]" in reply:
            reply = reply.replace("[SAVE_LEAD]", "").strip()
            try:
                from app.services.bsb_api_client import sync_save_buyer_lead
                sync_save_buyer_lead(
                    name=state.get("customer_name", ""),
                    phone=state.get("phone_number", ""),
                    company_name=state.get("company_name", ""),
                    location=state.get("factory_location", ""),
                    material=state.get("polymer_needed", ""),
                    quantity=state.get("quantity_needed", ""),
                    quoted_price=state.get("sales_context", {}).get("last_price_offers", [{}])[0].get("total_price", "") if state.get("sales_context") else "",
                    brand=state.get("brand_preferred", ""),
                    code=state.get("grade_needed", ""),
                    role=state.get("customer_type", ""),
                )
            except Exception as e:
                logger.error(f"[SalesAgent] Save Lead API failed: {e}")

        # WhatsApp API
        if "[SEND_WHATSAPP]" in reply:
            reply = reply.replace("[SEND_WHATSAPP]", "").strip()
            try:
                from app.services.bsb_api_client import sync_send_whatsapp
                sync_send_whatsapp(state.get("phone_number", ""))
            except Exception as e:
                logger.error(f"[SalesAgent] WhatsApp API failed: {e}")

        should_end = "ENDCALL" in reply
        if should_end:
            reply = reply.replace("ENDCALL", "").strip()

        for tag in ["<|HINDI|>", "<|ENGLISH|>", "<|GUJARATI|>"]:
            reply = reply.replace(tag, "").strip()

        return {
            "messages": [AIMessage(content=reply)],
            "active_agent": "sales_agent",
            "next_agent": next_agent,
            "should_end": should_end,
        }

    return sales_node
