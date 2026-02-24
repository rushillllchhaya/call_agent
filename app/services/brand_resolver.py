"""
brand_resolver.py
─────────────────
Resolves (polymer_type, brand_name) → grade_code from the LIVE production DB.

The production table `brand_grade_maps` is the single source of truth:
  - brand_id   → FK to brands.id
  - grade_id   → FK to grades.id (grades = polymer types: PVC=1, HDPE=2, etc.)
  - name       → the grade code string (e.g. "SG50", "H030 SG")

Priority:
  1. Live DB lookup via brand_grade_maps (primary — works against demo + production)
  2. Static fallback map (for when the caller mentions a brand not yet in DB,
     or when DB is unreachable; purely a safety net)

Why this approach:
  The demo DB is the same schema as production. By querying brand_grade_maps live
  we always get up-to-date data, and new brands/grades added by admins are
  automatically available to Mohini without any code changes.
"""

from __future__ import annotations
from sqlalchemy.orm import Session
from app.core.models import Brand, BrandGradeMap, PolymerType
import re
import time

# ── TTL Cache ──────────────────────────────────────────────────────────────────
_resolve_cache: dict = {}
_ALL_BRANDS_CACHE: tuple[float, list[dict]] = (0.0, [])
CACHE_TTL = 120  # seconds (2 minutes)


# ── Static fallback (only used if brand not in DB at all) ─────────────────────
# Keys: (polymer_keyword, brand_keyword). Values: grade code string.
# Keep in sync with real data. This is a last-resort safety net only.
FALLBACK_BRAND_GRADE_MAP: dict[tuple[str, str], str] = {
    # HDPE
    ("hdpe", "gail"):      "H030 SG",
    ("hdpe", "reliance"):  "2426H",
    ("hdpe", "iocl"):      "220H",
    ("hdpe", "haldia"):    "2426H",
    ("hdpe", "opal"):      "M80064",
    # PVC
    ("pvc",  "reliance"):  "K-67",
    ("pvc",  "finolex"):   "K-67",
    ("pvc",  "chemplast"): "K-70",
    ("pvc",  "dcw"):       "SG-5",
    ("pvc",  "nayara"):    "K-67",
    # PP
    ("pp",   "reliance"):  "H030 SG",
    ("pp",   "iocl"):      "1030 EG",
    ("pp",   "gail"):      "HP-500N",
    ("pp",   "hpcl"):      "HP-500N",
    # LDPE
    ("ldpe", "iocl"):      "LD-170",
    ("ldpe", "reliance"):  "LD-150",
    # LLDPE
    ("lldpe", "reliance"): "LL-1002",
    ("lldpe", "iocl"):     "LL-1004",
    # PET
    ("pet",  "reliance"):  "IV-0.84",
}

# Brand name aliases: what callers say → canonical search keyword
BRAND_ALIASES: dict[str, str] = {
    "ril":        "reliance",
    "rpl":        "reliance",
    "hpcl":       "hpcl",
    "hp":         "hpcl",
    "gsl":        "gail",
    "ioc":        "iocl",
    "nrl":        "mrpl",
    # Spelling corrections (common Indian caller patterns)
    "reliens":    "reliance",
    "relaince":   "reliance",
    "gayel":      "gail",
}


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse spaces."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _canonical_brand(raw: str) -> str:
    """Return a canonical brand keyword for matching."""
    n = _normalize(raw)
    for alias, canon in BRAND_ALIASES.items():
        if alias in n:
            return canon
    # Return first word as best-guess keyword
    return n.split()[0] if n else n


def _get_all_brands_cached(db: Session) -> list[dict]:
    """Fetches ALL brands with TTL cache. Returns list of {id, name_lower}."""
    global _ALL_BRANDS_CACHE
    ts, cached = _ALL_BRANDS_CACHE
    if time.time() - ts < CACHE_TTL:
        return cached
    rows = db.query(Brand.id, Brand.name).filter(Brand.status == "1").all()
    result = [{"id": r.id, "name": r.name.lower().strip()} for r in rows]
    _ALL_BRANDS_CACHE = (time.time(), result)
    return result


def _live_db_lookup(db: Session, polymer_kw: str, brand_kw: str) -> str | None:
    """
    Queries brand_grade_maps for a 1-to-1 brand+polymer → grade code mapping.

    Logic:
      1. Find brands whose name contains brand_kw (e.g. "reliance" in "Reliance Industries")
      2. Find polymer type (grades table) whose name matches polymer_kw
      3. Query brand_grade_maps WHERE brand_id IN (...) AND grade_id = polymer.id
      4. If exactly 1 distinct grade code name → return it (1-to-1 confirmed)
      5. If 0 or multiple → return None (ambiguous, let AI ask)
    """
    cache_key = f"live_{polymer_kw}_{brand_kw}"
    entry = _resolve_cache.get(cache_key)
    if entry and (time.time() - entry[0]) < CACHE_TTL:
        return entry[1]

    try:
        # Step 1: Find matching brand IDs
        all_brands = _get_all_brands_cached(db)
        matched_brand_ids = [
            str(b["id"]) for b in all_brands if brand_kw in b["name"]
        ]
        if not matched_brand_ids:
            print(f"[BrandResolver] No brand found matching '{brand_kw}' in DB")
            _resolve_cache[cache_key] = (time.time(), None)
            return None

        # Step 2: Find polymer type row
        polymer_row = db.query(PolymerType).filter(
            PolymerType.name.ilike(f"%{polymer_kw}%"),
            PolymerType.status == "1"
        ).first()
        if not polymer_row:
            print(f"[BrandResolver] No polymer found matching '{polymer_kw}'")
            _resolve_cache[cache_key] = (time.time(), None)
            return None

        polymer_id_str = str(polymer_row.id)

        # Step 3: Query brand_grade_maps
        maps = db.query(BrandGradeMap).filter(
            BrandGradeMap.brand_id.in_(matched_brand_ids),
            BrandGradeMap.grade_id == polymer_id_str,
            BrandGradeMap.status == "1",
        ).all()

        if not maps:
            print(f"[BrandResolver] No brand_grade_maps entries for brand={matched_brand_ids}, polymer_id={polymer_id_str}")
            _resolve_cache[cache_key] = (time.time(), None)
            return None

        # Step 4: Check uniqueness
        unique_grade_codes = list({m.name for m in maps if m.name})
        if len(unique_grade_codes) == 1:
            result = unique_grade_codes[0]
            print(f"[BrandResolver] Live DB match: ({polymer_kw}, {brand_kw}) → {result}")
            _resolve_cache[cache_key] = (time.time(), result)
            return result

        # Multiple grades — too ambiguous to auto-resolve
        print(f"[BrandResolver] Multiple grade codes for ({polymer_kw}, {brand_kw}): {unique_grade_codes} — not auto-resolving")
        _resolve_cache[cache_key] = (time.time(), None)
        return None

    except Exception as e:
        print(f"[BrandResolver] DB lookup error: {e}")
        _resolve_cache[cache_key] = (time.time(), None)
        return None


# ── Public API ─────────────────────────────────────────────────────────────────

def resolve_grade(
    db: Session,
    polymer_raw: str,
    brand_raw: str,
) -> str | None:
    """
    Given what the caller said for polymer and brand, return the resolved grade code
    if a 1-to-1 mapping exists in the production DB. Returns None if ambiguous/unknown.

    Examples (against real data):
        resolve_grade(db, "HDPE", "GAIL")     → "H030 SG"  (from brand_grade_maps)
        resolve_grade(db, "PVC", "Reliance")  → "K-67"     (from brand_grade_maps or fallback)
        resolve_grade(db, "HDPE", "Reliance") → None (multiple grades → AI will ask)
    """
    if not polymer_raw or not brand_raw:
        return None

    polymer_kw = _normalize(polymer_raw).split()[0]   # e.g. "hdpe"
    brand_kw   = _canonical_brand(brand_raw)           # e.g. "gail"

    # 1. PRIMARY: Query the real brand_grade_maps table in the live DB
    db_result = _live_db_lookup(db, polymer_kw, brand_kw)
    if db_result:
        return db_result

    # 2. FALLBACK: Static map (safety net for brands not in DB yet)
    seeded = FALLBACK_BRAND_GRADE_MAP.get((polymer_kw, brand_kw))
    if seeded:
        print(f"[BrandResolver] Fallback match: ({polymer_kw}, {brand_kw}) → {seeded}")
        return seeded

    print(f"[BrandResolver] No mapping found for ({polymer_kw}, {brand_kw})")
    return None


def get_brands_for_polymer(db: Session, polymer_raw: str) -> list[str]:
    """
    Returns all brand names that have entries in brand_grade_maps for
    a given polymer. Used by the AI to know which brands it can suggest.
    Cached automatically via _get_all_brands_cached.
    """
    if not polymer_raw:
        return []

    polymer_kw = _normalize(polymer_raw).split()[0]
    cache_key = f"brands_for_{polymer_kw}"
    entry = _resolve_cache.get(cache_key)
    if entry and (time.time() - entry[0]) < CACHE_TTL:
        return entry[1]

    try:
        polymer_row = db.query(PolymerType).filter(
            PolymerType.name.ilike(f"%{polymer_kw}%"),
            PolymerType.status == "1"
        ).first()
        if not polymer_row:
            return []

        # Get all brand IDs that have maps for this polymer
        maps = db.query(BrandGradeMap.brand_id).filter(
            BrandGradeMap.grade_id == str(polymer_row.id),
            BrandGradeMap.status == "1"
        ).distinct().all()
        brand_ids = [str(m.brand_id) for m in maps]

        if not brand_ids:
            return []

        # Fetch brand names for those IDs
        brands = db.query(Brand.name).filter(
            Brand.id.in_([int(b) for b in brand_ids if b.isdigit()]),
            Brand.status == "1"
        ).all()

        result = [b.name for b in brands]
        _resolve_cache[cache_key] = (time.time(), result)
        return result

    except Exception as e:
        print(f"[BrandResolver] get_brands_for_polymer error: {e}")
        return []


def invalidate_cache():
    """Call after admin updates brands/grade_maps to refresh lookups."""
    global _ALL_BRANDS_CACHE
    _resolve_cache.clear()
    _ALL_BRANDS_CACHE = (0.0, [])
