import json

data = json.load(open("prices_cache.json", encoding="utf-8"))
updated = 0
for item in data:
    brand = item.get("brand", "").lower()
    if brand in ("hygain", "hanwha"):
        if item.get("location") == "Solan":
            item["location"] = "Solan, Himachal Pradesh"
            updated += 1
            print("Updated:", item["brand"], "/", item["grade"])

with open("prices_cache.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print("Done. Updated", updated, "entries.")
