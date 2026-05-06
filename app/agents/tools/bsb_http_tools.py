"""
BSB HTTP API Tools — All 4 real HTTP API calls as LangChain @tools.
====================================================================

APIs:
  1. get_buyer_details        — Buyer Details API        (GET  /api/call-agent/buyer-details)
  2. get_live_price           — Get Price API             (GET  /api/call-agent/get-price)
  3. save_enquiry_details     — OnCall Save Details API   (POST /api/call-agent/save-details)
  4. send_whatsapp_app_link   — WhatsApp Link Share API   (POST /api/call-agent/whatsapp-link)

All URLs are configured via Settings (app/core/config.py).
Override BSB_API_BASE or individual URL vars in .env as needed.

STT corrections are applied before any API call (per the script's STT SPELLING CORRECTION RULE).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import requests
from langchain_core.tools import tool

from app.core.config import settings

logger = logging.getLogger("bsb_http_tools")

_TIMEOUT = 8  # seconds — keep under TTS latency budget

# ── STT correction map (Script: STT SPELLING CORRECTION RULE) ─────────────────
_STT_CORRECTIONS: dict[str, str] = {
    "children tye":    "Jilantai",
    "children type":   "Jilantai",
    "hi game":         "Hygain",
    "hs":              "Hygain",          # context-dependent
    "hs1000r":         "HS1000R",
}

def _fix_stt(text: str) -> str:
    """Apply STT brand/grade corrections before sending to API."""
    if not text:
        return text
    lower = text.lower().strip()
    for bad, good in _STT_CORRECTIONS.items():
        if bad in lower:
            text = re.sub(re.escape(bad), good, text, flags=re.IGNORECASE)
    return text.strip()


def _post(url: str, payload: dict, headers: Optional[dict] = None) -> dict:
    """HTTP POST with timeout and error wrapping."""
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=_TIMEOUT)
        r.raise_for_status()
        
        # Guard against empty response bodies if status is 2xx
        if not r.text.strip():
            return {}
        return r.json()
    except requests.exceptions.Timeout:
        logger.error(f"[BSB HTTP] POST {url} timed out")
        return {"error": "API timeout", "status": "error"}
    except requests.exceptions.HTTPError as e:
        logger.error(f"[BSB HTTP] POST {url} HTTP error: {e} | body={r.text[:200]}")
        return {"error": str(e), "status": "error", "detail": r.text[:200]}
    except Exception as e:
        logger.error(f"[BSB HTTP] POST {url} failed: {e}")
        return {"error": str(e), "status": "error"}


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 1: Buyer Details API
# ─────────────────────────────────────────────────────────────────────────────

@tool
def get_buyer_details(phone_number: str) -> str:
    """
    BSB Buyer Details API — Identify the caller and retrieve their previous
    enquiries, name, company, and delivery location.

    CRITICAL TRIGGERS (from script):
    - Call IMMEDIATELY at the start of every call (concurrent with greeting).
    - Call AGAIN after user confirms interest in material (location viable).
    - Call if user explicitly asks to check their previous/last order.

    Args:
        phone_number: Caller's mobile number (with or without country code).
                      Example: "+916353675876" or "9876543210".

    Returns:
        JSON with keys: exists, name, company_name, location, last_material,
        last_quantity, last_quoted_price, last_call_date, status.
        If exists=False: new caller — do NOT say "couldn't find details",
        just smoothly ask for the next missing field.
        If exists=True and past enquiries exist: ONLY say
        "Do you want to repeat your last order which was on [Material]?"
        NEVER read out the full list of past enquiries.
    """
    phone_clean = phone_number.strip()
    logger.info(f"[BuyerDetailsAPI] phone={phone_clean}")

    payload = {"phone_number": phone_clean}
    result = _post(settings.BSB_BUYER_DETAILS_URL, payload)

    if "error" in result:
        logger.warning(f"[BuyerDetailsAPI] Failed: {result['error']}")
        return json.dumps({
            "exists": False,
            "new_user": True,
            "error": result["error"],
            "note": "API unavailable — proceed as new caller.",
        }, ensure_ascii=False)

    logger.info(f"[BuyerDetailsAPI] Response: exists={result.get('exists')}")
    return json.dumps(result, ensure_ascii=False, default=str)


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 2: Get Price API
# ─────────────────────────────────────────────────────────────────────────────

@tool
def get_live_price(
    material: str = "",
    brand: str = "",
    grade: str = "",
    code: str = "",
) -> str:
    """
    BSB Get Price API — Fetch live dispatch prices for polymer materials.

    Call IMMEDIATELY whenever the user asks about price, rate, cost,
    "kitna hai", stock, or availability.

    ZERO MEMORY RULE: This API has NO memory. Only pass values explicitly
    mentioned in the CURRENT user sentence. Leave unused parameters as "".
    Never carry forward brand/material from a previous turn.

    Args:
        material: Polymer type if mentioned. E.g. "PVC", "HDPE", "PET".
                  ALWAYS English — never Devanagari. Leave "" if not mentioned.
        brand:    Brand name if mentioned. E.g. "Hygain", "Dongxing", "Reliance".
                  Apply STT correction before passing. Leave "" if not mentioned.
        grade:    Grade name if mentioned. E.g. "SG5", "S65D". Leave "" if not mentioned.
        code:     Code name if mentioned. E.g. "HS1000R", "WK-801". Leave "" if not mentioned.

    Returns:
        JSON with keys: found (bool), results (list of price rows).
        Each row has: grade_name, brand_name, code_name, total_price, location.
        PRICE RESPONSE RULE: Always speak ONLY total_price per kilogram.
        FORMAT: "[grade_name] [brand_name] [code_name] Rupees [total_price] per kilogram
                 [city] mein available hai."
        City extraction: take SECOND term from location field.
        If results empty: material out of stock — offer to generate enquiry.
    """
    # Apply STT corrections
    brand   = _fix_stt(brand)
    grade   = _fix_stt(grade)
    code    = _fix_stt(code)

    payload = {
        "grade_name": grade.strip() if grade.strip() else None,
        "brand_name": brand.strip() if brand.strip() else None,
        "code_name":  code.strip() if code.strip() else None,
    }
    
    # Pass material if provided (in case API depends on it later, though not in preview)
    if material.strip():
        payload["material"] = material.strip()
        
    logger.info(f"[GetPriceAPI] payload={payload}")

    result = _post(settings.BSB_GET_PRICE_URL, payload)

    if "error" in result or not result.get("success"):
        logger.warning(f"[GetPriceAPI] Failed: {result.get('error', result.get('message', 'unknown'))}")
        return json.dumps({
            "found": False,
            "error": result.get("error", "Failed to fetch price"),
            "results": [],
            "note": "I'm unable to fetch that information right now. Our team will confirm it for you.",
        }, ensure_ascii=False)

    # To prevent the TTS from reading an endlessly long list, limit to top 4 results.
    filtered_results = []
    for item in result.get("data", [])[:4]:
        filtered_results.append({
            "grade_name":  item.get("grade_name"),
            "brand_name":  item.get("brand_name"),
            "code_name":   item.get("code_name"),
            "total_price": item.get("total_price"),
            "location":    item.get("location"),
            "qty":         item.get("qty"),
        })

    return json.dumps({
        "found": len(filtered_results) > 0,
        "results": filtered_results
    }, ensure_ascii=False, default=str)


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 3: OnCall Save Details API
# ─────────────────────────────────────────────────────────────────────────────

@tool
def save_enquiry_details(
    name: str,
    phone: str,
    material: str,
    quantity: str,
    location: str,
    company_name: str = "",
    brand: str = "",
    code: str = "",
    quoted_price: str = "",
    role: str = "buyer",
) -> str:
    """
    BSB OnCall Save Details API — Save the confirmed enquiry after user agrees.

    TRIGGER: Call IMMEDIATELY after user confirms enquiry details during
    the final confirmation step (Step 8 of the lead capture flow).
    After this completes, AUTOMATICALLY call send_whatsapp_app_link — do NOT
    wait for user to ask.

    EXTRACTION RULE: STRICTLY separate material, brand, and code.
    Correct:   material="PET", brand="Wankai", code="WK-801"
    Incorrect: material="PET Wankai WK-801"

    Args:
        name:         Caller's full name (collected in Step 2).
        phone:        Caller's phone number (+91xxxxxxxxxx).
        material:     Polymer type ONLY in English. E.g. "PVC", "PET", "HDPE".
        quantity:     Quantity with unit. E.g. "25 MT", "10 tons".
        location:     Delivery location / city (Step 7).
        company_name: Company name if known (Step 7). Empty string if unknown.
        brand:        Brand name in English. E.g. "Dongxing", "Hygain".
        code:         Grade/code name. E.g. "SG5", "HS1000R".
        quoted_price: total_price value from Get Price API (as string). E.g. "78.35".
        role:         "buyer" (manufacturer) or "seller" (trader).

    Returns:
        JSON with status, lead_id, and storage confirmation.
        After success, IMMEDIATELY call send_whatsapp_app_link with the same phone.
    """
    payload = {
        "name":         name.strip(),
        "phone":        phone.strip(),
        "material":     material.strip(),
        "quantity":     quantity.strip(),
        "location":     location.strip(),
        "company_name": company_name.strip(),
        "brand":        brand.strip(),
        "code":         code.strip(),
        "quoted_price": quoted_price.strip(),
        "role":         role.strip(),
    }
    logger.info(f"[SaveDetailsAPI] name={name} phone={phone} material={material} qty={quantity}")

    result = _post(settings.BSB_SAVE_DETAILS_URL, payload)

    if result.get("error") or result.get("status") == "error":
        logger.error(f"[SaveDetailsAPI] Failed: {result}")
        return json.dumps({
            "status": "error",
            "note": "Unable to save enquiry. Our team will follow up.",
            "error": result.get("error", "unknown"),
        }, ensure_ascii=False)

    logger.info(f"[SaveDetailsAPI] Success: {result}")
    
    # ─────────────────────────────────────────────────────────────────────────
    # ZOHO CRM PUSH (Dual-sync)
    # ─────────────────────────────────────────────────────────────────────────
    from app.services.zoho_service import push_to_zoho_lead
    import threading
    
    # Run the Zoho push in a separate thread so it doesn't block the AI response
    # or cause latency in the voice conversation pacing.
    threading.Thread(target=push_to_zoho_lead, args=(
        name.strip(), phone.strip(), company_name.strip(),
        material.strip(), quantity.strip(), location.strip(),
        brand.strip(), quoted_price.strip()
    )).start()

    return json.dumps({
        **result,
        "next_action": "IMMEDIATELY call send_whatsapp_app_link with this phone number.",
    }, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 4: WhatsApp Link Share API
# ─────────────────────────────────────────────────────────────────────────────

@tool
def send_whatsapp_app_link(phone_number: str) -> str:
    """
    BSB WhatsApp Link Share API — Send the BSB Polymer App download link
    to the caller's WhatsApp AUTOMATICALLY right after save_enquiry_details succeeds.

    DO NOT wait for the user to ask. Fire immediately after save completes.
    DO NOT fire concurrently with save_enquiry_details — fire AFTER it succeeds.

    The WhatsApp template, language, and video URL are fixed constants.
    You only need to supply the caller's phone number.

    Args:
        phone_number: Caller's WhatsApp-enabled mobile number.
                      Format: "+916353675876" (with country code).
                      This is DYNAMIC — always the actual caller's number.

    Returns:
        JSON with status and confirmation the message was dispatched.
    """
    payload = {
        "phone":           phone_number.strip(),
        "template_name":   settings.BSB_WA_TEMPLATE,       # "ai_call_buyer_message"
        "language":        settings.BSB_WA_LANGUAGE,        # "en"
        "header_video_url": settings.BSB_WA_HEADER_VIDEO,  # static video URL
    }
    
    headers = {
        "Authorization": f"Bearer {settings.BSB_WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    
    logger.info(f"[WhatsAppAPI] Sending to {phone_number} template={settings.BSB_WA_TEMPLATE}")

    result = _post(settings.BSB_WHATSAPP_URL, payload, headers=headers)

    if result.get("error") or result.get("status") == "error":
        logger.error(f"[WhatsAppAPI] Failed: {result}")
        return json.dumps({
            "status": "error",
            "note": "WhatsApp message could not be sent. Our team will manually follow up.",
        }, ensure_ascii=False)

    logger.info(f"[WhatsAppAPI] Success: {result}")
    return json.dumps({
        "status": "sent",
        "to": phone_number,
        "template": settings.BSB_WA_TEMPLATE,
        "note": "BSB Polymer App link sent on WhatsApp.",
        **result,
    }, ensure_ascii=False)


# ── Export: list of all 4 BSB HTTP tools ─────────────────────────────────────
BSB_HTTP_TOOLS = [
    get_buyer_details,
    get_live_price,
    save_enquiry_details,
    send_whatsapp_app_link,
]
