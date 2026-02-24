from pydantic import BaseModel
from typing import Optional, List, Dict
from enum import Enum


class ConversationStage(str, Enum):
    GREETING          = "greeting"
    NAME_COLLECTION   = "name_collection"   # Collect customer name
    PITCH             = "pitch"             # Company pitch + Manufacturer or Trader?
    QUALIFICATION     = "qualification"     # Product + Grade (brand/process later)
    APP_OFFER         = "app_offer"         # Ask if app downloaded; offer link
    WHATSAPP_CONFIRM  = "whatsapp_confirm"  # Confirm WhatsApp number, send link
    COMPLETED         = "completed"


class CustomerInfo(BaseModel):
    """Lead qualification data collected during the call."""
    name:             Optional[str]  = None   # e.g. "Rushil"
    customer_type:    Optional[str]  = None   # "manufacturer" (buyer) | "trader" (seller)
    product_made:     Optional[str]  = None   # e.g. "PVC Pipe"
    polymer_needed:   Optional[str]  = None   # e.g. "PVC", "HDPE"
    grade_needed:     Optional[str]  = None   # e.g. "SG-70", "H030 SG"
    brand_preferred:  Optional[str]  = None   # e.g. "Reliance", "Sigfied" — auto-resolves to grade
    factory_location: Optional[str]  = None   # e.g. "Kathwada GIDC, Ahmedabad"
    quantity_needed:  Optional[str]  = None   # e.g. "25 MT"


class NegotiationState(BaseModel):
    min_price:      Optional[float] = None
    max_price:      Optional[float] = None
    current_offer:  Optional[float] = None
    final_price:    Optional[float] = None


class DealDetails(BaseModel):
    """Kept for compatibility with pricing/order modules."""
    polymer:  Optional[str]   = None
    grade:    Optional[str]   = None
    quantity: Optional[float] = None
    price:    Optional[float] = None
    location: Optional[str]   = None


class ConversationState(BaseModel):
    # Business role derived from customer_type
    #   manufacturer → buyer  |  trader → seller
    role:            Optional[str]        = None
    stage:           ConversationStage    = ConversationStage.GREETING
    lead_id:         Optional[int]        = None
    detected_language: str               = "hi-IN"   # tracks caller's current language
    customer:        CustomerInfo         = CustomerInfo()
    details:         DealDetails          = DealDetails()
    negotiation:     NegotiationState     = NegotiationState()
    missing_fields:  List[str]            = []
    should_end_call: bool                 = False

    # ── Conversation memory (last 20 pairs = 40 messages) ────────────────────
    history: List[Dict[str, str]] = []

    def add_turn(self, user_text: str, ai_text: str):
        self.history.append({"role": "user",      "content": user_text})
        self.history.append({"role": "assistant",  "content": ai_text})
        if len(self.history) > 40:
            self.history = self.history[-40:]

    def update_missing_fields(self):
        """Legacy helper — used by pricing flow if user transitions to ordering."""
        required = ["polymer", "grade", "quantity", "location"]
        if self.role == "seller":
            required.append("price")
        self.missing_fields = [
            f for f in required if getattr(self.details, f) is None
        ]
