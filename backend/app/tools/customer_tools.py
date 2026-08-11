import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

MOCK_CUSTOMERS: Dict[str, Dict[str, Any]] = {
    "user123": {
        "customer_id": "user123",
        "name": "Jane Doe",
        "email": "jane.doe@example.com",
        "member_tier": "Gold VIP",
        "total_orders": 4,
        "phone": "+1 (555) 234-5678"
    },
    "CUST002": {
        "customer_id": "CUST002",
        "name": "Alex Smith",
        "email": "alex.smith@example.com",
        "member_tier": "Standard",
        "total_orders": 1,
        "phone": "+1 (555) 987-6543"
    }
}

def get_customer_details(customer_id: str) -> Dict[str, Any]:
    """
    Retrieves profile information for a customer by ID.
    """
    logger.info(f"Tool call: get_customer_details('{customer_id}')")
    customer = MOCK_CUSTOMERS.get(customer_id)
    if not customer:
        return {
            "success": False,
            "error": f"Customer ID '{customer_id}' not found."
        }
    return {
        "success": True,
        "customer": customer
    }
