from __future__ import annotations
from typing import Dict, Any

def detect_platform(html: str, final_url: str) -> Dict[str, Any]:
    h = (html or "").lower()
    u = (final_url or "").lower()

    shopify_strong = [
        "cdn.shopify.com",
        "myshopify.com",
        "x-shopify-stage",
        "shopify-checkout",
        "shopify-pay",
        "shopify.theme",
    ]
    shopify_weak = ["/cart", "/collections", "/products"]
    wordpress_signals = ["/wp-content/", "/wp-json", "wordpress", "wp-emoji", "wp-block"]

    is_shopify_strong = any(sig in h or sig in u for sig in shopify_strong)
    is_shopify_weak = any(sig in h or sig in u for sig in shopify_weak)
    is_wp = any(sig in h or sig in u for sig in wordpress_signals)

    if is_shopify_strong and not is_wp:
        return {"name": "Shopify", "confidence": 0.9}
    if is_shopify_strong and is_wp:
        return {"name": "Shopify/WordPress?", "confidence": 0.6}
    if is_shopify_weak and not is_wp:
        return {"name": "Maybe Shopify", "confidence": 0.4}
    if is_wp and not is_shopify_strong and not is_shopify_weak:
        return {"name": "WordPress", "confidence": 0.9}
    return {"name": "Unknown", "confidence": 0.3}
