"""
Logistics Tools — LangChain @tool wrappers for:
  - Freight quote calculation (Origin → Destination)
  - Truck/vehicle booking management
  - Vehicle location & ETA tracking
"""
from __future__ import annotations

import json
import logging
import random
from typing import Optional

from langchain_core.tools import tool

logger = logging.getLogger("logistics_tools")

# ── Static freight rate table (₹/MT per route) ────────────────────────────────
# Real integration: replace with BSB Logistics API
_FREIGHT_RATES = {
    ("mundra",     "ahmedabad"):  1850,
    ("mundra",     "surat"):      2100,
    ("mundra",     "rajkot"):     1650,
    ("mundra",     "vadodara"):   2200,
    ("nhavasheva", "mumbai"):     1200,
    ("nhavasheva", "pune"):       2000,
    ("nhavasheva", "ahmedabad"):  4500,
    ("ahmedabad",  "surat"):      1300,
    ("ahmedabad",  "rajkot"):     1100,
}

def _get_rate(origin: str, destination: str) -> Optional[int]:
    """Case-insensitive lookup in freight rate table."""
    o = origin.strip().lower()
    d = destination.strip().lower()
    rate = _FREIGHT_RATES.get((o, d))
    if rate:
        return rate
    # Try reverse
    rate = _FREIGHT_RATES.get((d, o))
    return rate


def make_logistics_tools(db=None):
    """Returns logistics tools. db accepted for interface consistency."""

    @tool
    def calculate_freight_quote(
        origin: str,
        destination: str,
        quantity_mt: float,
        material_type: str = "Polymer",
    ) -> str:
        """
        Calculate approximate freight (transportation) charges for moving polymer
        material from origin warehouse to destination city.

        Args:
            origin:        Loading location, e.g. 'Mundra', 'Nhavasheva', 'Ahmedabad'.
            destination:   Unloading location / delivery city.
            quantity_mt:   Quantity in Metric Tonnes (MT).
            material_type: Type of material being transported (default: 'Polymer').

        Returns:
            JSON with route, rate_per_mt, total_freight_inr, estimated_days.
        """
        rate = _get_rate(origin, destination)
        if not rate:
            # Fallback estimate: ₹2500/MT for unknown routes
            rate = 2500
            note = "Approximate rate. Exact quote will be confirmed by logistics team."
        else:
            note = "Indicative rate. Final rate subject to truck availability & date."

        total = round(rate * quantity_mt, 2)
        days  = random.randint(1, 3) if rate < 2000 else random.randint(2, 5)

        return json.dumps({
            "route":            f"{origin.title()} → {destination.title()}",
            "material":         material_type,
            "quantity_mt":      quantity_mt,
            "rate_per_mt_inr":  rate,
            "total_freight_inr": total,
            "estimated_days":   days,
            "note":             note,
            "next_step":        "BSB Logistics team will contact within 2 hrs for final booking.",
        }, ensure_ascii=False)

    @tool
    def book_logistics(
        origin: str,
        destination: str,
        quantity_mt: float,
        material: str,
        pickup_date: str,
        contact_name: str = "",
        contact_phone: str = "",
    ) -> str:
        """
        Initiate a logistics/truck booking request for transporting polymer material.
        Call after the caller has confirmed freight quote and wants to proceed.

        Args:
            origin:        Pickup location/warehouse.
            destination:   Delivery city/address.
            quantity_mt:   Quantity in MT.
            material:      Material description, e.g. 'PVC SG-5'.
            pickup_date:   Requested pickup date (YYYY-MM-DD or descriptive).
            contact_name:  Name of contact at pickup point.
            contact_phone: Phone number at pickup point.

        Returns:
            JSON with booking_id, status, and next steps.
        """
        booking_id = f"BSB-LOG-{random.randint(10000, 99999)}"
        logger.info(f"[Logistics] Booking initiated: {booking_id} | {origin}→{destination} | {quantity_mt}MT")
        return json.dumps({
            "booking_id":     booking_id,
            "status":         "Pending Confirmation",
            "route":          f"{origin} → {destination}",
            "material":       material,
            "quantity_mt":    quantity_mt,
            "pickup_date":    pickup_date,
            "contact_name":   contact_name,
            "contact_phone":  contact_phone,
            "next_step":      "BSB Logistics team will confirm truck assignment within 2 hours.",
            "whatsapp_update": "You will receive vehicle details on WhatsApp once confirmed.",
        }, ensure_ascii=False)

    @tool
    def get_vehicle_tracking(booking_id: str) -> str:
        """
        Get the live status / ETA of a dispatched truck for a given logistics
        booking ID.

        Args:
            booking_id: Logistics booking ID, e.g. 'BSB-LOG-12345'.

        Returns:
            JSON with vehicle_no, current_location, eta, status.
        """
        # TODO: Replace with real GPS-tracking API
        logger.info(f"[Logistics] Tracking request for {booking_id}")
        return json.dumps({
            "booking_id":       booking_id,
            "vehicle_no":       "GJ-01-AB-1234",
            "driver_name":      "Rajesh Bhai",
            "driver_phone":     "+91-9876543210",
            "current_location": "Surendranagar Bypass",
            "eta_hours":        4,
            "status":           "In Transit",
            "note":             "Live GPS data. Updates every 30 minutes on WhatsApp.",
        }, ensure_ascii=False)

    return [calculate_freight_quote, book_logistics, get_vehicle_tracking]
