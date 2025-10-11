# core/context_processors.py
from typing import Dict

CART_SESSION_KEY = "cart"

def cart(request) -> Dict[str, int]:
    raw = request.session.get(CART_SESSION_KEY, {})
    count = 0
    if isinstance(raw, dict):
        for v in raw.values():
            try:
                q = int(v)
                if q > 0:
                    count += q
            except Exception:
                continue
    return {"cart_count": count}
