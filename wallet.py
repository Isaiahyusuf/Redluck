# Wallet.py - Real Solana Mainnet Wallet Management
# SQLite fully removed. PostgreSQL only.
# Now uses centralized RPC manager for load balancing and failover.

import os
import time
from db import get_db_conn, q

# PostgreSQL only - no SQLite fallback
import psycopg2
DBIntegrityError = psycopg2.IntegrityError
from decimal import Decimal
from typing import Optional, List, Dict
import asyncio

from encryption import encrypt_private_key, decrypt_private_key, is_encryption_configured
from cache_layer import get_cache

try:
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from solders.system_program import transfer, TransferParams
    from solders.transaction import Transaction
    from solders.message import Message
except ImportError:
    from solana.keypair import Keypair
    from solana.publickey import PublicKey as Pubkey

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed

# ==============================================================================
# RPC CONFIGURATION - Uses centralized RPC Manager
# ==============================================================================
from rpc_manager import get_rpc_manager

RPC_TIMEOUT = 10  # Timeout per RPC request in seconds
MAX_WALLETS_PER_USER = 1

# RPC Endpoints - will be populated from environment
FALLBACK_RPC = "https://api.mainnet-beta.solana.com"
_solana_rpc = os.getenv("SOLANA_RPC", "")
_helius_rpc = os.getenv("HELIUS_RPC", "")

# Build RPC_ENDPOINTS list with fallbacks
RPC_ENDPOINTS = []
if _helius_rpc:
    RPC_ENDPOINTS.append(_helius_rpc)
if _solana_rpc and _solana_rpc != FALLBACK_RPC:
    RPC_ENDPOINTS.append(_solana_rpc)
RPC_ENDPOINTS.append(FALLBACK_RPC)  # Always have fallback

# Get singleton instances
_rpc_manager = None
_cache = None

def _get_rpc():
    global _rpc_manager
    if _rpc_manager is None:
        _rpc_manager = get_rpc_manager()
    return _rpc_manager

def _get_cache():
    global _cache
    if _cache is None:
        _cache = get_cache()
    return _cache


def get_rpc_list() -> list:
    """Get the list of RPC endpoints from the RPC manager."""
    rpc = _get_rpc()
    return [ep.url for ep in rpc._endpoints]


async def get_working_rpc() -> str:
    """Get a working RPC endpoint using RPC manager."""
    rpc = _get_rpc()
    for ep in rpc._endpoints:
        if ep.is_healthy:
            return ep.url
    return "https://api.mainnet-beta.solana.com"



def init_wallet_db():
    """Initialize wallet tables - now handled by db.py init_all_tables()"""
    pass


async def get_real_balance(wallet_address: str, use_cache: bool = True) -> Decimal:
    """
    Fetch real balance from Solana mainnet using lamports conversion.
    Uses centralized RPC manager with load balancing and failover.
    Returns balance in SOL.
    
    Args:
        wallet_address: Solana wallet address
        use_cache: If True, uses cached balance if available (default: True)
    """
    cache = _get_cache()
    
    # Check cache first (for faster button responses)
    if use_cache:
        cached_balance = cache.get_balance(wallet_address)
        if cached_balance is not None:
            return cached_balance
    
    try:
        rpc = _get_rpc()
        lamports = await rpc.get_balance(wallet_address)
        sol_balance = Decimal(lamports) / Decimal(1_000_000_000)
        print(f"[Wallet] Balance for {wallet_address[:8]}...: {sol_balance} SOL")
        cache.set_balance(wallet_address, sol_balance)
        return sol_balance
    except Exception as e:
        print(f"[Wallet] Error fetching balance for {wallet_address[:8]}...: {e}")
        # Return cached value if available (even expired)
        cached = cache.get_balance(wallet_address)
        if cached is not None:
            return cached
        return Decimal("0")


def get_user_wallets(user_id: int) -> List[Dict]:
    """
    Get all wallets for a user by their user_id.
    Only returns wallets that haven't been deleted (is_active = 1).
    Wallets persist in the database until user explicitly deletes them.
    """
    conn = get_db_conn()
    c = conn.cursor()
    
    # Ensure user_id is integer for consistent comparison
    user_id = int(user_id)
    
    print(f"[Wallet] Fetching wallets for user_id: {user_id}")
    
    # Get all non-deleted wallets for this user
    c.execute(q("""
        SELECT wallet_address, wallet_type, wallet_name, is_active, user_id
        FROM wallets 
        WHERE user_id = ? AND is_active = 1
        ORDER BY created_at ASC
    """), (user_id,))
    rows = c.fetchall()
    
    print(f"[Wallet] Found {len(rows)} active wallets for user {user_id}")
    
    # If no wallets found, check if user has ANY wallets (even deleted ones)
    if len(rows) == 0:
        c.execute(q("SELECT COUNT(*), user_id FROM wallets WHERE user_id = ? GROUP BY user_id"), (user_id,))
        check = c.fetchone()
        if check:
            print(f"[Wallet] User {user_id} has {check[0]} total wallets (including deleted)")
        else:
            print(f"[Wallet] User {user_id} has no wallets in database at all")
    
    conn.close()

    wallets = []
    for row in rows:
        wallets.append({
            "address": row[0],
            "type": row[1],
            "name": row[2] or "Wallet",
            "is_active": bool(row[3])
        })
    return wallets


def get_user_wallet_count(user_id: int) -> int:
    """Get number of active wallets for a user by user_id"""
    user_id = int(user_id)  # Ensure integer type
    conn = get_db_conn()
    c = conn.cursor()
    c.execute(q("SELECT COUNT(*) FROM wallets WHERE user_id = ? AND is_active = 1"), (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count


def get_active_wallet(user_id: int) -> Optional[str]:
    """
    Get user's currently active wallet address by user_id.
    Falls back to finding any existing wallet if no active wallet is set.
    Wallets are identified by user_id and persist until user deletes them.
    """
    user_id = int(user_id)  # Ensure integer type
    conn = get_db_conn()
    c = conn.cursor()
    
    # First check the active wallet table
    c.execute(q("SELECT active_wallet_address FROM user_active_wallet WHERE user_id = ?"), (user_id,))
    row = c.fetchone()
    
    if row and row[0]:
        # Verify this wallet still exists and is active
        c.execute(q("SELECT 1 FROM wallets WHERE user_id = ? AND wallet_address = ? AND is_active = 1"), (user_id, row[0]))
        if c.fetchone():
            conn.close()
            return row[0]
    
    # Fallback: Find any active wallet for this user and set it as active
    c.execute(q("""
        SELECT wallet_address FROM wallets 
        WHERE user_id = ? AND is_active = 1 
        ORDER BY created_at ASC LIMIT 1
    """), (user_id,))
    fallback_row = c.fetchone()
    
    if fallback_row:
        wallet_address = fallback_row[0]
        # Auto-recover: Set this as the active wallet
        c.execute(q("""
            INSERT INTO user_active_wallet (user_id, active_wallet_address)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET active_wallet_address = ?
        """), (user_id, wallet_address, wallet_address))
        conn.commit()
        conn.close()
        return wallet_address
    
    conn.close()
    return None


def set_active_wallet(user_id: int, wallet_address: str) -> bool:
    """Set which wallet is active for the user by user_id"""
    user_id = int(user_id)  # Ensure integer type
    conn = get_db_conn()
    c = conn.cursor()

    # Verify wallet belongs to user
    c.execute(q("SELECT 1 FROM wallets WHERE user_id = ? AND wallet_address = ?"), (user_id, wallet_address))
    if not c.fetchone():
        conn.close()
        return False

    # Update or insert active wallet
    c.execute(q("""
        INSERT INTO user_active_wallet (user_id, active_wallet_address) 
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET active_wallet_address = ?
    """), (user_id, wallet_address, wallet_address))

    conn.commit()
    conn.close()
    return True


def create_wallet(user_id: int, wallet_name: Optional[str] = None) -> Optional[Dict]:
    """
    Create a new bot-managed wallet for the user by user_id.
    Returns wallet dict with address (private key is ENCRYPTED in database)
    
    Wallets are stored with user_id and persist until user explicitly deletes them.
    """
    user_id = int(user_id)  # Ensure integer type
    
    # Check wallet limit FIRST - never exceed MAX_WALLETS_PER_USER
    current_count = get_user_wallet_count(user_id)
    if current_count >= MAX_WALLETS_PER_USER:
        return None
    
    # Check if this is the user's first wallet (before inserting)
    is_first_wallet = current_count == 0
    
    # Verify encryption is configured
    if not is_encryption_configured():
        raise ValueError("Encryption not configured. Set ENCRYPTION_KEY environment variable.")

    # Generate new keypair
    keypair = Keypair()
    wallet_address = str(keypair.pubkey())
    private_key_hex = bytes(keypair).hex()
    
    # ENCRYPT the private key before storing
    encrypted_private_key = encrypt_private_key(private_key_hex)

    if not wallet_name:
        wallet_name = f"Bot Wallet {current_count + 1}"

    conn = get_db_conn()
    c = conn.cursor()

    try:
        # Store ENCRYPTED private key in database
        print(f"[Wallet] Creating wallet for user {user_id}: {wallet_address[:8]}...")
        c.execute(q("""
            INSERT INTO wallets (user_id, wallet_address, wallet_type, wallet_name, private_key)
            VALUES (?, ?, ?, ?, ?)
        """), (user_id, wallet_address, "bot", wallet_name, encrypted_private_key))

        # Set as active if it's the first wallet (checked BEFORE insert)
        if is_first_wallet:
            print(f"[Wallet] Setting first wallet as active for user {user_id}")
            c.execute(q("""
                INSERT INTO user_active_wallet (user_id, active_wallet_address)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET active_wallet_address = ?
            """), (user_id, wallet_address, wallet_address))

        conn.commit()
        print(f"[Wallet] Successfully created wallet for user {user_id}")
        conn.close()

        return {
            "address": wallet_address,
            "type": "bot",
            "name": wallet_name
        }
    except (DBIntegrityError, Exception) as e:
        # Handle both SQLite and PostgreSQL integrity errors
        print(f"[Wallet] Error creating wallet for user {user_id}: {e}")
        if "unique" in str(e).lower() or "duplicate" in str(e).lower() or isinstance(e, DBIntegrityError):
            conn.close()
            return None
        conn.close()
        raise


def save_external_wallet(user_id: int, wallet_address: str, wallet_type: str = "external", wallet_name: Optional[str] = None) -> bool:
    """
    Save an external wallet (Phantom, Solflare, etc.)
    
    IMPORTANT: Checks for existing wallets FIRST to prevent duplicates.
    """
    # Check wallet limit FIRST
    current_count = get_user_wallet_count(user_id)
    if current_count >= MAX_WALLETS_PER_USER:
        return False
    
    # Check if this is the user's first wallet (before inserting)
    is_first_wallet = current_count == 0

    if not wallet_name:
        wallet_name = f"{wallet_type.capitalize()} Wallet {current_count + 1}"

    conn = get_db_conn()
    c = conn.cursor()

    try:
        c.execute(q("""
            INSERT INTO wallets (user_id, wallet_address, wallet_type, wallet_name)
            VALUES (?, ?, ?, ?)
        """), (user_id, wallet_address, wallet_type, wallet_name))

        # Always set as active wallet (upsert - won't override if already set, but ensures we have one)
        c.execute(q("""
            INSERT INTO user_active_wallet (user_id, active_wallet_address)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET active_wallet_address = ?
        """), (user_id, wallet_address, wallet_address))

        conn.commit()
        conn.close()
        return True
    except (DBIntegrityError, Exception) as e:
        # Handle both SQLite and PostgreSQL integrity errors
        if "unique" in str(e).lower() or "duplicate" in str(e).lower() or isinstance(e, DBIntegrityError):
            conn.close()
            return False
        conn.close()
        raise


def import_wallet_from_private_key(user_id: int, private_key_input: str, wallet_name: Optional[str] = None) -> Dict:
    """
    Import a wallet using private key (hex or base58 format) for a user by user_id.
    Returns dict with success status, address, or error message.
    Wallet is stored with user_id and persists until user explicitly deletes it.
    
    SECURITY: Private key is encrypted before storage
    """
    import base58
    user_id = int(user_id)  # Ensure integer type
    
    # Check wallet limit
    if get_user_wallet_count(user_id) >= MAX_WALLETS_PER_USER:
        return {"success": False, "error": "You already have a wallet. Delete it first to import a new one."}
    
    # Verify encryption is configured
    if not is_encryption_configured():
        return {"success": False, "error": "Encryption not configured"}
    
    private_key_input = private_key_input.strip()
    private_key_bytes = None
    
    try:
        # Try to parse as hex (128 chars = 64 bytes)
        if len(private_key_input) == 128 and all(c in '0123456789abcdefABCDEF' for c in private_key_input):
            private_key_bytes = bytes.fromhex(private_key_input)
        # Try to parse as base58 (typical Phantom export format, ~88 chars)
        elif len(private_key_input) >= 64 and len(private_key_input) <= 100:
            try:
                private_key_bytes = base58.b58decode(private_key_input)
            except:
                pass
        # Try to parse as JSON array (Solflare format)
        elif private_key_input.startswith('[') and private_key_input.endswith(']'):
            import json
            try:
                key_array = json.loads(private_key_input)
                if isinstance(key_array, list) and len(key_array) == 64:
                    private_key_bytes = bytes(key_array)
            except:
                pass
        
        if not private_key_bytes or len(private_key_bytes) != 64:
            return {
                "success": False, 
                "error": "Invalid private key format. Expected hex (128 chars), base58, or JSON array [64 numbers]"
            }
        
        # Create keypair from private key bytes
        keypair = Keypair.from_bytes(private_key_bytes)
        wallet_address = str(keypair.pubkey())
        private_key_hex = private_key_bytes.hex()
        
        # Check if wallet already exists for this user
        conn = get_db_conn()
        c = conn.cursor()
        c.execute(q("SELECT 1 FROM wallets WHERE user_id = ? AND wallet_address = ?"), (user_id, wallet_address))
        if c.fetchone():
            conn.close()
            return {"success": False, "error": "This wallet is already imported"}
        conn.close()
        
        # Encrypt the private key
        encrypted_private_key = encrypt_private_key(private_key_hex)
        
        if not wallet_name:
            count = get_user_wallet_count(user_id) + 1
            wallet_name = f"Imported Wallet {count}"
        
        conn = get_db_conn()
        c = conn.cursor()
        
        c.execute(q("""
            INSERT INTO wallets (user_id, wallet_address, wallet_type, wallet_name, private_key)
            VALUES (?, ?, ?, ?, ?)
        """), (user_id, wallet_address, "imported", wallet_name, encrypted_private_key))
        
        # Set as active wallet
        c.execute(q("""
            INSERT INTO user_active_wallet (user_id, active_wallet_address)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET active_wallet_address = ?
        """), (user_id, wallet_address, wallet_address))
        
        conn.commit()
        conn.close()
        
        return {
            "success": True,
            "address": wallet_address,
            "name": wallet_name
        }
        
    except Exception as e:
        return {"success": False, "error": f"Failed to import wallet: {str(e)}"}


def _is_hex_string(s: str) -> bool:
    """Check if a string is a valid hex string"""
    try:
        int(s, 16)
        return len(s) == 128  # Solana private keys are 64 bytes = 128 hex chars
    except (ValueError, TypeError):
        return False


def _migrate_plaintext_key(user_id: int, wallet_address: str, plaintext_key: str) -> bool:
    """
    Migrate a plaintext private key to encrypted format
    Returns True if migration successful
    """
    try:
        # Encrypt the plaintext key
        encrypted_key = encrypt_private_key(plaintext_key)
        
        # Update database with encrypted key
        conn = get_db_conn()
        c = conn.cursor()
        c.execute(q("""
            UPDATE wallets 
            SET private_key = ? 
            WHERE user_id = ? AND wallet_address = ? AND wallet_type = 'bot'
        """), (encrypted_key, user_id, wallet_address))
        conn.commit()
        conn.close()
        
        print(f"✅ Migrated plaintext key to encrypted for wallet {wallet_address[:8]}...")
        return True
    except Exception as e:
        print(f"❌ Failed to migrate key for wallet {wallet_address[:8]}: {e}")
        return False


def get_wallet_private_key(user_id: int, wallet_address: str) -> Optional[str]:
    """
    Get DECRYPTED private key for a bot-managed or imported wallet.
    Supports both 'bot' and 'imported' wallet types.
    Automatically migrates plaintext keys AND legacy-encrypted keys to new encryption.
    Returns the decrypted hex private key, or None if not found or no private key stored.
    """
    conn = get_db_conn()
    c = conn.cursor()
    c.execute(q("""
        SELECT private_key, wallet_type FROM wallets 
        WHERE user_id = ? AND wallet_address = ? AND wallet_type IN ('bot', 'imported')
    """), (user_id, wallet_address))
    row = c.fetchone()
    conn.close()
    
    if not row or not row[0]:
        return None
    
    stored_key = row[0]
    wallet_type = row[1]
    
    # Check if this is a plaintext hex key (legacy format)
    if _is_hex_string(stored_key):
        print(f"⚠️ Detected plaintext key for {wallet_address[:8]}..., migrating to encrypted format")
        if _migrate_plaintext_key_any_type(user_id, wallet_address, stored_key, wallet_type):
            return stored_key
        else:
            return stored_key
    
    # It's an encrypted key, decrypt it
    try:
        decrypted_key = decrypt_private_key(stored_key)
        return decrypted_key
    except Exception as e:
        print(f"Error decrypting private key: {e}")
        return None


def _migrate_plaintext_key_any_type(user_id: int, wallet_address: str, plaintext_key: str, wallet_type: str) -> bool:
    """
    Migrate a plaintext private key to encrypted format for any wallet type.
    Returns True if migration successful.
    """
    try:
        encrypted_key = encrypt_private_key(plaintext_key)
        conn = get_db_conn()
        c = conn.cursor()
        c.execute(q("""
            UPDATE wallets 
            SET private_key = ? 
            WHERE user_id = ? AND wallet_address = ? AND wallet_type = ?
        """), (encrypted_key, user_id, wallet_address, wallet_type))
        conn.commit()
        conn.close()
        print(f"✅ Migrated plaintext key to encrypted for {wallet_type} wallet {wallet_address[:8]}...")
        return True
    except Exception as e:
        print(f"❌ Failed to migrate key for wallet {wallet_address[:8]}: {e}")
        return False


async def estimate_transaction_fee(from_address: str, to_address: str, amount_sol: Decimal) -> Decimal:
    """
    Estimate the transaction fee for a SOL transfer.
    Uses all configured RPC endpoints with automatic failover.
    Returns fee in SOL (typically ~0.00002 SOL for simple transfers).
    NOTE: Does not require private key - only estimates the fee for a transfer.
    """
    # Standard network fee for SOL transfers (includes buffer for network fees)
    NETWORK_FEE = Decimal("0.00002")
    
    for rpc in RPC_ENDPOINTS:
        try:
            lamports = int(amount_sol * Decimal(1_000_000_000))

            async with AsyncClient(rpc) as client:
                recent_blockhash_resp = await client.get_latest_blockhash()
                recent_blockhash = recent_blockhash_resp.value.blockhash

                from_pubkey = Pubkey.from_string(from_address)
                to_pubkey = Pubkey.from_string(to_address)

                transfer_ix = transfer(
                    TransferParams(
                        from_pubkey=from_pubkey,
                        to_pubkey=to_pubkey,
                        lamports=lamports
                    )
                )

                message = Message.new_with_blockhash(
                    [transfer_ix],
                    from_pubkey,
                    recent_blockhash
                )
                
                fee_response = await client.get_fee_for_message(message)
                if fee_response.value is not None:
                    fee_lamports = fee_response.value
                    return Decimal(fee_lamports) / Decimal(1_000_000_000)
                
                return NETWORK_FEE
        except Exception as e:
            print(f"Fee estimation error ({rpc[:30]}...): {e}, trying fallback...")
            continue
    
    print(f"Using default network fee: {NETWORK_FEE} SOL")
    return NETWORK_FEE


async def send_sol(from_address: str, to_address: str, amount_sol: Decimal, private_key_hex: str) -> Dict:
    """
    Send SOL from one address to another.
    Uses primary RPC with automatic fallback to ensure transaction success.
    Always fetches latest blockhash right before creating transaction.
    Returns transaction result dict.
    
    Updated to use correct solders 0.18.x transaction format.
    """
    # Network fee buffer for transaction
    NETWORK_FEE = Decimal("0.00002")
    
    # Check balance before attempting transaction
    current_balance = await get_real_balance(from_address)
    required_amount = amount_sol + NETWORK_FEE
    
    print(f"Transaction: {from_address[:8]}... -> {to_address[:8]}...")
    print(f"  Amount: {amount_sol} SOL + {NETWORK_FEE} SOL fee = {required_amount} SOL required")
    print(f"  Current balance: {current_balance} SOL")
    
    if current_balance < required_amount:
        error_msg = f"Insufficient balance. Have {current_balance} SOL, need {required_amount} SOL (including ~{NETWORK_FEE} SOL network fee)"
        print(f"  ERROR: {error_msg}")
        return {
            "success": False,
            "error": error_msg
        }
    
    last_error = None
    for rpc in RPC_ENDPOINTS:
        try:
            lamports = int(amount_sol * Decimal(1_000_000_000))
            private_key_bytes = bytes.fromhex(private_key_hex)
            keypair = Keypair.from_bytes(private_key_bytes)

            print(f"  Using RPC: {rpc[:30]}...")
            
            async with AsyncClient(rpc) as client:
                recent_blockhash_resp = await client.get_latest_blockhash()
                recent_blockhash = recent_blockhash_resp.value.blockhash
                print(f"  Latest blockhash: {str(recent_blockhash)[:20]}...")

                from_pubkey = Pubkey.from_string(from_address)
                to_pubkey = Pubkey.from_string(to_address)

                transfer_ix = transfer(
                    TransferParams(
                        from_pubkey=from_pubkey,
                        to_pubkey=to_pubkey,
                        lamports=lamports
                    )
                )

                message = Message.new_with_blockhash(
                    [transfer_ix],
                    from_pubkey,
                    recent_blockhash
                )
                
                transaction = Transaction([keypair], message, recent_blockhash)
                result = await client.send_raw_transaction(bytes(transaction))
                
                signature = str(result.value)
                print(f"  Transaction sent! Signature: {signature[:20]}...")

                return {
                    "success": True,
                    "signature": signature,
                    "amount": float(amount_sol),
                    "rpc_used": rpc[:30]
                }
        except Exception as e:
            last_error = e
            print(f"  RPC error ({rpc[:30]}...): {e}, trying fallback...")
            continue
    
    print(f"Error sending SOL after all RPC attempts: {last_error}")
    import traceback
    traceback.print_exc()
    return {
        "success": False,
        "error": str(last_error)
    }


def delete_wallet(user_id: int, wallet_address: str) -> bool:
    """Permanently delete a wallet from database"""
    conn = get_db_conn()
    c = conn.cursor()

    # Clear active wallet reference first
    c.execute(q("""
        DELETE FROM user_active_wallet 
        WHERE user_id = ? AND active_wallet_address = ?
    """), (user_id, wallet_address))

    # Permanently delete the wallet record from database
    c.execute(q("""
        DELETE FROM wallets 
        WHERE user_id = ? AND wallet_address = ?
    """), (user_id, wallet_address))

    affected = c.rowcount > 0
    conn.commit()
    conn.close()
    return affected


# Legacy compatibility functions for Main.py
def get_user_wallet(user_id: int) -> Optional[str]:
    """Get user's active wallet address (legacy compatibility)"""
    return get_active_wallet(user_id)


def save_user_wallet(user_id: int, wallet_address: str):
    """Save external wallet (legacy compatibility)"""
    return save_external_wallet(user_id, wallet_address, "external")


async def get_wallet_balance(user_id: int) -> Decimal:
    """Get balance of user's active wallet"""
    wallet_address = get_active_wallet(user_id)
    if not wallet_address:
        return Decimal("0")
    return await get_real_balance(wallet_address)


def deduct_wallet_balance(user_id: int, amount: Decimal):
    """This is a no-op since we're using real balances now"""
    pass


def add_funds_to_wallet(wallet_address: str, amount: Decimal):
    """This is a no-op since we're using real balances now"""
    pass


def set_user_pin(user_id: int, pin: str) -> bool:
    """
    Set or update user's 4-digit PIN by user_id.
    PIN is hashed before storage.
    """
    import hashlib
    user_id = int(user_id)  # Ensure integer type
    
    # Validate PIN is 4 digits
    if not pin.isdigit() or len(pin) != 4:
        print(f"[PIN] Invalid PIN format for user {user_id}: length={len(pin)}, isdigit={pin.isdigit()}")
        return False
    
    # Hash the PIN
    pin_hash = hashlib.sha256(pin.encode()).hexdigest()
    
    conn = get_db_conn()
    c = conn.cursor()
    try:
        c.execute(q("""
            INSERT INTO user_pins (user_id, pin_hash)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET pin_hash = ?
        """), (user_id, pin_hash, pin_hash))
        conn.commit()
        print(f"[PIN] Successfully saved PIN for user {user_id}")
        conn.close()
        return True
    except Exception as e:
        print(f"[PIN] Error saving PIN for user {user_id}: {e}")
        conn.close()
        return False


def verify_user_pin(user_id: int, pin: str) -> bool:
    """
    Verify user's PIN by user_id.
    Returns True if PIN matches.
    """
    import hashlib
    user_id = int(user_id)  # Ensure integer type
    
    # Validate PIN format
    if not pin.isdigit() or len(pin) != 4:
        return False
    
    # Hash the provided PIN
    pin_hash = hashlib.sha256(pin.encode()).hexdigest()
    
    conn = get_db_conn()
    c = conn.cursor()
    c.execute(q("SELECT pin_hash FROM user_pins WHERE user_id = ?"), (user_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        print(f"[PIN] No PIN found for user {user_id}")
        return False
    
    matches = row[0] == pin_hash
    print(f"[PIN] Verify for user {user_id}: {'match' if matches else 'no match'}")
    return matches


def has_user_pin(user_id: int) -> bool:
    """Check if user has set a PIN by user_id"""
    user_id = int(user_id)  # Ensure integer type
    conn = get_db_conn()
    c = conn.cursor()
    c.execute(q("SELECT 1 FROM user_pins WHERE user_id = ?"), (user_id,))
    result = c.fetchone() is not None
    conn.close()
    print(f"[PIN] has_user_pin({user_id}): {result}")
    return result


def delete_user_pin(user_id: int) -> bool:
    """Delete user's PIN after successful verification (one-time use)"""
    user_id = int(user_id)  # Ensure integer type
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute(q("DELETE FROM user_pins WHERE user_id = ?"), (user_id,))
        conn.commit()
        deleted = c.rowcount > 0
        conn.close()
        print(f"[PIN] Deleted PIN for user {user_id}: {deleted}")
        return deleted
    except Exception as e:
        print(f"[PIN] Error deleting PIN for user {user_id}: {e}")
        return False


# ==============================================================================
# REAL-TIME TRANSACTION FUNCTIONS
# ==============================================================================
# These functions enable automatic Solana transactions signed by the bot.
# They build, sign, and send transactions in real-time using stored private keys.
#
# FLOW:
# 1. build_transaction() - Creates UNSIGNED message and instructions
# 2. sign_and_send_transaction() - Signs once and sends atomically
#
# Note: The primary send_sol() function above is the recommended way to send SOL.
# These helper functions are provided for more granular control if needed.

async def build_unsigned_transaction(sender_address: str, receiver_address: str, amount_sol: Decimal) -> Dict:
    """
    Build an UNSIGNED Solana SOL transfer transaction.
    Uses RPC_ENDPOINTS for automatic failover.
    """
    try:
        if amount_sol <= Decimal("0"):
            return {"success": False, "error": "Amount must be greater than 0"}
        
        lamports = int(amount_sol * Decimal(1_000_000_000))
        sender_pubkey = Pubkey.from_string(sender_address)
        to_pubkey = Pubkey.from_string(receiver_address)
        
        last_error = None
        for rpc in RPC_ENDPOINTS:
            try:
                async with AsyncClient(rpc) as client:
                    balance_resp = await client.get_balance(sender_pubkey, commitment=Confirmed)
                    if balance_resp.value is None:
                        continue
                    
                    balance_lamports = balance_resp.value
                    estimated_fee = 20000
                    
                    if balance_lamports < lamports + estimated_fee:
                        return {
                            "success": False, 
                            "error": f"Insufficient balance. Have: {balance_lamports/1e9:.6f} SOL, Need: {(lamports + estimated_fee)/1e9:.6f} SOL"
                        }
                    
                    recent_blockhash_resp = await client.get_latest_blockhash()
                    recent_blockhash = recent_blockhash_resp.value.blockhash
                    
                    transfer_ix = transfer(
                        TransferParams(
                            from_pubkey=sender_pubkey,
                            to_pubkey=to_pubkey,
                            lamports=lamports
                        )
                    )
                    
                    message = Message.new_with_blockhash(
                        [transfer_ix],
                        sender_pubkey,
                        recent_blockhash
                    )
                    
                    return {
                        "success": True,
                        "message": message,
                        "blockhash": recent_blockhash,
                        "sender_pubkey": sender_pubkey,
                        "receiver": receiver_address,
                        "amount_lamports": lamports,
                        "rpc_used": rpc
                    }
            except Exception as e:
                last_error = e
                print(f"Build tx RPC error ({rpc[:30]}...): {e}")
                continue
        
        return {"success": False, "error": str(last_error) if last_error else "All RPC endpoints failed"}
            
    except Exception as e:
        print(f"Error building unsigned transaction: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}


async def sign_and_send_transaction(message, blockhash, private_key_hex: str, rpc_url: Optional[str] = None) -> Dict:
    """
    Sign a transaction message and send it atomically.
    Uses RPC_ENDPOINTS for automatic failover.
    """
    try:
        if not private_key_hex or len(private_key_hex) != 128:
            return {"success": False, "error": "Invalid private key format"}
        
        private_key_bytes = bytes.fromhex(private_key_hex)
        keypair = Keypair.from_bytes(private_key_bytes)
        signed_tx = Transaction([keypair], message, blockhash)
        
        rpc_list = ([rpc_url] if rpc_url else []) + list(RPC_ENDPOINTS)
        
        last_error = None
        for rpc in rpc_list:
            if not rpc:
                continue
            try:
                async with AsyncClient(rpc) as client:
                    try:
                        result = await client.send_raw_transaction(bytes(signed_tx))
                    except Exception as rpc_error:
                        error_msg = str(rpc_error)
                        if "insufficient funds" in error_msg.lower():
                            return {"success": False, "error": "Insufficient funds for transaction"}
                        elif "blockhash" in error_msg.lower():
                            return {"success": False, "error": "Blockhash expired, please retry"}
                        else:
                            last_error = error_msg
                            continue
                    
                    signature = str(result.value)
                    
                    confirmed = False
                    for attempt in range(15):
                        await asyncio.sleep(1)
                        try:
                            status = await client.get_signature_statuses([result.value])
                            if status.value and status.value[0]:
                                if status.value[0].confirmation_status:
                                    confirmed = True
                                    break
                                if status.value[0].err:
                                    return {
                                        "success": False,
                                        "error": f"Transaction failed: {status.value[0].err}",
                                        "signature": signature
                                    }
                        except Exception:
                            pass
                    
                    return {
                        "success": True,
                        "signature": signature,
                        "confirmed": confirmed
                    }
            except Exception as e:
                last_error = str(e)
                print(f"Sign/send RPC error ({rpc[:30]}...): {e}")
                continue
        
        return {"success": False, "error": f"All RPC endpoints failed: {last_error}"}
            
    except ValueError as ve:
        return {"success": False, "error": f"Invalid key format: {ve}"}
    except Exception as e:
        print(f"Error signing and sending transaction: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}


async def execute_automatic_transfer(sender_private_key_hex: str, receiver_address: str, amount_sol: Decimal) -> Dict:
    """
    Complete automatic SOL transfer: build, sign, and send in one call.
    
    This is the main function for real-time automatic transactions.
    The bot signs using the stored private key and sends immediately.
    
    NOTE: For most use cases, prefer the send_sol() function which handles
    everything in a single call. This function provides the same functionality
    but is structured as a helper for the build/sign/send workflow.
    
    Args:
        sender_private_key_hex: The sender's private key in hex format
        receiver_address: The recipient's Solana wallet address
        amount_sol: Amount of SOL to transfer
    
    Returns:
        Dict with 'success', 'signature', 'amount', 'confirmed' or 'error'
    """
    try:
        # Validate inputs
        if not sender_private_key_hex or len(sender_private_key_hex) != 128:
            return {"success": False, "error": "Invalid private key format"}
        
        if not receiver_address:
            return {"success": False, "error": "Receiver address required"}
        
        if amount_sol <= Decimal("0"):
            return {"success": False, "error": "Amount must be greater than 0"}
        
        # Get sender address from private key
        private_key_bytes = bytes.fromhex(sender_private_key_hex)
        keypair = Keypair.from_bytes(private_key_bytes)
        sender_address = str(keypair.pubkey())
        
        # Step 1: Build unsigned transaction (validates balance)
        build_result = await build_unsigned_transaction(sender_address, receiver_address, amount_sol)
        if not build_result.get("success"):
            return {"success": False, "error": build_result.get("error", "Build failed")}
        
        # Step 2: Sign and send atomically (single signing)
        # Use the same RPC endpoint that was used to build the transaction
        send_result = await sign_and_send_transaction(
            build_result["message"],
            build_result["blockhash"],
            sender_private_key_hex,
            build_result.get("rpc_used")
        )
        if not send_result.get("success"):
            return {"success": False, "error": send_result.get("error", "Send failed")}
        
        return {
            "success": True,
            "signature": send_result["signature"],
            "amount": float(amount_sol),
            "confirmed": send_result.get("confirmed", False),
            "sender": sender_address,
            "receiver": receiver_address
        }
        
    except ValueError as ve:
        return {"success": False, "error": f"Invalid input: {ve}"}
    except Exception as e:
        print(f"Error in automatic transfer: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e)
        }


# ==============================================================================
# WALLET VALIDATION FUNCTIONS
# ==============================================================================

def is_valid_solana_address(address: str) -> bool:
    """
    Validate a Solana wallet address format.
    Solana addresses are base58 encoded and 32-44 characters.
    """
    import base58
    
    if not address or not isinstance(address, str):
        return False
    
    address = address.strip()
    
    if len(address) < 32 or len(address) > 44:
        return False
    
    try:
        decoded = base58.b58decode(address)
        return len(decoded) == 32
    except Exception:
        return False


def validate_wallet_address(address: str) -> Dict:
    """
    Validate a Solana wallet address and return detailed info.
    Returns dict with 'valid', 'address', and optional 'error'.
    """
    if not address:
        return {"valid": False, "error": "Address is required"}
    
    address = address.strip()
    
    if not is_valid_solana_address(address):
        return {"valid": False, "error": "Invalid Solana address format"}
    
    return {"valid": True, "address": address}


# ==============================================================================
# TRANSACTION HISTORY FUNCTIONS
# ==============================================================================

def log_wallet_transaction(user_id: int, wallet_address: str, tx_type: str, 
                          amount: Decimal, to_address: str = None, 
                          from_address: str = None, tx_signature: str = None,
                          status: str = "completed") -> bool:
    """
    Log a wallet transaction to the database for history tracking.
    
    tx_type: 'send', 'receive', 'lottery_stake', 'lottery_win', 'refund'
    status: 'pending', 'completed', 'failed'
    """
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute(q("""
            INSERT INTO wallet_transactions 
            (user_id, wallet_address, tx_type, amount, to_address, from_address, tx_signature, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """), (user_id, wallet_address, tx_type, float(amount), to_address, from_address, tx_signature, status))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error logging transaction: {e}")
        return False


def get_wallet_transactions(user_id: int, wallet_address: str = None, limit: int = 20) -> List[Dict]:
    """
    Get transaction history for a user's wallet.
    If wallet_address is None, returns transactions for all user's wallets.
    """
    conn = get_db_conn()
    c = conn.cursor()
    
    if wallet_address:
        c.execute(q("""
            SELECT id, wallet_address, tx_type, amount, to_address, from_address, 
                   tx_signature, status, created_at
            FROM wallet_transactions 
            WHERE user_id = ? AND wallet_address = ?
            ORDER BY created_at DESC
            LIMIT ?
        """), (user_id, wallet_address, limit))
    else:
        c.execute(q("""
            SELECT id, wallet_address, tx_type, amount, to_address, from_address, 
                   tx_signature, status, created_at
            FROM wallet_transactions 
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """), (user_id, limit))
    
    rows = c.fetchall()
    conn.close()
    
    transactions = []
    for row in rows:
        transactions.append({
            "id": row[0],
            "wallet_address": row[1],
            "tx_type": row[2],
            "amount": Decimal(str(row[3])),
            "to_address": row[4],
            "from_address": row[5],
            "tx_signature": row[6],
            "status": row[7],
            "created_at": row[8]
        })
    
    return transactions


def get_wallet_summary(user_id: int, wallet_address: str) -> Dict:
    """
    Get a summary of wallet activity including total sent, received, etc.
    """
    conn = get_db_conn()
    c = conn.cursor()
    
    c.execute(q("""
        SELECT tx_type, SUM(amount), COUNT(*)
        FROM wallet_transactions 
        WHERE user_id = ? AND wallet_address = ? AND status = 'completed'
        GROUP BY tx_type
    """), (user_id, wallet_address))
    
    rows = c.fetchall()
    conn.close()
    
    summary = {
        "total_sent": Decimal("0"),
        "total_received": Decimal("0"),
        "total_staked": Decimal("0"),
        "total_won": Decimal("0"),
        "total_refunds": Decimal("0"),
        "transaction_count": 0
    }
    
    for tx_type, total, count in rows:
        summary["transaction_count"] += count
        if tx_type == "send":
            summary["total_sent"] = Decimal(str(total or 0))
        elif tx_type == "receive":
            summary["total_received"] = Decimal(str(total or 0))
        elif tx_type == "lottery_stake":
            summary["total_staked"] = Decimal(str(total or 0))
        elif tx_type == "lottery_win":
            summary["total_won"] = Decimal(str(total or 0))
        elif tx_type == "refund":
            summary["total_refunds"] = Decimal(str(total or 0))
    
    return summary


# ==============================================================================
# ENHANCED SEND SOL WITH LOGGING
# ==============================================================================

async def send_sol_with_logging(user_id: int, from_address: str, to_address: str, 
                                amount_sol: Decimal, private_key_hex: str) -> Dict:
    """
    Send SOL and automatically log the transaction to history.
    """
    log_wallet_transaction(
        user_id=user_id,
        wallet_address=from_address,
        tx_type="send",
        amount=amount_sol,
        to_address=to_address,
        status="pending"
    )
    
    result = await send_sol(from_address, to_address, amount_sol, private_key_hex)
    
    if result["success"]:
        log_wallet_transaction(
            user_id=user_id,
            wallet_address=from_address,
            tx_type="send",
            amount=amount_sol,
            to_address=to_address,
            tx_signature=result.get("signature"),
            status="completed"
        )
    else:
        log_wallet_transaction(
            user_id=user_id,
            wallet_address=from_address,
            tx_type="send",
            amount=amount_sol,
            to_address=to_address,
            status="failed"
        )
    
    return result