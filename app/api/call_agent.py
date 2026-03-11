"""
Call Agent Router - BSB Voice Call Agent lifecycle endpoints.

  POST /api/call/check-caller  - Start of call  (DB read-only)
  POST /api/call/get-price     - During call     (DB read-only)
  POST /api/call/save-lead     - End of call     (JSON file only, no DB write)
  GET  /api/call/leads         - View all temp leads in JSON file
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.services.bsb_api_client import CallAgentService, _load_leads

router = APIRouter(tags=["Call Agent"])


# -- Request Schemas -----------------------------------------------------------

class CheckCallerRequest(BaseModel):
    phone_number: str = Field(..., example="9876543210")


class GetPriceRequest(BaseModel):
    brand: Optional[str] = Field(default=None, example="Formosa")
    grade: Optional[str] = Field(default=None, example="S60")


class SaveLeadRequest(BaseModel):
    name:         str             = Field(...,         example="Rushi Chhaya")
    phone:        str             = Field(...,         example="9876543210")
    company_name: str             = Field(default="",  example="ABC Polymers")
    location:     str             = Field(default="",  example="Ahmedabad")
    material:     str             = Field(default="",  example="Formosa S60")
    quantity:     str             = Field(default="",  example="25 MT")
    quoted_price: Optional[float] = Field(default=None, example=46000.0)
    status:       str             = Field(default="New")


# -- 1. START OF CALL - Check Caller -------------------------------------------

@router.post(
    "/check-caller",
    summary="[Start of Call] Look up caller history",
    description=(
        "Triggered immediately when a call begins. "
        "Checks leads_temp.json, then leads table, then buyer_masters (all READ-ONLY). "
        "Returns previous data if caller is known, or new_user=true if unknown."
    ),
)
def check_caller(payload: CheckCallerRequest, db: Session = Depends(get_db)):
    print("[CallAgent] check-caller | phone=" + payload.phone_number)
    try:
        return CallAgentService(db).check_caller(payload.phone_number)
    except Exception as e:
        print("[CallAgent] check-caller error: " + str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -- 2. DURING CALL - Get Live Price -------------------------------------------

@router.post(
    "/get-price",
    summary="[During Call] Fetch live price for a brand + grade",
    description=(
        "Triggered when the caller asks for a price. "
        "Looks up brand, grade code, and offer price (all READ-ONLY from DB)."
    ),
)
def get_price(payload: GetPriceRequest, db: Session = Depends(get_db)):
    brand = (payload.brand or "").strip()
    grade = (payload.grade or "").strip()
    print(f"[CallAgent] get-price | brand={brand!r} grade={grade!r}")
    try:
        return CallAgentService(db).get_price(brand, grade)
    except Exception as e:
        print("[CallAgent] get-price error: " + str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -- 3. END OF CALL - Save Lead (JSON only) ------------------------------------

@router.post(
    "/save-lead",
    summary="[End of Call] Save lead to leads_temp.json (no DB write)",
    description=(
        "Triggered after the call ends. "
        "Saves lead data ONLY to leads_temp.json - the main database is never touched. "
        "Once verified, leads can be migrated to the DB manually or via a script."
    ),
)
def save_lead(payload: SaveLeadRequest, db: Session = Depends(get_db)):
    print("[CallAgent] save-lead | phone=" + payload.phone + " name=" + payload.name)
    try:
        return CallAgentService(db).save_lead(
            name=payload.name,
            phone=payload.phone,
            company_name=payload.company_name,
            location=payload.location,
            material=payload.material,
            quantity=payload.quantity,
            quoted_price=payload.quoted_price,
            status=payload.status,
        )
    except Exception as e:
        print("[CallAgent] save-lead error: " + str(e))
        raise HTTPException(status_code=500, detail=str(e))


# -- 4. VIEW TEMP LEADS --------------------------------------------------------

@router.get(
    "/leads",
    summary="View all leads saved in leads_temp.json",
    description="Returns every lead currently saved in the temporary JSON file.",
)
def get_temp_leads():
    leads = _load_leads()
    return {
        "total": len(leads),
        "storage": "leads_temp.json",
        "leads": leads,
    }
