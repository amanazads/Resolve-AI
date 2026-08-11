import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Mock Orders Database
MOCK_ORDERS: Dict[str, Dict[str, Any]] = {
    "ORD123": {
        "order_id": "ORD123",
        "customer_id": "user123",
        "status": "Shipped",
        "product_name": "TechPro Wireless Headphones (Product A)",
        "quantity": 1,
        "total_amount": "$199.99",
        "order_date": "2026-08-08",
        "estimated_delivery": "2026-08-15",
        "carrier": "FedEx",
        "tracking_number": "FX-998877665",
        "cancellable": False
    },
    "ORD456": {
        "order_id": "ORD456",
        "customer_id": "user123",
        "status": "Processing",
        "product_name": "SmartFit Watch Ultra (Product B)",
        "quantity": 1,
        "total_amount": "$149.99",
        "order_date": "2026-08-11",
        "estimated_delivery": "2026-08-18",
        "carrier": "UPS",
        "tracking_number": "UPS-112233445",
        "cancellable": True
    },
    "ORD789": {
        "order_id": "ORD789",
        "customer_id": "CUST002",
        "status": "Delivered",
        "product_name": "TechPro Wireless Headphones (Product A)",
        "quantity": 2,
        "total_amount": "$399.98",
        "order_date": "2026-08-01",
        "estimated_delivery": "2026-08-05",
        "carrier": "USPS",
        "tracking_number": "USPS-445566778",
        "cancellable": False
    }
}

def get_order_status(order_id: str) -> Dict[str, Any]:
    """
    Retrieves the current status and shipping tracking info for a given order ID.
    """
    logger.info(f"Tool call: get_order_status('{order_id}')")
    order_key = order_id.strip().upper()
    order = MOCK_ORDERS.get(order_key)
    if not order:
        return {
            "success": False,
            "error": f"Order with ID '{order_id}' was not found in our database."
        }
    return {
        "success": True,
        "order_id": order["order_id"],
        "status": order["status"],
        "estimated_delivery": order["estimated_delivery"],
        "carrier": order["carrier"],
        "tracking_number": order["tracking_number"]
    }

def get_order_details(order_id: str) -> Dict[str, Any]:
    """
    Retrieves complete details of an order including products, price, and dates.
    """
    logger.info(f"Tool call: get_order_details('{order_id}')")
    order_key = order_id.strip().upper()
    order = MOCK_ORDERS.get(order_key)
    if not order:
        return {
            "success": False,
            "error": f"Order '{order_id}' not found."
        }
    return {
        "success": True,
        "details": order
    }

def cancel_order(order_id: str) -> Dict[str, Any]:
    """
    Attempts to cancel an active order if it is in Processing or Pending status.
    """
    logger.info(f"Tool call: cancel_order('{order_id}')")
    order_key = order_id.strip().upper()
    order = MOCK_ORDERS.get(order_key)
    
    if not order:
        return {
            "success": False,
            "message": f"Order ID '{order_id}' not found."
        }
        
    if order["status"] == "Cancelled":
        return {
            "success": True,
            "order_id": order_key,
            "message": f"Order '{order_key}' was already cancelled."
        }
        
    if not order.get("cancellable", False) or order["status"] in ["Shipped", "Delivered"]:
        return {
            "success": False,
            "order_id": order_key,
            "current_status": order["status"],
            "message": f"Order '{order_key}' cannot be cancelled because its status is already '{order['status']}'. It must be returned upon delivery according to our Refund Policy."
        }
        
    # Cancel the order
    order["status"] = "Cancelled"
    order["cancellable"] = False
    return {
        "success": True,
        "order_id": order_key,
        "status": "Cancelled",
        "message": f"Order '{order_key}' has been successfully cancelled and a full refund has been initiated to your original payment method."
    }
