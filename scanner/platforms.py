from __future__ import annotations
from typing import Dict, Any

def detect_platform(html: str, final_url: str) -> Dict[str, Any]:
    h = (html or "").lower()
    u = (final_url or "").lower()

    shopify_signals = ["cdn.shopify.com", "shopify.theme", "myshopify", "/cart", "/collections", "/products"]
    wordpress_signals = ["/wp-content/", "/wp-json", "wordpress", "wp-emoji", "wp-block"]

    is_shopify = any(sig in h or sig in u for sig in shopify_signals)
    is_wp = any(sig in h or sig in u for sig in wordpress_signals)

    if is_shopify and not is_wp:
        return {"name": "Shopify", "confidence": 0.9}
    if is_wp and not is_shopify:
        return {"name": "WordPress", "confidence": 0.9}
    if is_shopify and is_wp:
        return {"name": "Shopify/WordPress?", "confidence": 0.5}
    return {"name": "Unknown", "confidence": 0.3}
