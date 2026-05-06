import logging
import requests
from typing import Optional
from app.core.config import settings

logger = logging.getLogger("zoho_service")

# Module level cache for access token to avoid refreshing on every single call
_ZOHO_ACCESS_TOKEN: Optional[str] = None


def _get_access_token() -> Optional[str]:
    """Fetch or refresh the Zoho OAuth2 Access Token using the refresh token."""
    global _ZOHO_ACCESS_TOKEN
    
    # If we had a mechanism to check expiry, we'd do it here. 
    # For now we'll just try to fetch a new one if it's None.
    # Note: Access tokens last 1 hour. In a robust setup, caching with a TTL is better.
    # Let's just blindly refresh it for safety in this simple wrapper, 
    # or rely on catching 401 Unauthorized later. To be safe, we fetch every time.
    
    url = f"{settings.ZOHO_ACCOUNTS_URL}/oauth/v2/token"
    payload = {
        "refresh_token": settings.ZOHO_REFRESH_TOKEN,
        "client_id": settings.ZOHO_CLIENT_ID,
        "client_secret": settings.ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token"
    }

    try:
        r = requests.post(url, data=payload, timeout=8)
        r.raise_for_status()
        data = r.json()
        token = data.get("access_token")
        if token:
            _ZOHO_ACCESS_TOKEN = token
            logger.info("Zoho CRM access token refreshed successfully.")
            return token
        else:
            logger.error(f"Zoho token response error: {data}")
            return None
    except Exception as e:
        logger.error(f"Failed to refresh Zoho access token: {e}")
        return None


def push_to_zoho_lead(
    name: str, 
    phone: str, 
    company_name: str, 
    material: str, 
    quantity: str, 
    location: str, 
    brand: str,
    quoted_price: str
) -> bool:
    """
    Pushes an enquiry directly into Zoho CRM as a Lead.
    """
    token = _get_access_token()
    if not token:
        logger.error("Cannot push to Zoho CRM: No access token available.")
        return False

    url = f"{settings.ZOHO_API_URL}/crm/v2/Leads"
    
    headers = {
        "Authorization": f"Zoho-oauthtoken {token}",
        "Content-Type": "application/json"
    }

    # Zoho CRM requires 'Last_Name' for Leads by default.
    # We put the full collected name in Last_Name, and set company name.
    # We format the rest into the Description.
    description = (
        f"Voice AI Agent Enquiry\n"
        f"Material: {material}\n"
        f"Brand: {brand}\n"
        f"Quantity: {quantity}\n"
        f"Location: {location}\n"
        f"Quoted Price: {quoted_price}"
    )

    payload = {
        "data": [
            {
                "Last_Name": name if name.strip() else "Unknown Caller",
                "Company": company_name if company_name.strip() else "Unknown Company",
                "Mobile": phone,
                "Lead_Source": "Voice AI Agent",
                "Description": description,
                "City": location
            }
        ]
    }

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=8)
        r.raise_for_status()
        resp_data = r.json()
        logger.info(f"Successfully pushed Lead to Zoho CRM: {resp_data}")
        return True
    except Exception as e:
        logger.error(f"Failed to push Lead to Zoho CRM: {e}")
        return False
