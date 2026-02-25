"""
db_context.py — Fetches live catalog data from the production DB for the AI prompt.

DB Schema (from production SQL dump):
  - `grades`           → polymer types (PVC=1, HDPE=2, LDPE=3, LLDPE=4, PP=5, PET=6)
  - `brands`           → manufacturer brands (Reliance, GAIL, IOCL, HPCL, Haldia, etc.)
  - `brand_grade_maps` → grade codes per brand+polymer (e.g. HDPE + GAIL → H030 SG)
  - `add_new_materials`→ active seller offers (grade_code_id, polymer_id, brand_id, price)
  - `polymer_inventory`→ BSB's own aggregated stock (may be empty in production initially)
  - `pricing_rules`    → freight cost + tax % per city/state

All data is fetched live. 2-minute TTL cache prevents DB overload.

Context is INTENT-AWARE: narrows down based on known customer info
(polymer, brand, location, quantity) so the LLM sees only relevant data.
"""
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.models import PolymerType, PolymerInventory, ProductOffer, PricingRule, Brand, BrandGradeMap
import time
import re

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


def _normalize(text_: str) -> str:
    return re.sub(r"\s+", " ", text_.lower().strip())


# ─── Freight / Pricing ───────────────────────────────────────────────────────

def get_freight_for_location(db: Session, location: str) -> dict | None:
    """
    Query pricing_rules for freight cost, tax %, and margin % for a given city/state.
    Tries substring match so "Jamnagar, Gujarat" matches "Jamnagar".
    Returns dict with freight_cost, tax_percent, margin_percent or None if not found.
    """
    if not location:
        return None

    cache_key = f"freight_{_normalize(location)}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        # Try exact match first, then substring
        row = db.query(PricingRule).filter(
            PricingRule.location.ilike(f"%{location.strip()}%")
        ).first()

        if row:
            result = {
                "location": row.location,
                "freight_cost": float(row.freight_cost or 0),
                "tax_percent": float(row.tax_percent or 0.18),
                "margin_percent": float(row.margin_percent or 0.05),
            }
            _cache_set(cache_key, result)
            return result
    except Exception as e:
        print(f"[db_context] get_freight_for_location error: {e}")

    _cache_set(cache_key, None)
    return None


# ─── Brand-Filtered Offers ────────────────────────────────────────────────────

def get_offers_by_brand(db: Session, polymer: str | None = None, brand: str | None = None) -> list[dict]:
    """
    Returns active seller offers filtered by BOTH polymer AND brand.
    More focused than get_market_offers which only filters by polymer.
    """
    cache_key = f"offers_brand_{_normalize(polymer or 'any')}_{_normalize(brand or 'any')}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        query = """
            SELECT
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
        if brand:
            from app.services.brand_resolver import _canonical_brand
            canon_brand = _canonical_brand(brand)
            # The DB often stores abbreviation as name or code, so check both
            query += " AND (b.name LIKE :brand OR b.code LIKE :brand OR b.name LIKE :canon_brand)"
            params["brand"] = f"%{brand}%"
            params["canon_brand"] = f"%{canon_brand}%"

        query += " ORDER BY anm.id DESC LIMIT 15"

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
        print(f"[db_context] get_offers_by_brand error: {e}")
        return []


def get_grades_for_brand(db: Session, polymer: str | None, brand: str | None) -> list[str]:
    """
    Returns all grade codes available for a given polymer + brand combo from brand_grade_maps.
    Used when gives brand but no specific grade, so Mohini can present options.
    """
    if not polymer and not brand:
        return []

    cache_key = f"grades_{_normalize(polymer or '')}_{_normalize(brand or '')}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        query = """
            SELECT DISTINCT bgm.name AS grade_code
            FROM brand_grade_maps bgm
            LEFT JOIN grades g    ON g.id = bgm.grade_id
            LEFT JOIN brands b    ON b.id = bgm.brand_id
            WHERE bgm.status = '1'
        """
        params = {}
        if polymer:
            query += " AND g.name LIKE :polymer"
            params["polymer"] = f"%{polymer}%"
        if brand:
            query += " AND b.name LIKE :brand"
            params["brand"] = f"%{brand}%"

        rows = db.execute(text(query), params).fetchall()
        result = [r.grade_code for r in rows if r.grade_code]
        _cache_set(cache_key, result)
        return result

    except Exception as e:
        print(f"[db_context] get_grades_for_brand error: {e}")
        return []


def get_price_ranges_by_brand(db: Session, polymer: str) -> list[dict]:
    """
    For a given polymer, returns price summary PER BRAND from active listings:
      - brand name
      - list of (grade_code, price_per_kg) tuples
      - min_price and max_price across all their grades

    Used when user asks for a polymer but hasn't specified a brand yet.
    Example output for PVC:
      [
        {"brand": "Reliance", "grades": [("K-67", 95.5), ("SG-5", 92.0)], "min": 92.0, "max": 95.5},
        {"brand": "Finolex",  "grades": [("K-67", 96.0)],                  "min": 96.0, "max": 96.0},
      ]
    """
    cache_key = f"price_ranges_{_normalize(polymer)}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        query = """
            SELECT
                b.name      AS brand_name,
                bgm.name    AS grade_code,
                (anm.original_price + anm.bsb_margin_price) AS total_price
            FROM add_new_materials anm
            LEFT JOIN grades g        ON g.id = anm.grade_id
            LEFT JOIN brands b        ON b.id = anm.brand_id
            LEFT JOIN brand_grade_maps bgm ON bgm.id = anm.grade_code_id
            WHERE anm.status = '1'
              AND g.name LIKE :polymer
            ORDER BY b.name, bgm.name
        """
        rows = db.execute(text(query), {"polymer": f"%{polymer}%"}).fetchall()

        # Group by brand
        brand_map: dict[str, list[tuple[str, float]]] = {}
        for r in rows:
            brand = r.brand_name or "Unknown"
            grade = r.grade_code or "N/A"
            price = float(r.total_price or 0)
            if price > 0:
                brand_map.setdefault(brand, []).append((grade, price))

        result = []
        for brand, grades in brand_map.items():
            prices = [p for _, p in grades]
            result.append({
                "brand":  brand,
                "grades": grades,           # list of (grade_code, price)
                "min":    min(prices),
                "max":    max(prices),
            })

        # Sort by min price ascending
        result.sort(key=lambda x: x["min"])
        _cache_set(cache_key, result)
        return result

    except Exception as e:
        print(f"[db_context] get_price_ranges_by_brand error: {e}")
        return []



def get_producers_for_grade(db: Session, grade: str) -> list[str]:
    """
    Queries brand_grade_maps to find which brands produce a given grade code.
    Tries both the raw name and its alias (SG-5 → K-67) so either naming convention works.
    Returns a sorted list of distinct brand names from the DB.
    """
    from app.services.brand_resolver import resolve_grade_alias
    canonical = resolve_grade_alias(grade)

    cache_key = f"producers_{_normalize(grade)}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        # Try both the caller's name and the alias so e.g. "SG-5" also matches "K-67" rows
        names_to_try = list({grade, canonical})   # deduplicate
        placeholders = ", ".join(f":g{i}" for i in range(len(names_to_try)))
        params = {f"g{i}": n for i, n in enumerate(names_to_try)}

        query = f"""
            SELECT DISTINCT b.name AS brand_name
            FROM brand_grade_maps bgm
            LEFT JOIN brands b ON b.id = bgm.brand_id
            WHERE bgm.status = '1'
              AND bgm.name IN ({placeholders})
            ORDER BY b.name
        """
        rows = db.execute(text(query), params).fetchall()
        result = [r.brand_name for r in rows if r.brand_name]
        _cache_set(cache_key, result)
        return result

    except Exception as e:
        print(f"[db_context] get_producers_for_grade error: {e}")
        return []

# ─── Existing helpers ─────────────────────────────────────────────────────────

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
    Only active offers (status='1').
    """
    cache_key = f"offers_{polymer or 'all'}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        query = """
            SELECT
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
    """Returns BSB's own inventory from polymer_inventory table."""
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


# ─── Intent-Aware Context Builder ─────────────────────────────────────────────

def build_catalog_context(
    db: Session,
    polymer: str | None = None,
    brand: str | None = None,
    grade: str | None = None,
    location: str | None = None,
    quantity_needed: str | None = None,
) -> str:
    """
    Builds a compact, LLM-ready catalog string injected into Mohini's AI prompt.

    INTENT-AWARE: narrows data based on what the customer has shared so far:
      - polymer known  → filter offers by polymer
      - brand known    → show only that brand's grades + prices (more focused)
      - grade known    → spotlight EXACTLY that grade's price + stock (most precise)
      - location known → calculate landed price (base + freight + GST)
      - quantity known → show total order cost estimate

    All data fetched live from production DB with 2-min TTL cache.
    """
    lines = []
    from app.services.brand_resolver import resolve_grade_alias
    # Normalise grade alias early: SG-5 → K-67, sg5 → K-67, etc.
    if grade:
        grade = resolve_grade_alias(grade)

    # 1. Available polymer types (always shown)
    polymers = get_available_polymers(db)
    if polymers:
        lines.append(f"Available polymers: {', '.join(polymers)}")
    else:
        lines.append("Available polymers: PVC, HDPE, LDPE, LLDPE, PP, PET")

    # 2. Parse quantity to KG for cost estimates
    qty_kg: float | None = None
    if quantity_needed:
        # Support formats like "25 MT", "25 tons", "500 kg", "25"
        m = re.search(r"(\d+(?:\.\d+)?)\s*(mt|ton|metric\s*ton|kg)?", quantity_needed.lower())
        if m:
            qty_val = float(m.group(1))
            unit = (m.group(2) or "mt").strip()
            qty_kg = qty_val * 1000 if unit != "kg" else qty_val

    # 3. Freight data for customer's location
    freight_data = None
    if location:
        freight_data = get_freight_for_location(db, location)
        if freight_data:
            freight_per_kg = freight_data["freight_cost"]
            gst = freight_data["tax_percent"]
            lines.append(
                f"\nFreight to {freight_data['location']}: "
                f"₹{freight_per_kg:.2f}/kg | GST: {gst*100:.0f}%"
            )
        else:
            lines.append(f"\nFreight to {location}: Not available in system (RM will confirm)")

    # 4. BSB's own inventory
    inventory = get_inventory_for_polymer(db, polymer)
    if inventory:
        lines.append("\nBSB Stock (our inventory):")
        for item in inventory:
            base = item['base_price_per_kg']
            line = (
                f"  - {item['polymer']} | Grade: {item['grade']} | "
                f"Base: ₹{base:.2f}/kg"
            )
            if freight_data:
                freight = freight_data["freight_cost"]
                gst = freight_data["tax_percent"]
                landed = (base + freight) * (1 + gst)
                line += f" | Landed ({freight_data['location']}): ₹{landed:.2f}/kg"
                if qty_kg:
                    total = landed * qty_kg
                    line += f" | Est. Total for {qty_kg/1000:.0f} MT: ₹{total:,.0f}"
            elif qty_kg:
                line += f" | Est. Total for {qty_kg/1000:.0f} MT: ₹{base * qty_kg:,.0f} (ex-warehouse)"
            line += (
                f" | Stock: {item['available_qty_mt']} MT | "
                f"Warehouse: {item['location']}"
            )
            lines.append(line)

    # 5. Market offers — progressively more focused based on what we know:
    #    grade known  → show only that exact grade (most specific)
    #    brand known  → show all grades for that brand + polymer
    #    neither      → show all offers filtered by polymer only
    if brand:
        all_offers = get_offers_by_brand(db, polymer, brand)
        if grade:
            # Filter down to ONLY the confirmed grade
            grade_offers = [o for o in all_offers if grade.lower() in o['grade_code'].lower()]
            if grade_offers:
                lines.append(f"\n★ CONFIRMED GRADE — {brand} {grade} ({polymer or ''})")
                for o in grade_offers:
                    base = o['price_per_kg']
                    line = f"  Grade: {o['grade_code']} | Base: ₹{base:.2f}/kg"
                    if freight_data and base > 0:
                        freight = freight_data["freight_cost"]
                        gst = freight_data["tax_percent"]
                        landed = (base + freight) * (1 + gst)
                        line += f" | Landed ({freight_data['location']}): ₹{landed:.2f}/kg"
                        if qty_kg:
                            total = landed * qty_kg
                            line += f" | Est. Total for {qty_kg/1000:.0f} MT: ₹{total:,.0f}"
                    elif qty_kg and base > 0:
                        line += f" | Est. Total for {qty_kg/1000:.0f} MT: ₹{base * qty_kg:,.0f} (ex-warehouse)"
                    line += f" | Stock: {o['stock_kg']} kg | MOQ: {o['moq_kg']} kg"
                    lines.append(line)
                offers = []   # already handled above, skip the generic loop
            else:
                # Exact grade not in active listings — show all brand offers + note
                lines.append(f"\nNote: {grade} not in current active listings for {brand}.")
                offers = all_offers
                # Still show all brand grades as alternatives
                all_grades = get_grades_for_brand(db, polymer, brand)
                if all_grades:
                    lines.append(f"Available {brand} grades ({polymer}): {', '.join(all_grades)}")
        else:
            # Grade unknown — show all brand grades as options
            offers = all_offers
            all_grades = get_grades_for_brand(db, polymer, brand)
            if all_grades:
                lines.append(f"\nAll {brand} grades available ({polymer or 'any polymer'}): {', '.join(all_grades)}")
    else:
        # No brand known
        if grade:
            # Grade stated, brand not yet known → tell Mohini who makes it
            producers = get_producers_for_grade(db, grade)
            if producers:
                lines.append(f"\nProducers of {grade} ({polymer or 'PVC'}): {', '.join(producers)}")
                lines.append(f"  Ask customer which brand they prefer among: {', '.join(producers)}")
            elif producers is not None:
                lines.append(f"\nNote: {grade} is not commonly produced at scale in India. Confirm availability with RM.")
        # No brand known — show per-brand price ranges so user can pick
        if polymer:
            brand_ranges = get_price_ranges_by_brand(db, polymer)
            if brand_ranges:
                lines.append(f"\n{polymer} — Price Ranges by Brand (from active listings):")
                for br in brand_ranges:
                    brand_name = br["brand"]
                    grade_list = br["grades"]  # list of (grade_code, price)
                    lo, hi = br["min"], br["max"]
                    if lo == hi:
                        range_str = f"₹{lo:.2f}/kg"
                    else:
                        range_str = f"₹{lo:.2f}–₹{hi:.2f}/kg"
                    # Show each grade individually with price to ensure it correctly grabs Brand, Grade, Price
                    grade_lines = " | ".join(
                        f"Brand: {brand_name} | Grade: {g} | Base Price: ₹{p:.2f}/kg" for g, p in grade_list
                    )
                    line = f"  • {grade_lines}"
                    if freight_data:
                        fr = freight_data["freight_cost"]
                        gst = freight_data["tax_percent"]
                        landed_lo = (lo + fr) * (1 + gst)
                        landed_hi = (hi + fr) * (1 + gst)
                        if lo == hi:
                            line += f" | Landed {freight_data['location']}: ₹{landed_lo:.2f}/kg"
                        else:
                            line += f" | Landed {freight_data['location']}: ₹{landed_lo:.2f}–₹{landed_hi:.2f}/kg"
                        if qty_kg and lo > 0:
                            line += f" | Est. Total for {qty_kg/1000:.0f} MT: ₹{landed_lo * qty_kg:,.0f}–₹{landed_hi * qty_kg:,.0f}"
                    elif qty_kg and lo > 0:
                        line += f" | Est. Total for {qty_kg/1000:.0f} MT: ₹{lo * qty_kg:,.0f}–₹{hi * qty_kg:,.0f}"
                    lines.append(line)
                offers = []  # already displayed above
            else:
                offers = get_market_offers(db, polymer)
        else:
            offers = get_market_offers(db, None)

    if offers:
        label = f"Market Offers{f' — {brand}' if brand else ''}{f' ({polymer})' if polymer else ''}:"
        lines.append(f"\n{label}")
        for o in offers:
            base = o['price_per_kg']
            line = (
                f"  - {o['polymer']} | Brand: {o['brand']} | "
                f"Grade: {o['grade_code']} | "
                f"Base: ₹{base:.2f}/kg"
            )
            if freight_data and base > 0:
                freight = freight_data["freight_cost"]
                gst = freight_data["tax_percent"]
                landed = (base + freight) * (1 + gst)
                line += f" | Landed ({freight_data['location']}): ₹{landed:.2f}/kg"
                if qty_kg:
                    total = landed * qty_kg
                    line += f" | Est. Total for {qty_kg/1000:.0f} MT: ₹{total:,.0f}"
            elif qty_kg and base > 0:
                line += f" | Est. Total for {qty_kg/1000:.0f} MT: ₹{base * qty_kg:,.0f} (ex-warehouse)"
            line += f" | Stock: {o['stock_kg']} kg | MOQ: {o['moq_kg']} kg"
            lines.append(line)
    elif not inventory and not (brand and grade) and not polymer:
        lines.append("\nNo live offers found. Inform customer we will check and call back.")

    return "\n".join(lines)


def invalidate_cache():
    """Call this after any DB write to ensure fresh data on the next query."""
    _cache.clear()
