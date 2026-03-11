"""
Quick offline test for get-price enhancements.
No server/DB needed – tests the logic directly.
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.bsb_api_client import CallAgentService

# Minimal stub so we don't need a real DB session
class FakeDB:
    pass

svc = CallAgentService(FakeDB())

PASS = "[PASS]"
FAIL = "[FAIL]"

def check(label, result, *, found=True, mode=None, min_results=1):
    ok = True
    if result.get("found") != found:
        ok = False
        print(f"{FAIL} [{label}] found={result.get('found')} expected {found}")
    if mode and result.get("mode") != mode:
        ok = False
        print(f"{FAIL} [{label}] mode={result.get('mode')} expected {mode}")
    if found:
        n = result.get("total", 0)
        if n < min_results:
            ok = False
            print(f"{FAIL} [{label}] total={n} expected >={min_results}")
    if ok:
        total = result.get("total", 0)
        locs = [r.get("location") for r in result.get("results", [])]
        pkgs = [r.get("price_per_kg") for r in result.get("results", [])]
        print(f"{PASS} [{label}] mode={result.get('mode')} total={total} locations={locs} price_per_kg={pkgs[:3]}")

print("\n======= GET-PRICE TEST SUITE =======\n")

# 1. Exact match – brand + grade
r = svc.get_price("Hygain", "HS1000R")
check("Exact brand+grade", r, found=True, mode="brand_grade")

# 2. Fuzzy grade – mis-heard character
r = svc.get_price("Formosa", "S65")
check("Fuzzy grade S65 to SG5", r, found=True, mode="brand_grade")

# 3. Fuzzy grade - zero vs O confusion
r = svc.get_price("Hygain", "HS10OOR")
check("Fuzzy grade HS10OOR to HS1000R", r, found=True, mode="brand_grade")

# 4. Brand only – all grades for Formosa
r = svc.get_price("Formosa", "")
check("Brand only – Formosa", r, found=True, mode="brand_only", min_results=2)

# 5. Grade only – all brands with SG5
r = svc.get_price("", "SG5")
check("Grade only – SG5", r, found=True, mode="grade_only", min_results=1)

# 6. Grade only – SDF
r = svc.get_price("", "SDF")
check("Grade only – SDF", r, found=True, mode="grade_only")

# 7. Price per kg is present in results
r = svc.get_price("Hygain", "HS1000R")
result_items = r.get("results", [])
has_pkg = all("price_per_kg" in item for item in result_items)
print(f"{'✅ PASS' if has_pkg else '❌ FAIL'} [price_per_kg field present] items={len(result_items)}")

# 8. Location field present
r = svc.get_price("Hygain", "HS1000R")
has_loc = all("location" in item for item in r.get("results", []))
print(f"{'✅ PASS' if has_loc else '❌ FAIL'} [location field present]")

# 9. No match
r = svc.get_price("XUnknownBrand", "ZZZ999")
check("No match", r, found=False)

# 10. Both empty
r = svc.get_price("", "")
check("Both empty", r, found=False)

print("\n===================================\n")
