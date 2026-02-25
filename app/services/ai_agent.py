from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser
from app.core.config import settings
from app.services.state import ConversationState
from app.services.semantic_cache import semantic_cache
from typing import AsyncGenerator, List, Dict
import json
import re
import asyncio
import logging

# Setup logger
logger = logging.getLogger("ai_agent")
logger.setLevel(logging.INFO)

# Semaphore: at most 1 embedding thread at a time (prevents thread-pool starvation
# when concurrent calls each try to run sentence-transformers on CPU)
_EMBED_SEMAPHORE = asyncio.Semaphore(1)

# ─── LLM Client ──────────────────────────────────────────────────────────────
llm = ChatGroq(
    temperature=0.15,
    groq_api_key=settings.GROQ_API_KEY,
    model_name="llama-3.1-8b-instant",  # Higher rate limits (TPM) than 70B
    max_tokens=600,
    model_kwargs={"response_format": {"type": "json_object"}}, # Force JSON output
)

# ─── LLM for summarisation (no JSON mode needed) ─────────────────────────────
_summary_llm = ChatGroq(
    temperature=0,
    groq_api_key=settings.GROQ_API_KEY,
    model_name="llama-3.1-8b-instant",
    max_tokens=120,
)


async def warmup_llm():
    """
    Fire a lightweight ping to Groq so the connection/model is warm
    by the time the user finishes speaking after the greeting.
    Called as a fire-and-forget background task during call start.
    """
    try:
        logger.info("[WARMUP] Firing LLM warm-up ping...")
        await asyncio.wait_for(
            llm.ainvoke([HumanMessage(content="ping")]),
            timeout=4.0
        )
        logger.info("[WARMUP] LLM warm-up complete.")
    except Exception as e:
        # Must NEVER break the call — complete silence on failure
        logger.debug(f"[WARMUP] Warm-up skipped or timed out: {e}")


async def _get_summarized_history(
    history: List[Dict[str, str]]
) -> List:
    """
    Rolling context window with summarisation.

    If conversation has ≤ 10 messages (5 turns): returns them verbatim (same as before).
    If > 10 messages: summarises the OLDEST messages into 1-2 sentences and
    prepends that as a memory note, then appends the last 10 messages verbatim.

    Falls back to raw window on any error or timeout.
    """
    RECENT_N = 10  # Always keep last 10 messages verbatim

    if len(history) <= RECENT_N:
        # Short conversation — no summarisation needed, identical to old behaviour
        lc_history = []
        for msg in history:
            if msg["role"] == "user":
                lc_history.append(HumanMessage(content=msg["content"]))
            else:
                lc_history.append(AIMessage(content=msg["content"]))
        return lc_history

    # Split: old turns to summarise + recent turns to keep verbatim
    old_messages  = history[:-RECENT_N]
    recent_msgs   = history[-RECENT_N:]

    # Build a compact transcript of the old turns for the summariser
    transcript_lines = []
    for msg in old_messages:
        prefix = "User" if msg["role"] == "user" else "Agent"
        transcript_lines.append(f"{prefix}: {msg['content']}")
    transcript = "\n".join(transcript_lines)

    summary_text = None
    try:
        summary_prompt = (
            "Summarise the following voice-call conversation in 1-2 short sentences. "
            "Focus on: customer's name, what they need, what was already discussed. "
            "Be concise.\n\n" + transcript
        )
        summary_response = await asyncio.wait_for(
            _summary_llm.ainvoke([HumanMessage(content=summary_prompt)]),
            timeout=2.5
        )
        summary_text = getattr(summary_response, "content", str(summary_response)).strip()
        logger.info(f"[HISTORY] Summarised {len(old_messages)} older messages: '{summary_text[:80]}'")
    except Exception as e:
        logger.warning(f"[HISTORY] Summarisation failed ({e}), falling back to raw window.")

    lc_history = []
    if summary_text:
        lc_history.append(
            HumanMessage(content=f"[Earlier conversation summary]: {summary_text}")
        )

    for msg in recent_msgs:
        if msg["role"] == "user":
            lc_history.append(HumanMessage(content=msg["content"]))
        else:
            lc_history.append(AIMessage(content=msg["content"]))

    return lc_history


async def _build_dynamic_context(
    history: List[Dict[str, str]],
    current_query: str,
    top_k: int = 3,
) -> List:
    """
    Dynamic Prompt Trimming — RAG over conversation history.

    Builds a tiny in-memory FAISS index of ALL past turns, then retrieves
    the `top_k` most semantically relevant turns to the current query.
    Always keeps the last 2 turns verbatim (most recent context).

    Result order: [retrieved relevant turns in original order] + [last 2 turns]

    Falls back to raw last-10 window if embedder is unavailable or times out.

    Why this beats a fixed rolling window:
    - If the user asks about price mid-call, we retrieve the earlier turn where
      they mentioned their grade/brand, even if it was 8 turns ago.
    - If a user brings up something new, old irrelevant turns are not included.
    """
    if len(history) <= 4:  # Too short to benefit — return as-is
        return [
            HumanMessage(content=m["content"]) if m["role"] == "user"
            else AIMessage(content=m["content"])
            for m in history
        ]

    loop = asyncio.get_running_loop()

    def _retrieve():
        try:
            from app.services.semantic_cache import _get_embedder
            import numpy as np
            import faiss

            embedder = _get_embedder()
            if embedder is None:
                return None  # fall back to rolling window

            # Embed each turn's content as «ROLE: content» so user/agent are distinguishable
            texts = [
                f"{m['role'].upper()}: {m['content']}"
                for m in history
            ]
            all_vecs = embedder.encode(
                texts, convert_to_numpy=True, normalize_embeddings=True
            ).astype("float32")

            # Build a tiny temporary FAISS index just for this call
            dim = all_vecs.shape[1]
            tmp_index = faiss.IndexFlatIP(dim)
            tmp_index.add(all_vecs)

            # Embed the current query
            q_vec = embedder.encode(
                [current_query], convert_to_numpy=True, normalize_embeddings=True
            ).astype("float32")

            k = min(top_k, len(history))
            scores, indices = tmp_index.search(q_vec, k)

            # Always keep last 2 turns — exclude them from retrieved so no duplicates
            always_keep = set(range(len(history) - 2, len(history)))
            retrieved_indices = sorted(
                {int(i) for i in indices[0] if i >= 0} - always_keep
            )

            logger.info(
                f"[DYNCTX] Query: '{current_query[:40]}' → "
                f"Retrieved turns: {retrieved_indices} | "
                f"Scores: {[round(float(scores[0][j]), 3) for j in range(k)]}"
            )
            return retrieved_indices

        except Exception as e:
            logger.warning(f"[DYNCTX] Retrieval failed ({e}), falling back.")
            return None

    retrieved_indices = await loop.run_in_executor(None, _retrieve)

    if retrieved_indices is None:
        # Fallback: simple last-10 window
        return [
            HumanMessage(content=m["content"]) if m["role"] == "user"
            else AIMessage(content=m["content"])
            for m in history[-10:]
        ]

    # Build final message list: retrieved relevant turns + last 2 verbatim
    selected_indices = list(retrieved_indices) + list(range(len(history) - 2, len(history)))
    seen = set()
    ordered = []
    for idx in selected_indices:
        if idx not in seen:
            seen.add(idx)
            ordered.append(idx)
    ordered.sort()  # preserve chronological order

    lc_history = []
    for idx in ordered:
        msg = history[idx]
        if msg["role"] == "user":
            lc_history.append(HumanMessage(content=msg["content"]))
        else:
            lc_history.append(AIMessage(content=msg["content"]))

    return lc_history


# ─── System Prompt ──────────────────────────────────────────────────────────
SYSTEM_TEMPLATE = """
You are Mohini, a warm and professional women who is a Senior Relationship Manager for BSB Polymer — India's tech-driven polymer distribution platform which is located in Ahmedabad, Gujarat.

WHO IS BSB POLYMER (use ONLY these facts):
- Full name: BSB Polymer.
- Founder & Backbone: Mr. Nalin Bhavsar. Managing the entire ecosystem.
- Scale: 200+ prime polymer grades (PVC, HDPE, LDPE, LLDPE, PP, PET, etc.).
- Services: Polymer Buying/Selling, Logistics, Finance/Credit, Global Sourcing, Customs Clearing, Warehousing, Freight Forwarding.
- Main Objective: To make distribution smooth and give buyers a "tension-free" experience. Premium service at competitive prices.
- Differentiator: Unlike domestic traders, we handle the whole supply chain from global sourcing to delivery.
- App: BSB Polymer App — customers register on the app, after which a Dedicated RM is assigned
- After app download: dedicated Relationship Manager (RM) will be allotted to handle pricing and orders

LIVE CATALOG (grades from our inventory):
{catalog_context}

CURRENT CALL CONTEXT:
Stage: {stage}
Customer info collected so far: {customer_info}
{system_instruction}

POLICIES & Q&A KNOWLEDGE:
- Head Office: Ahmedabad, Gujarat.
- App: BSB Polymer App (Free). Register to get a Dedicated Relationship Manager (RM).
- Elite Buyer Club: Premium program with priority service and rate alerts.
- Trade ERP: System for procurement/logistic/plant heads to coordinate.
- Supply Partner: Download "BSB Polymer Partner App" or "Book Demo" on website.
- Market Presence: Mundra and Nhava Sheva warehouses.
- Logistics: We provide logistics services separately (Mundra to Ahmedabad, etc.).
- Founder/Backbone: Mr. Nalin Bhavsar.

YOUR PERSONA — MOHINI:
- You are a **Senior Relationship Manager (RM)**. You do not just answer; you LEAD the conversation.
- Speak in natural, warm Hinglish (mix of Hindi/Gujarati and English, like a real senior Indian sales professional).
- SCRIPT RULE: ALWAYS use Devanagari script for Hindi and Gujarati script for Gujarati. NEVER use Roman script (English letters) for Indian words (e.g., Use "नमस्ते" instead of "नमस्ते"). Only English technical terms like "App", "Link", "RM" should stay in English script.
- Always address the customer by their name + "ji" (e.g. "Rushil ji") once you know it.
- **CONTEXT RULE**: Review "Customer info collected so far" before every turn. NEVER ask for information that is already present (e.g., if "customer_name" is "Rushil", do NOT ask for their name).
- DIALOGUE MOMENTUM RULE: Never just answer or ask. ALWAYS acknowledge/repeat what the user shared first (e.g., "Theek hai ji, SG-5 noted", "25 MT noted") and THEN immediately ask the next targeted question. This keeps the customer engaged and the conversation moving forward.
- ATTENTION: ALWAYS end every single response with a short, engaging question to guide the user to the next stage of the conversation.
- **DIALOGUE MOMENTUM**: Acknowledge/repeat shared info first ("PVC pipe noted") and THEN ask the next question.
- **REPETITION GUARD**: If you've already answered a question or stated a fact, do not repeat it unless the user asks for clarification. If the user says "You already told me this", apologize warmly and move to the next logical step.

JSON format:
{{
    "ai_response":"...",
    "call_completed":false,
    "customer_name":null,
    "customer_type":null,
    "product_made":null,
    "polymer_needed":null,
    "grade_needed":null,
    "quantity_needed":null,
    "brand_preferred":null,
    "factory_location":null,
    "intent":null,
    "advance_stage":false
}}


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
- PRICING RULE: You CAN mention the "approximate live price" if it is available in the LIVE CATALOG above. Always clarify that this is a "dispatch price" and the final landed price will be shared by their RM on the App. If the user asks for a material like HDPE, read out the Brand, Grade Code, and Price accurately from the catalog.
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

Questions and Answers


User Case - Main Flow
User: Hello?
Agent: Hello Sir, Monica this side. Welcome to BSB. May I know your good name sir?

User: My name is {{user_name}}.
Agent: So {{user_name}}ji, Let me know how can I help you?

User: I want to know about BSB?
Agent: Ji, BSB ek Hitech AI-based Polymer Distribution Platform hai. Hum pure end-to-end supply chain manage karte hain. Right from Polymer Buying, Selling, logistics manage karna, finance/credit dena, global sourcing, customs clearing, warehousing and freight forwarding sab ek hi jagah. We are a complete solutions for Industrial Raw materials.

User: Aapka head office kahan hai?
Agent: Our Head office is in Ahmedabad, Gujarat.

User: Iske founder kaun hain? or Who is the backbone of BSB? or Who manages everthing?
Agent: BSB ke founder Mr. Nalin Bhavsar hain. He along with his strong and robust team 

User: Polymer mein kya-kya sell karte ho?
Agent: Sir, hum sabhi prime grades me deal karte hain—PVC, HDPE, LDPE, LLDPE, PP, PET aur engineering grades.

User: Aap manufacturer ho ya trader?
Agent: Hum distribution platform hain sir—buyers aur suppliers ko connect karte hain.

Agent: Are you a Manufacturer or trader?

Agent: What is your monthly consumption quantity in metric tons?

User: Where is your material located?
Agent: Our material is generally located in Mundra and Nhavasheva Warehouses and few stock in other city areas.

Agent: Sir, aap kis type ka grade regular basis par lete ho?
User: (answers)

Agent: Aapka favourite grade kya hai? Main ise system mein “Favourite” ke under add kar dungi, taaki agli baar quick offers mil jayein.

Agent: {{user_name}} Ji, aap EX terms par buy karte ho ya Delivered terms par?

Agent: Aapka delivery location kaunse city/state mein hoga sir?
User: (answers)

User: Daily mere favourite grade ka rate mil sakta hai?
Agent: Bilkul ji. Aap batayiye—WhatsApp par bheju ya email par? Number de dijiye, main abhi update kar deti hoon.

User: Do you have a mobile application? or How can i get daily offers? or Can you share application link? or Can you share app download link?
Agent: Sure sir, I have shared you app download link on your registered mobile no. Do you want me to share on any other number?
User: Yes, please share on my other no. {{user_number}}.
Agent: Sure sir, I have sent whatsapp message on your number {{user_number}}.

User: App download karne ke baad kya karein?
Agent: Apko KYC team me se call ayega. Bas KYC complete kar dijiyega jisse aapko saare features ka access mil jayega.

User: Elite Buyer Club kya hai?
Agent: Ji, Elite Buyer Club hamara premium buyer program hai. Isme aapko priority service, rate alerts aur multiple benefits milte hain. Iske liye Platinum Plan lena hota hai. Agar chaho to main aapko saare benefits WhatsApp kar deti hoon.

User: BSB ka main objective kya hai?
Agent: Sir, humara core objective simple hai—distribution ko smooth banana aur humare buyer ko ek peaceful, tension-free buying experience dena. Premium serivce at competitive price.

User: Ye Trade ERP jaisa hai kya?
Agent: Ji haan, bilkul. Agar aap plan lete ho to aapka procurement head, logistic team, plant head—sabko ek hi system par add kar sakte ho. Internal coordination bahut asaan ho jata hai.

User: RM kaise milta hai?
Agent: Sir, jaise hi aap app download kar loge, hum aapko dedicated Relationship Manager allot kar denge. Phir wahi person aapke saare calls pick karega.

User: Negotiation kaise hoga?
Agent: Ji, initial negotiation main kar deti hoon. Final rate approval aapke RM se hota hai. Main unko note de deti hoon—wo aapko call back kar lenge.

User:  Is this app free?
Agent: Yes {{user_name}} ji, this is a free app. Lekin agar apko premium features jaise Polyclub, Elite Club etc. access karne ho to subscription lena zaruri hai.

User: Subscription plan lena zaroori hai kya?
Agent: Ji nahi sir, It is not mandatory to take subscription.

User: Supply karna ho to? or If I want to sell material to BSB? or I want to sell my material to BSB.
Agent: Great Sir, Supply Partner banne ke liye BSB Polymer Partner App download karna hota hai jiski link mene apko whatsapp kar di hai. Aap hamari website par jaa kar “Book Demo” kar sakte hai. Hum limited supplier se kaam karte hai. Humari team apko 24 hours me contact karegi to check your eligibilty and assist you for registering as BSB Polymer Partner.

User: How are you different from Source one or polystox or Polymart?
Agent: Sir our focus is not only domestic trading. Our ultimate objective is to develop whole Supply chain ecosystem right from Global Sourcing to Delivery, everything under one roof. We are not limited to polymers. We will enter into all Industrial Raw Material commodities.

User: I want to talk to your senior/ I want to talk to human agent/ Mujhe apke senior se baat karni hai/ I want to talk to your boss/ mujhe apke management se baat karni hai
Agent: Sure sir, Please hold for a moment, I will transfer your call to my senior.

User: I want to buy this material/Please confirm this material for me.
Agent: Thankyou Sir, Let me confirm the order details with you. You want to buy this material ____ at Rs. ___/kilo from _____ location. 
If User Says Yes, then ask for delivery location. If user says No, then confirm order details on "Ex" delivery terms.

If user confirms the above details:
Agent: Thankyou sir, I have sent you a whatsapp message for order confirmation. After checking the details please click on "Confirm" button. You can also confirm from confirmation request sent in BSB Polymer application. After confirmation you will immediately receive your Sales order in your App, Whatsapp and Email. 
User: I want logistics services only.
Agent: Yes sir. We provide logistics services separately also. What is your loading location and unloading location, Quantity and Material.
User: My Loading location is Mundra and unloading location is Ahmedabad and Quantity is 25mt of PVC Prime material.
Agent: Ok sir, let me check. Your transportation charges are approximately Rs. 37000. I have sent a whatsapp message with approx transportation rate. Our Logistics team will contact you for more details. 

User case 2 - whole user flow in short and in hinglish
User : Hello 
Agent : Ji Namaste Main Mohini, BSB Polymer Se Baat Kar rahi hun Bataye main kya help kar sakti hun

User : Ji Mohini ji, Muje BSB ke Barain main Jaan na hain
Agent : Ji Please app apna Name Bataye

User : Je Mera Name Rushil hain
Agent : Rushil ji Thanks for your name

Agent : Rushil ji BSB Polymer - Tech Driven Distribution Platform hain jaha aapko Polymer main Sarain Grade milenge 
iss ke sath stah apko Logistic aur Credit Facility ke option bhi hain, 

Agent : Kya aap Manufacturer ho ya Trader
User : Ji main Manufacturer hun

Agent : Ok Thanks, To Rushil ji aap kya Product banate ho aur apko kaunsa Grade use hoga hain, aur kya brand or Grade code lagta hain wo bhi bataye
User : Ji Main PVC Pipe Banata hun aur muje Jyadatar Reliance ka Resin lagta hain

Agent : Accha, got it, Aur aapka Factory kaun se location main hain
User : Mohini ji mera factory, Ahmedabad Gujarat main Kathwada GIDC main

Agent : Ok, Kya aap ne hamarai App download ki hain
User : ji nahi

Agent : Thik hain Ji main aapko App download ki linke aur Tutorial share kar deti hun aap uss ko download kar ke register kar lo
User : Thik hain ji

Agent : Ek baar aap App download kar loge phir aap ko Dedicated RM allot kiya jayega aur iss ke alawa aur bhi bahut sarain feature hain
User : ok

Agent : kya aapka yahi mobile number pan Whatsaapp hain?
User : Ji yahi hain

Agent : Think hain to mainne iss ke upar App ka link aur Tutorial bhej diya hain aap dekh lijiye aur app download kar lijiye 
User : Ok

Agent : Thank you very much Rushil ji

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
    # Build a structured customer context the LLM can clearly parse.
    # Shows CAPTURED fields explicitly ("do not re-ask") and lists MISSING ones.
    c = state.customer
    _captured = []
    _missing = []
    _fields = [
        ("Name",              c.name),
        ("Customer Type",     c.customer_type),
        ("Product Made",      c.product_made),
        ("Polymer Needed",    c.polymer_needed),
        ("Grade/Brand",       c.grade_needed or c.brand_preferred),
        ("Quantity Needed",   c.quantity_needed),
        ("Factory Location",  c.factory_location),
    ]
    for label, val in _fields:
        if val:
            _captured.append(f"  ✓ {label}: {val}")
        else:
            _missing.append(f"  ✗ {label}")
    customer_info_str = (
        "CAPTURED (do NOT ask again):\n" + ("\n".join(_captured) if _captured else "  (none yet)") +
        "\nSTILL NEEDED:\n" + ("\n".join(_missing) if _missing else "  (all collected!)")
    )

    # ── Semantic Cache Check ─────────────────────────────────────────────────
    # Skip cache for highly context-dependent turns (e.g. stage advancement)
    cached_answer = await semantic_cache.get(user_input)
    if cached_answer:
        # Yield the cached answer split into sentences, then the result dict
        _cached_sentences = [s.strip() for s in re.split(r'(?<=[.?!।])', cached_answer) if s.strip()]
        for _s in _cached_sentences:
            if any(c.isalnum() or ord(c) > 127 for c in _s):
                yield _s, None
        yield "", parse_ai_result_from_raw(json.dumps({"ai_response": cached_answer}))
        return

    # ── Context Strategy (3-tier) ────────────────────────────────────────────
    # Tier 1: ≤16 msgs (≤8 turns)  → verbatim full history (no embedding overhead)
    # Tier 2: 17-24 msgs            → dynamic trimming with 1.5s hard budget
    # Tier 3: >24 msgs              → summarise oldest tail first
    #
    # RATIONALE: A typical BSB call completes in 6-10 turns (≤20 msgs).
    # For those calls, last-10 IS the full history — RAG adds zero benefit but
    # adds 3-10s CPU embedding per turn, exhausting the thread pool.
    # Dynamic context is only useful for GENUINELY long conversations.
    history_len = len(state.history)
    _verbatim_history = [
        HumanMessage(content=m["content"]) if m["role"] == "user"
        else AIMessage(content=m["content"])
        for m in state.history
    ]
    _fallback_history = [
        HumanMessage(content=m["content"]) if m["role"] == "user"
        else AIMessage(content=m["content"])
        for m in state.history[-10:]
    ]
    if history_len <= 16:
        # Short-to-medium call: send all history verbatim, instant, no CPU cost
        lc_history = _verbatim_history
    elif history_len <= 24:
        # Dynamic retrieval — hard 1.5s budget so embedding never eats the LLM window
        # Semaphore ensures at most 1 embedding thread runs at a time across concurrent calls
        try:
            async with _EMBED_SEMAPHORE:
                lc_history = await asyncio.wait_for(
                    _build_dynamic_context(state.history, user_input),
                    timeout=1.5,
                )
        except asyncio.TimeoutError:
            logger.warning("[DYNCTX] Timed out (>1.5s) — falling back to last-10 window.")
            lc_history = _fallback_history
    else:
        # Long call: summarise old messages, strict 2.5s budget
        try:
            lc_history = await asyncio.wait_for(
                _get_summarized_history(state.history),
                timeout=2.5,
            )
        except asyncio.TimeoutError:
            logger.warning("[HISTORY] Summarisation timed out — falling back to last-10 window.")
            lc_history = _fallback_history

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

    # 10.0s Timeout for initial token or slow stream.
    # Raised from 6s → 10s: the embedding step (_build_dynamic_context) runs on CPU
    # and can take up to 1-2s, so we need headroom for Groq to still respond.
    LLM_TIMEOUT = 50.0

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

        # ── Store in Semantic Cache ──────────────────────────────────────────
        # Only cache if we got a real AI response (not just a fallback)
        ai_response_text = result.get("ai_response", "").strip()
        if ai_response_text and len(ai_response_text) > 10:
            asyncio.create_task(semantic_cache.put(user_input, ai_response_text))

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
    c = state.customer
    _captured, _missing = [], []
    for label, val in [
        ("Name", c.name), ("Customer Type", c.customer_type), ("Product Made", c.product_made),
        ("Polymer Needed", c.polymer_needed), ("Grade/Brand", c.grade_needed or c.brand_preferred),
        ("Quantity Needed", c.quantity_needed), ("Factory Location", c.factory_location),
    ]:
        (_captured if val else _missing).append(f"  {'OK' if val else 'XX'} {label}: {val or ''}")
    customer_info_str = (
        "CAPTURED (do NOT ask again):\n" + ("\n".join(_captured) if _captured else "  (none yet)") +
        "\nSTILL NEEDED:\n" + ("\n".join(_missing) if _missing else "  (all collected!)")
    )


    # This function is sync (called via run_in_executor). Use simple raw window —
    # full summarisation only happens on the async streaming path.
    lc_history = [
        HumanMessage(content=msg["content"]) if msg["role"] == "user"
        else AIMessage(content=msg["content"])
        for msg in state.history[-10:]
    ]

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
