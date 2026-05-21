import base64
import hashlib
import hmac


def compute_hmac_base64(serialized_payload: str, secret: str) -> str:
    """Sign a serialized payload using HMAC-SHA256."""
    digest = hmac.new(
        secret.encode("utf-8"),
        serialized_payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def is_signature_valid(serialized_payload: str, signature: str | None, secret: str) -> bool:
    """Compare an event signature in constant time."""
    expected_signature = compute_hmac_base64(serialized_payload, secret)
    expected = expected_signature.encode("utf-8")
    received = (signature or "").encode("utf-8")
    return len(expected) == len(received) and hmac.compare_digest(expected, received)
