import base64
import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.hashes import SHA256

def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value+"="*((4-len(value)%4)%4))

def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

def canonical_jwk(jwk: dict) -> str:
    if jwk.get("kty")!="EC" or jwk.get("crv")!="P-256" or not jwk.get("x") or not jwk.get("y"):
        raise ValueError("Only EC P-256 public keys are accepted")
    return json.dumps({"crv":"P-256","kty":"EC","x":jwk["x"],"y":jwk["y"]},separators=(",",":"),sort_keys=True)

def thumbprint(jwk: dict) -> str:
    return hashlib.sha256(canonical_jwk(jwk).encode()).hexdigest()

def verify_signature(jwk: dict,challenge: bytes,signature_b64: str) -> None:
    canonical_jwk(jwk)
    x=int.from_bytes(b64url_decode(jwk["x"]),"big"); y=int.from_bytes(b64url_decode(jwk["y"]),"big")
    signature=b64url_decode(signature_b64)
    if len(signature)!=64: raise ValueError("Signature must be a 64-byte P-256 signature")
    der=encode_dss_signature(int.from_bytes(signature[:32],"big"),int.from_bytes(signature[32:],"big"))
    try: ec.EllipticCurvePublicNumbers(x,y,ec.SECP256R1()).public_key().verify(der,challenge,ec.ECDSA(SHA256()))
    except (InvalidSignature,ValueError) as exc: raise ValueError("Invalid device signature") from exc
