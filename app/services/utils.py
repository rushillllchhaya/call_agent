from typing import Optional

def identify_role(text: str) -> Optional[str]:
    text = text.lower()
    if "buy" in text or "looking for" in text or "need" in text:
        return "buyer"
    if "sell" in text or "have" in text or "offering" in text:
        return "seller"
    return None
