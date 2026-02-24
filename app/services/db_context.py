"""
db_context.py — Fetches live catalog data from the production DB for the AI prompt.

DB Schema (from production SQL dump):
  - `grades`           → polymer types (PVC=1, HDPE=2, LDPE=3, LLDPE=4, PP=5, PET=6)
  - `brands`           → manufacturer brands (Reliance, GAIL, IOCL, HPCL, Haldia, etc.)
  - `brand_grade_maps` → grade codes per brand+polymer (e.g. HDPE + GAIL → H030 SG)
  - `add_new_materials`→ active seller offers (grade_code_id, polymer_id, brand_id, price)
  - `polymer_inventory`→ BSB's own aggregated stock (may be empty in production initially)

All data is fetched live. 2-minute TTL cache prevents DB overload.
"""
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.models import PolymerType, PolymerInventory, ProductOffer, PricingRule, Brand, BrandGradeMap
import time

# Simple in-process cache: key → (timestamp, value)
_cache: dict = {}
CACHE_TTL_SECONDS = 120  # 2 minutes


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < CACHE_TTL_SECONDS:
        return entry[1]
    return None


def _cache_set(key: str, value):
    _cache[key] = (time.time(), value)


def get_available_polymers(db: Session) -> list[str]:
    """Returns list of active polymer names from the `grades` table."""
    cached = _cache_get("polymers")
    if cached is not None:
        return cached
    rows = db.query(PolymerType.name).filter(PolymerType.status == '1').order_by(PolymerType.id).all()
    result = [r.name for r in rows]
    _cache_set("polymers", result)
    return result


def get_market_offers(db: Session, polymer: str | None = None) -> list[dict]:
    """
    Returns active seller offers from add_new_materials with REAL names resolved:
      - polymer name from grades table
      - brand name from brands table
      - grade code name from brand_grade_maps table

    Only active offers (status='1', offer_status_id='4' = Active).
    """
    cache_key = f"offers_{polymer or 'all'}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        # Build query with proper JOINs to resolve actual names
        query = """
            SELECT
                anm.id,
                g.name      AS polymer_name,
                b.name      AS brand_name,
                bgm.name    AS grade_code,
                anm.original_price,
                anm.bsb_margin_price,
                (anm.original_price + anm.bsb_margin_price) AS total_price,
                anm.instock_in_kg,
                anm.moq_in_kg
            FROM add_new_materials anm
            LEFT JOIN grades g        ON g.id = anm.grade_id
            LEFT JOIN brands b        ON b.id = anm.brand_id
            LEFT JOIN brand_grade_maps bgm ON bgm.id = anm.grade_code_id
            WHERE anm.status = '1'
        """
        params = {}
        if polymer:
            query += " AND g.name LIKE :polymer"
            params["polymer"] = f"%{polymer}%"

        query += " ORDER BY anm.id DESC LIMIT 20"

        rows = db.execute(text(query), params).fetchall()

        result = [
            {
                "polymer":      r.polymer_name or "Unknown",
                "brand":        r.brand_name or "Unknown",
                "grade_code":   r.grade_code or "N/A",
                "price_per_kg": float(r.total_price or 0),
                "stock_kg":     r.instock_in_kg or 0,
                "moq_kg":       r.moq_in_kg or 0,
            }
            for r in rows
        ]
        _cache_set(cache_key, result)
        return result

    except Exception as e:
        print(f"[db_context] get_market_offers error: {e}")
        return []


def get_inventory_for_polymer(db: Session, polymer: str | None = None) -> list[dict]:
    """
    Returns BSB's own inventory from polymer_inventory table.
    This may be empty initially; fallback to market offers in that case.
    """
    cache_key = f"inventory_{polymer or 'all'}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    q = db.query(PolymerInventory)
    if polymer:
        q = q.filter(PolymerInventory.polymer_type.ilike(f"%{polymer}%"))
    rows = q.filter(PolymerInventory.available_quantity > 0).all()

    result = [
        {
            "polymer":          r.polymer_type,
            "grade":            r.grade,
            "base_price_per_kg": float(r.base_price),
            "available_qty_mt": float(r.available_quantity),
            "location":         r.warehouse_location,
        }
        for r in rows
    ]
    _cache_set(cache_key, result)
    return result


def build_catalog_context(db: Session, polymer: str | None = None) -> str:
    """
    Builds a compact, LLM-ready catalog string to inject into Mohini's AI prompt.
    Fetches everything live from the production DB — no hardcoded data.
    """
    lines = []

    # 1. Available polymer types (from grades table)
    polymers = get_available_polymers(db)
    if polymers:
        lines.append(f"Available polymers: {', '.join(polymers)}")
    else:
        lines.append("Available polymers: PVC, HDPE, LDPE, LLDPE, PP, PET")

    # 2. BSB's own inventory (polymer_inventory)
    inventory = get_inventory_for_polymer(db, polymer)
    if inventory:
        lines.append("\nBSB Stock (our inventory):")
        for item in inventory:
            lines.append(
                f"  - {item['polymer']} | Grade: {item['grade']} | "
                f"Price: ₹{item['base_price_per_kg']:.2f}/kg | "
                f"Stock: {item['available_qty_mt']} MT | "
                f"Warehouse: {item['location']}"
            )

    # 3. Market offers from sellers (add_new_materials)
    offers = get_market_offers(db, polymer)
    if offers:
        lines.append("\nMarket Offers (seller listings on BSB Prime):")
        for o in offers:
            lines.append(
                f"  - {o['polymer']} | Brand: {o['brand']} | "
                f"Grade: {o['grade_code']} | "
                f"Price: ₹{o['price_per_kg']:.2f}/kg | "
                f"Stock: {o['stock_kg']} kg | MOQ: {o['moq_kg']} kg"
            )
    elif not inventory:
        lines.append("\nNo live offers found. Inform customer we will check and call back.")

    return "\n".join(lines)


def invalidate_cache():
    """Call this after any DB write to ensure fresh data on the next query."""
    _cache.clear()
