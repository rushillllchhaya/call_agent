from sqlalchemy.orm import Session
from app.services.state import ConversationState, DealDetails, ConversationStage, CustomerInfo
from app.core.models import Conversation, User, Order, ProductOffer, PolymerType, Lead, PolymerInventory, PricingRule
from app.core.database import SessionLocal
from app.services.ai_agent import process_conversation_step
from app.services.brand_resolver import resolve_grade
from app.services.language_handler import LanguageHandler
from app.services.db_context import build_catalog_context
import json
import datetime
import os
from app.services.router import IntentRouter
from app.services.pricing import PricingEngine

# ── Stage order for advancement ──────────────────────────────────────────────
_STAGE_ORDER = [
    ConversationStage.GREETING,
    ConversationStage.NAME_COLLECTION,
    ConversationStage.PITCH,
    ConversationStage.QUALIFICATION,
    ConversationStage.APP_OFFER,
    ConversationStage.WHATSAPP_CONFIRM,
    ConversationStage.COMPLETED,
]

def _next_stage(current: ConversationStage) -> ConversationStage:
    """Returns the next stage in the linear flow."""
    try:
        idx = _STAGE_ORDER.index(current)
        return _STAGE_ORDER[idx + 1] if idx + 1 < len(_STAGE_ORDER) else ConversationStage.COMPLETED
    except ValueError:
        return ConversationStage.COMPLETED


class Orchestrator:
    def __init__(self, db: Session):
        self.db = db
        self.pricing_engine = PricingEngine(db)

    # ── State persistence ─────────────────────────────────────────────────────

    def _load_state(self, user_id: int) -> ConversationState:
        conversation = self.db.query(Conversation).filter(Conversation.user_id == user_id).first()
        if conversation and conversation.state:
            data = conversation.state
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    pass
            if isinstance(data, dict):
                try:
                    return ConversationState(**data)
                except Exception:
                    pass
        return ConversationState()

    def _save_state(self, user_id: int, state: ConversationState):
        conversation = self.db.query(Conversation).filter(Conversation.user_id == user_id).first()
        if not conversation:
            conversation = Conversation(user_id=user_id, messages=[])
            self.db.add(conversation)
        conversation.state = state.dict()
        self.db.commit()

    def reset_state(self, user_id: int):
        conversation = self.db.query(Conversation).filter(Conversation.user_id == user_id).first()
        if conversation:
            conversation.state = ConversationState().dict()
            self.db.commit()

    # ── Lead upsert ───────────────────────────────────────────────────────────

    def _upsert_lead(self, state: ConversationState, phone: str | None = None):
        """Create or update the Lead record with latest qualification data."""
        c = state.customer
        if state.lead_id:
            lead = self.db.query(Lead).filter(Lead.id == state.lead_id).first()
            if lead:
                if c.name:          lead.customer_name  = c.name
                if c.factory_location: lead.location    = c.factory_location
                if c.customer_type: lead.intent         = state.role or c.customer_type
                if c.polymer_needed: lead.polymer_type  = c.polymer_needed
                if c.grade_needed:  lead.grade          = c.grade_needed
                self.db.commit()
                return

        # Create new lead
        lead = Lead(
            customer_name=c.name or (f"User {phone}" if phone else "Unknown"),
            mobile_no=phone,
            intent=state.role or c.customer_type,
            polymer_type=c.polymer_needed,
            grade=c.grade_needed,
            location=c.factory_location,
            status="New",
        )
        self.db.add(lead)
        self.db.commit()
        self.db.refresh(lead)
        state.lead_id = lead.id

    def _dump_lead_to_json(self, state: ConversationState):
        """Saves lead details to a temp JSON file for real-time backup."""
        os.makedirs("lead_dumps", exist_ok=True)
        filename = f"lead_dumps/{state.mobile_no}.json"
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(state.customer.dict(), f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Error dumping lead to JSON: {e}")

    # ── Per-stage system instructions ─────────────────────────────────────────

    def _stage_instruction(self, state: ConversationState) -> str:
        c = state.customer
        stage = state.stage

        if stage == ConversationStage.GREETING:
            # Greeting was already played by streams.py; just advance
            if c.name:
                return (
                    f"The customer has responded to your greeting. Since you already know their name is {c.name}, "
                    f"don't ask for it again. Proceed directly to acknowledging them and ask if they'd like to "
                    "know more about BSB or skip to details."
                )
            return (
                "The customer has responded to your greeting. "
                "Now politely ask for their name. Set advance_stage=true once ready to move on."
            )

        if stage == ConversationStage.NAME_COLLECTION:
            if not c.name:
                return (
                    "Ask the customer for their name warmly. "
                    "Once they give it, extract it into customer_name and set advance_stage=true."
                )
            return (
                f"You already have the customer's name: {c.name}. "
                f"SAY: 'Acha {c.name} ji, kya apko BSB ke baare mein janna hai ya hum aage badhe? ' "
                "Set advance_stage=true."
            )

        if stage == ConversationStage.PITCH:
            if not c.customer_type:
                return (
                    f"Address the customer as '{c.name} ji'. "
                    "If they asked to move forward, skip the pitch and just ask: 'Aap Manufacturer ho ya Trader?' "
                    "Otherwise, give the brief BSB Polymer pitch (Tech platform, 200+ grades, credit facility) "
                    "and THEN ask: 'Aap Manufacturer ho ya Trader?' "
                    "Manufacturer = buyer. Trader = seller. "
                    "Set customer_type and advance_stage=true."
                )
            return f"customer_type is {c.customer_type}. Set advance_stage=true."

        if stage == ConversationStage.QUALIFICATION:
            missing = []
            if not c.product_made:     missing.append(("product_made", "what product they manufacture? (e.g. PVC Pipe)"))
            if not c.polymer_needed:   missing.append(("polymer_needed", "polymer type? (e.g. PVC, HDPE)"))
            if not c.grade_needed:     missing.append(("grade_needed", "specific grade? (e.g. SG-5)"))
            if not c.quantity_needed:  missing.append(("quantity_needed", "required quantity? (e.g. 25 MT)"))
            if not c.factory_location: missing.append(("factory_location", "factory location/city?"))

            if missing:
                target_key, target_desc = missing[0]
                return (
                    f"PROACTIVE INSTRUCTION: The customer is in QUALIFICATION stage. "
                    f"Your priority is to collect: {target_desc}. "
                    f"First, acknowledge/repeat any info they just shared to show you understood. "
                    f"THEN immediately ask for the {target_desc} to keep the momentum. "
                    f"Example: 'Theek hai SG-5 noted. Kitni quantity ki requirement hai aapko?'"
                )
            return "All qualification details collected. Warmly acknowledge and set advance_stage=true."

        if stage == ConversationStage.APP_OFFER:
            return (
                f"{c.name} ji ke saath hamaari app ke baare mein baat karni hai. "
                "Ask: 'Kya aapne hamaari BSB Polymer App download ki hai?' "
                "If yes, great — move on. If no, explain: after download, a Dedicated RM will be allotted, "
                "they can see live prices and track orders. "
                "Tell them you'll send the link. Set advance_stage=true."
            )

        if stage == ConversationStage.WHATSAPP_CONFIRM:
            return (
                "Ask: 'Kya yahi mobile number WhatsApp par bhi hai?' "
                "Once confirmed (yes/haan/ji), say you've sent the BSB Polymer app link "
                "and tutorial on WhatsApp. Set advance_stage=true."
            )

        if stage == ConversationStage.COMPLETED:
            return (
                f"Give a warm closing. Thank {c.name or 'the customer'} ji. "
                "Say their dedicated RM will be in touch shortly. "
                "Wish them a great day. Keep it under 40 words."
            )

        return ""

    # ── Main message handler ──────────────────────────────────────────────────

    def process_message(
        self, user_id: int, message_text: str,
        phone_number: str | None = None,
        language_code: str = "hi-IN",
    ) -> tuple[str, bool]:
        """Returns (ai_response, should_end_call)."""

        # 1. Load state
        state = self._load_state(user_id)

        # 1a. Update detected language (stick to the latest the caller spoke)
        if language_code:
            state.detected_language = language_code

        # 2. Normalize language
        try:
            english_text = LanguageHandler.normalize_text(message_text)
        except Exception:
            english_text = message_text

        # 3. Noise guard — skip garbage transcripts
        stripped_check = english_text.strip().lower().rstrip(".")
        words_check    = stripped_check.split()
        is_noise = (
            len(stripped_check) <= 3
            or (len(words_check) >= 2 and len(set(words_check)) == 1)
            or all(len(w) <= 2 for w in words_check)
        )
        if is_noise:
            print(f"DEBUG: Skipping noise transcript: '{english_text}'")
            return "", False  # Silence for noise (empty string satisfies type hint)

        print(f"DEBUG: Stage={state.stage.value} | Input={english_text}")

        # 3.1. Intent Detection (The Brain Layer)
        intent = IntentRouter.classify_intent(english_text)
        print(f"DEBUG: Intent Detected={intent}")

        # Handle MOVE_FORWARD (Skip/Advance flow)
        # If user says "Aage badhte hain" during name/intro, move to PITCH to ask Manuf/Trader
        if intent == "MOVE_FORWARD":
            if state.stage == ConversationStage.NAME_COLLECTION:
                state.stage = ConversationStage.PITCH
            elif state.stage == ConversationStage.PITCH:
                state.stage = ConversationStage.QUALIFICATION

        # 3.2. Proactive Pricing Context
        price_context = ""
        if intent == "ASK_PRICE" or "price" in english_text.lower() or "rate" in english_text.lower():
            # If we have basic info, calculate a real quote
            if state.customer.polymer_needed and state.customer.grade_needed:
                poly = state.customer.polymer_needed
                grad = state.customer.grade_needed
                # Use defaults for missing info to get an estimate
                qty  = 25.0 # default 25 MT for estimation
                loc  = state.customer.factory_location or "Ahmedabad" 
                
                quote = self.pricing_engine.calculate_price(poly, grad, qty, loc)
                if quote.get("found"):
                    price_context = (
                        f"\nPRICING DATA: The current calculated price for {grad} {poly} "
                        f"is approximately ₹{quote['final_price']}/kg (Dispatch Price). "
                        f"Breakdown: Base ₹{quote['base_price']}, Freight ₹{quote['freight']}, Tax ₹{quote['tax']}."
                    )
                else:
                    price_context = "\nPRICING DATA: Live price not found in database. Tell customer we will check with RM."

        # 4. Advance GREETING automatically (greeting was played by streams.py)
        if state.stage == ConversationStage.GREETING:
            if state.customer.name:
                # If name is already prepopulated, skip NAME_COLLECTION and go to PITCH
                state.stage = ConversationStage.PITCH
            else:
                state.stage = ConversationStage.NAME_COLLECTION

        # 5. Build per-stage instruction — also inject language instruction
        lang_label = {
            "hi-IN": "Hindi (Hinglish is fine, use Devanagari where natural)",
            "gu-IN": "Gujarati (mix with Hindi/English naturally if needed)",
            "en-IN": "English (you may sprinkle Hindi courtesy words like 'Ji', 'Theek hai')",
        }.get(state.detected_language, "Hindi")

        lang_instruction = (
            f"LANGUAGE: The caller is currently speaking {lang_label}. "
            f"Respond primarily in {lang_label}. "
            f"Adapt naturally — if they switch language mid-call, you switch too."
        )
        system_instruction = self._stage_instruction(state)
        if system_instruction:
            system_instruction = lang_instruction + "\n" + system_instruction
        else:
            system_instruction = lang_instruction

        # 6. Fetch catalog (relevant when polymer is known)
        catalog = build_catalog_context(self.db, polymer=state.customer.polymer_needed)

        # 7. Call AI
        system_instruction += price_context # Append the proactive pricing info
        result = process_conversation_step(
            english_text, state, system_instruction, catalog_context=catalog
        )

        ai_response = result.get("ai_response", "Ji, thoda sa repeat karein please?")

        # 8. Extract and persist customer fields from AI JSON
        c = state.customer
        if result.get("customer_name")   and not c.name:
            c.name             = str(result["customer_name"])
        if result.get("customer_type")   and not c.customer_type:
            raw_type           = str(result["customer_type"]).lower()
            c.customer_type    = "manufacturer" if "manuf" in raw_type else "trader" if "trad" in raw_type else raw_type
        if result.get("product_made")    and not c.product_made:
            c.product_made     = str(result["product_made"])
        if result.get("polymer_needed")  and not c.polymer_needed:
            c.polymer_needed   = str(result["polymer_needed"])
        if result.get("grade_needed")    and not c.grade_needed:
            c.grade_needed     = str(result["grade_needed"])
        if result.get("quantity_needed") and not c.quantity_needed:
            c.quantity_needed  = str(result["quantity_needed"])
        if result.get("brand_preferred")  and not c.brand_preferred:
            c.brand_preferred  = str(result["brand_preferred"])
        if result.get("factory_location") and not c.factory_location:
            c.factory_location = str(result["factory_location"])

        # 8a. Brand → Grade auto-resolution
        #     If caller mentioned a brand+polymer but no grade yet, resolve silently
        if c.polymer_needed and c.brand_preferred and not c.grade_needed:
            resolved = resolve_grade(self.db, c.polymer_needed, c.brand_preferred)
            if resolved:
                c.grade_needed = resolved
                print(f"[Orchestrator] Brand auto-resolved: {c.brand_preferred} + {c.polymer_needed} → {resolved}")

        # 9. Derive business role from customer_type
        #    Manufacturer = buyer | Trader = seller
        if c.customer_type and not state.role:
            if c.customer_type == "manufacturer":
                state.role = "buyer"
            elif c.customer_type == "trader":
                state.role = "seller"

        state.customer = c

        # 10. Upsert lead once we have at least a name or location
        if c.name or c.factory_location:
            self._upsert_lead(state, phone_number)

        # 11. Advance stage when AI signals it and we're not already at the end
        if result.get("advance_stage") and state.stage != ConversationStage.COMPLETED:
            # Extra guard: don't leave QUALIFICATION until all four fields are collected
            if state.stage == ConversationStage.QUALIFICATION:
                qual_complete = all([c.product_made, c.polymer_needed, c.grade_needed, c.quantity_needed, c.factory_location])
                if not qual_complete:
                    pass  # stay in QUALIFICATION
                else:
                    state.stage = _next_stage(state.stage)
            else:
                state.stage = _next_stage(state.stage)

        # 12. Mark call as done on COMPLETED
        if state.stage == ConversationStage.COMPLETED:
            state.should_end_call = True

        # 13. Persist turn to history
        state.add_turn(english_text, ai_response)
        self._save_state(user_id, state)

        print(f"DEBUG: → Stage now={state.stage.value}")
        return ai_response, state.should_end_call
