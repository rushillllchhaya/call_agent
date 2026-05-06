"""
BSB API Client
==============
Thin async HTTP wrappers for all 4 BSB/WhatsApp APIs.
All calls broadcast their status to the dashboard automatically.

APIs:
  1. Buyer Details    → GET call history by phone
  2. Get Price        → Live polymer offer list
  3. Save Buyer Lead  → Save enquiry after call
  4. WhatsApp Template → Send app link via WhatsApp

Every method returns a typed dict and raises no exceptions
(errors are logged + broadcast and a safe fallback is returned).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger("bsb_api_client")

# ── Endpoint constants ────────────────────────────────────────────────────────
BUYER_DETAILS_URL  = "https://backend.bsbinfotrade.com/api/web/get-buyer-lead"
GET_PRICE_URL      = "https://backend.bsbinfotrade.com/api/web/seller-offer-list"
SAVE_LEAD_URL      = "https://backend.bsbinfotrade.com/api/web/save-buyer-lead"
WHATSAPP_URL       = "https://whatsapp.bsbinfotrade.com/api/v2/messages/template"
WHATSAPP_TOKEN     = "Bearer wm_aNMFbDuiNjBRwpfKSipVVtArsOqbTcqImEgu4PnbCz1aKOREw0N3fkqBSzCr"
WHATSAPP_VIDEO_URL = "https://backend.bsbinfotrade.com/ai_static/aivideo2.mp4"

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=12)


# ── Dashboard broadcaster (non-blocking) ─────────────────────────────────────
def _broadcast(endpoint: str, status: int, details: Any = ""):
    """Fire-and-forget broadcast to the dashboard WebSocket manager."""
    try:
        from app.services.dashboard import dashboard_manager
        msg = {"type": "api", "endpoint": endpoint, "status": status, "details": details}
        try:
            loop = asyncio.get_running_loop()
            # We're inside an async context — schedule it
            asyncio.ensure_future(dashboard_manager.broadcast(msg))
        except RuntimeError:
            # No running loop (called from thread executor) — create one
            _loop = asyncio.new_event_loop()
            try:
                _loop.run_until_complete(dashboard_manager.broadcast(msg))
            finally:
                _loop.close()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# 1. Buyer Details API
# Called at the START of every call to look up caller history.
# ─────────────────────────────────────────────────────────────────────────────
async def fetch_buyer_details(phone_number: str) -> dict:
    """
    POST https://backend.bsbinfotrade.com/api/web/get-buyer-lead
    Body: {"phone_number": "<phone>"}

    Returns:
        {
            "exists": bool,
            "name": str | None,
            "company_name": str | None,
            "location": str | None,
            "last_material": str | None,
            "last_brand": str | None,
            "last_code": str | None,
            "last_quoted_price": str | None,
            "last_call_date": str | None,
            "raw": dict          ← full API response
        }
    """
    payload = {"phone_number": phone_number}
    label = "Buyer Details API"

    try:
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            async with session.post(BUYER_DETAILS_URL, json=payload) as resp:
                status = resp.status
                data = await resp.json(content_type=None)

        exists = data.get("exists", False)
        first  = data.get("data", [{}])[0] if data.get("data") else {}

        result = {
            "exists":           exists,
            "name":             first.get("name"),
            "company_name":     first.get("company_name"),
            "location":         first.get("location"),
            "last_material":    first.get("material"),
            "last_brand":       first.get("brand"),
            "last_code":        first.get("code"),
            "last_quoted_price": first.get("quoted_price"),
            "last_call_date":   first.get("last_call_date"),
            "raw":              data,
        }

        # Broadcast: summary fields + first record from response
        _broadcast(label, status, {
            "request":  {"phone_number": phone_number},
            "response": {
                "exists": exists,
                "name": first.get("name"),
                "company_name": first.get("company_name"),
                "location": first.get("location"),
                "last_material": first.get("material"),
                "last_brand": first.get("brand"),
                "last_code": first.get("code"),
                "last_quoted_price": first.get("quoted_price"),
                "total_records": data.get("totalCount", 0),
            },
        })
        logger.info(f"[BSB API] {label}: exists={exists} phone={phone_number}")
        return result

    except Exception as e:
        logger.error(f"[BSB API] {label} failed: {e}")
        _broadcast(label, 500, str(e))
        return {"exists": False, "raw": {}}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Get Price API
# Called by Sales Agent when the user asks for live rates.
# ─────────────────────────────────────────────────────────────────────────────
async def fetch_prices(
    grade_name: Optional[str] = None,
    brand_name: Optional[str] = None,
    code_name:  Optional[str] = None,
) -> dict:
    """
    POST https://backend.bsbinfotrade.com/api/web/seller-offer-list
    Body: {"grade_name": ..., "brand_name": ..., "code_name": ...}

    Returns:
        {
            "success": bool,
            "offers": list[dict],   ← list of offer dicts
            "raw": dict
        }

    Each offer dict has: grade_name, brand_name, code_name, total_price, location, qty, qty_in_kg.
    """
    payload = {
        "grade_name": grade_name or None,
        "brand_name": brand_name or None,
        "code_name":  code_name  or None,
    }
    label = "Get Price API"

    try:
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            async with session.post(GET_PRICE_URL, json=payload) as resp:
                status = resp.status
                data = await resp.json(content_type=None)

        offers = data.get("data", [])
        # Broadcast: request filters + all returned offers
        _broadcast(label, status, {
            "request": payload,
            "response": {
                "total_results": len(offers),
                "offers": [
                    {
                        "grade": o.get("grade_name"),
                        "brand": o.get("brand_name"),
                        "code": o.get("code_name"),
                        "total_price": o.get("total_price"),
                        "qty_in_kg": o.get("qty_in_kg"),
                        "location": o.get("location"),
                    }
                    for o in offers
                ],
            },
        })
        logger.info(f"[BSB API] {label}: {len(offers)} offers for {payload}")
        return {"success": data.get("success", False), "offers": offers, "raw": data}

    except Exception as e:
        logger.error(f"[BSB API] {label} failed: {e}")
        _broadcast(label, 500, str(e))
        return {"success": False, "offers": [], "raw": {}}


# ─────────────────────────────────────────────────────────────────────────────
# 3. Save Buyer Lead API
# Called at the END of call after enquiry is confirmed.
# ─────────────────────────────────────────────────────────────────────────────
async def save_buyer_lead(
    name: str,
    phone: str,
    company_name: str = "",
    location: str = "",
    material: str = "",
    quantity: str = "",
    quoted_price: str = "",
    brand: str = "",
    code: str = "",
    role: str = "",
) -> dict:
    """
    POST https://backend.bsbinfotrade.com/api/web/save-buyer-lead

    Returns: {"success": bool, "message": str}
    """
    payload = {
        "name":         name,
        "phone":        phone,
        "company_name": company_name,
        "location":     location,
        "material":     material,
        "quantity":     quantity,
        "quoted_price": quoted_price,
        "brand":        brand,
        "code":         code,
        "role":         role,
    }
    label = "Save Lead API"

    try:
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            async with session.post(SAVE_LEAD_URL, json=payload) as resp:
                status = resp.status
                data = await resp.json(content_type=None)

        _broadcast(label, status, {
            "request": {
                "name": name, "phone": phone, "company": company_name,
                "location": location, "material": material, "quantity": quantity,
                "brand": brand, "code": code,
            },
            "response": {
                "success": data.get("success"),
                "message": data.get("message"),
            },
        })
        logger.info(f"[BSB API] {label}: {data.get('message')} for {phone}")
        return data

    except Exception as e:
        logger.error(f"[BSB API] {label} failed: {e}")
        _broadcast(label, 500, str(e))
        return {"success": False, "message": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# 4. WhatsApp Template API
# Called when user agrees to receive the app link.
# ─────────────────────────────────────────────────────────────────────────────
async def send_whatsapp_link(
    phone: str,
    template_name: str = "ai_call_buyer_message",
    language: str = "en",
) -> dict:
    """
    POST https://whatsapp.bsbinfotrade.com/api/v2/messages/template
    Headers: Authorization, Content-Type
    Body: {phone, template_name, language, header_video_url}

    Returns: {"success": bool, "message_id": str | None}
    """
    headers = {
        "Authorization": WHATSAPP_TOKEN,
        "Content-Type":  "application/json",
    }
    payload = {
        "header_video_url": WHATSAPP_VIDEO_URL,
        "phone":            phone,
        "template_name":    template_name,
        "language":         language,
    }
    label = "WhatsApp Template API"

    try:
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            async with session.post(WHATSAPP_URL, json=payload, headers=headers) as resp:
                status = resp.status
                data = await resp.json(content_type=None)

        msg_id  = data.get("data", {}).get("message_id")
        sent_at = data.get("data", {}).get("sent_at")
        _broadcast(label, status, {
            "request": {"phone": phone, "template": template_name},
            "response": {
                "success": data.get("success"),
                "message": data.get("message"),
                "message_id": msg_id,
                "sent_at": sent_at,
                "status": data.get("data", {}).get("status"),
            },
        })
        logger.info(f"[BSB API] {label}: sent to {phone}, msg_id={msg_id}")
        return {"success": data.get("success", False), "message_id": msg_id, "raw": data}

    except Exception as e:
        logger.error(f"[BSB API] {label} failed: {e}")
        _broadcast(label, 500, str(e))
        return {"success": False, "message_id": None}


# ─────────────────────────────────────────────────────────────────────────────
# Sync wrappers (for use inside thread executor in LangGraph nodes)
# NOTE: These run in ThreadPoolExecutor threads which have NO event loop.
# We must create a fresh loop per call — asyncio.get_event_loop() fails here.
# ─────────────────────────────────────────────────────────────────────────────
def _run_async(coro):
    """Run an async coroutine from a sync thread that has no event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)  # aiohttp needs this on the thread
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

def sync_fetch_buyer_details(phone_number: str) -> dict:
    return _run_async(fetch_buyer_details(phone_number))

def sync_fetch_prices(grade=None, brand=None, code=None) -> dict:
    return _run_async(fetch_prices(grade, brand, code))

def sync_save_buyer_lead(**kwargs) -> dict:
    return _run_async(save_buyer_lead(**kwargs))

def sync_send_whatsapp(phone: str, template: str = "ai_call_buyer_message") -> dict:
    return _run_async(send_whatsapp_link(phone, template))
