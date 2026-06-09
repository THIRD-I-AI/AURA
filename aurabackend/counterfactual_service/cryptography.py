import base64
import json
import logging
from typing import Dict, Optional, Tuple

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

logger = logging.getLogger("aura.cryptography")

# Mock database of active keys for the MVP
# In production, this would be backed by HashiCorp Vault or Azure KeyVault.
_key_store: Dict[str, ed25519.Ed25519PrivateKey] = {}
_revoked_keys: set = set()

def generate_agent_keypair(kid: str) -> Tuple[str, str]:
    """
    Generates an ED25519 keypair for an AI agent.
    Returns (private_key_pem, public_key_pem)

    Note: ED25519 *signatures* are deterministic (no per-signature nonce);
    key *generation* uses the OS CSPRNG. Don't conflate the two.
    """
    private_key = ed25519.Ed25519PrivateKey.generate()
    _key_store[kid] = private_key
    
    # Export private key
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    
    # Export public key
    public_key = private_key.public_key()
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    
    logger.info(f"Generated new ED25519 keypair for kid={kid}")
    return private_bytes.decode('utf-8'), public_bytes.decode('utf-8')

def sign_payload(kid: str, payload: Dict) -> str:
    """
    Deterministically signs a JSON payload using the agent's ED25519 private key.
    Returns a Base64 encoded signature (64 bytes).
    """
    if kid not in _key_store:
        raise ValueError(f"Key ID {kid} not found.")
    # Soft revocation must actually block NEW signatures (historical ones stay
    # valid via JWKS). Without this guard, revocation was cosmetic — a revoked
    # key could still sign, contradicting the docstring + the compliance promise.
    if kid in _revoked_keys:
        raise ValueError(f"Key ID {kid} is revoked; it cannot sign new payloads.")

    private_key = _key_store[kid]
    
    # Serialize payload deterministically
    message = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    
    # ED25519 signature generation
    signature = private_key.sign(message)
    
    # Return Base64 encoded signature
    return base64.b64encode(signature).decode('utf-8')

def get_jwks() -> Dict:
    """
    Returns the JSON Web Key Set (JWKS) for all active and revoked keys.
    Downstream verifiers use this to fetch public keys to verify signatures.
    """
    keys = []
    for kid, private_key in _key_store.items():
        public_key = private_key.public_key()
        
        # Extract raw bytes for JWK format
        raw_public_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        
        jwk = {
            "kty": "OKP",
            "crv": "Ed25519",
            "kid": kid,
            "x": base64.urlsafe_b64encode(raw_public_bytes).decode('utf-8').rstrip("="),
            "revoked": kid in _revoked_keys
        }
        keys.append(jwk)
        
    return {"keys": keys}

def soft_revoke_key(kid: str) -> None:
    """
    Soft Revocation: Flags a key as compromised for future use, 
    but maintains mathematical validity of historical signatures.
    """
    if kid in _key_store:
        _revoked_keys.add(kid)
        logger.warning(f"Key {kid} has been soft-revoked. Historical signatures remain valid, but future signatures will be rejected.")
    else:
        logger.error(f"Attempted to revoke unknown key: {kid}")
