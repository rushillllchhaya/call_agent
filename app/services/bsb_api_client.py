"""
CallAgentService — BSB Voice Call Agent.

Database access: READ-ONLY (check_caller, get_price).
Lead storage:    JSON file only (save_lead → leads_temp.json).

No data is ever inserted or modified in the main database.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.core.models import (
    Lead, Brand, GradeCode, PolymerType, ProductOffer, Buyer
)

# ── JSON temp-file paths ──────────────────────────────────────────────────────
LEADS_JSON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "leads_temp.json"
)
LEADS_JSON_PATH = os.path.normpath(LEADS_JSON_PATH)

PRICES_JSON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "prices_cache.json"
)
PRICES_JSON_PATH = os.path.normpath(PRICES_JSON_PATH)

DEFAULT_DELIVERY_DAYS = 3


# ── JSON helpers ─────────────────────────────────────────────────────────────

def _load_leads() -> list:
    """Load existing leads from JSON file (returns empty list if file missing)."""
    if not os.path.exists(LEADS_JSON_PATH):
        return []
    try:
        with open(LEADS_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, IOError):
        return []


def _save_leads(leads: list) -> None:
    """Write leads list back to JSON file."""
    with open(LEADS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(leads, f, indent=2, ensure_ascii=False, default=str)


# ── Service class ─────────────────────────────────────────────────────────────

class CallAgentService:

    def __init__(self, db: Session):
        self.db = db

    # ─────────────────────────────────────────────────────────────────────────
    # 1. START OF CALL — check caller  (DB: READ-ONLY)
    # ─────────────────────────────────────────────────────────────────────────
    def check_caller(self, phone: str) -> dict:
        """
        Look up a caller by phone. Checks:
          1. leads_temp.json  (temp leads saved via this API)
          2. leads table      (existing leads in DB — read only)
          3. buyer_masters    (registered buyers — read only)
        Returns caller profile or new_user=True if unknown.
        """
        phone_clean = self._normalise_phone(phone)

        # --- 1. Check JSON file first (most recent saves) ---
        json_leads = _load_leads()
        for lead in reversed(json_leads):
            stored_phone = self._normalise_phone(lead.get("phone", ""))
            if phone_clean and stored_phone and phone_clean in stored_phone:
                return {
                    "exists": True,
                    "source": "temp_json",
                    "name": lead.get("name", ""),
                    "company_name": lead.get("company_name", ""),
                    "location": lead.get("location", ""),
                    "last_material": lead.get("material", ""),
                    "last_quantity": lead.get("quantity", ""),
                    "last_quoted_price": lead.get("quoted_price"),
                    "last_call_date": lead.get("call_date"),
                    "status": lead.get("status", "New"),
                }

        # --- 2. Check leads table (DB, read only, using existing columns only) ---
        lead_db: Optional[Lead] = (
            self.db.query(Lead)
            .filter(Lead.mobile_no.contains(phone_clean))
            .order_by(Lead.created_at.desc())
            .first()
        )

        if lead_db:
            # Build last_material from existing polymer_type + grade columns
            parts = [lead_db.polymer_type or "", lead_db.grade or ""]
            last_material = " ".join(p for p in parts if p).strip()
            return {
                "exists": True,
                "source": "lead_db",
                "name": lead_db.customer_name or "",
                "company_name": "",
                "location": lead_db.location or "",
                "last_material": last_material,
                "last_quantity": lead_db.quantity or "",
                "last_quoted_price": None,
                "last_call_date": lead_db.created_at.isoformat() if lead_db.created_at else None,
                "status": lead_db.status or "New",
            }

        # --- 3. Check buyer_masters (DB, read only) ---
        buyer: Optional[Buyer] = (
            self.db.query(Buyer)
            .filter(Buyer.mobile_no.contains(phone_clean))
            .first()
        )

        if buyer:
            loc = ", ".join(filter(None, [buyer.city or "", buyer.state or ""]))
            return {
                "exists": True,
                "source": "buyer_db",
                "name": buyer.name or "",
                "company_name": buyer.company_name or "",
                "location": loc,
                "last_material": "",
                "last_quantity": "",
                "last_quoted_price": None,
                "last_call_date": None,
                "status": "Registered Buyer",
            }

        # --- 4. Unknown caller ---
        return {
            "exists": False,
            "new_user": True,
            "name": "",
            "company_name": "",
            "location": "",
            "last_material": "",
            "last_quantity": "",
            "last_quoted_price": None,
            "last_call_date": None,
            "status": "New",
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 2. DURING CALL — get live price  (JSON CACHE)
    # ─────────────────────────────────────────────────────────────────────────
    def get_price(self, brand: str, grade: str) -> dict:
        """
        Fetch price from prices_cache.json with 3-mode search:
          - brand + grade → fuzzy match both, return all location rows
          - brand only    → all grades for that brand across locations
          - grade only    → all brands carrying that grade across locations

        Response uses price_per_kg as the primary price field.
        """
        brand_in = brand.strip().lower()
        grade_in = grade.strip().lower()

        # Require at least 2 chars to avoid junk single-letter matches (e.g. "h" matching HMEL)
        has_brand = len(brand_in) >= 2
        has_grade = len(grade_in) >= 2

        # Load cache
        try:
            with open(PRICES_JSON_PATH, "r", encoding="utf-8") as f:
                prices_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"found": False, "reason": "Price cache unavailable", "results": []}

        results = []

        if has_brand and has_grade:
            # ── MODE 1: brand + grade ──────────────────────────────────────
            mode = "brand_grade"
            for item in prices_cache:
                b_match = self._fuzzy_match(brand_in, item.get("brand", ""))
                g_match = self._fuzzy_match(grade_in, item.get("grade", ""))
                if b_match and g_match:
                    results.append(self._format_result(item))

        elif has_brand and not has_grade:
            # ── MODE 2: brand only ─────────────────────────────────────────
            mode = "brand_only"
            seen = set()
            for item in prices_cache:
                if self._fuzzy_match(brand_in, item.get("brand", "")):
                    key = (item.get("brand", ""), item.get("grade", ""), item.get("location", ""))
                    if key not in seen:
                        seen.add(key)
                        results.append(self._format_result(item))

        elif has_grade and not has_brand:
            # ── MODE 3: grade only ─────────────────────────────────────────
            mode = "grade_only"
            seen = set()
            for item in prices_cache:
                if self._fuzzy_match(grade_in, item.get("grade", "")):
                    key = (item.get("brand", ""), item.get("grade", ""), item.get("location", ""))
                    if key not in seen:
                        seen.add(key)
                        results.append(self._format_result(item))

        else:
            return {"found": False, "reason": "No brand or grade provided", "results": []}

        if not results:
            label = brand or grade
            return {
                "found": False,
                "mode": mode,
                "reason": f"No match found for '{label}' in price cache",
                "results": [],
            }

        return {
            "found": True,
            "mode": mode,
            "total": len(results),
            "results": results,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # 3. END OF CALL — save lead  (JSON FILE ONLY — no DB writes)
    # ─────────────────────────────────────────────────────────────────────────
    def save_lead(
        self,
        name: str,
        phone: str,
        company_name: str,
        location: str,
        material: str,
        quantity: str,
        quoted_price: Optional[float],
        status: str = "New",
    ) -> dict:
        """
        Save lead data to leads_temp.json only.
        The main database is NEVER touched.

        If a lead with the same phone already exists in the JSON file,
        it is updated in-place. Otherwise a new entry is appended.
        """
        phone_clean = self._normalise_phone(phone)
        leads = _load_leads()

        # Check if phone already exists in JSON
        existing_idx = None
        for i, lead in enumerate(leads):
            if self._normalise_phone(lead.get("phone", "")) == phone_clean:
                existing_idx = i
                break

        record = {
            "id": str(uuid.uuid4()),
            "name": name,
            "phone": phone_clean,
            "company_name": company_name,
            "location": location,
            "material": material,
            "quantity": quantity,
            "quoted_price": quoted_price,
            "status": status,
            "call_date": datetime.utcnow().isoformat(),
            "source": "temp_json",
            "note": "Pending DB verification",
        }

        if existing_idx is not None:
            record["id"] = leads[existing_idx].get("id", record["id"])
            leads[existing_idx] = record
            action = "updated"
        else:
            leads.append(record)
            action = "created"

        _save_leads(leads)

        return {
            "status": action,
            "lead_id": record["id"],
            "storage": "leads_temp.json",
            "note": "Stored temporarily. Will be pushed to database after verification.",
            "total_leads_in_file": len(leads),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _normalise_phone(phone: str) -> str:
        p = phone.strip().lstrip("+")
        if p.startswith("91") and len(p) == 12:
            p = p[2:]
        return p

    @staticmethod
    def _fuzzy_match(query: str, candidate: str) -> bool:
        """
        Returns True if query fuzzy-matches candidate.
        - Normalises both: lowercase, strip hyphens/spaces/dots
        - Also tries O<->0 substitution (common AI mis-transcription)
        - Accepts if one contains the other (substring)
        - Accepts if SequenceMatcher ratio >= 0.70
        """
        import difflib
        import re

        def norm(s: str) -> str:
            return re.sub(r"[\s\-\.]", "", s.lower())

        def variants(s: str):
            """Generate O<->0 substitution variants."""
            n = norm(s)
            yield n
            yield n.replace("o", "0")
            yield n.replace("0", "o")

        q_base = norm(query)
        c_base = norm(candidate)
        if not q_base or not c_base:
            return False

        for q in variants(query):
            for c in variants(candidate):
                if q in c:
                    return True
                if difflib.SequenceMatcher(None, q, c).ratio() >= 0.85:
                    return True
        return False

    @staticmethod
    def _format_result(item: dict) -> dict:
        """Format a single cache row into the standard result dict (price per kg primary)."""
        price_per_kg = item.get("price_per_kg")
        price_per_mt = item.get("price_per_mt")

        # Derive missing value from the other
        if price_per_kg and not price_per_mt:
            price_per_mt = round(price_per_kg * 1000, 2)
        elif price_per_mt and not price_per_kg:
            price_per_kg = round(price_per_mt / 1000, 2)

        return {
            "brand": item.get("brand", "Unknown"),
            "grade": item.get("grade", "Unknown"),
            "category": item.get("category") or "Unknown",
            "price_per_kg": price_per_kg,
            "price_per_mt": price_per_mt,
            "location": item.get("location") or "Not specified",
            "currency": "INR",
            "availability": "In Stock" if price_per_kg else "No active offer",
            "delivery_days": DEFAULT_DELIVERY_DAYS,
        }

