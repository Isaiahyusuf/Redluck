# encryption.py - Secure encryption for private keys
import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend


def _get_encryption_key() -> bytes:
    """
    Derive encryption key from environment variable
    Uses PBKDF2 for key derivation with a stable salt
    """
    # Get master password from environment
    master_password = os.getenv("ENCRYPTION_KEY", "")
    
    if not master_password:
        raise ValueError(
            "ENCRYPTION_KEY environment variable is required for secure wallet storage. "
            "Please set a strong random key (min 32 characters)."
        )
    
    # Use a STABLE salt that won't change
    # This ensures encrypted keys remain readable even if other configs change
    # In production, consider storing this in a secure config or dedicated env var
    # For now, we use a fixed but unique-per-installation salt
    salt = b'RedLuckLotto01'  # 16 bytes, stable across restarts
    
    # Derive a key using PBKDF2
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
        backend=default_backend()
    )
    
    key = base64.urlsafe_b64encode(kdf.derive(master_password.encode()))
    return key


def encrypt_private_key(private_key_hex: str) -> str:
    """
    Encrypt a private key hex string
    Returns encrypted string (safe to store in database)
    """
    try:
        key = _get_encryption_key()
        fernet = Fernet(key)
        
        # Encrypt the hex private key
        encrypted = fernet.encrypt(private_key_hex.encode())
        
        # Return as base64 string for storage
        return base64.b64encode(encrypted).decode('utf-8')
    except Exception as e:
        raise RuntimeError(f"Failed to encrypt private key: {str(e)}")


def _get_legacy_encryption_key() -> bytes | None:
    """
    Get the OLD encryption key (BOT_TOKEN-derived salt) for migration
    This is needed to decrypt keys created before the salt was fixed
    """
    master_password = os.getenv("ENCRYPTION_KEY", "")
    if not master_password:
        return None
    
    # OLD salt derivation (from BOT_TOKEN) - for backward compatibility
    bot_token = os.getenv("BOT_TOKEN", "")
    if not bot_token:
        return None
        
    salt = base64.b64encode(bot_token[:16].encode()).ljust(16, b'0')[:16]
    
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
        backend=default_backend()
    )
    
    key = base64.urlsafe_b64encode(kdf.derive(master_password.encode()))
    return key


def decrypt_private_key(encrypted_key: str) -> str:
    """
    Decrypt an encrypted private key
    Tries new salt first, then falls back to legacy salt for migration
    Returns the original hex private key string
    """
    # Try with new stable salt first
    try:
        key = _get_encryption_key()
        fernet = Fernet(key)
        encrypted_bytes = base64.b64decode(encrypted_key.encode('utf-8'))
        decrypted = fernet.decrypt(encrypted_bytes)
        return decrypted.decode('utf-8')
    except Exception:
        pass  # Try legacy salt
    
    # Try with legacy BOT_TOKEN-derived salt
    try:
        legacy_key = _get_legacy_encryption_key()
        if legacy_key:
            fernet = Fernet(legacy_key)
            encrypted_bytes = base64.b64decode(encrypted_key.encode('utf-8'))
            decrypted = fernet.decrypt(encrypted_bytes)
            print("⚠️ Decrypted with legacy salt - needs re-encryption")
            return decrypted.decode('utf-8')
    except Exception:
        pass
    
    raise RuntimeError(f"Failed to decrypt private key with both current and legacy salts")


def is_encryption_configured() -> bool:
    """Check if encryption is properly configured"""
    try:
        _get_encryption_key()
        return True
    except ValueError:
        return False
