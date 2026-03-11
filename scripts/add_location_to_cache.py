"""
add_location_to_cache.py
Adds a 'location' field to every entry in prices_cache.json.

Brand → location mapping rules (origin / typical stock location):
- Iran brands (BIPC etc.)        → "Mumbai Port"
- Korean brands (LG, Lotte, etc.)→ "Mumbai Port"
- Taiwan brands (Formosa etc.)   → "Mumbai Port"
- Indian brands (Reliance, GAIL, IOCL etc.) → "Ahmedabad" (BSB's home)
- Hygain                         → "Surat"
- Others / unknown               → "Mumbai"

Run once to migrate the cache in-place.
"""

import json
import os
import re

CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "prices_cache.json")
CACHE_PATH = os.path.normpath(CACHE_PATH)


# Brand keyword → location
BRAND_LOCATION: dict[str, str] = {
    # Indian origins
    "reliance": "Ahmedabad",
    "ril":      "Ahmedabad",
    "iocl":     "Ahmedabad",
    "gail":     "Ahmedabad",
    "hpcl":     "Ahmedabad",
    "opal":     "Surat",
    "ongc":     "Ahmedabad",
    "haldia":   "Kolkata",
    "chemplast":"Chennai",
    "dcw":      "Chennai",
    "finolex":  "Pune",
    "nayara":   "Vadinar",
    "mrpl":     "Mangalore",
    # Hygain
    "hygain":   "Surat",
    # Taiwan
    "formosa":  "Mumbai Port",
    # Korea
    "lg":       "Mumbai Port",
    "lotte":    "Mumbai Port",
    "hanwha":   "Mumbai Port",
    "sk":       "Mumbai Port",
    "lg chem":  "Mumbai Port",
    # Iran
    "bipc":     "Mumbai Port",
    "jam":      "Mumbai Port",
    "arya":     "Mumbai Port",
    "more":     "Mumbai Port",
    # Saudi/Middle East
    "sabic":    "Mumbai Port",
    "tasnee":   "Mumbai Port",
    "ibn":      "Mumbai Port",
    "sipchem":  "Mumbai Port",
    # Europe/US
    "basell":   "Mumbai Port",
    "borealis": "Mumbai Port",
    "lyondell": "Mumbai Port",
    "ineos":    "Mumbai Port",
    "exxon":    "Mumbai Port",
    "dow":      "Mumbai Port",
}


def assign_location(brand: str) -> str:
    b = brand.lower().strip()
    for kw, loc in BRAND_LOCATION.items():
        if kw in b:
            return loc
    return "Mumbai"  # fallback


def main():
    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    updated = 0
    for item in data:
        if "location" not in item or not item["location"]:
            item["location"] = assign_location(item.get("brand", ""))
            updated += 1

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Done. Added/updated 'location' for {updated} entries in prices_cache.json")


if __name__ == "__main__":
    main()
