from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser
from app.core.config import settings
from app.services.state import ConversationState
from typing import AsyncGenerator
import json
import re
import asyncio
import logging

# Setup logger
logger = logging.getLogger("ai_agent")
logger.setLevel(logging.INFO)

# ─── LLM Client ──────────────────────────────────────────────────────────────
llm = ChatGroq(
    temperature=0.15,
    groq_api_key=settings.GROQ_API_KEY,
    model_name="llama-3.1-8b-instant",  # Higher rate limits (TPM) than 70B
    max_tokens=600,
    model_kwargs={"response_format": {"type": "json_object"}}, # Force JSON output
)

# ─── System Prompt ────────────────────────────────────────────────────────────
SYSTEM_TEMPLATE = """You are Mohini(women), a warm and professional sales representative for BSB Polymer — India's tech-driven polymer distribution platform based in Ahmedabad.

WHO IS BSB POLYMER (use ONLY these facts):
- Full name: BSB Polymer
- Type: Tech-Driven Distribution Platform for polymers
- Offerings: All major polymer grades (PVC, HDPE, LDPE, LLDPE, PP, PET, etc.)
- Additional services: Logistics facility, Credit facility
- App: BSB Polymer App — customers register on the app, after which a Dedicated RM is assigned
- After app download: dedicated Relationship Manager (RM) will be allotted to handle pricing and orders

LIVE CATALOG (grades from our inventory):
{catalog_context}

CURRENT CALL CONTEXT:
Stage: {stage}
Customer info collected so far: {customer_info}
{system_instruction}

YOUR PERSONA — MOHINI:
- You are a **Senior Relationship Manager (RM)**. You do not just answer; you LEAD the conversation.
- Speak in natural, warm Hinglish (mix of Hindi/Gujarati and English, like a real senior Indian sales professional).
- SCRIPT RULE: ALWAYS use Devanagari script for Hindi and Gujarati script for Gujarati. NEVER use Roman script (English letters) for Indian words (e.g., Use "नमस्ते" instead of "नमस्ते"). Only English technical terms like "App", "Link", "RM" should stay in English script.
- Always address the customer by their name + "ji" (e.g. "Rushil ji") once you know it.
- **CONTEXT RULE**: Review "Customer info collected so far" before every turn. NEVER ask for information that is already present (e.g., if "customer_name" is "Rushil", do NOT ask for their name).
- DIALOGUE MOMENTUM RULE: Never just answer or ask. ALWAYS acknowledge/repeat what the user shared first (e.g., "Theek hai ji, SG-5 noted", "25 MT noted") and THEN immediately ask the next targeted question. This keeps the customer engaged and the conversation moving forward.
- ATTENTION: ALWAYS end every single response with a short, engaging question to guide the user to the next stage of the conversation.

CONVERSATION FLOW YOU MUST MANAGE:
1. greeting       → Initial intro done. Wait for user to respond.
2. name_collection → If name is null, collect it. If name is already known, skip this entirely.
3. pitch          → Give the BSB pitch. Then ask: "Aap Manufacturer ho ya Trader?"
4. qualification  → Drive the discovery: Product made → Polymer type → Material Grade → **Quantity needed** → Factory location.
   - If they ask for price, use the PRICING DATA provided in the context to give an estimate and immediately pivot back to asking for the next missing info (e.g., "Quantity kitni chahiye aapko?").
5. app_offer      → Pitch the BSB Polymer App benefits.
6. whatsapp_confirm → Confirm mobile number and closing.
7. completed      → Warm thank-you. Mention RM contact.

POLYMER NAME RULE — ABSOLUTE:
- If you need to clarify what a polymer is, say it in simple Indian trade terms (e.g. "HDPE pipe wala material"), not chemical names.

STRICT STAGE RULE:
- NEVER skip the Qualification stage. You MUST collect: Product → Polymer → Grade → Quantity → Location.
- DO NOT pitch the BSB Polymer App until you have at least the Polymer, Grade, and Quantity. If the user asks for price, give it (if in catalog) but immediately ask for the missing info (e.g. "Theek hai, 25 MT noted. Aapka factory location kya hai?").

BUSINESS RULES:
- Manufacturer = Buyer (they buy polymer to make products)
- Trader = Seller (they trade/resell polymer)
- PRICING RULE: You CAN mention the "approximate live price" if it is available in the LIVE CATALOG above. Always clarify that this is a "dispatch price" and the final landed price will be shared by their RM on the App.
- If the user asks for a price that is NOT in the catalog, say: "Ji, exact price aapke location ke liye hum check karke batayenge — pehle app download kar lete hain."
- Do NOT discuss payment or credit terms in detail — just mention they are available.
- Keep responses under 60 words, warm and natural

STRICT JSON OUTPUT RULES:
1. Reply ONLY with valid JSON. No text outside the JSON object.
2. **AI_RESPONSE IS FIRST**: The "ai_response" key MUST be the very first key in your JSON.
3. **MANDATORY CONTENT**: "ai_response" MUST NEVER be empty. Even if you are just acknowledging, give a warm Hinglish response and ask the next missing discovery question.
4. Always include all keys (use null for unknown).
5. Use the conversation history to never re-ask something already answered.
6. If the user mentions a BRAND NAME (e.g. Reliance, GAIL, Sigfied, IOCL, Chemplast, Finolex, Indorama),
   capture it in "brand_preferred". The system will automatically resolve the grade from the brand — you
   do NOT need to re-ask for the grade if a brand was given.

7. **CALL_COMPLETED**: Set "call_completed": true ONLY when you have given a final goodbye/closing note (e.g. "Dhanyavad", "RM contact karenge"). This will hang up the call.

JSON format:
{{"ai_response":"...","call_completed":false,"customer_name":null,"customer_type":null,"product_made":null,"polymer_needed":null,"grade_needed":null,"quantity_needed":null,"brand_preferred":null,"factory_location":null,"intent":null,"advance_stage":false}}

- "advance_stage": true means you have collected what's needed for the current stage and are ready to move on
- "intent": set to "buy" if manufacturer, "sell" if trader, null if unknown
- "brand_preferred": the brand name spoken by the user (Reliance, GAIL, Sigfied, etc.), null if not mentioned
"""

unified_prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_TEMPLATE),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{input}"),
])

unified_chain = unified_prompt | llm | StrOutputParser()

_JSON_RE = re.compile(r'\{.*?\}', re.DOTALL)

# Sentence-boundary characters for streaming TTS chunking
_SENTENCE_END_RE = re.compile(r'[.?!।]')

# Matches JSON key-value tail that leaks after the ai_response string closing quote
# e.g.  ", "advance_stage": false}"  or  "\"advance_stage\": false}"
_JSON_TAIL_RE = re.compile(r'["\\]*[,}].*$', re.DOTALL)

# ─── Helpers ─────────────────────────────────────────────────────────────────

def parse_ai_result_from_raw(raw_output: str) -> dict:
    """
    Parse the JSON dict from a fully-assembled LLM output string.
    Used after streaming completes to extract state-update fields.
    Includes a 'Fragment Parser' to salvage text from partial JSON.
    """
    stripped = raw_output.strip()
    
    # 1. Try standard JSON parsing
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    match = _JSON_RE.search(raw_output)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # 2. Fragment Parser: Salvage ai_response from partial JSON
    # Looks for "ai_response": "..." and extracts the content even if closing brace is missing
    response_match = re.search(r'"ai_response":\s*"(.*?)(?:"|$)', raw_output, re.DOTALL)
    salvaged_text = response_match.group(1).strip() if response_match else ""
    
    if salvaged_text:
        # Success! Salvaged some text from the wreckage
        return {
            "ai_response":     salvaged_text,
            "call_completed":  False,
            "customer_name":   None, "customer_type": None,
            "product_made":    None, "polymer_needed": None,
            "grade_needed":    None, "quantity_needed": None,
            "brand_preferred": None, "intent": None, "advance_stage": False,
        }

    # 3. Last Resort: Clean and return everything
    print(f"AI JSON parse failed (stream): {raw_output[:200]}")
    clean = raw_output.replace("```json", "").replace("```", "").strip()
    return {
        "ai_response":     clean[:400] if len(clean) > 400 else clean,
        "call_completed":  False,
        "customer_name":   None, "customer_type": None,
        "product_made":    None, "polymer_needed": None,
        "grade_needed":    None, "quantity_needed": None,
        "brand_preferred": None, "intent": None, "advance_stage": False,
    }


async def stream_ai_response_sentences(
    user_input: str,
    state: ConversationState,
    system_instruction: str = "",
    catalog_context: str = "No live catalog data available.",
) -> AsyncGenerator[tuple[str, dict | None], None]:
    """
    Async generator that:
      1. Streams LLM tokens in real time.
      2. Accumulates tokens until a sentence boundary (. ? ! ।) is found
         INSIDE the "ai_response" JSON value.
      3. Yields (sentence_text, None) for each sentence chunk as soon as it's ready.
      4. After the stream ends, yields ("", result_dict) with the full parsed JSON
         so the caller can update conversation state.

    Usage in streams.py:
        async for sentence, final_result in stream_ai_response_sentences(...):
            if final_result is not None:
                # use final_result to advance stage / update state
            else:
                # sentence is ready → fire TTS immediately
    """
    customer_info_str = str(state.customer.dict())

    # Only send the last 10 messages (5 turns) — structured customer_info carries
    # all collected data, so deep history adds tokens without real benefit.
    recent_history = state.history[-10:]
    lc_history = []
    for msg in recent_history:
        if msg["role"] == "user":
            lc_history.append(HumanMessage(content=msg["content"]))
        else:
            lc_history.append(AIMessage(content=msg["content"]))

    invoke_input = {
        "input":              user_input,
        "stage":              state.stage.value,
        "customer_info":      customer_info_str,
        "system_instruction": f"INSTRUCTION: {system_instruction}" if system_instruction else "",
        "catalog_context":    catalog_context,
        "history":            lc_history,
    }

    full_raw = ""          # accumulates the entire LLM output
    in_ai_response = False  # True once we've entered the ai_response value string
    ai_response_done = False # True once the closing quote of ai_response value is seen
    sentence_buf = ""       # builds up current sentence candidate

    # 4.5s Timeout for initial token or slow stream
    LLM_TIMEOUT = 4.5

    try:
        # Wrap iterator in wait_for
        stream_iter = unified_chain.astream(invoke_input)
        
        while True:
            try:
                token = await asyncio.wait_for(stream_iter.__anext__(), timeout=LLM_TIMEOUT)
                full_raw += token
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                logger.error(f"LLM Stream Timeout after {LLM_TIMEOUT}s. Cutting loss.")
                break

            # ── Locate the ai_response value inside the streaming JSON ──────
            # We scan for the opening of the ai_response string value.
            # Once inside it, we collect chars until a sentence-end delimiter.

            if not in_ai_response:
                # Check if the accumulated stream has crossed into the value
                marker = '"ai_response":'
                idx = full_raw.find(marker)
                if idx != -1:
                    val_start = full_raw.find('"', idx + len(marker))
                    if val_start != -1:
                        in_ai_response = True
                        # Everything after the opening quote belongs to ai_response
                        sentence_buf = full_raw[val_start + 1:]
                elif len(full_raw) > 30 and '{' not in full_raw:
                    # HEURISTIC: AI is giving plain text. Yield immediately.
                    in_ai_response = True
                    sentence_buf = full_raw
            elif not ai_response_done:
                sentence_buf += token

            if in_ai_response and not ai_response_done:
                # Detect the closing quote of the ai_response string value.
                # An unescaped " signals the end of the JSON string.
                close_q = -1
                i = 0
                while i < len(sentence_buf):
                    if sentence_buf[i] == '\\':   # skip escaped chars
                        i += 2
                        continue
                    if sentence_buf[i] == '"':
                        close_q = i
                        break
                    i += 1

                if close_q != -1:
                    # Truncate: only text BEFORE the closing quote is real speech
                    sentence_buf = sentence_buf[:close_q]
                    ai_response_done = True

                # Check for sentence-ending punctuation
                while True:
                    m = _SENTENCE_END_RE.search(sentence_buf)
                    if not m:
                        break
                    end_pos = m.end()
                    candidate = sentence_buf[:end_pos].strip()

                    # Safety: yield ONLY if it's actual speech (contains letters or unicode)
                    if candidate and any(c.isalnum() or ord(c) > 127 for c in candidate):
                        yield candidate, None

                    sentence_buf = sentence_buf[end_pos:]

        # ── Stream finished — flush any leftover fragment ──────────────────
        if sentence_buf:
            # Safety: cut off anything from a closing quote onward
            close_q = sentence_buf.find('"')
            if close_q != -1:
                sentence_buf = sentence_buf[:close_q]
            leftover = sentence_buf.strip()
            
            if leftover and not ai_response_done:
                 # Only yield if it's actual speech content (contains alpha/unicode)
                 if any(c.isalnum() or ord(c) > 127 for c in leftover):
                    yield leftover, None

        # ── Yield the final parsed result dict so state can be updated ────
        result = parse_ai_result_from_raw(full_raw)
        yield "", result

    except Exception as e:
        print(f"AI Streaming Error: {e}")
        yield "Ji, thoda sa repeat karein please?", None
        yield "", {
            "ai_response":     "Ji, thoda sa repeat karein please?",
            "customer_name":   None, "customer_type": None,
            "product_made":    None, "polymer_needed": None,
            "grade_needed":    None, "quantity_needed": None, "brand_preferred": None,
            "factory_location": None, "intent": None, "advance_stage": False,
        }


# ─── Main Function (blocking / non-streaming) ─────────────────────────────────

def process_conversation_step(
    user_input: str,
    state: ConversationState,
    system_instruction: str = "",
    catalog_context: str = "No live catalog data available.",
) -> dict:
    """
    Single entry point for AI processing. Passes full call history for memory.
    Returns dict with customer_name, customer_type, product_made, polymer_needed,
    grade_needed, factory_location, intent, ai_response, advance_stage.
    """
    customer_info_str = str(state.customer.dict())

    # Only send the last 10 messages (5 turns) — structured customer_info carries
    # all collected data, so deep history adds tokens without real benefit.
    recent_history = state.history[-10:]
    lc_history = []
    for msg in recent_history:
        if msg["role"] == "user":
            lc_history.append(HumanMessage(content=msg["content"]))
        else:
            lc_history.append(AIMessage(content=msg["content"]))

    try:
        raw_output = unified_chain.invoke({
            "input":            user_input,
            "stage":            state.stage.value,
            "customer_info":    customer_info_str,
            "system_instruction": f"INSTRUCTION: {system_instruction}" if system_instruction else "",
            "catalog_context":  catalog_context,
            "history":          lc_history,
        })

        stripped = raw_output.strip()
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass

        match = _JSON_RE.search(raw_output)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        print(f"AI JSON parse failed: {raw_output[:200]}")
        clean = raw_output.replace("```json", "").replace("```", "").strip()
        if len(clean) > 400:
            clean = clean[:400].rsplit(" ", 1)[0] + "."

        return {
            "ai_response":     clean,
            "customer_name":   None, "customer_type": None,
            "product_made":    None, "polymer_needed": None,
            "grade_needed":    None, "factory_location": None,
            "intent":          None, "advance_stage": False,
        }

    except Exception as e:
        print(f"AI Processing Error: {e}")
        return {
            "ai_response":     "Ji, thoda sa repeat karein please?",
            "customer_name":   None, "customer_type": None,
            "product_made":    None, "polymer_needed": None,
            "grade_needed":    None, "quantity_needed": None, "brand_preferred": None,
            "factory_location": None,
            "intent":          None, "advance_stage": False,
        }


# ─── Legacy Adapters (kept for compatibility) ─────────────────────────────────
def extract_deal_details(text: str, context: str = "") -> dict:
    return {}

def generate_ai_response(text: str, state: ConversationState, instr: str) -> str:
    return ""
