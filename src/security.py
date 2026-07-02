import os
import json
import base64
import logging
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

logger = logging.getLogger("project_vigil.security")

# Resolve deterministic persistent directory in AppData to prevent recreating on runtime
appdata = os.environ.get("APPDATA")
if appdata:
    BASE_DIR = os.path.join(appdata, "ProjectVigil")
else:
    BASE_DIR = os.path.join(os.path.expanduser("~"), ".project_vigil")

os.makedirs(BASE_DIR, exist_ok=True)
KEY_FILE = os.path.join(BASE_DIR, "secret.key")


def get_or_create_master_key() -> bytes:
    """
    Retrieves the master secret key from the environment variable, or loads it
    from a local persistent file. Generates a new random one if not set.
    """
    # 1. Try environment variable first
    env_key = os.environ.get("PROJECT_VIGIL_SECRET_KEY")
    if env_key:
        try:
            return base64.urlsafe_b64decode(env_key.encode())
        except Exception:
            # If not valid base64, hash it to ensure 32-bytes length
            import hashlib
            return hashlib.sha256(env_key.encode()).digest()

    # 2. Try local file
    if os.path.exists(KEY_FILE):
        try:
            with open(KEY_FILE, "rb") as f:
                content = f.read().strip()
                return base64.urlsafe_b64decode(content)
        except Exception as e:
            logger.error(f"[Security] Failed reading key file: {e}. Generating new key.")

    # 3. Generate new key
    new_key = os.urandom(32)
    try:
        with open(KEY_FILE, "wb") as f:
            f.write(base64.urlsafe_b64encode(new_key))
        logger.info(f"[Security] Generated new persistent master key and saved to {KEY_FILE}")
    except Exception as e:
        logger.error(f"[Security] Failed writing new key file: {e}")
    return new_key


def derive_fernet_key(master_key: bytes, salt: bytes) -> bytes:
    """
    Derives a 32-byte URL-safe base64-encoded key from the master key and salt using PBKDF2.
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=10000,  # 10k iterations is fast for local bot startups but cryptographically robust
    )
    return base64.urlsafe_b64encode(kdf.derive(master_key))


def encrypt_token(token: str) -> str:
    """
    Encrypts a token string using Fernet and a unique cryptographic salt.
    Returns a serialized JSON payload containing the base64 salt and base64 ciphertext.
    """
    if not token or not token.strip():
        return ""
    
    try:
        master_key = get_or_create_master_key()
        salt = os.urandom(16)
        fernet_key = derive_fernet_key(master_key, salt)
        f = Fernet(fernet_key)
        ciphertext = f.encrypt(token.encode("utf-8"))
        
        payload = {
            "salt": base64.b64encode(salt).decode("utf-8"),
            "ciphertext": ciphertext.decode("utf-8")
        }
        return json.dumps(payload)
    except Exception as e:
        logger.exception(f"[Security] Token encryption failed: {e}")
        return ""


def decrypt_token(encrypted_payload: str) -> str:
    """
    Decrypts a token from its serialized JSON payload.
    If the payload is not in JSON format, returns the payload as-is for legacy/backward compatibility.
    """
    if not encrypted_payload or not encrypted_payload.strip():
        return ""
    
    # Check if payload is valid JSON (encrypted format)
    try:
        payload = json.loads(encrypted_payload)
        if not isinstance(payload, dict) or "salt" not in payload or "ciphertext" not in payload:
            # Fallback: not our encrypted JSON format, return raw string
            return encrypted_payload
    except json.JSONDecodeError:
        # Fallback: raw plain text token (backward compatible)
        return encrypted_payload

    try:
        master_key = get_or_create_master_key()
        salt = base64.b64decode(payload["salt"].encode("utf-8"))
        ciphertext = payload["ciphertext"].encode("utf-8")
        
        fernet_key = derive_fernet_key(master_key, salt)
        f = Fernet(fernet_key)
        decrypted = f.decrypt(ciphertext)
        return decrypted.decode("utf-8")
    except Exception as e:
        logger.error(f"[Security] Token decryption failed: {e}")
        return ""
