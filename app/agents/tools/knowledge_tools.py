"""
Knowledge Tools — LangChain @tool wrappers that surface BSB Polymer
business knowledge for the General Info and Sub-Functionality agents.
"""
from __future__ import annotations

import json
import logging
import os

from langchain_core.tools import tool

logger = logging.getLogger("knowledge_tools")

# ── Load BSB knowledge base from markdown file ────────────────────────────────
_KB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "core", "bsb_knowledge.md"
)

def _load_knowledge() -> str:
    try:
        with open(os.path.normpath(_KB_PATH), "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "BSB knowledge base not found."


# ── Static FAQ sections ────────────────────────────────────────────────────────
_FAQ: dict[str, str] = {
    "what_is_bsb": (
        "BSB Polymer ek tech-driven polymer distribution platform hai jo Ahmedabad, Gujarat mein based hai. "
        "Hum 200+ prime polymer grades (PVC, HDPE, LDPE, LLDPE, PP, PET aur engineering grades) deal karte hain. "
        "Hamaara core objective buyers aur suppliers ko ek seamless, tension-free buying experience dena hai. "
        "Founder: Mr. Nalin Bhavsar."
    ),
    "why_bsb": (
        "BSB sirf ek domestic trader nahi hai — hum puri supply chain manage karte hain. "
        "Global Sourcing → Customs Clearing → Warehousing (Mundra & Nhavasheva) → Logistics → Delivery. "
        "Sab ek hi jagah. Buyers ko multiple vendors se deal nahi karna padta. "
        "We also offer NBFC credit facility, logistics services, and a dedicated RM for each buyer."
    ),
    "bsb_app": (
        "BSB Polymer App ek free app hai. Download karne ke baad: "
        "1. Register karein (KYC team call karegi). "
        "2. Dedicated Relationship Manager (RM) assign hoga. "
        "3. Live polymer prices dekh sakte ho. "
        "4. Instant enquiry kar sakte ho. "
        "5. Market trends monitor karo. "
        "6. Logistics tracking karo. "
        "Premium features ke liye subscription available hai (Elite Buyer Club, Polyclub etc.)."
    ),
    "onboarding": (
        "BSB App onboarding steps: "
        "1. App download karein (link WhatsApp par bhejenge). "
        "2. Mobile number se register karein. "
        "3. KYC complete karein (team call karegi). "
        "4. Dedicated RM assign hoga jo aapki queries handle karega. "
        "5. Live prices, enquiries, aur order tracking — sab app mein. "
        "KYC ke baad full features unlock ho jaate hain."
    ),
    "logistics": (
        "BSB Logistics service: Hum Mundra aur Nhavasheva warehouses se delivery provide karte hain. "
        "Freight charges origin-destination ke hisaab se calculate hoti hain. "
        "Truck booking, vehicle tracking, aur ETA updates WhatsApp par milti hain. "
        "Logistics sirf bhi le sakte hain (material BSB se na lein to bhi)."
    ),
    "credit": (
        "BSB NBFC Credit Facility: Registered buyers credit limit ke saath material khareed sakte hain. "
        "Eligibility: 2+ saal ka business, 5+ MT monthly consumption, GST registered. "
        "Credit limit buyer ki consumption ke hisaab se set hoti hai. "
        "Application BSB App mein kar sakte hain."
    ),
    "elite_buyer_club": (
        "Elite Buyer Club BSB ka premium buyer program hai. "
        "Benefits: Priority service, exclusive rate alerts, special discount tiers, "
        "dedicated senior RM, aur early access to new grades. "
        "Platinum Plan khareednaa hota hai."
    ),
}


def make_knowledge_tools(db=None):
    """Returns knowledge tools for General Info and Sub-Functionality agents."""

    @tool
    def get_bsb_faq(topic: str) -> str:
        """
        Retrieve BSB Polymer's factual knowledge on a specific topic.
        Use this to answer caller questions about what BSB is, why to choose BSB,
        the app, onboarding, logistics, credit, or the Elite Buyer Club.

        Args:
            topic: One of: 'what_is_bsb', 'why_bsb', 'bsb_app', 'onboarding',
                   'logistics', 'credit', 'elite_buyer_club'.

        Returns:
            Factual text to use in your response to the caller.
        """
        topic_clean = topic.strip().lower().replace(" ", "_").replace("-", "_")
        content = _FAQ.get(topic_clean)
        if not content:
            # Try partial match
            for key, val in _FAQ.items():
                if topic_clean in key or key in topic_clean:
                    content = val
                    break

        if content:
            return json.dumps({"topic": topic, "content": content})
        return json.dumps({
            "topic": topic,
            "content": _load_knowledge(),
            "note": "Full knowledge base returned — extract relevant section.",
        })

    @tool
    def get_polymer_types() -> str:
        """
        Return the list of polymer types and grades that BSB Polymer deals in.
        Use when a caller asks what materials BSB handles.

        Returns:
            JSON with polymer categories and example grades.
        """
        return json.dumps({
            "polymers": [
                {"type": "PVC",  "grades": ["SG-5", "SG-7", "S-65", "S-57", "K-57", "K-67"]},
                {"type": "HDPE", "grades": ["H030SG", "M60", "F46003", "P65006"]},
                {"type": "LDPE", "grades": ["19MA400", "22FA002", "16UF002"]},
                {"type": "LLDPE","grades": ["LL6201RQ", "LL1001KW"]},
                {"type": "PP",   "grades": ["H030SG", "M600", "Repol H030SG", "SM170S"]},
                {"type": "PET",  "grades": ["BC100", "BC102", "IV0.82"]},
                {"type": "Engineering Grades", "grades": ["ABS", "Nylon 66", "PC", "Acetal"]},
            ],
            "total_grades": "200+",
            "note": "Live prices and availability via Get Live Price tool.",
        }, ensure_ascii=False)

    return [get_bsb_faq, get_polymer_types]
