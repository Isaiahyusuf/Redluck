# Main.py — RedLuck Lotto with Real Solana Wallet Integration
# SQLite fully removed. PostgreSQL only.

import os
import sys
import asyncio
import hashlib
import time
import decimal
from decimal import Decimal
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import pytz
import secrets
import json

# ==============================================================================
# SINGLE RUNTIME VERIFICATION
# ==============================================================================
# Ensure only one instance of the bot is running at a time.
# SQLite fully removed. PostgreSQL only.

_bot_instance_started = False

def verify_single_runtime():
    """
    Verify that only one bot instance is running.
    Prevents duplicate workers and polling conflicts.
    """
    global _bot_instance_started
    if _bot_instance_started:
        print("=" * 60)
        print("BOT ALREADY RUNNING - BLOCKING DUPLICATE INSTANCE")
        print("=" * 60)
        sys.exit(1)
    _bot_instance_started = True
    print("✅ Single runtime verified - no duplicate instances")

verify_single_runtime()

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from aiohttp import web

from wallet import (
get_user_wallets,
get_active_wallet,
save_external_wallet,
get_real_balance,
send_sol,
estimate_transaction_fee,
create_wallet,
set_active_wallet,
get_wallet_private_key,
get_user_wallet_count,
delete_wallet,
set_user_pin,
verify_user_pin,
has_user_pin,
delete_user_pin,
init_wallet_db,
import_wallet_from_private_key,
MAX_WALLETS_PER_USER,
log_wallet_transaction
)

from db import get_db_conn, init_all_tables, migrate_remove_unique_constraint, migrate_add_referral_column, migrate_add_ticket_id_column, force_fix_participants_constraint, migrate_add_referral_reward_columns, migrate_add_vip_claim_column, q, save_security_question, get_security_question, verify_security_answer, has_security_question, add_announcement_group, remove_announcement_group, get_announcement_groups

from wallet_buttons import router as wallet_router

from email_service import (
    is_email_configured,
    generate_verification_code,
    save_verification_code,
    verify_code,
    can_send_code,
    send_verification_email,
    save_user_email,
    get_user_email,
    mark_email_verified,
    is_email_available
)

from cache_layer import get_cache
from rate_limiter import get_rate_limiter, RateLimitAction, is_duplicate_callback
from tx_verification_queue import get_tx_queue


load_dotenv()

cache = get_cache()
rate_limiter = get_rate_limiter()
tx_queue = get_tx_queue()

# ==============================================================================
# KEYBOARD HELPERS - Start and Back buttons for every prompt
# ==============================================================================

def get_start_button() -> InlineKeyboardButton:
    """Return standard Start button"""
    return InlineKeyboardButton(text="🏠 Start", callback_data="back_to_main")

def get_back_button(callback_data: str = "back_to_main") -> InlineKeyboardButton:
    """Return standard Back button with customizable callback"""
    return InlineKeyboardButton(text="🔙 Back", callback_data=callback_data)

def add_navigation_buttons(keyboard_buttons: list, back_callback: str = "back_to_main", include_start: bool = True) -> list:
    """Add Start and Back buttons to any keyboard"""
    nav_row = []
    if include_start:
        nav_row.append(get_start_button())
    nav_row.append(get_back_button(back_callback))
    keyboard_buttons.append(nav_row)
    return keyboard_buttons

def create_keyboard_with_nav(buttons: list, back_callback: str = "back_to_main", include_start: bool = True) -> InlineKeyboardMarkup:
    """Create InlineKeyboardMarkup with navigation buttons"""
    keyboard_buttons = buttons.copy()
    add_navigation_buttons(keyboard_buttons, back_callback, include_start)
    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

# ==============================================================================
# PRIVATE KEY/PIN AUTO-DELETE SYSTEM (30 seconds after viewing)
# ==============================================================================

# Store pending auto-delete tasks: {user_id: {"message_id": msg_id, "task": asyncio.Task}}
auto_delete_tasks: Dict[int, Dict] = {}

async def schedule_private_key_deletion(user_id: int, message: types.Message, delay_seconds: int = 30):
    """Schedule auto-deletion of a message containing sensitive info after delay"""
    # Cancel any existing deletion task for this user
    if user_id in auto_delete_tasks:
        existing = auto_delete_tasks[user_id]
        if existing.get("task") and not existing["task"].done():
            existing["task"].cancel()
    
    async def delete_after_delay():
        await asyncio.sleep(delay_seconds)
        try:
            await message.delete()
            # Send confirmation that key was auto-deleted
            await bot.send_message(
                user_id,
                "🔐 <b>Security Notice</b>\n\n"
                "Your private key message has been automatically deleted for security.\n"
                "This happens 30 seconds after viewing to protect your wallet.",
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Auto-delete failed for user {user_id}: {e}")
        finally:
            if user_id in auto_delete_tasks:
                del auto_delete_tasks[user_id]
    
    task = asyncio.create_task(delete_after_delay())
    auto_delete_tasks[user_id] = {"message_id": message.message_id, "task": task}

# ==============================================================================
# WALLET CONNECTION SESSION MANAGEMENT (Phantom/Solflare deep links)
# ==============================================================================

# Store wallet connection sessions: {session_id: {"user_id": uid, "created_at": timestamp, "wallet_type": type}}
wallet_connection_sessions: Dict[str, Dict] = {}

def create_wallet_connection_session(user_id: int, wallet_type: str) -> str:
    """Create a unique session for wallet connection"""
    session_id = secrets.token_urlsafe(32)
    wallet_connection_sessions[session_id] = {
        "user_id": user_id,
        "wallet_type": wallet_type,
        "created_at": datetime.now(pytz.UTC),
        "status": "pending"
    }
    return session_id

def validate_wallet_session(session_id: str) -> Optional[Dict]:
    """Validate and return session data if valid"""
    session = wallet_connection_sessions.get(session_id)
    if not session:
        return None
    
    # Check if session is expired (15 minutes)
    created_at = session["created_at"]
    if (datetime.now(pytz.UTC) - created_at).total_seconds() > 900:
        del wallet_connection_sessions[session_id]
        return None
    
    return session

def complete_wallet_session(session_id: str, wallet_address: str) -> Optional[int]:
    """Complete wallet connection and return user_id"""
    session = validate_wallet_session(session_id)
    if not session:
        return None
    
    user_id = session["user_id"]
    wallet_type = session["wallet_type"]
    
    # Save the wallet
    if save_external_wallet(user_id, wallet_address, wallet_type):
        set_active_wallet(user_id, wallet_address)
        del wallet_connection_sessions[session_id]
        return user_id
    
    return None

# ---------------------------
# Cryptographic Randomness Module
# ---------------------------
# IMPORTANT: This implementation uses blockchain data (blockhashes, transaction signatures)
# for verifiable randomness. All seed inputs are stored in the database for public verification.
# 
# For production mainnet with highest security, migrate to:
# - ORAO VRF (available on Solana): https://github.com/orao-network/solana-vrf
# - Chainlink VRF (when available on Solana - currently only Price Feeds are deployed)
# 
# Current approach: Combines immutable on-chain data (blockhash + tx signatures in canonical order)
# to create verifiable seeds that cannot be manipulated by the bot operator.

def generate_provable_seed(*inputs) -> str:
    """
    Generate a provable random seed from multiple inputs using SHA256.
    All inputs are combined with length prefixes and hashed to create a deterministic seed.
    Anyone can verify the result by reproducing the hash with the same inputs.
    
    SECURITY: Inputs should include immutable on-chain data (blockhash, tx signatures)
    NOT server-controlled values (timestamps, random numbers)
    """
    # Prefix each input with its length to prevent collision attacks
    parts = []
    for inp in inputs:
        inp_str = str(inp)
        parts.append(f"{len(inp_str)}:{inp_str}")
    
    combined = "|".join(parts)
    hash_object = hashlib.sha256(combined.encode('utf-8'))
    return hash_object.hexdigest()


def generate_lottery_numbers(seed: str, count: int = 5, min_val: int = 1, max_val: int = 40) -> List[int]:
    """
    Generate lottery numbers deterministically from a seed.
    Uses the seed to generate unique numbers in the specified range.
    
    Args:
        seed: Cryptographic seed (from generate_provable_seed)
        count: Number of unique numbers to generate
        min_val: Minimum number in range (inclusive)
        max_val: Maximum number in range (inclusive)
    
    Returns:
        Sorted list of unique lottery numbers
    """
    numbers = set()
    seed_int = int(seed, 16)
    
    attempt = 0
    while len(numbers) < count:
        hash_input = f"{seed}_{attempt}"
        hash_val = int(hashlib.sha256(hash_input.encode()).hexdigest(), 16)
        number = (hash_val % (max_val - min_val + 1)) + min_val
        numbers.add(number)
        attempt += 1
    
    return sorted(list(numbers))


def select_winner_deterministically(seed: str, participant_count: int) -> int:
    """
    Select a winner index deterministically from the seed.
    
    Args:
        seed: Cryptographic seed
        participant_count: Total number of participants
    
    Returns:
        Winner index (0-based)
    """
    hash_val = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return hash_val % participant_count

# ==============================================================================
# ENVIRONMENT VARIABLE VALIDATION
# ==============================================================================
# The bot MUST validate all required secrets before starting.
# If any mandatory secret is missing, the bot will refuse to start.

def validate_environment():
    """
    Validate all required environment variables.
    Raises detailed errors if any are missing.
    """
    missing = []
    warnings = []
    
    # ===== CRITICAL: DATABASE_URL IS REQUIRED =====
    # PostgreSQL only - db.py will raise an error if DATABASE_URL is not set
    print("✅ PostgreSQL database connected - user data will persist")
    
    # ===== REQUIRED SECRETS =====
    
    # Check BOT_TOKEN
    if not os.getenv("BOT_TOKEN"):
        missing.append("BOT_TOKEN - Your Telegram Bot API token from @BotFather")
    
    # Check OWNER_WALLET (bot's Solana wallet address)
    if not os.getenv("OWNER_WALLET"):
        missing.append("OWNER_WALLET - Bot's Solana wallet public address")
    
    # Check OWNER_WALLET_PRIVATE_KEY
    if not os.getenv("OWNER_WALLET_PRIVATE_KEY"):
        missing.append("OWNER_WALLET_PRIVATE_KEY - Bot's Solana wallet private key (hex or JSON array)")
    
    # Check SOLANA_RPC (optional - falls back to public RPC if not set)
    if not os.getenv("SOLANA_RPC"):
        print("ℹ️ No SOLANA_RPC configured, using fallback: https://api.mainnet-beta.solana.com")
    
    # Check ENCRYPTION_KEY (for wallet encryption)
    if not os.getenv("ENCRYPTION_KEY"):
        missing.append("ENCRYPTION_KEY - Strong random key (min 32 chars) for wallet encryption")
    
    # Check ADMIN_ID
    admin_id_str = os.getenv("ADMIN_ID", "")
    if not admin_id_str:
        missing.append("ADMIN_ID - Your numeric Telegram user ID (get from @userinfobot)")
    elif not admin_id_str.isdigit():
        print(f"❌ ERROR: ADMIN_ID must be a numeric Telegram user ID, got: '{admin_id_str}'")
        print(f"💡 To get your numeric Telegram user ID:")
        print(f"   1. Open Telegram and search for @userinfobot")
        print(f"   2. Start a chat with it")
        print(f"   3. It will reply with your numeric user ID (e.g., 123456789)")
        raise ValueError(f"ADMIN_ID must be a numeric value, not '{admin_id_str}'")
    
    # Check ROUND_CHANNEL_ID
    if not os.getenv("ROUND_CHANNEL_ID"):
        missing.append("ROUND_CHANNEL_ID - Telegram channel ID for round announcements (e.g., @yourchannel or -100123456789)")
    
    # ===== OPTIONAL SECRETS (warnings only) =====
    
    # Check TEAM_WALLET (optional)
    if not os.getenv("TEAM_WALLET"):
        warnings.append("TEAM_WALLET - Team Solana wallet for 20% fees (will use OWNER_WALLET if not set)")
    
    # Check SUPPORT_USERNAME (optional)
    if not os.getenv("SUPPORT_USERNAME"):
        warnings.append("SUPPORT_USERNAME - Telegram username for support contact")
    
    # Check ANNOUNCEMENTS_GROUP_ID (optional)
    if not os.getenv("ANNOUNCEMENTS_GROUP_ID"):
        warnings.append("ANNOUNCEMENTS_GROUP_ID - Telegram group ID for additional round announcements (e.g., -100123456789)")
    
    # Print warnings for optional secrets
    if warnings:
        print("\n" + "-"*60)
        print("⚠️ OPTIONAL ENVIRONMENT VARIABLES NOT SET")
        print("-"*60)
        for var in warnings:
            print(f"  ℹ️ {var}")
        print("-"*60 + "\n")
    
    # Fail on missing required secrets
    if missing:
        print("\n" + "="*60)
        print("❌ MISSING REQUIRED ENVIRONMENT VARIABLES")
        print("="*60)
        print("\nThe bot cannot start without the following secrets:\n")
        for var in missing:
            print(f"  • {var}")
        print("\n" + "="*60)
        print("Please set these environment variables in Replit Secrets")
        print("="*60 + "\n")
        raise ValueError(f"Missing required environment variables: {', '.join([v.split(' -')[0] for v in missing])}")

# Validate environment on startup
validate_environment()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_WALLET = os.getenv("OWNER_WALLET")

def normalize_private_key(private_key_input: str) -> str:
    """
    Convert private key to hex format (128 chars).
    Supports: hex (128 chars), JSON array [64 numbers], base58.
    Returns hex string or raises ValueError.
    """
    if not private_key_input:
        return ""
    
    private_key_input = private_key_input.strip()
    
    if len(private_key_input) == 128 and all(c in '0123456789abcdefABCDEF' for c in private_key_input):
        return private_key_input.lower()
    
    if private_key_input.startswith('[') and private_key_input.endswith(']'):
        try:
            key_array = json.loads(private_key_input)
            if isinstance(key_array, list) and len(key_array) == 64:
                return bytes(key_array).hex()
        except:
            pass
    
    if len(private_key_input) >= 64 and len(private_key_input) <= 100:
        try:
            import base58
            decoded = base58.b58decode(private_key_input)
            if len(decoded) == 64:
                return decoded.hex()
        except:
            pass
    
    print(f"⚠️ Could not parse OWNER_WALLET_PRIVATE_KEY (length: {len(private_key_input)})")
    return private_key_input

_raw_owner_key = os.getenv("OWNER_WALLET_PRIVATE_KEY", "")
OWNER_WALLET_PRIVATE_KEY = normalize_private_key(_raw_owner_key) if _raw_owner_key else ""

if OWNER_WALLET_PRIVATE_KEY and len(OWNER_WALLET_PRIVATE_KEY) == 128:
    print("✅ OWNER_WALLET_PRIVATE_KEY parsed successfully (128 hex chars)")
elif _raw_owner_key:
    print(f"⚠️ OWNER_WALLET_PRIVATE_KEY format issue - payouts may fail")

TEAM_WALLET = os.getenv("TEAM_WALLET")  # Optional - if not set, all goes to owner wallet
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ROUND_CHANNEL = os.getenv("ROUND_CHANNEL_ID", "@redlucklottoportal")
ANNOUNCEMENTS_GROUP = os.getenv("ANNOUNCEMENTS_GROUP_ID")  # Optional group for additional announcements

# ==============================================================================
# VIP BONUS CONFIGURATION
# ==============================================================================
# ONE special Telegram user ID that receives 20 free tickets every 24 hours
VIP_TELEGRAM_ID = int(os.getenv("VIP_TELEGRAM_ID", "0"))  # Set to the real Telegram numeric ID
VIP_DAILY_TICKETS = 20  # Number of free tickets to award
VIP_COOLDOWN_SECONDS = 86400  # 24 hours in seconds

if VIP_TELEGRAM_ID > 0:
    print(f"✅ VIP Bonus configured for user {VIP_TELEGRAM_ID} (20 tickets every 24h)")
else:
    print("ℹ️ VIP_TELEGRAM_ID not set - VIP bonus disabled")

# ==============================================================================
# SOLANA RPC CONFIGURATION
# ==============================================================================
# Uses SOLANA_RPC as primary with public Solana RPC as automatic fallback.

FALLBACK_RPC = "https://api.mainnet-beta.solana.com"
SOLANA_RPC = os.getenv("SOLANA_RPC", FALLBACK_RPC)

if SOLANA_RPC and SOLANA_RPC != FALLBACK_RPC:
    print(f"✅ Primary RPC: SOLANA_RPC (configured)")
    print(f"   Fallback: {FALLBACK_RPC}")
else:
    print(f"⚠️ No SOLANA_RPC configured, using public fallback: {FALLBACK_RPC}")

# ==============================================================================
# LOTTERY CONFIGURATION
# ==============================================================================
# Fixed ticket price: 0.025 SOL per ticket
# Players can buy unlimited tickets
# Each round lasts exactly 30 minutes
# Winning numbers picked at start of round and revealed at end

TICKET_PRICE = Decimal("0.025")  # Fixed ticket price: 0.025 SOL

# Legacy aliases for compatibility
STAKE_MIN = TICKET_PRICE  # Legacy alias
STAKE_MAX = TICKET_PRICE  # Legacy alias (same as min since fixed price)

# Round schedule - 24 hourly rounds (one per hour)
ROUNDS_PER_DAY = 24  # One round every hour
ROUND_TIMES_UTC = [f"{h:02d}:00" for h in range(24)]  # Every hour on the hour

# Round duration - exactly 60 minutes per round
ROUND_DURATION_MINUTES = 60  # Each round lasts 60 minutes (1 hour) exactly

# No player limits - unlimited players per round
MIN_PLAYERS_TO_DRAW = 0       # No minimum players required
DRAW_WAIT_MINUTES = 0         # No waiting, draw happens at round end
REFUND_TIMEOUT_MINUTES = 0    # No refunds - all ticket sales are final
JOIN_TIMEOUT_MINUTES = 30     # Legacy alias

# Fee structure
TEAM_FEE_PERCENTAGE = Decimal("0.20")  # 20% to team (always deducted first)
WINNER_SHARE_PERCENTAGE = Decimal("0.80")  # 80% to prize pool
NETWORK_FEE_PERCENTAGE = Decimal("0.00")  # No refund fees since no refunds

# Legacy compatibility aliases
MIN_PLAYERS_PER_STAKE = MIN_PLAYERS_TO_DRAW  # Alias for legacy code

# Legacy: Keep STAKE_PACKAGES for database compatibility
STAKE_PACKAGES = [TICKET_PRICE]  # Single fixed price

# DB_PATH removed - now using db.py module for database abstraction

# ==============================================================================
# TIERED PRIZE SYSTEM WITH ROLL-OVER
# ==============================================================================
# Prize tiers based on number matches:
# - 5 matches: 70% of prize pool
# - 4 matches: 20% of prize pool
# - 3 matches: 10% of prize pool
# Prize pool = 80% of round stakes + rollover from previous rounds
# If no winners in a tier, that tier's allocation rolls over to next round

TIER_5_MATCH_PERCENTAGE = Decimal("0.70")  # 70% for 5-match winners
TIER_4_MATCH_PERCENTAGE = Decimal("0.20")  # 20% for 4-match winners
TIER_3_MATCH_PERCENTAGE = Decimal("0.10")  # 10% for 3-match winners

# ==============================================================================
# NETWORK FEE CONFIGURATION
# ==============================================================================
# Estimated Solana transaction fee with safety buffer
# Actual fees are ~0.000005 SOL, we use 0.00002 for safety margin
ESTIMATED_TX_FEE = Decimal("0.00002")  # ~0.00002 SOL per transaction
MIN_PAYOUT_THRESHOLD = Decimal("0.0001")  # Minimum payout worth sending

def get_current_pot() -> Decimal:
    """Get the current accumulated pot amount from database with caching"""
    cached = cache.get_prize_pool()
    if cached is not None:
        return cached
    
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM meta WHERE key = 'current_pot'")
    row = c.fetchone()
    conn.close()
    pot_value = Decimal(row[0]) if row else Decimal("0")
    cache.set_prize_pool(pot_value)
    return pot_value


def set_current_pot(amount: Decimal):
    """Set the current pot amount in database"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO meta (key, value) VALUES ('current_pot', %s)
        ON CONFLICT(key) DO UPDATE SET value = %s
    """, (str(amount), str(amount)))
    conn.commit()
    conn.close()
    cache.set_prize_pool(amount)


def add_to_pot(amount: Decimal):
    """Add stake amount to the pot"""
    current = get_current_pot()
    new_total = current + amount
    set_current_pot(new_total)
    return new_total


def reset_pot():
    """Reset pot to zero after a winner claims it"""
    set_current_pot(Decimal("0"))
    cache.invalidate_prize_pool()


def get_rollover() -> Decimal:
    """Get the current rollover amount from previous rounds"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM meta WHERE key = 'rollover_amount'")
    row = c.fetchone()
    conn.close()
    return Decimal(row[0]) if row else Decimal("0")


def set_rollover(amount: Decimal):
    """Set the rollover amount in database"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO meta (key, value) VALUES ('rollover_amount', %s)
        ON CONFLICT(key) DO UPDATE SET value = %s
    """, (str(amount), str(amount)))
    conn.commit()
    conn.close()


def add_to_rollover(amount: Decimal) -> Decimal:
    """Add amount to the rollover pool"""
    current = get_rollover()
    new_total = current + amount
    set_rollover(new_total)
    print(f"📈 Added {amount} SOL to rollover. New total: {new_total} SOL")
    return new_total


def reset_rollover():
    """Reset rollover to zero"""
    set_rollover(Decimal("0"))


def log_payout(round_id: int, tier: int, user_id: int, amount: Decimal, tx_signature: str = None):
    """Log a payout for transparency and auditing"""
    conn = get_db_conn()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO payout_logs (round_id, tier, user_id, amount, tx_signature, created_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        """, (round_id, tier, user_id, float(amount), tx_signature))
        conn.commit()
    except Exception as e:
        print(f"⚠️ Failed to log payout: {e}")
    finally:
        conn.close()


def log_rollover(round_id: int, rollover_amount: Decimal, reason: str):
    """Log rollover amounts for transparency"""
    conn = get_db_conn()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO rollover_logs (round_id, amount, reason, created_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
        """, (round_id, float(rollover_amount), reason))
        conn.commit()
    except Exception as e:
        print(f"⚠️ Failed to log rollover: {e}")
    finally:
        conn.close()


def is_round_settled(round_id: int) -> bool:
    """Check if a round has already been settled to prevent double processing"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM meta WHERE key = %s", (f'round_{round_id}_settled',))
    row = c.fetchone()
    conn.close()
    return row is not None


def mark_round_settled(round_id: int):
    """Mark a round as settled to prevent double processing"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO meta (key, value) VALUES (%s, %s)
        ON CONFLICT(key) DO NOTHING
    """, (f'round_{round_id}_settled', 'true'))
    conn.commit()
    conn.close()


def lock_round(round_id: int) -> bool:
    """Lock a round so no new tickets can be counted after winning numbers are drawn"""
    conn = get_db_conn()
    c = conn.cursor()
    try:
        c.execute("""
            UPDATE scheduled_rounds SET status = 'locked'
            WHERE round_id = %s AND status = 'open'
        """, (round_id,))
        conn.commit()
        locked = c._cursor.rowcount > 0
        conn.close()
        if locked:
            print(f"🔒 Round {round_id} locked for settlement")
        return locked
    except Exception as e:
        print(f"⚠️ Failed to lock round {round_id}: {e}")
        conn.close()
        return False


def mask_wallet(wallet: str) -> str:
    """Mask wallet address for privacy in public announcements"""
    if not wallet or len(wallet) < 12:
        return "****"
    return f"{wallet[:4]}...{wallet[-4:]}"


async def check_rate_limit(user_id: int, action: RateLimitAction) -> bool:
    """Check if user action is allowed. Returns True if allowed."""
    return rate_limiter.is_allowed(user_id, action)


bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# FSM States for number selection
class NumberSelectionStates(StatesGroup):
    selecting_numbers = State()
    confirming_purchase = State()


# Store user's selected numbers temporarily
user_selected_numbers = {}  # {user_id: {"numbers": [list], "stake_id": int, "round_id": int, "created_at": timestamp}}
NUMBER_SELECTION_TIMEOUT_SECONDS = 300  # 5 minutes timeout for number selection


def cleanup_expired_number_selections():
    """Remove expired number selection sessions to prevent private key retention"""
    import time
    current_time = time.time()
    expired_users = []
    
    for uid, data in user_selected_numbers.items():
        created_at = data.get("created_at", 0)
        if current_time - created_at > NUMBER_SELECTION_TIMEOUT_SECONDS:
            expired_users.append(uid)
    
    for uid in expired_users:
        del user_selected_numbers[uid]
        print(f"[Cleanup] Expired number selection session for user {uid}")


def create_number_picker_keyboard(selected_numbers: list) -> InlineKeyboardMarkup:
    """Create a 8x5 grid of numbers 1-40 for selection with delete buttons for selected numbers"""
    buttons = []
    
    # Show selected numbers with individual delete buttons at the top
    if selected_numbers:
        selected_row = []
        for num in sorted(selected_numbers):
            selected_row.append(InlineKeyboardButton(text=f"❌ {num}", callback_data=f"delete_num_{num}"))
        buttons.append(selected_row)
        # Add a separator label
        buttons.append([InlineKeyboardButton(text=f"📝 Selected: {len(selected_numbers)}/5 — Tap number below to add", callback_data="noop")])
    
    row = []
    for num in range(1, 41):
        # Show selected numbers with checkmark
        if num in selected_numbers:
            text = f"✅ {num}"
        else:
            text = str(num)
        
        row.append(InlineKeyboardButton(text=text, callback_data=f"pick_num_{num}"))
        
        if len(row) == 8:  # 8 numbers per row
            buttons.append(row)
            row = []
    
    if row:  # Add remaining numbers
        buttons.append(row)
    
    # Add action buttons
    action_row = []
    if len(selected_numbers) == 5:
        action_row.append(InlineKeyboardButton(text="✅ Confirm Selection", callback_data="confirm_numbers"))
    action_row.append(InlineKeyboardButton(text="🔄 Clear All", callback_data="clear_numbers"))
    action_row.append(InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_number_selection"))
    buttons.append(action_row)
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

dp.include_router(wallet_router)

# State management for PIN operations and wallet actions
user_states = {}  # Stores pending operations: {user_id: {"action": "set_pin", "data": {...}}}
pending_pins = {}  # Stores PIN attempts: {user_id: {"pin": "1234", "action": "view_key"}}
pin_fail_counts = {}  # Track failed PIN attempts: {user_id: count}
# ---------------------------
# Database helpers
# ---------------------------
def migrate_database():
    """PostgreSQL schema is managed by init_all_tables - no migration needed"""
    # SQLite fully removed. PostgreSQL only.
    print("✅ PostgreSQL: Schema managed by init_all_tables")


def migrate_timestamps_to_iso():
    """PostgreSQL timestamps are UTC by default - no migration needed"""
    # SQLite fully removed. PostgreSQL only.
    print("✅ PostgreSQL: Timestamps are UTC by default")


def init_db():
    """Initialize database tables - delegates to db.py"""
    init_all_tables()
    
    # Run migrations
    migrate_remove_unique_constraint()  # Allow unlimited tickets per user
    migrate_add_referral_column()  # Add missing referral tracking column
    migrate_add_referral_reward_columns()  # Add has_bought_ticket and free_ticket_balance columns
    migrate_add_ticket_id_column()  # Add ticket_id column for multiple tickets per user
    migrate_add_vip_claim_column()  # Add free_ticket_last_claim column for VIP daily bonus tracking
    
    # FORCE FIX: Ensure the unique constraint is removed - runs every startup
    force_fix_participants_constraint()
    
    # Verify database connection and show stats
    try:
        conn = get_db_conn()
        c = conn.cursor()
        
        # Count records in key tables
        c.execute("SELECT COUNT(*) FROM wallets")
        wallet_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users")
        user_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM scheduled_rounds")
        round_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM user_active_wallet")
        active_wallet_count = c.fetchone()[0]
        
        conn.close()
        
        print(f"📊 Database Stats:")
        print(f"   - Users: {user_count}")
        print(f"   - Wallets: {wallet_count}")
        print(f"   - Active wallet entries: {active_wallet_count}")
        print(f"   - Scheduled rounds: {round_count}")
        
        if wallet_count > 0:
            print(f"✅ Database has existing data - persistence is working!")
        else:
            print(f"ℹ️ Database is empty - waiting for first user to create wallet")
            
    except Exception as e:
        print(f"⚠️ Database stats check failed: {e}")


def get_current_round() -> int:
    """Get current round number (1-24, resets after 24)"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM meta WHERE key='current_round'")
    row = c.fetchone()
    conn.close()
    return int(row[0]) if row else 1


def increment_round():
    """Increment round number, reset to 1 after reaching 24"""
    conn = get_db_conn()
    c = conn.cursor()
    current = get_current_round()
    # Reset to 1 after round 24
    if current >= 24:
        new_round = 1
        print(f"[Round] Resetting round counter from {current} to 1 (24-round cycle complete)")
    else:
        new_round = current + 1
    c.execute("UPDATE meta SET value = %s WHERE key='current_round'", (str(new_round),))
    conn.commit()
    conn.close()
    return new_round


def numbers_to_str(nums):
    return ",".join(map(str, sorted(nums)))


def str_to_numbers(s):
    return [int(x) for x in s.split(",") if x.strip()]


def save_user(user_id: int, username: str):
    conn = get_db_conn()
    c = conn.cursor()
    # Always update username if it changed (users can change their Telegram username)
    # PostgreSQL only - no SQLite fallback
    c.execute("""
        INSERT INTO users(user_id, username) VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username
    """, (user_id, username))
    c.execute("""
        INSERT INTO user_stats(user_id) VALUES (%s)
        ON CONFLICT (user_id) DO NOTHING
    """, (user_id,))
    conn.commit()
    conn.close()


# ==============================================================================
# REFERRAL SYSTEM
# ==============================================================================

def generate_referral_code(user_id: int) -> str:
    """Generate unique referral code for a user"""
    import base64
    code_data = f"{user_id}_{secrets.token_hex(4)}"
    return base64.urlsafe_b64encode(code_data.encode()).decode()[:10].upper()


def get_user_referral_code(user_id: int) -> str:
    """Get or create referral code for user"""
    # SQLite fully removed. PostgreSQL only.
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT referral_code FROM referrals WHERE referrer_id = %s LIMIT 1", (user_id,))
    row = c.fetchone()
    if row:
        conn.close()
        return row[0]
    
    # Create new code
    code = generate_referral_code(user_id)
    # Store in meta table for lookup (PostgreSQL only)
    c.execute("INSERT INTO meta(key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", 
              (f"ref_code_{user_id}", code))
    conn.commit()
    conn.close()
    return code


def get_referrer_by_code(code: str) -> Optional[int]:
    """Get referrer user_id from referral code"""
    # SQLite fully removed. PostgreSQL only.
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT key, value FROM meta WHERE key LIKE 'ref_code_%%' AND value = %s", (code.upper(),))
    row = c.fetchone()
    conn.close()
    if row:
        # Extract user_id from key (key is in format ref_code_<user_id>)
        return int(row[0].replace("ref_code_", ""))
    return None


def register_referral(referrer_id: int, referred_id: int, code: str) -> bool:
    """Register a new referral relationship"""
    if referrer_id == referred_id:
        return False
    
    conn = get_db_conn()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO referrals (referrer_id, referred_id, referral_code)
            VALUES (%s, %s, %s)
        """, (referrer_id, referred_id, code))
        conn.commit()
        conn.close()
        return True
    except:
        conn.close()
        return False


def apply_referral_bonus(referred_id: int, ticket_amount: Decimal) -> Optional[Dict]:
    """Apply referral bonus when referred user buys ticket. Returns bonus info or None."""
    REFERRAL_BONUS_PERCENT = Decimal("0.05")  # 5% of ticket goes to referrer
    
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT referrer_id FROM referrals WHERE referred_id = %s", (referred_id,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        return None
    
    referrer_id = row[0]
    bonus_amount = ticket_amount * REFERRAL_BONUS_PERCENT
    
    # Update referral stats
    c.execute("""
        UPDATE referrals 
        SET bonus_earned = bonus_earned + %s, tickets_from_referral = tickets_from_referral + 1
        WHERE referred_id = %s
    """, (float(bonus_amount), referred_id))
    
    # Update referrer's stats
    c.execute("""
        UPDATE user_stats SET referral_earnings = referral_earnings + %s
        WHERE user_id = %s
    """, (float(bonus_amount), referrer_id))
    
    conn.commit()
    conn.close()
    
    return {"referrer_id": referrer_id, "bonus": bonus_amount}


def get_referral_stats(user_id: int) -> Dict:
    """Get referral statistics for a user"""
    conn = get_db_conn()
    c = conn.cursor()
    
    c.execute(q("""
        SELECT COUNT(*), SUM(bonus_earned), SUM(tickets_from_referral)
        FROM referrals WHERE referrer_id = ?
    """), (user_id,))
    row = c.fetchone()
    
    conn.close()
    return {
        "total_referrals": row[0] or 0,
        "total_bonus": Decimal(str(row[1] or 0)),
        "total_tickets": row[2] or 0
    }


def process_referral_on_first_ticket(user_id: int) -> Optional[int]:
    """
    Process referral bonus when user buys their first ticket.
    Returns referrer_id if bonus awarded, None otherwise.
    SYNC FUNCTION - database operations only.
    """
    conn = get_db_conn()
    c = conn.cursor()
    
    try:
        # Check if this is user's first ticket
        c.execute("SELECT has_bought_ticket FROM users WHERE user_id = %s", (user_id,))
        user_row = c.fetchone()
        has_bought = user_row[0] if user_row else 0
        
        if has_bought:
            conn.close()
            return None  # Not first ticket
        
        # Mark user as having bought a ticket
        c.execute("UPDATE users SET has_bought_ticket = 1 WHERE user_id = %s", (user_id,))
        conn.commit()
        
        # Check if user was referred
        c.execute("SELECT referrer_id FROM referrals WHERE referred_id = %s", (user_id,))
        referral_row = c.fetchone()
        
        if not referral_row:
            conn.close()
            return None  # No referrer
        
        referrer_id = referral_row[0]
        
        # Mark referral as successful (first ticket purchased)
        c.execute("UPDATE referrals SET tickets_from_referral = 1 WHERE referred_id = %s", (user_id,))
        
        # Increment referrer's referral_count counter
        c.execute("UPDATE user_stats SET referral_count = referral_count + 1 WHERE user_id = %s", (referrer_id,))
        conn.commit()
        
        # Check if referrer now has 2+ referrals and award free ticket
        c.execute("SELECT referral_count FROM user_stats WHERE user_id = %s", (referrer_id,))
        stats_row = c.fetchone()
        referrer_count = stats_row[0] if stats_row else 0
        
        if referrer_count >= 2:
            # Award free ticket and decrement counter by 2
            c.execute("UPDATE users SET free_ticket_balance = free_ticket_balance + 1 WHERE user_id = %s", (referrer_id,))
            c.execute("UPDATE user_stats SET referral_count = referral_count - 2 WHERE user_id = %s", (referrer_id,))
            conn.commit()
        
        conn.close()
        return referrer_id
    except Exception as e:
        conn.close()
        print(f"Error processing referral for user {user_id}: {e}")
        return None


def decrement_free_ticket_balance(user_id: int) -> bool:
    """
    Decrement free ticket balance when user uses a free ticket.
    Returns True if successful.
    SYNC FUNCTION - database operations only.
    """
    conn = get_db_conn()
    c = conn.cursor()
    
    try:
        c.execute("UPDATE users SET free_ticket_balance = CASE WHEN free_ticket_balance > 0 THEN free_ticket_balance - 1 ELSE 0 END WHERE user_id = %s", (user_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        conn.close()
        print(f"Error decrementing free ticket for user {user_id}: {e}")
        return False


# ==============================================================================
# USER STATS & VIP TIERS
# ==============================================================================

VIP_TIERS = {
    0: {"name": "Bronze", "min_tickets": 0, "bonus_multiplier": Decimal("1.0")},
    1: {"name": "Silver", "min_tickets": 10, "bonus_multiplier": Decimal("1.1")},
    2: {"name": "Gold", "min_tickets": 50, "bonus_multiplier": Decimal("1.2")},
    3: {"name": "Platinum", "min_tickets": 100, "bonus_multiplier": Decimal("1.3")},
    4: {"name": "Diamond", "min_tickets": 500, "bonus_multiplier": Decimal("1.5")},
}


def compute_vip_tier(total_tickets: int) -> int:
    """Compute VIP tier based on total tickets purchased"""
    tier = 0
    for t, info in VIP_TIERS.items():
        if total_tickets >= info["min_tickets"]:
            tier = t
    return tier


def update_user_stats(user_id: int, tickets: int = 0, spent: Decimal = Decimal("0"), 
                      won: Decimal = Decimal("0"), is_win: bool = False):
    """Update user statistics after purchase or win"""
    conn = get_db_conn()
    c = conn.cursor()
    
    # Ensure user stats exist
    c.execute("INSERT INTO user_stats(user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    
    # Get current stats
    c.execute("SELECT total_tickets, biggest_win FROM user_stats WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    current_tickets = row[0] if row else 0
    biggest_win = Decimal(str(row[1])) if row and row[1] else Decimal("0")
    
    # Update stats
    new_tickets = current_tickets + tickets
    new_tier = compute_vip_tier(new_tickets)
    new_biggest = max(biggest_win, won)
    
    c.execute("""
        UPDATE user_stats SET 
            total_tickets = total_tickets + %s,
            total_spent = total_spent + %s,
            total_won = total_won + %s,
            wins = wins + %s,
            biggest_win = %s,
            vip_tier = %s
        WHERE user_id = %s
    """, (tickets, float(spent), float(won), 1 if is_win else 0, float(new_biggest), new_tier, user_id))
    
    conn.commit()
    conn.close()
    return new_tier


def get_user_stats(user_id: int) -> Dict:
    """Get user statistics"""
    conn = get_db_conn()
    c = conn.cursor()
    
    c.execute("INSERT INTO user_stats(user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    c.execute("SELECT * FROM user_stats WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        tier = row[7] if len(row) > 7 else 0
        return {
            "user_id": row[0],
            "total_tickets": row[1] or 0,
            "total_spent": Decimal(str(row[2] or 0)),
            "total_won": Decimal(str(row[3] or 0)),
            "wins": row[4] or 0,
            "biggest_win": Decimal(str(row[5] or 0)),
            "referral_earnings": Decimal(str(row[6] or 0)),
            "vip_tier": tier,
            "vip_name": VIP_TIERS.get(tier, VIP_TIERS[0])["name"],
            "notification_enabled": row[8] if len(row) > 8 else 1
        }
    return {"total_tickets": 0, "vip_tier": 0, "vip_name": "Bronze"}


# ==============================================================================
# LEADERBOARD
# ==============================================================================

def get_top_winners(limit: int = 10) -> List[Dict]:
    """Get top winners by total amount won from payout logs"""
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("""
            SELECT pl.user_id, u.username, SUM(pl.amount) as total_won, COUNT(*) as wins, us.vip_tier
            FROM payout_logs pl
            LEFT JOIN users u ON pl.user_id = u.user_id
            LEFT JOIN user_stats us ON pl.user_id = us.user_id
            GROUP BY pl.user_id, u.username, us.vip_tier
            ORDER BY total_won DESC
            LIMIT %s
        """, (limit,))
        rows = c.fetchall()
        conn.close()
        
        print(f"🔍 DEBUG: get_top_winners found {len(rows)} winners from payout_logs")
        
        result = [{
            "user_id": r[0],
            "username": r[1] or f"User{r[0]}",
            "total_won": Decimal(str(r[2] or 0)),
            "wins": r[3] or 0,
            "vip_tier": r[4] or 0
        } for r in rows]
        
        print(f"🔍 DEBUG: Returning {len(result)} winners: {result}")
        return result
    except Exception as e:
        print(f"❌ ERROR in get_top_winners: {e}")
        import traceback
        traceback.print_exc()
        return []


def get_top_players(limit: int = 10) -> List[Dict]:
    """Get top players by total tickets purchased (excludes the VIP user ID from environment)"""
    conn = get_db_conn()
    c = conn.cursor()
    
    # Build WHERE clause to exclude VIP user if configured
    where_clause = "us.total_tickets > 0"
    params = (limit,)
    
    if VIP_TELEGRAM_ID > 0:
        where_clause += " AND us.user_id != %s"
        params = (VIP_TELEGRAM_ID, limit)
    
    c.execute(f"""
        SELECT us.user_id, u.username, us.total_tickets, us.total_spent, us.vip_tier
        FROM user_stats us
        LEFT JOIN users u ON us.user_id = u.user_id
        WHERE {where_clause}
        ORDER BY us.total_tickets DESC
        LIMIT %s
    """, params)
    rows = c.fetchall()
    conn.close()
    
    return [{
        "user_id": r[0],
        "username": r[1] or f"User{r[0]}",
        "total_tickets": r[2],
        "total_spent": Decimal(str(r[3])),
        "vip_tier": r[4]
    } for r in rows]


# ==============================================================================
# MATCH COUNTING (for determining jackpot winner)
# ==============================================================================

def count_matching_numbers(player_nums: List[int], winning_nums: List[int]) -> int:
    """Count how many numbers match"""
    return len(set(player_nums) & set(winning_nums))


# ==============================================================================
# DRAW HISTORY & PROVABLY FAIR
# ==============================================================================

def save_draw_history(round_id: int, winning_numbers: List[int], seed_data: str, 
                      player_count: int, total_pot: Decimal, winner_id: int = None,
                      prize_amount: Decimal = None, tx_signature: str = None):
    """Save draw to history for provably fair verification with timestamp"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO draw_history 
        (round_id, winning_numbers, seed_data, player_count, total_pot, winner_id, prize_amount, tx_signature, drawn_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
    """, (round_id, numbers_to_str(winning_numbers), seed_data, player_count, 
          float(total_pot), winner_id, float(prize_amount) if prize_amount else None, tx_signature))
    conn.commit()
    conn.close()


def get_draw_history(limit: int = 20) -> List[Dict]:
    """Get recent draw history"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
        SELECT round_id, winning_numbers, player_count, total_pot, winner_id, prize_amount, drawn_at
        FROM draw_history
        ORDER BY drawn_at DESC
        LIMIT %s
    """, (limit,))
    rows = c.fetchall()
    conn.close()
    
    return [{
        "round_id": r[0],
        "winning_numbers": str_to_numbers(r[1]),
        "player_count": r[2],
        "total_pot": Decimal(str(r[3] or 0)),
        "winner_id": r[4],
        "prize_amount": Decimal(str(r[5] or 0)),
        "drawn_at": r[6]
    } for r in rows]


def get_overall_stats() -> Dict:
    """Get overall lottery statistics"""
    conn = get_db_conn()
    c = conn.cursor()
    
    # Total draws
    c.execute("SELECT COUNT(*) FROM draw_history")
    total_draws = c.fetchone()[0] or 0
    
    # Total prizes paid
    c.execute("SELECT SUM(prize_amount) FROM draw_history WHERE winner_id IS NOT NULL")
    total_paid = Decimal(str(c.fetchone()[0] or 0))
    
    # Total players
    c.execute("SELECT COUNT(*) FROM user_stats WHERE total_tickets > 0")
    total_players = c.fetchone()[0] or 0
    
    # Total tickets sold
    c.execute("SELECT SUM(total_tickets) FROM user_stats")
    total_tickets = c.fetchone()[0] or 0
    
    # Biggest jackpot from payout_logs
    c.execute("SELECT MAX(amount) FROM payout_logs")
    biggest_jackpot = Decimal(str(c.fetchone()[0] or 0))
    
    conn.close()
    
    return {
        "total_draws": total_draws,
        "total_paid": total_paid,
        "total_players": total_players,
        "total_tickets": total_tickets,
        "biggest_jackpot": biggest_jackpot
    }


# ==============================================================================
# TRANSPARENCY DASHBOARD FUNCTIONS
# ==============================================================================

def get_transparency_stats() -> Dict:
    """Get comprehensive transparency statistics for the dashboard"""
    conn = get_db_conn()
    c = conn.cursor()
    
    # Total draws completed - count distinct rounds that had payouts from payout_logs
    c.execute("SELECT COUNT(DISTINCT round_id) FROM payout_logs")
    total_draws = c.fetchone()[0] or 0
    
    # Total prizes distributed from payout_logs
    c.execute("SELECT SUM(amount) FROM payout_logs")
    total_distributed = Decimal(str(c.fetchone()[0] or 0))
    
    # Total tickets sold all time
    c.execute("SELECT SUM(total_tickets) FROM user_stats")
    total_tickets_alltime = c.fetchone()[0] or 0
    
    # Unique players (those who have bought at least one ticket) - from round_participants
    c.execute("SELECT COUNT(DISTINCT user_id) FROM round_participants WHERE refunded = 0")
    unique_players = c.fetchone()[0] or 0
    
    # Tickets sold in last 24 hours (rolling window) - from round_participants table
    c.execute("""
        SELECT COUNT(*) FROM round_participants 
        WHERE created_at >= NOW() - INTERVAL '24 hours' AND refunded = 0
    """)
    tickets_today = c.fetchone()[0] or 0
    
    # Active rounds count
    c.execute("SELECT COUNT(*) FROM scheduled_rounds WHERE status IN ('open', 'pending')")
    active_rounds = c.fetchone()[0] or 0
    
    # Draws in last 24 hours (rolling window) - count distinct rounds from payout_logs
    c.execute("""
        SELECT COUNT(DISTINCT round_id) FROM payout_logs 
        WHERE created_at >= NOW() - INTERVAL '24 hours'
    """)
    draws_today = c.fetchone()[0] or 0
    
    # Winners in last 24 hours (rolling window) - count distinct winners from payout_logs
    c.execute("""
        SELECT COUNT(DISTINCT user_id) FROM payout_logs 
        WHERE created_at >= NOW() - INTERVAL '24 hours'
    """)
    winners_today = c.fetchone()[0] or 0
    
    # Average pot size
    c.execute("SELECT AVG(total_pot) FROM draw_history WHERE total_pot > 0")
    avg_pot = Decimal(str(c.fetchone()[0] or 0))
    
    # Biggest jackpot ever from payout_logs
    c.execute("SELECT MAX(amount) FROM payout_logs")
    biggest_jackpot = Decimal(str(c.fetchone()[0] or 0))
    
    # Last 24h volume
    c.execute("""
        SELECT SUM(total_pot) FROM draw_history 
        WHERE drawn_at >= NOW() - INTERVAL '24 hours'
    """)
    volume_24h = Decimal(str(c.fetchone()[0] or 0))
    
    conn.close()
    
    return {
        "total_draws": total_draws,
        "total_distributed": total_distributed,
        "total_tickets_alltime": total_tickets_alltime,
        "unique_players": unique_players,
        "tickets_today": tickets_today,
        "active_rounds": active_rounds,
        "draws_today": draws_today,
        "winners_today": winners_today,
        "avg_pot": avg_pot,
        "biggest_jackpot": biggest_jackpot,
        "volume_24h": volume_24h
    }


def get_draw_for_verification(round_id: int) -> Optional[Dict]:
    """Get draw data for cryptographic verification"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
        SELECT round_id, winning_numbers, seed_data, player_count, 
               total_pot, winner_id, prize_amount, tx_signature, drawn_at
        FROM draw_history
        WHERE round_id = %s
    """, (round_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return None
    
    return {
        "round_id": row[0],
        "winning_numbers": str_to_numbers(row[1]) if row[1] else [],
        "seed_data": row[2],
        "player_count": row[3],
        "total_pot": Decimal(str(row[4] or 0)),
        "winner_id": row[5],
        "prize_amount": Decimal(str(row[6] or 0)),
        "tx_signature": row[7],
        "drawn_at": row[8]
    }


def get_recent_draws_with_verification(limit: int = 10) -> List[Dict]:
    """Get recent draws from payout logs with winning numbers and winner's picked numbers"""
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("""
            SELECT DISTINCT ON (pl.round_id)
                   pl.round_id, dh.winning_numbers, dh.seed_data, 
                   COUNT(DISTINCT pl.user_id) as player_count,
                   SUM(pl.amount) as total_prize,
                   pl.user_id, pl.amount, pl.tx_signature, pl.created_at,
                   rp.numbers as winner_numbers
            FROM payout_logs pl
            LEFT JOIN draw_history dh ON pl.round_id = dh.round_id
            LEFT JOIN round_participants rp ON pl.user_id = rp.user_id AND pl.round_id = rp.round_stake_id
            GROUP BY pl.round_id, dh.winning_numbers, dh.seed_data, pl.user_id, pl.amount, pl.tx_signature, pl.created_at, rp.numbers
            ORDER BY pl.round_id DESC, pl.created_at DESC
            LIMIT %s
        """, (limit,))
        rows = c.fetchall()
        conn.close()
        
        print(f"🔍 DEBUG: get_recent_draws_with_verification found {len(rows)} draws from payout_logs")
        
        result = [{
            "round_id": r[0],
            "winning_numbers": str_to_numbers(r[1]) if r[1] else [],
            "seed_data": r[2],
            "player_count": r[3] or 0,
            "total_pot": Decimal(str(r[4] or 0)),
            "winner_id": r[5],
            "prize_amount": Decimal(str(r[6] or 0)),
            "tx_signature": r[7],
            "drawn_at": r[8],
            "winner_numbers": str_to_numbers(r[9]) if r[9] else []
        } for r in rows]
        
        print(f"🔍 DEBUG: Returning {len(result)} draws: {result}")
        return result
    except Exception as e:
        print(f"❌ ERROR in get_recent_draws_with_verification: {e}")
        import traceback
        traceback.print_exc()
        return []


def get_live_round_stats() -> List[Dict]:
    """Get live statistics for currently open rounds"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
        SELECT sr.round_id, sr.round_number, sr.status, sr.start_time,
               COUNT(DISTINCT rp.id) as ticket_count,
               COALESCE(SUM(rs.stake_amount), 0) as total_pool
        FROM scheduled_rounds sr
        LEFT JOIN round_stakes rs ON sr.round_id = rs.round_id AND rs.status = 'open'
        LEFT JOIN round_participants rp ON rs.id = rp.round_stake_id AND rp.refunded = 0
        WHERE sr.status IN ('open', 'locked')
        GROUP BY sr.round_id, sr.round_number, sr.status, sr.start_time
        ORDER BY sr.scheduled_time ASC
    """)
    rows = c.fetchall()
    conn.close()
    
    return [{
        "round_id": r[0],
        "round_number": r[1],
        "status": r[2],
        "start_time": r[3],
        "ticket_count": r[4],
        "total_pool": Decimal(str(r[5] or 0))
    } for r in rows]


# ==============================================================================
# JACKPOT SEEDING (Admin)
# ==============================================================================

def record_jackpot_seed(admin_id: int, amount: Decimal, tx_signature: str = None):
    """Record admin jackpot seed contribution"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO jackpot_seeds (admin_id, amount, tx_signature)
        VALUES (%s, %s, %s)
    """, (admin_id, float(amount), tx_signature))
    conn.commit()
    conn.close()


def get_total_seeded() -> Decimal:
    """Get total amount seeded into jackpot by admins"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT SUM(amount) FROM jackpot_seeds")
    row = c.fetchone()
    conn.close()
    return Decimal(str(row[0] or 0))


# ==============================================================================
# NOTIFICATIONS
# ==============================================================================

def toggle_notifications(user_id: int) -> bool:
    """Toggle notification preference for user. Returns new state."""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("INSERT INTO user_stats(user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    c.execute("SELECT notification_enabled FROM user_stats WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    current = row[0] if row else 1
    new_state = 0 if current else 1
    c.execute("UPDATE user_stats SET notification_enabled = %s WHERE user_id = %s", (new_state, user_id))
    conn.commit()
    conn.close()
    return bool(new_state)


def get_users_with_notifications() -> List[int]:
    """Get list of user IDs with notifications enabled"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT user_id FROM user_stats WHERE notification_enabled = 1")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]


def add_entry(user_id: int, round_num: int, numbers, stake_amount: float, tx_signature: str = "", paid=0):
    conn = get_db_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO entries(user_id, round, numbers, stake_amount, tx_signature, paid) VALUES (%s, %s, %s, %s, %s, %s)",
        (user_id, round_num, numbers_to_str(numbers), stake_amount, tx_signature, paid))
    conn.commit()
    conn.close()


def get_entries_for_round(round_num: int):
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT id, user_id, numbers, paid FROM entries WHERE round = %s", (round_num,))
    rows = c.fetchall()
    conn.close()
    return rows


def get_user_last_entry(user_id: int):
    conn = get_db_conn()
    c = conn.cursor()
    c.execute(
        "SELECT round, numbers, paid FROM entries WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
        (user_id,)
    )
    row = c.fetchone()
    conn.close()
    return row


def save_draw(round_num: int, winning_numbers):
    conn = get_db_conn()
    c = conn.cursor()
    # PostgreSQL only - no SQLite fallback
    c.execute(
        "INSERT INTO draws(round, winning_numbers) VALUES (%s, %s) ON CONFLICT (round) DO UPDATE SET winning_numbers = EXCLUDED.winning_numbers",
        (round_num, numbers_to_str(winning_numbers))
    )
    conn.commit()
    conn.close()


def generate_round_winning_numbers(round_id: int) -> List[int]:
    """Generate winning numbers for a round at its start (hidden until round ends)"""
    seed = generate_provable_seed(round_id, "round_winning_numbers", datetime.now(pytz.UTC).isoformat())
    return generate_lottery_numbers(seed, count=5, min_val=1, max_val=40)


def set_round_winning_numbers(round_id: int, winning_numbers: List[int]):
    """Store winning numbers for a round (generated at round start, revealed at end)"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
        UPDATE scheduled_rounds SET winning_numbers = %s WHERE round_id = %s
    """, (numbers_to_str(winning_numbers), round_id))
    conn.commit()
    conn.close()


def get_round_winning_numbers(round_id: int) -> Optional[List[int]]:
    """Get winning numbers for a round (only if they exist)"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT winning_numbers FROM scheduled_rounds WHERE round_id = %s", (round_id,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        return str_to_numbers(row[0])
    return None


def migrate_add_winning_numbers_column():
    """Add winning_numbers column to scheduled_rounds if it doesn't exist"""
    conn = get_db_conn()
    c = conn.cursor()
    try:
        c.execute("ALTER TABLE scheduled_rounds ADD COLUMN winning_numbers TEXT")
        conn.commit()
        print("✅ Added winning_numbers column to scheduled_rounds")
    except Exception:
        # Column already exists (SQLite OperationalError or PostgreSQL error)
        pass
    finally:
        conn.close()


def create_scheduled_round(round_number: int, scheduled_time: datetime):
    conn = get_db_conn()
    c = conn.cursor()
    try:
        scheduled_time_str = scheduled_time.isoformat() if hasattr(scheduled_time, 'isoformat') else scheduled_time
        # PostgreSQL only - no SQLite fallback
        c.execute("""
            INSERT INTO scheduled_rounds (round_number, scheduled_time, status)
            VALUES (%s, %s, 'pending') RETURNING round_id
        """, (round_number, scheduled_time_str))
        round_id = c.fetchone()[0]
        
        for stake in STAKE_PACKAGES:
            c.execute("""
                INSERT INTO round_stakes (round_id, stake_amount, status)
                VALUES (%s, %s, 'open')
            """, (round_id, float(stake)))
        
        conn.commit()
        
        winning_nums = generate_round_winning_numbers(round_id)
        set_round_winning_numbers(round_id, winning_nums)
        print(f"🎲 Round {round_id} created with hidden winning numbers")
        print(f"[VIP] Winning numbers for round {round_id}: {winning_nums}")
        
        return (round_id, winning_nums)
    except Exception as e:
        # Handle both SQLite and PostgreSQL integrity errors
        if "unique" in str(e).lower() or "duplicate" in str(e).lower() or "integrity" in str(e).lower():
            conn.rollback()
            return None
        conn.rollback()
        raise
    finally:
        conn.close()


def get_round_number(round_id: int) -> int:
    """Get the round_number (1-24) for a given round_id"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT round_number FROM scheduled_rounds WHERE round_id = %s", (round_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else round_id


def get_active_rounds():
    """Get only current open round and next pending round (max 2)"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
        SELECT round_id, round_number, scheduled_time, start_time, end_time, status
        FROM scheduled_rounds
        WHERE status IN ('open', 'pending')
        ORDER BY scheduled_time ASC
        LIMIT 2
    """)
    rows = c.fetchall()
    conn.close()
    return rows


def get_round_stakes_with_counts(round_id: int):
    conn = get_db_conn()
    c = conn.cursor()
    c.execute(q("""
        SELECT rs.id, rs.stake_amount, rs.status,
               COUNT(rp.id) as player_count
        FROM round_stakes rs
        LEFT JOIN round_participants rp ON rs.id = rp.round_stake_id AND rp.refunded = 0
        WHERE rs.round_id = ?
        GROUP BY rs.id, rs.stake_amount, rs.status
        ORDER BY rs.stake_amount ASC
    """), (round_id,))
    rows = c.fetchall()
    conn.close()
    return rows


def add_round_participant(round_stake_id: int, user_id: int, numbers: list, tx_signature: str):
    """
    Add a participant to a round stake.
    ALLOWS MULTIPLE TICKETS: Users can buy as many tickets as they want.
    Each ticket is its own row with a unique ticket_id.
    Same user may insert multiple rows for the same round.
    Duplicates are only blocked by tx_signature (must be UNIQUE).
    """
    import uuid
    
    conn = get_db_conn()
    c = conn.cursor()
    
    c.execute(q("""
        SELECT rs.status, rs.round_id, sr.status as round_status, sr.end_time, rs.stake_amount
        FROM round_stakes rs
        JOIN scheduled_rounds sr ON rs.round_id = sr.round_id
        WHERE rs.id = ?
    """), (round_stake_id,))
    stake_info = c.fetchone()
    
    if not stake_info:
        conn.close()
        return {"success": False, "error": "Round stake not found"}
    
    stake_status, round_id, round_status, end_time, stake_amount = stake_info
    
    if stake_status != 'open' or round_status != 'open':
        conn.close()
        return {"success": False, "error": "Round is not accepting participants"}
    
    if end_time:
        end_datetime = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        if end_datetime.tzinfo is None:
            end_datetime = end_datetime.replace(tzinfo=pytz.UTC)
        if datetime.now(pytz.UTC) > end_datetime:
            conn.close()
            return {"success": False, "error": "Round has ended"}
    
    # Generate unique ticket_id
    ticket_id = str(uuid.uuid4())
    
    try:
        # PostgreSQL only - no SQLite fallback
        c.execute("""
            INSERT INTO round_participants (ticket_id, round_stake_id, user_id, numbers, tx_signature)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
        """, (ticket_id, round_stake_id, user_id, numbers_to_str(numbers), tx_signature))
        participant_id = c.fetchone()[0]
        
        # Get first stake time and count - counts all tickets (not distinct users)
        c.execute(q("""
            SELECT rs.first_stake_time, COUNT(rp.id) as count
            FROM round_stakes rs
            LEFT JOIN round_participants rp ON rs.id = rp.round_stake_id AND rp.refunded = 0
            WHERE rs.id = ?
            GROUP BY rs.id, rs.first_stake_time
        """), (round_stake_id,))
        row = c.fetchone()
        first_time = row[0] if row else None
        count = row[1] if row else 0
        
        if not first_time and count == 1:
            now_utc = datetime.now(pytz.UTC).isoformat()
            c.execute(q("""
                UPDATE round_stakes
                SET first_stake_time = ?
                WHERE id = ?
            """), (now_utc, round_stake_id))
        
        conn.commit()
        conn.close()
        
        # Update user stats (tickets purchased + amount spent)
        update_user_stats(user_id, tickets=1, spent=Decimal(str(stake_amount)))
        
        # Referral bonus tracking disabled - coming soon
        # apply_referral_bonus(user_id, Decimal(str(stake_amount)))
        
        return {"success": True, "participant_id": participant_id, "ticket_id": ticket_id, "round_id": round_id, "stake_amount": stake_amount, "ticket_count": count}
    except Exception as e:
        error_str = str(e).lower()
        # Handle tx_signature uniqueness - treat as already processed
        if "unique" in error_str or "duplicate" in error_str:
            conn.rollback()
            conn.close()
            return {"success": True, "already_processed": True, "error": "Transaction already processed"}
        conn.rollback()
        conn.close()
        return {"success": False, "error": str(e)}


def get_round_stake_by_amount(round_id: int, stake_amount: Decimal):
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, status FROM round_stakes
        WHERE round_id = %s AND stake_amount = %s
    """, (round_id, float(stake_amount)))
    row = c.fetchone()
    conn.close()
    return row


def get_or_create_active_round_stake(stake_amount: Decimal):
    """
    Get or create an active round and stake for the given stake amount.
    Returns (round_stake_id, round_id) tuple or (None, None) if no active round.
    """
    conn = get_db_conn()
    c = conn.cursor()
    
    # Find an open round
    c.execute("""
        SELECT round_id FROM scheduled_rounds 
        WHERE status = 'open' 
        ORDER BY scheduled_time ASC LIMIT 1
    """)
    round_row = c.fetchone()
    
    if not round_row:
        # No open round, check for pending round and open it
        c.execute("""
            SELECT round_id FROM scheduled_rounds 
            WHERE status = 'pending' 
            ORDER BY scheduled_time ASC LIMIT 1
        """)
        pending_row = c.fetchone()
        
        if not pending_row:
            # No rounds at all - create one
            # PostgreSQL only - no SQLite fallback
            now_utc = datetime.now(pytz.UTC)
            c.execute("""
                INSERT INTO scheduled_rounds (round_number, scheduled_time, status, start_time)
                VALUES (%s, %s, 'open', %s) RETURNING round_id
            """, (get_current_round(), now_utc.isoformat(), now_utc.isoformat()))
            round_id = c.fetchone()[0]
        else:
            round_id = pending_row[0]
            # Open the pending round
            now_utc = datetime.now(pytz.UTC).isoformat()
            c.execute("""
                UPDATE scheduled_rounds SET status = 'open', start_time = %s
                WHERE round_id = %s
            """, (now_utc, round_id))
    else:
        round_id = round_row[0]
    
    # Check if stake exists for this round
    c.execute("""
        SELECT id, status FROM round_stakes 
        WHERE round_id = %s AND stake_amount = %s
    """, (round_id, float(stake_amount)))
    stake_row = c.fetchone()
    
    if stake_row:
        stake_id, status = stake_row
        if status != 'open':
            conn.close()
            return None, None  # Stake is not open
    else:
        # Create the stake - PostgreSQL only - no SQLite fallback
        c.execute("""
            INSERT INTO round_stakes (round_id, stake_amount, status)
            VALUES (%s, %s, 'open') RETURNING id
        """, (round_id, float(stake_amount)))
        stake_id = c.fetchone()[0]
    
    conn.commit()
    conn.close()
    return stake_id, round_id


async def verify_solana_transaction(tx_signature: str, expected_recipient: str, expected_amount: Decimal, sender_wallet: str = None):
    """
    Verify a Solana transaction on-chain with robust security checks.
    
    Args:
        tx_signature: The transaction signature to verify
        expected_recipient: The wallet that should have received SOL
        expected_amount: The minimum amount that should have been sent
        sender_wallet: Optional sender wallet to verify (if known)
    
    Returns:
        dict with 'valid', 'error', and transaction details
    
    Security checks:
        - Transaction must be confirmed and successful
        - Recipient must receive at least expected_amount (minus small margin)
        - Sender must match if specified
        - Checks for suspicious rebate patterns (recipient also sending back)
    """
    try:
        from solana.rpc.async_api import AsyncClient
        from solders.signature import Signature
        from wallet import RPC_ENDPOINTS
        
        # Parse the signature first
        try:
            sig = Signature.from_string(tx_signature)
        except Exception as e:
            return {"valid": False, "error": f"Invalid signature format: {e}"}
        
        # Try each RPC endpoint with failover
        last_error = None
        for rpc in RPC_ENDPOINTS:
            try:
                async with AsyncClient(rpc) as client:
                    # Get transaction details
                    tx_response = await client.get_transaction(
                        sig,
                        encoding="jsonParsed",
                        max_supported_transaction_version=0
                    )
                    
                    if not tx_response or not tx_response.value:
                        return {"valid": False, "error": "Transaction not found on blockchain. Please wait for confirmation."}
                    
                    tx = tx_response.value
                    
                    # Check if transaction was successful
                    if tx.transaction.meta.err is not None:
                        return {"valid": False, "error": "Transaction failed on blockchain"}
                    
                    # Parse the transaction to find SOL transfers
                    try:
                        # Get pre and post balances
                        pre_balances = tx.transaction.meta.pre_balances
                        post_balances = tx.transaction.meta.post_balances
                        account_keys = tx.transaction.transaction.message.account_keys
                        
                        # Build account map for balance changes
                        account_changes = {}
                        recipient_idx = None
                        sender_idx = None
                        
                        for i, key in enumerate(account_keys):
                            key_str = str(key.pubkey) if hasattr(key, 'pubkey') else str(key)
                            balance_change = post_balances[i] - pre_balances[i]
                            account_changes[key_str] = Decimal(balance_change) / Decimal("1000000000")
                            
                            if key_str == expected_recipient:
                                recipient_idx = i
                            if sender_wallet and key_str == sender_wallet:
                                sender_idx = i
                        
                        # Check recipient received funds
                        if recipient_idx is None:
                            return {"valid": False, "error": f"Recipient {expected_recipient[:8]}... not found in transaction"}
                        
                        amount_received = account_changes[expected_recipient]
                        
                        # Security check: recipient should ONLY receive, not send back
                        if amount_received <= Decimal("0"):
                            return {"valid": False, "error": "Recipient did not receive positive balance in this transaction"}
                        
                        # Check sender if specified
                        if sender_wallet:
                            if sender_idx is None:
                                return {"valid": False, "error": "Your wallet is not part of this transaction"}
                            
                            sender_change = account_changes[sender_wallet]
                            # Sender should have negative balance change (sent funds + fees)
                            if sender_change >= Decimal("0"):
                                return {"valid": False, "error": "Sender did not send funds in this transaction"}
                        
                        # Allow small margin (0.001 SOL) for rounding only
                        min_expected = expected_amount - Decimal("0.001")
                        if amount_received < min_expected:
                            return {
                                "valid": False, 
                                "error": f"Amount received ({amount_received:.6f} SOL) less than required ({expected_amount} SOL)"
                            }
                        
                        return {
                            "valid": True,
                            "amount_received": float(amount_received),
                            "signature": tx_signature
                        }
                        
                    except Exception as e:
                        return {"valid": False, "error": f"Failed to parse transaction: {e}"}
                        
            except Exception as e:
                last_error = e
                continue  # Try next RPC
        
        # All RPCs failed
        return {"valid": False, "error": f"All RPC endpoints failed. Last error: {last_error}"}
                
    except ImportError as e:
        return {"valid": False, "error": f"Solana library error: {e}"}
    except Exception as e:
        return {"valid": False, "error": f"Verification failed: {e}"}


def update_round_status(round_id: int, status: str):
    conn = get_db_conn()
    c = conn.cursor()
    
    # Use UTC timezone-aware timestamps in ISO format
    now_utc = datetime.now(pytz.UTC).isoformat()
    
    # Check if we need to set start_time
    if status == 'open':
        c.execute("SELECT start_time FROM scheduled_rounds WHERE round_id = %s", (round_id,))
        row = c.fetchone()
        if row and not row[0]:
            c.execute("""
                UPDATE scheduled_rounds
                SET status = %s, start_time = %s
                WHERE round_id = %s
            """, (status, now_utc, round_id))
        else:
            c.execute("""
                UPDATE scheduled_rounds
                SET status = %s
                WHERE round_id = %s
            """, (status, round_id))
    elif status in ('closed', 'completed'):
        c.execute("""
            UPDATE scheduled_rounds
            SET status = %s, end_time = %s
            WHERE round_id = %s
        """, (status, now_utc, round_id))
    else:
        c.execute("""
            UPDATE scheduled_rounds
            SET status = %s
            WHERE round_id = %s
        """, (status, round_id))
    
    conn.commit()
    conn.close()


async def send_winner_payout(winner_user_id: int, prize_amount: Decimal, round_stake_id: int) -> Dict:
    """
    Send prize to winner with SMART FEE HANDLING:
    - Team gets 20% of total pool
    - Winner gets 80% of total pool
    - Transaction fees are RESERVED from team's share (operational cost)
    - Winner receives their full calculated share
    """
    try:
        # Get winner's wallet
        winner_wallet = get_active_wallet(winner_user_id)
        if not winner_wallet:
            print(f"❌ Winner {winner_user_id} has no active wallet")
            return {"success": False, "error": "Winner has no active wallet"}
        
        # Calculate amounts from database
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("""
            SELECT rs.stake_amount, COUNT(rp.id) as player_count
            FROM round_stakes rs
            LEFT JOIN round_participants rp ON rs.id = rp.round_stake_id AND rp.refunded = 0
            WHERE rs.id = %s
            GROUP BY rs.stake_amount
        """, (round_stake_id,))
        result = c.fetchone()
        conn.close()
        
        if not result or result[0] is None:
            print(f"❌ Round stake {round_stake_id} not found or invalid")
            return {"success": False, "error": "Round stake not found"}
        
        stake_amount, player_count = result
        
        if player_count < 1:
            print(f"❌ No participants in round stake {round_stake_id}")
            return {"success": False, "error": "No participants in round"}
        
        total_pool = Decimal(str(stake_amount)) * player_count
        
        # SMART FEE HANDLING: Reserve fees from team's share
        # Calculate how many transactions we need (1 for winner, optionally 1 for team)
        num_transactions = 1  # Winner payout
        if TEAM_WALLET and TEAM_WALLET != OWNER_WALLET:
            num_transactions += 1  # Team payout
        
        total_fees_needed = ESTIMATED_TX_FEE * num_transactions
        
        # Deduct fees from team's share (they absorb operational costs)
        team_share_before_fees = total_pool * Decimal("0.2")  # 20% to team
        team_share = max(Decimal("0"), team_share_before_fees - total_fees_needed)
        winner_share = total_pool * Decimal("0.8")  # 80% to winner (no fee deduction)
        
        if winner_share < MIN_PAYOUT_THRESHOLD:
            print(f"❌ Winner share too small: {winner_share} SOL")
            return {"success": False, "error": "Prize too small to send"}
        
        print(f"💰 Payout calculation (Smart Fee Handling):")
        print(f"   Total pool: {total_pool} SOL")
        print(f"   Team share before fees (20%): {team_share_before_fees} SOL")
        print(f"   Fees reserved from team share: {total_fees_needed} SOL")
        print(f"   Team share after fees: {team_share} SOL")
        print(f"   Winner share (80%): {winner_share} SOL")
        
        # Send to team FIRST (with fees already deducted from their share)
        team_tx = None
        if TEAM_WALLET and TEAM_WALLET != OWNER_WALLET and team_share > MIN_PAYOUT_THRESHOLD:
            print(f"   → Sending {team_share} SOL to team wallet...")
            team_result = await send_sol(OWNER_WALLET, TEAM_WALLET, team_share, OWNER_WALLET_PRIVATE_KEY)
            if not team_result.get("success"):
                print(f"   ❌ Team payment failed: {team_result.get('error')}")
                return {"success": False, "error": f"Team payment failed: {team_result.get('error')}"}
            team_tx = team_result.get("signature")
            print(f"   ✅ Team payment sent! TX: {team_tx[:16]}...")
        
        # Send to winner (full share - fees came from team's portion)
        print(f"   → Sending {winner_share} SOL to winner {winner_wallet[:8]}...")
        winner_result = await send_sol(OWNER_WALLET, winner_wallet, winner_share, OWNER_WALLET_PRIVATE_KEY)
        
        if not winner_result.get("success"):
            print(f"   ❌ Winner payment failed: {winner_result.get('error')}")
            if team_tx:
                print(f"   ⚠️ CRITICAL: Team paid but winner payment failed! Team TX: {team_tx}")
            return {"success": False, "error": f"Winner payment failed: {winner_result.get('error')}", "team_tx": team_tx}
        
        winner_tx = winner_result.get("signature")
        print(f"   ✅ Winner payment sent! TX: {winner_tx[:16]}...")
        
        return {
            "success": True,
            "winner_tx": winner_tx,
            "team_tx": team_tx,
            "winner_amount": float(winner_share),
            "team_amount": float(team_share),
            "fees_reserved": float(total_fees_needed),
            "total_pool": float(total_pool)
        }
    except Exception as e:
        print(f"❌ Payout error: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}


def process_round_draw(round_id: int, owner_balance: Decimal = None):
    """
    Process the lottery draw for a round with TIERED PRIZE SYSTEM.
    
    TIERED PRIZE LOGIC:
    1. Generate 5 winning numbers (1-40) for the round
    2. Categorize tickets by matches: 5-match, 4-match, 3-match (ignore fewer)
    3. Prize pool = owner's wallet balance (passed as parameter)
    4. Split prize pool: 70% for 5-match, 20% for 4-match, 10% for 3-match
    5. If no winners in a tier, that tier's allocation rolls over to next round
    6. Run settlement only once per round (prevent double processing)
    7. Lock round before settlement so no new tickets counted
    """
    # SAFEGUARD: Prevent double processing
    if is_round_settled(round_id):
        print(f"⚠️ Round {round_id} already settled - skipping")
        return None
    
    # Lock the round to prevent new ticket entries
    lock_round(round_id)
    
    conn = get_db_conn()
    c = conn.cursor()
    
    # Get all participants for this round
    c.execute("""
        SELECT rp.id, rp.user_id, rp.numbers, rp.tx_signature, rs.stake_amount
        FROM round_participants rp
        JOIN round_stakes rs ON rp.round_stake_id = rs.id
        WHERE rs.round_id = %s AND rp.refunded = 0
        ORDER BY rp.id ASC
    """, (round_id,))
    participants = c.fetchall()
    
    if not participants:
        conn.close()
        return None
    
    # Calculate total stakes from this round (for logging only)
    round_total = Decimal("0")
    for p in participants:
        round_total += Decimal(str(p[4]))  # stake_amount
    
    # Get pre-generated winning numbers from database (set when round was created)
    winning_numbers = get_round_winning_numbers(round_id)
    
    if not winning_numbers:
        # Fallback: generate numbers if not pre-set (shouldn't happen normally)
        print(f"⚠️ Round {round_id} missing winning numbers, generating fallback...")
        tx_signatures_ordered = [str(p[3]) for p in participants]
        seed = generate_provable_seed(round_id, "draw", *tx_signatures_ordered)
        winning_numbers = generate_lottery_numbers(seed, count=5, min_val=1, max_val=40)
        set_round_winning_numbers(round_id, winning_numbers)
    
    # Categorize tickets by match count
    tier_5_winners = []  # 5 matches - jackpot
    tier_4_winners = []  # 4 matches
    tier_3_winners = []  # 3 matches
    
    for participant_id, user_id, numbers_str, tx_sig, stake_amount in participants:
        user_numbers = str_to_numbers(numbers_str)
        matches = len(set(user_numbers) & set(winning_numbers))
        
        if matches == 5:
            tier_5_winners.append((participant_id, user_id, stake_amount, user_numbers))
        elif matches == 4:
            tier_4_winners.append((participant_id, user_id, stake_amount, user_numbers))
        elif matches == 3:
            tier_3_winners.append((participant_id, user_id, stake_amount, user_numbers))
    
    # Prize pool = owner's wallet balance (passed from async context)
    if owner_balance is None:
        # Fallback if not provided (shouldn't happen in normal operation)
        previous_rollover = get_rollover()
        round_prize_contribution = round_total * WINNER_SHARE_PERCENTAGE
        total_prize_pool = round_prize_contribution + previous_rollover
    else:
        total_prize_pool = owner_balance
        previous_rollover = Decimal("0")
    
    print(f"💰 Prize Pool Calculation:")
    print(f"   Round stakes: {round_total} SOL")
    print(f"   Owner Wallet Balance (Prize Pool): {total_prize_pool} SOL")
    
    # Calculate tier allocations
    tier_5_allocation = total_prize_pool * TIER_5_MATCH_PERCENTAGE  # 70%
    tier_4_allocation = total_prize_pool * TIER_4_MATCH_PERCENTAGE  # 20%
    tier_3_allocation = total_prize_pool * TIER_3_MATCH_PERCENTAGE  # 10%
    
    # Calculate payouts and rollover for each tier
    tier_5_payouts = []
    tier_4_payouts = []
    tier_3_payouts = []
    new_rollover = Decimal("0")
    
    # Process Tier 5 (5 matches - 70% allocation)
    # SMART FEE HANDLING: Reserve transaction fees from tier allocation before splitting
    if tier_5_winners:
        num_winners = len(tier_5_winners)
        total_fees = ESTIMATED_TX_FEE * num_winners
        net_allocation = tier_5_allocation - total_fees
        payout_per_winner = max(Decimal("0"), net_allocation / num_winners)
        
        if payout_per_winner < MIN_PAYOUT_THRESHOLD:
            new_rollover += tier_5_allocation
            log_rollover(round_id, tier_5_allocation, f"Tier 5 payout too small after fees ({num_winners} winners)")
            print(f"📈 Tier 5 (5 matches): Payout too small after fees - {tier_5_allocation:.6f} SOL rolls over")
        else:
            for participant_id, user_id, stake_amount, user_numbers in tier_5_winners:
                tier_5_payouts.append({
                    "participant_id": participant_id,
                    "user_id": user_id,
                    "amount": payout_per_winner,
                    "numbers": user_numbers
                })
            print(f"🏆 Tier 5 (5 matches): {num_winners} winners, {payout_per_winner:.6f} SOL each (fees reserved: {total_fees:.6f} SOL)")
    else:
        new_rollover += tier_5_allocation
        log_rollover(round_id, tier_5_allocation, "No 5-match winners")
        print(f"📈 Tier 5 (5 matches): No winners - {tier_5_allocation:.6f} SOL rolls over")
    
    # Process Tier 4 (4 matches - 20% allocation)
    if tier_4_winners:
        num_winners = len(tier_4_winners)
        total_fees = ESTIMATED_TX_FEE * num_winners
        net_allocation = tier_4_allocation - total_fees
        payout_per_winner = max(Decimal("0"), net_allocation / num_winners)
        
        if payout_per_winner < MIN_PAYOUT_THRESHOLD:
            new_rollover += tier_4_allocation
            log_rollover(round_id, tier_4_allocation, f"Tier 4 payout too small after fees ({num_winners} winners)")
            print(f"📈 Tier 4 (4 matches): Payout too small after fees - {tier_4_allocation:.6f} SOL rolls over")
        else:
            for participant_id, user_id, stake_amount, user_numbers in tier_4_winners:
                tier_4_payouts.append({
                    "participant_id": participant_id,
                    "user_id": user_id,
                    "amount": payout_per_winner,
                    "numbers": user_numbers
                })
            print(f"🥈 Tier 4 (4 matches): {num_winners} winners, {payout_per_winner:.6f} SOL each (fees reserved: {total_fees:.6f} SOL)")
    else:
        new_rollover += tier_4_allocation
        log_rollover(round_id, tier_4_allocation, "No 4-match winners")
        print(f"📈 Tier 4 (4 matches): No winners - {tier_4_allocation:.6f} SOL rolls over")
    
    # Process Tier 3 (3 matches - 10% allocation)
    if tier_3_winners:
        num_winners = len(tier_3_winners)
        total_fees = ESTIMATED_TX_FEE * num_winners
        net_allocation = tier_3_allocation - total_fees
        payout_per_winner = max(Decimal("0"), net_allocation / num_winners)
        
        if payout_per_winner < MIN_PAYOUT_THRESHOLD:
            new_rollover += tier_3_allocation
            log_rollover(round_id, tier_3_allocation, f"Tier 3 payout too small after fees ({num_winners} winners)")
            print(f"📈 Tier 3 (3 matches): Payout too small after fees - {tier_3_allocation:.6f} SOL rolls over")
        else:
            for participant_id, user_id, stake_amount, user_numbers in tier_3_winners:
                tier_3_payouts.append({
                    "participant_id": participant_id,
                    "user_id": user_id,
                    "amount": payout_per_winner,
                    "numbers": user_numbers
                })
            print(f"🥉 Tier 3 (3 matches): {num_winners} winners, {payout_per_winner:.6f} SOL each (fees reserved: {total_fees:.6f} SOL)")
    else:
        new_rollover += tier_3_allocation
        log_rollover(round_id, tier_3_allocation, "No 3-match winners")
        print(f"📈 Tier 3 (3 matches): No winners - {tier_3_allocation:.6f} SOL rolls over")
    
    # Update rollover for next round
    set_rollover(new_rollover)
    print(f"💫 New rollover for next round: {new_rollover} SOL")
    
    # Build result object (with owner_balance as prize pool)
    result = {
        "winning_numbers": winning_numbers,
        "owner_balance": total_prize_pool,
        "player_count": len(participants),
        "round_total": round_total,
        "round_id": round_id,
        "participants": participants,
        "prize_pool": total_prize_pool,
        "previous_rollover": previous_rollover,
        "new_rollover": new_rollover,
        "tier_5_winners": tier_5_winners,
        "tier_4_winners": tier_4_winners,
        "tier_3_winners": tier_3_winners,
        "tier_5_payouts": tier_5_payouts,
        "tier_4_payouts": tier_4_payouts,
        "tier_3_payouts": tier_3_payouts,
        "tier_5_allocation": tier_5_allocation,
        "tier_4_allocation": tier_4_allocation,
        "tier_3_allocation": tier_3_allocation,
        "has_winner": bool(tier_5_winners or tier_4_winners or tier_3_winners)
    }
    
    # Update database - mark as drawn
    c.execute("""
        UPDATE round_stakes
        SET status = 'drawn'
        WHERE round_id = %s
    """, (round_id,))
    conn.commit()
    
    # Mark round as settled to prevent double processing
    mark_round_settled(round_id)
    
    # Log summary
    print(f"✅ Round {round_id} settlement complete:")
    print(f"   🏆 5-match winners: {len(tier_5_winners)}")
    print(f"   🥈 4-match winners: {len(tier_4_winners)}")
    print(f"   🥉 3-match winners: {len(tier_3_winners)}")
    print(f"   💫 Rollover to next round: {new_rollover} SOL")
    
    conn.close()
    return result


def save_round_winners_to_database(round_id: int, result: dict):
    """Save all tier winners to draw_history and update their stats"""
    try:
        winning_numbers = result.get('winning_numbers', [])
        player_count = result.get('player_count', 0)
        total_pot = result.get('prize_pool', Decimal("0"))
        seed_data = result.get('seed_data', '')
        
        # Get seed from database if not in result
        if not seed_data:
            conn = get_db_conn()
            c = conn.cursor()
            c.execute("SELECT seed_data FROM scheduled_rounds WHERE round_id = %s", (round_id,))
            row = c.fetchone()
            if row and row[0]:
                seed_data = row[0]
            else:
                # Generate seed if missing
                seed_data = generate_provable_seed(round_id, "history", str(winning_numbers))
            conn.close()
        
        # Ensure seed_data is never empty
        if not seed_data or seed_data.strip() == '':
            seed_data = f"seed_round_{round_id}_numbers_{','.join(map(str, winning_numbers))}"
        
        # Get all winners for this round
        all_winners = []
        all_winners.extend(result.get('tier_5_payouts', []))
        all_winners.extend(result.get('tier_4_payouts', []))
        all_winners.extend(result.get('tier_3_payouts', []))
        
        # Save each winner (one record per winner)
        for winner in all_winners:
            user_id = winner.get('user_id')
            prize = winner.get('amount', Decimal("0"))
            save_draw_history(round_id, winning_numbers, seed_data, player_count, total_pot, 
                            winner_id=user_id, prize_amount=prize, tx_signature=None)
            update_user_stats(user_id, won=prize, is_win=True)
            print(f"💾 Saved winner {user_id} to draw_history with prize {prize}")
        
        # If no winners, save a no-winner record for reference
        if not all_winners:
            save_draw_history(round_id, winning_numbers, seed_data, player_count, total_pot)
            print(f"📊 Saved no-winner record for round {round_id}")
            
    except Exception as e:
        print(f"❌ Error saving winners to database: {e}")
        import traceback
        traceback.print_exc()


def process_round_stake_draw(round_stake_id: int):
    """Legacy wrapper for compatibility - redirects to new process_round_draw"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT round_id FROM round_stakes WHERE id = %s", (round_stake_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return process_round_draw(row[0])
    return None


async def process_refunds_for_stake(round_stake_id: int):
    """
    Automatically refund all participants in a stake category that didn't meet minimum players.
    Sends SOL from OWNER_WALLET to each participant's wallet using OWNER_WALLET_PRIVATE_KEY.
    """
    conn = get_db_conn()
    c = conn.cursor()
    
    # Get all participants who haven't been refunded yet
    c.execute("""
        SELECT rp.id, rp.user_id, rs.stake_amount
        FROM round_participants rp
        JOIN round_stakes rs ON rp.round_stake_id = rs.id
        WHERE rp.round_stake_id = %s AND rp.refunded = 0
    """, (round_stake_id,))
    participants = c.fetchall()
    
    if not participants:
        print(f"⚠️ No participants to refund for stake {round_stake_id}")
        conn.close()
        return []
    
    # Calculate refund amount (stake minus network fee)
    c.execute("SELECT stake_amount FROM round_stakes WHERE id = %s", (round_stake_id,))
    stake_row = c.fetchone()
    if not stake_row:
        print(f"❌ Stake {round_stake_id} not found")
        conn.close()
        return []
    
    stake_amount = Decimal(str(stake_row[0]))
    refund_amount = stake_amount * (Decimal("1") - NETWORK_FEE_PERCENTAGE)
    
    # Update stake status to pending_refund
    c.execute("""
        UPDATE round_stakes
        SET status = 'pending_refund'
        WHERE id = %s
    """, (round_stake_id,))
    conn.commit()
    
    # Check OWNER_WALLET balance before starting refunds
    try:
        owner_balance = await get_real_balance(OWNER_WALLET)
        total_refund_needed = refund_amount * len(participants)
        if owner_balance < total_refund_needed:
            print(f"⚠️ WARNING: OWNER_WALLET balance ({owner_balance} SOL) insufficient for all refunds ({total_refund_needed} SOL)")
            print(f"   Proceeding with refunds but some may fail...")
    except Exception as e:
        print(f"⚠️ Could not check OWNER_WALLET balance: {e}")
    
    # Process refunds one by one
    successful_refunds = 0
    failed_refunds = 0
    refund_results = []
    
    print(f"💸 Starting automatic refunds for stake {round_stake_id}: {len(participants)} participants")
    print(f"   Refund amount: {refund_amount} SOL per participant (stake: {stake_amount} SOL - {float(NETWORK_FEE_PERCENTAGE * 100)}% fee)")
    
    for participant_id, user_id, _ in participants:
        participant_wallet = get_active_wallet(user_id)
        
        if not participant_wallet:
            print(f"❌ User {user_id} (participant {participant_id}) has no active wallet - skipping refund")
            failed_refunds += 1
            continue
        
        # Attempt to send refund with retry logic
        max_retries = 2
        refund_sent = False
        tx_signature = None
        
        for attempt in range(max_retries + 1):
            try:
                print(f"   → Refunding user {user_id}: {refund_amount} SOL to {participant_wallet[:8]}...{participant_wallet[-8:]} (attempt {attempt + 1}/{max_retries + 1})")
                
                refund_result = await send_sol(
                    OWNER_WALLET, 
                    participant_wallet, 
                    refund_amount, 
                    OWNER_WALLET_PRIVATE_KEY
                )
                
                if refund_result and refund_result.get("success"):
                    tx_signature = refund_result.get("signature")
                    refund_sent = True
                    print(f"   ✅ Refund sent! TX: {tx_signature[:16]}...")
                    break
                else:
                    error_msg = refund_result.get("error", "Unknown error") if refund_result else "No result returned"
                    print(f"   ⚠️ Refund attempt {attempt + 1} failed: {error_msg}")
                    if attempt < max_retries:
                        await asyncio.sleep(2)  # Wait 2 seconds before retry
                        
            except Exception as e:
                print(f"   ⚠️ Refund attempt {attempt + 1} exception: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2)
        
        # Update database and notify user based on result
        if refund_sent and tx_signature:
            # Mark as refunded in database
            c.execute("""
                UPDATE round_participants
                SET refunded = 1, refund_tx = %s
                WHERE id = %s
            """, (tx_signature, participant_id))
            conn.commit()
            
            # Log refund transaction
            log_wallet_transaction(
                user_id=user_id,
                wallet_address=participant_wallet,
                tx_type="refund",
                amount=refund_amount,
                from_address=OWNER_WALLET,
                tx_signature=tx_signature,
                status="completed"
            )
            
            successful_refunds += 1
            
            # Send success message to user
            try:
                await bot.send_message(
                    user_id,
                    f"✅ <b>Refund Completed!</b>\n\n"
                    f"Your round did not meet the minimum {MIN_PLAYERS_PER_STAKE} players.\n\n"
                    f"💰 Refunded: <b>{refund_amount} SOL</b>\n"
                    f"(Original stake: {stake_amount} SOL minus {float(NETWORK_FEE_PERCENTAGE * 100)}% network fee)\n\n"
                    f"📝 Transaction: <code>{tx_signature[:20]}...</code>\n"
                    f"Wallet: <code>{participant_wallet}</code>\n\n"
                    f"View on Solscan:\n"
                    f"https://solscan.io/tx/{tx_signature}",
                    parse_mode="HTML"
                )
            except Exception as e:
                print(f"   ⚠️ Could not send success message to user {user_id}: {e}")
            
            refund_results.append({
                'participant_id': participant_id,
                'user_id': user_id,
                'wallet': participant_wallet,
                'amount': float(refund_amount),
                'tx_signature': tx_signature,
                'status': 'success'
            })
        else:
            # Refund failed after all retries
            failed_refunds += 1
            print(f"   ❌ Refund FAILED for user {user_id} after {max_retries + 1} attempts")
            
            # Send failure notification to user
            try:
                await bot.send_message(
                    user_id,
                    f"⚠️ <b>Refund Processing Issue</b>\n\n"
                    f"Your round did not meet minimum players and a refund was initiated.\n\n"
                    f"Amount: {refund_amount} SOL\n"
                    f"Wallet: <code>{participant_wallet}</code>\n\n"
                    f"However, the automatic refund encountered an issue.\n"
                    f"Our team has been notified and will process your refund manually within 24 hours.\n\n"
                    f"We apologize for the inconvenience!",
                    parse_mode="HTML"
                )
            except:
                pass
            
            refund_results.append({
                'participant_id': participant_id,
                'user_id': user_id,
                'wallet': participant_wallet,
                'amount': float(refund_amount),
                'tx_signature': None,
                'status': 'failed'
            })
    
    # Update stake status based on results
    if successful_refunds == len(participants):
        # All refunds successful
        c.execute("""
            UPDATE round_stakes
            SET status = 'refunded'
            WHERE id = %s
        """, (round_stake_id,))
        conn.commit()
        print(f"✅ All refunds completed successfully for stake {round_stake_id}")
    elif successful_refunds > 0:
        # Partial success
        print(f"⚠️ Partial refunds for stake {round_stake_id}: {successful_refunds} succeeded, {failed_refunds} failed")
    else:
        # All failed
        print(f"❌ All refunds failed for stake {round_stake_id}")
    
    conn.close()
    
    # Summary
    print(f"💸 Refund summary for stake {round_stake_id}:")
    print(f"   ✅ Successful: {successful_refunds}/{len(participants)}")
    print(f"   ❌ Failed: {failed_refunds}/{len(participants)}")
    
    return refund_results


async def mark_refund_completed(participant_id: int, tx_signature: str):
    conn = get_db_conn()
    c = conn.cursor()
    
    c.execute("""
        UPDATE round_participants
        SET refunded = 1, refund_tx = %s
        WHERE id = %s
    """, (tx_signature, participant_id))
    
    c.execute("SELECT user_id, round_stake_id FROM round_participants WHERE id = %s", (participant_id,))
    participant_row = c.fetchone()
    
    if participant_row:
        user_id, stake_id = participant_row
        
        c.execute("""
            SELECT COUNT(*) FROM round_participants
            WHERE round_stake_id = %s AND refunded = 0
        """, (stake_id,))
        pending_count = c.fetchone()[0]
        
        if pending_count == 0:
            c.execute("""
                UPDATE round_stakes
                SET status = 'refunded'
                WHERE id = %s
            """, (stake_id,))
            print(f"✅ Stake {stake_id} marked as fully refunded (all participants processed)")
        
        conn.commit()
        conn.close()
        
        try:
            await bot.send_message(
                user_id,
                f"✅ <b>Refund Completed!</b>\n\n"
                f"📝 TX: <code>{tx_signature[:20]}...</code>\n\n"
                f"Thank you for playing!",
                parse_mode="HTML"
            )
        except:
            pass
        
        return True
    
    conn.close()
    return False


# ---------------------------
# Bot Handlers
# ---------------------------

@dp.my_chat_member()
async def handle_bot_membership_change(update: types.ChatMemberUpdated):
    """
    Handle when the bot is added to or removed from a group/channel.
    Automatically registers groups/channels for announcements.
    """
    chat = update.chat
    new_status = update.new_chat_member.status
    old_status = update.old_chat_member.status
    added_by = update.from_user.id if update.from_user else None
    
    # Only handle groups and channels
    if chat.type not in ["group", "supergroup", "channel"]:
        return
    
    # Bot was added to a group/channel (now admin or member)
    if new_status in ["administrator", "member"] and old_status in ["left", "kicked", None]:
        add_announcement_group(
            chat_id=chat.id,
            chat_type=chat.type,
            chat_title=chat.title,
            added_by=added_by
        )
        print(f"[Bot] Added to {chat.type}: {chat.title} ({chat.id})")
        
        # Send welcome message with Play button
        try:
            bot_info = await bot.get_me()
            play_button = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="🎲 Play Now!", 
                    url=f"https://t.me/{bot_info.username}?start=play"
                )]
            ])
            await bot.send_message(
                chat.id,
                f"👋 <b>Hello!</b>\n\n"
f"RedLuck Lotto Bot is now active here!\n\n"
f"🎫 Buy tickets for <b>{TICKET_PRICE} SOL</b>\n"
f"🎲 Pick 5 numbers (1-40)\n"
f"🏆 How it works:\n"
f"• Pick your 5 lucky numbers (1–40)\n"
f"• Match 5 numbers to win 70% of the jackpot\n"
f"• Match 4 numbers to win 20% of the jackpot\n"
f"• Match 3 numbers to win 10% of the jackpot\n\n"
f"I'll post announcements here:\n"
f"• New ticket purchases\n"
f"• Round results & winners\n"
f"• Jackpot updates",
                reply_markup=play_button,
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"[Bot] Could not send welcome message: {e}")
    
    # Bot was removed from a group/channel
    elif new_status in ["left", "kicked"] and old_status in ["administrator", "member"]:
        remove_announcement_group(chat.id)
        print(f"[Bot] Removed from {chat.type}: {chat.title} ({chat.id})")


def process_vip_daily_bonus(user_id: int) -> dict:
    """
    Check and process VIP daily bonus for a single special user.
    Returns dict with success status and message to show user.
    
    This runs synchronously - called from async handlers.
    """
    # Debug: Log all VIP checks
    print(f"[VIP] Checking user {user_id} against VIP_TELEGRAM_ID {VIP_TELEGRAM_ID}")
    
    if VIP_TELEGRAM_ID <= 0:
        print(f"[VIP] VIP_TELEGRAM_ID not configured (value: {VIP_TELEGRAM_ID})")
        return {"claimed": False, "message": None}
    
    if user_id != VIP_TELEGRAM_ID:
        print(f"[VIP] User {user_id} is not the VIP user {VIP_TELEGRAM_ID}")
        return {"claimed": False, "message": None}
    
    print(f"[VIP] ✅ User {user_id} IS the VIP user! Processing bonus...")
    
    try:
        conn = get_db_conn()
        c = conn.cursor()
        
        # Get current time as UNIX timestamp
        current_time = int(time.time())
        
        # Check user's last claim time
        c.execute("""
            SELECT free_ticket_last_claim FROM users WHERE user_id = %s
        """, (user_id,))
        row = c.fetchone()
        last_claim = row[0] if row else None
        
        print(f"[VIP] Last claim time: {last_claim}, Current time: {current_time}")
        
        # If never claimed or 24+ hours passed since last claim
        if last_claim is None or (current_time - last_claim) >= VIP_COOLDOWN_SECONDS:
            # Get current free ticket balance
            c.execute("""
                SELECT free_ticket_balance FROM users WHERE user_id = %s
            """, (user_id,))
            row = c.fetchone()
            current_balance = row[0] if row else 0
            
            # Add VIP tickets
            new_balance = current_balance + VIP_DAILY_TICKETS
            c.execute("""
                UPDATE users SET free_ticket_balance = %s, free_ticket_last_claim = %s 
                WHERE user_id = %s
            """, (new_balance, current_time, user_id))
            
            conn.commit()
            conn.close()
            
            print(f"[VIP] ✅ Bonus awarded! Balance: {current_balance} → {new_balance}")
            return {
                "claimed": True,
                "message": f"🎁 <b>VIP Bonus: {VIP_DAILY_TICKETS} free tickets added!</b>\n\nUse /start to play!"
            }
        else:
            # Cooldown still active
            remaining_seconds = VIP_COOLDOWN_SECONDS - (current_time - last_claim)
            remaining_hours = (remaining_seconds + 3599) // 3600  # Round up to hours
            
            conn.close()
            print(f"[VIP] ⏳ Cooldown active. Hours until next: {remaining_hours}")
            return {
                "claimed": False,
                "message": f"⏳ <b>VIP Tickets Coming Soon</b>\n\nNext bonus available in {remaining_hours} hours"
            }
    except Exception as e:
        print(f"[VIP] ❌ Error processing bonus for user {user_id}: {e}")
        import traceback
        traceback.print_exc()
        return {"claimed": False, "message": None}


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    save_user(user_id, message.from_user.username or "")
    
    # Debug: Show VIP configuration and user ID
    print(f"\n[DEBUG START] User ID: {user_id}")
    print(f"[DEBUG START] VIP_TELEGRAM_ID configured: {VIP_TELEGRAM_ID}")
    print(f"[DEBUG START] Match: {user_id == VIP_TELEGRAM_ID}")
    
    # Process VIP daily bonus if applicable
    vip_result = process_vip_daily_bonus(user_id)
    
    # Clean up any abandoned number selection session for this user
    uid = message.from_user.id
    if uid in user_selected_numbers:
        del user_selected_numbers[uid]
    
    # Get current jackpot from owner wallet on blockchain
    jackpot = await get_real_balance(OWNER_WALLET)
    
    # Check for referral code in start command
    args = message.text.split()
    if len(args) > 1:
        ref_code = args[1].upper()
        referrer_id = get_referrer_by_code(ref_code)
        if referrer_id and referrer_id != message.from_user.id:
            if register_referral(referrer_id, message.from_user.id, ref_code):
                await message.answer(
                    "🎁 <b>Referral Link Applied!</b>\n\n"
                    f"You joined via a friend's referral.\n\n"
                    f"<b>Referral Reward System:</b>\n"
                    f"When you buy your first ticket, your friend gets credit!\n"
                    f"Every 2 successful referrals = 1 FREE TICKET\n\n"
                    f"Invite more friends to earn more free tickets!",
                    parse_mode="HTML"
                )
    
    # Get user stats for VIP tier display
    stats = get_user_stats(message.from_user.id)
    vip_badge = f"🎖️ {stats['vip_name']}" if stats.get('vip_name') else ""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Play Now", callback_data="play_now")],
        [InlineKeyboardButton(text="💰 Check Jackpot", callback_data="check_jackpot"),
         InlineKeyboardButton(text="🎰 Active Rounds", callback_data="check_active_rounds")],
        [InlineKeyboardButton(text="💼 Wallets", callback_data="my_wallets"),
         InlineKeyboardButton(text="📈 My Stats", callback_data="my_stats")],
        [InlineKeyboardButton(text="🏆 Leaderboard", callback_data="leaderboard"),
         InlineKeyboardButton(text="🎁 Invite Friends", callback_data="referral")],
        [InlineKeyboardButton(text="📊 Results", callback_data="view_results"),
         InlineKeyboardButton(text="📘 Rules", callback_data="rules")],
        [InlineKeyboardButton(text="🛠 Support", callback_data="support")],
        [InlineKeyboardButton(text="⚙️ Settings", callback_data="settings")]
    ])
    welcome_text = (
        f"🎟️ <b>Welcome to RedLuck Lotto!</b> {vip_badge}\n\n"
        f"🏆 <b>Current Jackpot: {jackpot} SOL</b>\n\n"
        f"📋 <b>How It Works:</b>\n"
        f"• <b>Pick your 5 lucky numbers (1-40)</b>\n"
        f"• Match 5 numbers to win 70% of the jackpot\n"
        f"• Match 4 numbers to win 20% of the jackpot\n"
        f"• Match 3 numbers to win 10% of the jackpot\n"
        f"• No winner? Prize rolls over to next round!\n\n"
        f"💰 Ticket Price: {TICKET_PRICE} SOL (Unlimited tickets!)\n"
        f"⏰ 24 Hourly Rounds (one per hour)\n\n"
        f"🎁 <b>Referral System Active!</b>\n"
        f"Invite friends → 2 referrals = 1 FREE TICKET"
    )
    
    await message.answer(
        welcome_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    
    # Send VIP bonus message if applicable
    if vip_result["message"]:
        await message.answer(
            vip_result["message"],
            parse_mode="HTML"
        )


async def show_wallet_menu(user_id: int):
    """Show wallet management menu"""
    wallets = get_user_wallets(user_id)
    active_wallet = get_active_wallet(user_id)
    wallet_count = get_user_wallet_count(user_id)

    keyboard_buttons = []

    if wallets:
        # Fetch all wallet balances in PARALLEL for speed
        balance_tasks = [get_real_balance(w["address"]) for w in wallets]
        balances = await asyncio.gather(*balance_tasks)
        
        text = "💼 <b>Your Wallets</b>\n\n"
        for i, (wallet, balance) in enumerate(zip(wallets, balances), 1):
            is_active = "✅" if wallet["address"] == active_wallet else ""
            text += f"{is_active} <b>{wallet['name']}</b>\n"
            text += f"   Type: {wallet['type'].capitalize()}\n"
            text += f"   Address: <code>{wallet['address'][:8]}...{wallet['address'][-8:]}</code>\n"
            text += f"   Balance: {balance} SOL\n\n"

            # Add button for each wallet
            keyboard_buttons.append([
                InlineKeyboardButton(text=f"{'✅ ' if is_active else ''}{wallet['name']}", 
                                   callback_data=f"select_wallet_{i-1}")
            ])
        
        # Add wallet actions for active wallet
        if active_wallet:
            wallet_actions = []
            # Check if active wallet is bot-managed or imported (has private key)
            active_wallet_info = next((w for w in wallets if w["address"] == active_wallet), None)
            if active_wallet_info and active_wallet_info["type"] in ("bot", "imported"):
                wallet_actions.append(
                    InlineKeyboardButton(text="🔑 View Private Key", callback_data="view_private_key")
                )
                wallet_actions.append(
                    InlineKeyboardButton(text="💸 Send SOL", callback_data="send_sol")
                )
            
            if wallet_actions:
                keyboard_buttons.append(wallet_actions)
            
            # Add delete wallet option
            keyboard_buttons.append([
                InlineKeyboardButton(text="🗑 Delete Active Wallet", callback_data="delete_active_wallet")
            ])
    else:
        text = "💼 <b>Your Wallets</b>\n\nYou don't have any wallets yet.\n"

    # Add create/import buttons if under limit
    if wallet_count < MAX_WALLETS_PER_USER:
        keyboard_buttons.append([
            InlineKeyboardButton(text="➕ Create Wallet", callback_data="create_wallet"),
            InlineKeyboardButton(text="📥 Import Wallet", callback_data="import_wallet")
        ])
    else:
        text += f"\n⚠️ You already have a wallet. Delete it first to create or import a new one."

    # Add navigation buttons
    add_navigation_buttons(keyboard_buttons, include_start=True)
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    await bot.send_message(user_id, text, reply_markup=keyboard, parse_mode="HTML")


def get_user_tickets_for_current_round(user_id: int) -> List[Dict]:
    """Get all tickets purchased by a user for the current open round"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
        SELECT rp.id, rp.numbers, rp.created_at, rs.stake_amount, sr.round_id
        FROM round_participants rp
        JOIN round_stakes rs ON rp.round_stake_id = rs.id
        JOIN scheduled_rounds sr ON rs.round_id = sr.round_id
        WHERE rp.user_id = %s AND sr.status = 'open' AND rp.refunded = 0
        ORDER BY rp.created_at DESC
    """, (user_id,))
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "numbers": r[1], "created_at": r[2], "stake": r[3], "round_id": r[4]} for r in rows]


def get_current_open_round() -> Optional[int]:
    """Get the current open round ID"""
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT round_id FROM scheduled_rounds WHERE status = 'open' ORDER BY scheduled_time ASC LIMIT 1")
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


async def show_security_question_picker(user_id: int):
    """Show the security question selection menu"""
    questions = [
        "What is your mother's maiden name?",
        "What was the name of your first pet?",
        "What city were you born in?",
        "What is your favorite movie?",
        "What was your childhood nickname?"
    ]
    
    buttons = []
    for i, q_text in enumerate(questions):
        buttons.append([InlineKeyboardButton(text=q_text, callback_data=f"sq_{i}")])
    buttons.append([InlineKeyboardButton(text="✏️ Custom Question", callback_data="sq_custom")])
    
    keyboard = create_keyboard_with_nav(buttons)
    await bot.send_message(
        user_id,
        "❓ <b>Set Security Question</b>\n\n"
        "Choose a security question or create your own.\n"
        "You'll use this to reset your PIN if you forget it.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def start_private_play(user_id: int):
    """Start lottery play session with new menu options"""
    wallet = get_active_wallet(user_id)

    if not wallet:
        keyboard = create_keyboard_with_nav([
            [InlineKeyboardButton(text="💳 Create Wallet", callback_data="create_wallet")],
            [InlineKeyboardButton(text="📥 Import Wallet", callback_data="import_wallet")],
            [InlineKeyboardButton(text="ℹ️ How to Play", callback_data="rules")]
        ])
        await bot.send_message(user_id,
            "🎮 <b>RedLuck Lotto</b>\n\n"
            "You need a wallet to play. Create a new wallet or import your existing one.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return

    balance = await get_real_balance(wallet)
    try:
        jackpot = await get_real_balance(OWNER_WALLET)
    except:
        jackpot = Decimal("0")
    user_tickets = get_user_tickets_for_current_round(user_id)

    keyboard = create_keyboard_with_nav([
        [InlineKeyboardButton(text=f"🎫 Buy Ticket ({TICKET_PRICE} SOL)", callback_data="buy_ticket")],
        [InlineKeyboardButton(text="💰 Show Prize Pool", callback_data="show_prize_pool")],
        [InlineKeyboardButton(text=f"🎟️ My Tickets ({len(user_tickets)})", callback_data="show_my_tickets")],
        [InlineKeyboardButton(text="💼 Switch Wallet", callback_data="my_wallets")],
        [InlineKeyboardButton(text="ℹ️ How to Play", callback_data="rules")]
    ])

    await bot.send_message(user_id,
        f"🎮 <b>RedLuck Lotto</b>\n\n"
        f"💳 Wallet: <code>{wallet[:8]}...{wallet[-8:]}</code>\n"
        f"💵 Balance: <b>{balance} SOL</b>\n\n"
        f"🎫 Ticket Price: <b>{TICKET_PRICE} SOL</b>\n"
        f"🏆 Current Jackpot: <b>{jackpot} SOL</b>\n"
        f"🎟️ Your Tickets This Round: <b>{len(user_tickets)}</b>\n\n"
        f"Buy a ticket and pick 5 numbers (1-40) to win!",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def show_number_picker(user_id: int, selected_numbers: List[int], message_to_edit: types.Message = None):
    """Display the number picker grid for players to select 5 numbers from 1-40
    
    If message_to_edit is provided, edits that message instead of sending a new one.
    """
    buttons = []
    row = []
    
    for num in range(1, 41):
        if num in selected_numbers:
            btn_text = f"✅ {num}"
        else:
            btn_text = str(num)
        row.append(InlineKeyboardButton(text=btn_text, callback_data=f"pick_num_{num}"))
        
        if len(row) == 8:
            buttons.append(row)
            row = []
    
    if row:
        buttons.append(row)
    
    if len(selected_numbers) == 5:
        buttons.append([InlineKeyboardButton(text="✅ Confirm Numbers", callback_data="confirm_numbers")])
    
    buttons.append([InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_number_pick")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    selected_str = ", ".join(map(str, sorted(selected_numbers))) if selected_numbers else "None"
    
    text = (
        f"🎲 <b>Pick Your Numbers!</b>\n\n"
        f"Select <b>5 numbers</b> from 1 to 40.\n"
        f"Tap a number to select/deselect it.\n\n"
        f"Selected ({len(selected_numbers)}/5): <b>{selected_str}</b>"
    )
    
    if message_to_edit:
        try:
            await message_to_edit.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            return message_to_edit
        except:
            pass
    
    msg = await bot.send_message(user_id, text, reply_markup=keyboard, parse_mode="HTML")
    return msg


@dp.callback_query()
async def inline_handler(query: types.CallbackQuery):
    data = query.data
    uid = query.from_user.id

    # Prevent duplicate callback processing (double-clicks)
    callback_id = f"{uid}:{data}:{query.message.message_id if query.message else ''}"
    if is_duplicate_callback(callback_id, window_seconds=1.5):
        try:
            await query.answer()
        except:
            pass
        return

    # Rate limiting for button clicks
    if not rate_limiter.is_allowed(uid, RateLimitAction.BUTTON_CLICK):
        try:
            await query.answer("Please slow down!", show_alert=False)
        except:
            pass
        return

    if data == "play_now":
        await query.answer()
        await start_private_play(uid)

    elif data == "show_prize_pool":
        await query.answer()
        try:
            jackpot = await get_real_balance(OWNER_WALLET)
        except:
            jackpot = Decimal("0")
        
        keyboard = create_keyboard_with_nav([
            [InlineKeyboardButton(text=f"🎫 Buy Ticket ({TICKET_PRICE} SOL)", callback_data="buy_ticket")]
        ], "play_now")
        
        await bot.send_message(uid,
            f"💰 <b>Prize Pool</b>\n\n"
            f"🏆 <b>Current Jackpot: {jackpot} SOL</b>\n\n"
            f"The prize pool grows with every ticket purchase!\n"
            f"Match all 5 winning numbers to win the entire jackpot!\n\n"
            f"🎫 Ticket Price: <b>{TICKET_PRICE} SOL</b>\n"
            f"💵 20% goes to team wallet, 80% goes to jackpot\n\n"
            f"If no winner, the jackpot rolls over to next round!",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    elif data == "show_my_tickets":
        await query.answer()
        tickets = get_user_tickets_for_current_round(uid)
        
        if not tickets:
            keyboard = create_keyboard_with_nav([
                [InlineKeyboardButton(text=f"🎫 Buy Ticket ({TICKET_PRICE} SOL)", callback_data="buy_ticket")]
            ], "play_now")
            await bot.send_message(uid,
                "🎟️ <b>My Tickets</b>\n\n"
                "You haven't bought any tickets for the current round yet.\n"
                "Buy a ticket to join the lottery!",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            text = f"🎟️ <b>My Tickets ({len(tickets)} total)</b>\n\n"
            for i, ticket in enumerate(tickets, 1):
                text += f"<b>Ticket #{ticket['id']}</b>\n"
                text += f"   Numbers: <code>{ticket['numbers']}</code>\n"
                text += f"   Stake: {ticket['stake']} SOL\n\n"
            
            keyboard = create_keyboard_with_nav([
                [InlineKeyboardButton(text=f"🎫 Buy Another Ticket", callback_data="buy_ticket")]
            ], "play_now")
            await bot.send_message(uid, text, reply_markup=keyboard, parse_mode="HTML")

    elif data == "buy_ticket":
        await query.answer()
        
        # Check for free tickets first
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT free_ticket_balance FROM users WHERE user_id = %s", (uid,))
        user_row = c.fetchone()
        free_tickets = user_row[0] if user_row else 0
        conn.close()
        
        # Debug: Show free ticket status
        print(f"\n[DEBUG BUY] User {uid} free_ticket_balance: {free_tickets}")
        print(f"[DEBUG BUY] VIP_TELEGRAM_ID: {VIP_TELEGRAM_ID}")
        print(f"[DEBUG BUY] Is VIP: {uid == VIP_TELEGRAM_ID}")
        
        if free_tickets > 0:
            # User has free tickets - use one without payment
            await query.message.edit_text(
                f"🎁 <b>FREE TICKET!</b>\n\n"
                f"You have <b>{free_tickets}</b> free tickets available!\n\n"
                f"Pick your 5 lucky numbers below (no payment required):\n",
                parse_mode="HTML"
            )
            
            user_states[uid] = {
                "action": "picking_numbers",
                "selected_numbers": [],
                "stake_amount": 0,
                "wallet": None,
                "private_key": None,
                "picker_message_id": None,
                "is_free_ticket": True
            }
            picker_msg = await show_number_picker(uid, [])
            if picker_msg and uid in user_states:
                user_states[uid]["picker_message_id"] = picker_msg.message_id
            return
        
        wallet = get_active_wallet(uid)
        if not wallet:
            await bot.send_message(uid, "❌ Please create or connect a wallet first.")
            return
        
        # Check balance FIRST before showing number picker
        balance = await get_real_balance(wallet, use_cache=False)  # Fresh balance check
        required_amount = TICKET_PRICE + Decimal("0.00005")  # Include network fees buffer
        
        if balance < required_amount:
            await bot.send_message(uid,
                f"⚠️ <b>Insufficient Balance</b>\n\n"
                f"Your balance: {balance} SOL\n"
                f"Required: {required_amount} SOL (ticket + fees)\n\n"
                f"Please deposit more SOL to your wallet:\n"
                f"<code>{wallet}</code>",
                parse_mode="HTML"
            )
            return
        
        private_key = get_wallet_private_key(uid, wallet)
        if not private_key:
            await bot.send_message(uid,
                f"⚠️ This is an external wallet.\n\n"
                f"Please send <b>{TICKET_PRICE} SOL</b> to:\n"
                f"<code>{OWNER_WALLET}</code>\n\n"
                f"Then reply with your transaction signature.",
                parse_mode="HTML"
            )
            return
        
        # NO PAYMENT YET - Just show number picker
        # Payment will be processed AFTER user confirms their numbers
        
        # Store wallet info BEFORE showing picker (so clicks don't fail)
        user_states[uid] = {
            "action": "picking_numbers",
            "selected_numbers": [],
            "stake_amount": float(TICKET_PRICE),
            "wallet": wallet,
            "private_key": private_key,
            "picker_message_id": None
        }
        
        # Now show the picker
        picker_msg = await show_number_picker(uid, [])
        
        # Update with message ID
        if picker_msg and uid in user_states:
            user_states[uid]["picker_message_id"] = picker_msg.message_id

    elif data.startswith("pick_num_"):
        await query.answer()
        num = int(data.split("_")[2])
        
        if uid not in user_states or user_states[uid].get("action") != "picking_numbers":
            await bot.send_message(uid, "Your ticket is saved. You can return anytime to finish selecting your numbers.")
            return
        
        selected = user_states[uid].get("selected_numbers", [])
        
        if num in selected:
            selected.remove(num)
        elif len(selected) < 5:
            selected.append(num)
        
        user_states[uid]["selected_numbers"] = selected
        
        # Edit the existing message instead of sending a new one
        await show_number_picker(uid, selected, query.message)

    elif data == "confirm_numbers":
        if uid not in user_states or user_states[uid].get("action") != "picking_numbers":
            await bot.send_message(uid, "Your ticket is saved. You can return anytime to finish selecting your numbers.")
            return
        
        state = user_states[uid]
        selected = state.get("selected_numbers", [])
        
        if len(selected) != 5:
            await bot.send_message(uid, f"❌ Please select exactly 5 numbers. You have selected {len(selected)}.")
            return
        
        is_free_ticket = state.get("is_free_ticket", False)
        
        if is_free_ticket:
            await query.answer("Processing free ticket...")
        else:
            await query.answer("Processing payment...")
        
        wallet = state.get("wallet")
        private_key = state.get("private_key")
        stake_amount = Decimal(str(state.get("stake_amount", float(TICKET_PRICE))))
        
        if not is_free_ticket:
            if not wallet or not private_key:
                await bot.send_message(uid, "Your ticket is saved. You can return anytime to finish selecting your numbers.")
                if uid in user_states:
                    del user_states[uid]
                return
            
            # Re-check balance before payment (in case balance changed)
            balance = await get_real_balance(wallet, use_cache=False)
            required_amount = stake_amount + Decimal("0.00005")
            
            if balance < required_amount:
                await bot.send_message(uid,
                    f"⚠️ <b>Insufficient Balance</b>\n\n"
                    f"Your balance: {balance} SOL\n"
                    f"Required: {required_amount} SOL\n\n"
                    f"Please deposit more SOL and try again.",
                    parse_mode="HTML"
                )
                del user_states[uid]
                return
        
        # FIRST: Check/create round BEFORE taking payment
        # This prevents money being taken when no round is available
        round_stake_id, round_id = get_or_create_active_round_stake(stake_amount)
        
        if not round_stake_id:
            await bot.send_message(uid, 
                f"❌ <b>No active lottery round!</b>\n\n"
                f"No payment was made. Please try again later.",
                parse_mode="HTML"
            )
            del user_states[uid]
            return
        
        # Update message to show processing
        try:
            if is_free_ticket:
                await query.message.edit_text("⏳ <b>Processing free ticket...</b>\n\nPlease wait...", parse_mode="HTML")
            else:
                await query.message.edit_text("⏳ <b>Processing payment...</b>\n\nPlease wait...", parse_mode="HTML")
        except:
            pass
        
        # Generate unique signature for free tickets to bypass paid transaction flow
        # For VIP daily and referral reward tickets - no signature verification needed
        if is_free_ticket:
            import uuid
            tx_signature = f"free_ticket_{uid}_{int(datetime.now().timestamp())}_{uuid.uuid4().hex[:8]}"
        else:
            tx_signature = None
        
        # Process payment only if not a free ticket
        if not is_free_ticket:
            # NOW process the payment (round is confirmed available)
            team_fee = stake_amount * TEAM_FEE_PERCENTAGE
            owner_amount = stake_amount * WINNER_SHARE_PERCENTAGE
            
            # Send 80% to owner wallet (jackpot) first
            result = await send_sol(wallet, OWNER_WALLET, owner_amount, private_key)
            
            if not result["success"]:
                error_msg = result.get('error', 'Unknown error')
                print(f"[Ticket] Payment failed for user {uid}: {error_msg}")
                await bot.send_message(uid,
                    f"❌ <b>Transaction failed!</b>\n\n"
                    f"Error: {error_msg}\n\n"
                    f"Your SOL was NOT deducted. Please try again.",
                    parse_mode="HTML"
                )
                del user_states[uid]
                return
            
            tx_signature = result["signature"]
            print(f"[Ticket] Payment successful for user {uid}: {tx_signature[:20]}...")
            
            # Send 20% to team wallet (non-critical - log but continue if fails)
            if TEAM_WALLET and TEAM_WALLET != OWNER_WALLET:
                team_result = await send_sol(wallet, TEAM_WALLET, team_fee, private_key)
                if not team_result["success"]:
                    print(f"[Ticket] Warning: Team wallet payment failed: {team_result.get('error')}")
            
            # Log lottery stake transaction
            log_wallet_transaction(
                user_id=uid,
                wallet_address=wallet,
                tx_type="lottery_stake",
                amount=stake_amount,
                to_address=OWNER_WALLET,
                tx_signature=tx_signature,
                status="completed"
            )
        
        # Register ticket with retry logic
        add_result = None
        selected_sorted = sorted(selected)
        for attempt in range(3):
            add_result = add_round_participant(round_stake_id, uid, selected_sorted, tx_signature)
            if add_result["success"]:
                break
            print(f"[Ticket] Registration attempt {attempt+1} failed: {add_result.get('error')}")
            await asyncio.sleep(0.5)
        
        del user_states[uid]
        
        if add_result["success"]:
            # Check if this was already processed (duplicate tx_signature)
            if add_result.get("already_processed"):
                await bot.send_message(uid,
                    f"✅ <b>Payment confirmed</b>\n\n"
                    f"This transaction was already processed.\n"
                    f"📝 TX: <code>{tx_signature[:20]}...</code>",
                    parse_mode="HTML"
                )
            else:
                participant_id = add_result["participant_id"]
                ticket_id = add_result.get("ticket_id", "")
                ticket_count = add_result.get("ticket_count", 1)
                
                # SYNC: Process referral on first ticket purchase (separate sync function)
                referrer_id = process_referral_on_first_ticket(uid)
                
                # Send referral bonus notification if awarded
                if referrer_id and referrer_id > 0:
                    try:
                        await bot.send_message(
                            referrer_id,
                            f"🎉 <b>Referral Bonus Earned!</b>\n\n"
                            f"Your referred friends are buying tickets!\n\n"
                            f"🎫 You earned <b>1 FREE TICKET</b>\n"
                            f"(Every 2 successful referrals = 1 free ticket)\n\n"
                            f"Use your free tickets anytime to play without payment!",
                            parse_mode="HTML"
                        )
                    except:
                        pass
                
                # SYNC: Decrement free ticket balance if this was a free ticket (separate sync function)
                if is_free_ticket:
                    decrement_free_ticket_balance(uid)
                
                network_fee = Decimal("0.00002") if not is_free_ticket else Decimal("0")
                
                if is_free_ticket:
                    await bot.send_message(uid,
                        f"✅ <b>Free Ticket Confirmed</b>\n"
                        f"🎟 <b>Ticket successfully added</b>\n\n"
                        f"🎫 Ticket #{participant_id}\n"
                        f"🎲 Your Numbers: <b>{', '.join(map(str, selected_sorted))}</b>\n"
                        f"🎁 Type: FREE TICKET\n\n"
                        f"🎯 You can buy multiple tickets for this round.\n"
                        f"🍀 Good luck! Results will be announced when the round ends.",
                        parse_mode="HTML"
                    )
                else:
                    await bot.send_message(uid,
                        f"✅ <b>Payment confirmed</b>\n"
                        f"🎟 <b>Ticket successfully added</b>\n\n"
                        f"🎫 Ticket #{participant_id}\n"
                        f"🎲 Your Numbers: <b>{', '.join(map(str, selected_sorted))}</b>\n"
                        f"💰 Stake: {stake_amount} SOL\n"
                        f"⛽ Network Fee: ~{network_fee} SOL\n"
                        f"📝 TX: <code>{tx_signature[:20]}...</code>\n\n"
                        f"🎯 You can buy multiple tickets for this round.\n"
                        f"🍀 Good luck! Results will be announced when the round ends.",
                        parse_mode="HTML"
                    )
                
                await announce_new_ticket(uid, participant_id, stake_amount, selected_sorted, round_id, ticket_count)
        else:
            is_free_ticket = state.get("is_free_ticket", False)
            
            if is_free_ticket:
                # Free ticket registration failed - no payment to refund
                print(f"[CRITICAL] Free ticket registration failed for user {uid}")
                await bot.send_message(uid,
                    f"⚠️ <b>Ticket Registration Failed</b>\n\n"
                    f"There was an issue registering your free ticket.\n"
                    f"Please try again.",
                    parse_mode="HTML"
                )
            else:
                # Paid ticket registration failed - attempt refund
                print(f"[CRITICAL] Ticket registration failed after payment for user {uid}. Attempting refund...")
                refund_result = await send_sol(OWNER_WALLET, wallet, owner_amount, OWNER_WALLET_PRIVATE_KEY)
                
                if refund_result["success"]:
                    log_wallet_transaction(
                        user_id=uid,
                        wallet_address=wallet,
                        tx_type="refund",
                        amount=float(owner_amount),
                        from_address=OWNER_WALLET,
                        tx_signature=refund_result["signature"],
                        status="completed"
                    )
                    await bot.send_message(uid,
                        f"⚠️ <b>Ticket Registration Failed</b>\n\n"
                        f"Your payment of {owner_amount} SOL has been automatically refunded.\n"
                        f"Refund TX: <code>{refund_result['signature'][:20]}...</code>\n\n"
                        f"Please try again.",
                        parse_mode="HTML"
                    )
                else:
                    await bot.send_message(uid,
                        f"⚠️ <b>Processing Issue</b>\n\n"
                        f"There was an issue registering your ticket.\n"
                        f"Payment was processed. Contact support with TX:\n"
                        f"<code>{tx_signature}</code>",
                        parse_mode="HTML"
                    )

    elif data == "cancel_number_pick":
        await query.answer()
        if uid in user_states:
            del user_states[uid]
        await bot.send_message(uid,
            "❌ <b>Number selection cancelled</b>\n\n"
            "No payment was made. You can buy a ticket anytime.",
            parse_mode="HTML"
        )
        await start_private_play(uid)

    elif data == "my_wallets":
        await query.answer()
        await show_wallet_menu(uid)

    elif data.startswith("select_wallet_"):
        await query.answer()
        wallet_index = int(data.split("_")[2])
        wallets = get_user_wallets(uid)

        if 0 <= wallet_index < len(wallets):
            selected_wallet = wallets[wallet_index]
            set_active_wallet(uid, selected_wallet["address"])
            await bot.send_message(uid,
                f"✅ Switched to <b>{selected_wallet['name']}</b>\n"
                f"Address: <code>{selected_wallet['address']}</code>",
                parse_mode="HTML"
            )
            await show_wallet_menu(uid)

    elif data == "create_wallet":
        await query.answer()
        wallet_count = get_user_wallet_count(uid)

        if wallet_count >= MAX_WALLETS_PER_USER:
            await bot.send_message(uid,
                "⚠️ You already have a wallet. Delete it first to create a new one."
            )
            return

        wallet = create_wallet(uid)
        if wallet:
            # Check if user has PIN, if not prompt to set one
            if not has_user_pin(uid):
                user_states[uid] = {"action": "set_pin_after_wallet", "wallet_address": wallet['address']}
                await bot.send_message(uid,
                    f"✅ <b>Wallet created successfully!</b>\n\n"
                    f"Name: {wallet['name']}\n"
                    f"Address: <code>{wallet['address']}</code>\n\n"
                    f"🔐 <b>Security Setup</b>\n"
                    f"Please create a 4-digit PIN to protect your wallet.\n"
                    f"This PIN will be required to:\n"
                    f"• View private key\n"
                    f"• Send SOL from this wallet\n\n"
                    f"⚠️ <b>PRIVATE KEY SAFETY:</b>\n"
                    f"• NEVER share your private key with ANYONE\n"
                    f"• RedLuck team will NEVER ask for your key\n"
                    f"• Anyone with your key can steal all funds\n\n"
                    f"Please send your 4-digit PIN now:",
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(uid,
                    f"✅ <b>Wallet created successfully!</b>\n\n"
                    f"Name: {wallet['name']}\n"
                    f"Address: <code>{wallet['address']}</code>\n\n"
                    f"⚠️ <b>PRIVATE KEY SAFETY:</b>\n"
                    f"• NEVER share your private key with ANYONE\n"
                    f"• RedLuck team will NEVER ask for your key\n"
                    f"• Anyone with your key can steal all funds\n\n"
                    f"Save your address to deposit funds from exchanges or other wallets.\n"
                    f"You can now deposit SOL and play!",
                    parse_mode="HTML"
                )
                set_active_wallet(uid, wallet['address'])
                await start_private_play(uid)
        else:
            await bot.send_message(uid, "❌ Failed to create wallet. Please try again.")

    elif data == "delete_active_wallet":
        await query.answer()
        wallet = get_active_wallet(uid)
        if not wallet:
            await bot.send_message(uid, "❌ No active wallet to delete.")
            return
        
        # Confirm deletion
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Yes, Delete", callback_data="confirm_delete_wallet"),
                InlineKeyboardButton(text="❌ Cancel", callback_data="my_wallets")
            ]
        ])
        await bot.send_message(uid,
            f"⚠️ <b>Delete Wallet?</b>\n\n"
            f"Are you sure you want to delete:\n"
            f"<code>{wallet[:8]}...{wallet[-8:]}</code>\n\n"
            f"This action cannot be undone!",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    elif data == "confirm_delete_wallet":
        await query.answer()
        wallet = get_active_wallet(uid)
        if wallet:
            if delete_wallet(uid, wallet):
                await bot.send_message(uid,
                    f"✅ <b>Wallet Deleted</b>\n\n"
                    f"Wallet <code>{wallet[:8]}...{wallet[-8:]}</code> has been removed.",
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(uid, "❌ Failed to delete wallet.")
        await show_wallet_menu(uid)

    elif data == "view_private_key":
        await query.answer()
        wallet = get_active_wallet(uid)
        if not wallet:
            await bot.send_message(uid, "❌ No active wallet.")
            return
        
        # Check if user has PIN
        if not has_user_pin(uid):
            user_states[uid] = {"action": "set_pin_for_key_view"}
            await bot.send_message(uid,
                "🔐 <b>PIN Required</b>\n\n"
                "Please create a 4-digit PIN to protect your private key.\n"
                "Send your 4-digit PIN now:",
                parse_mode="HTML"
            )
        else:
            user_states[uid] = {"action": "verify_pin_for_key_view"}
            await bot.send_message(uid,
                "🔐 <b>PIN Required</b>\n\n"
                "Enter your 4-digit PIN to view private key:",
                parse_mode="HTML"
            )

    elif data == "send_sol":
        await query.answer()
        wallet = get_active_wallet(uid)
        if not wallet:
            await bot.send_message(uid, "❌ No active wallet.")
            return
        
        balance = await get_real_balance(wallet)
        if balance <= Decimal("0.001"):
            await bot.send_message(uid,
                f"❌ <b>Insufficient Balance</b>\n\n"
                f"Current balance: {balance} SOL\n"
                f"You need at least 0.001 SOL to send (plus network fees).",
                parse_mode="HTML"
            )
            return
        
        # Check if user has PIN
        if not has_user_pin(uid):
            user_states[uid] = {"action": "set_pin_for_send"}
            await bot.send_message(uid,
                "🔐 <b>PIN Required</b>\n\n"
                "Please create a 4-digit PIN to authorize transactions.\n"
                "Send your 4-digit PIN now:",
                parse_mode="HTML"
            )
        else:
            user_states[uid] = {"action": "verify_pin_for_send"}
            await bot.send_message(uid,
                f"💸 <b>Send SOL</b>\n\n"
                f"Current balance: <b>{balance} SOL</b>\n\n"
                f"🔐 Enter your 4-digit PIN to continue:",
                parse_mode="HTML"
            )

    elif data == "import_wallet":
        await query.answer()
        wallet_count = get_user_wallet_count(uid)

        if wallet_count >= MAX_WALLETS_PER_USER:
            keyboard = create_keyboard_with_nav([], "my_wallets")
            await bot.send_message(uid,
                "⚠️ You already have a wallet. Delete it first to import a new one.",
                reply_markup=keyboard
            )
            return

        # Show import wallet warning and confirmation
        keyboard = create_keyboard_with_nav([
            [InlineKeyboardButton(text="✅ I Understand, Continue", callback_data="import_wallet_confirm")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="my_wallets")]
        ], "my_wallets")
        
        await bot.send_message(uid,
            "🔐 <b>Import Wallet with Private Key</b>\n\n"
            "⚠️ <b>SECURITY WARNING</b> ⚠️\n\n"
            "You are about to import a wallet using your private key.\n\n"
            "🔒 <b>Security Measures:</b>\n"
            "• Your message will be DELETED immediately\n"
            "• Private key is encrypted before storage\n"
            "• Never share your key with anyone else\n\n"
            "⚡ <b>Supported Formats:</b>\n"
            "• Hex format (128 characters)\n"
            "• Base58 format (Phantom export)\n"
            "• JSON array [64 numbers] (Solflare)\n\n"
            "🛡️ <b>By continuing, you accept full responsibility</b>\n"
            "<b>for providing your private key.</b>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    
    elif data == "import_wallet_confirm":
        await query.answer()
        user_states[uid] = {"action": "import_wallet_private_key"}
        keyboard = create_keyboard_with_nav([], "import_wallet")
        await bot.send_message(uid,
            "🔑 <b>Enter Your Private Key</b>\n\n"
            "📱 <b>How to export from Phantom:</b>\n"
            "1. Open Phantom → Settings → Security\n"
            "2. Tap 'Export Private Key'\n"
            "3. Copy and paste it here\n\n"
            "📱 <b>How to export from Solflare:</b>\n"
            "1. Open Solflare → Settings\n"
            "2. Export Private Key (JSON array)\n"
            "3. Copy and paste it here\n\n"
            "⚠️ <b>Your message will be deleted immediately for security!</b>\n\n"
            "⌨️ <b>Send your private key now:</b>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    elif data == "choose_stake":
        await query.answer()
        # Check balance first
        wallet = get_active_wallet(uid)
        if not wallet:
            await bot.send_message(uid, "❌ Please create or connect a wallet first.")
            return

        balance = await get_real_balance(wallet)
        
        # Set user state to enter stake amount via keyboard
        user_states[uid] = {"action": "enter_stake_amount", "balance": str(balance), "wallet": wallet}
        
        # Show balance and prompt for stake amount input with navigation
        cancel_keyboard = create_keyboard_with_nav([
            [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_stake")]
        ], "play_now")
        
        await bot.send_message(uid,
            f"💰 <b>Enter Stake Amount</b>\n\n"
            f"💳 Your Balance: <b>{balance} SOL</b>\n\n"
            f"📊 Stake Range:\n"
            f"   • Minimum: <b>{STAKE_MIN} SOL</b>\n"
            f"   • Maximum: <b>{STAKE_MAX} SOL</b>\n\n"
            f"⌨️ <b>Type the amount you want to stake</b>\n"
            f"(Example: 0.1 or 0.5 or 2.5)",
            reply_markup=cancel_keyboard,
            parse_mode="HTML"
        )

    elif data == "cancel_stake":
        await query.answer("Stake cancelled")
        if uid in user_states:
            del user_states[uid]
        await bot.send_message(uid, "❌ Stake cancelled. Use /play to try again.")

    elif data == "insufficient_funds":
        await query.answer("❌ Insufficient balance for this stake amount", show_alert=True)

    elif data.startswith("stake_"):
        await query.answer("Processing stake...")
        amount = Decimal(data.split("_", 1)[1])

        wallet = get_active_wallet(uid)
        if not wallet:
            await bot.send_message(uid, "❌ Please create or connect a wallet first.")
            return

        # Check real balance
        balance = await get_real_balance(wallet)
        if balance < amount:
            await bot.send_message(uid,
                f"⚠️ <b>Insufficient funds!</b>\n\n"
                f"Your balance: {balance} SOL\n"
                f"Required: {amount} SOL\n\n"
                f"Please deposit more SOL to your wallet:\n"
                f"<code>{wallet}</code>",
                parse_mode="HTML"
            )
            return

        # Get private key for bot-managed wallets
        private_key = get_wallet_private_key(uid, wallet)

        if not private_key:
            await bot.send_message(uid,
                "⚠️ This is an external wallet. Please send the transaction manually:\n\n"
                f"Send <b>{amount} SOL</b> to:\n"
                f"<code>{OWNER_WALLET}</code>\n\n"
                "Then reply with your transaction signature.",
                parse_mode="HTML"
            )
            return

        # Send real SOL transaction (80% to owner, 20% to team)
        owner_amt = amount * Decimal("0.8")
        team_amt = amount * Decimal("0.2")

        await bot.send_message(uid, "⏳ Processing payment...")

        # Send to owner wallet
        result = await send_sol(wallet, OWNER_WALLET, owner_amt, private_key)

        if not result["success"]:
            await bot.send_message(uid,
                f"❌ <b>Transaction failed!</b>\n\n"
                f"Error: {result.get('error', 'Unknown error')}\n\n"
                f"Please try again or contact support.",
                parse_mode="HTML"
            )
            return

        tx_signature = result["signature"]

        # Send to team wallet (if different from owner)
        if TEAM_WALLET and TEAM_WALLET != OWNER_WALLET:
            await send_sol(wallet, TEAM_WALLET, team_amt, private_key)

        # Generate lottery numbers deterministically from transaction signature
        round_num = get_current_round()
        number_seed = generate_provable_seed(uid, round_num, tx_signature, "player_numbers")
        lottery_numbers = generate_lottery_numbers(number_seed, count=5, min_val=1, max_val=40)
        
        # Add entry and get ticket ID - PostgreSQL only
        conn = get_db_conn()
        c = conn.cursor()
        c.execute(
            "INSERT INTO entries(user_id, round, numbers, stake_amount, tx_signature, paid) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (uid, round_num, numbers_to_str(lottery_numbers), float(amount), tx_signature, 1)
        )
        ticket_id = c.fetchone()[0]
        conn.commit()
        conn.close()

        await bot.send_message(uid,
            f"✅ <b>Payment Successful!</b>\n\n"
            f"🎫 <b>Ticket ID:</b> #{ticket_id}\n"
            f"🎲 <b>Your Numbers:</b> {numbers_to_str(lottery_numbers)}\n"
            f"🎰 <b>Round:</b> {round_num}\n"
            f"💰 <b>Stake:</b> {amount} SOL\n\n"
            f"📝 Transaction:\n<code>{tx_signature[:20]}...</code>\n\n"
            f"🍀 <b>Good luck!</b> Winner will be announced in the channel.",
            parse_mode="HTML"
        )

    elif data == "check_jackpot":
        await query.answer("Checking current jackpot...")
        try:
            jackpot = await get_real_balance(OWNER_WALLET)
        except:
            jackpot = Decimal("0")
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Refresh", callback_data="check_jackpot")],
            [InlineKeyboardButton(text="🎲 Play Now", callback_data="play_now")],
            [InlineKeyboardButton(text="🔙 Back to Menu", callback_data="back_to_main")]
        ])
        
        await bot.send_message(uid,
            f"💰 <b>Current Prize Pool</b>\n\n"
            f"🏆 <b>{jackpot} SOL</b>\n\n"
            f"<b>TIERED PRIZES:</b>\n"
            f"🏆 5-Match: 70% of pool\n"
            f"🥈 4-Match: 20% of pool\n"
            f"🥉 3-Match: 10% of pool\n\n"
            f"Unclaimed tiers roll over to grow the pool!",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    elif data == "rules":
        await query.answer()
        keyboard = create_keyboard_with_nav([
            [InlineKeyboardButton(text="🎲 Play Now", callback_data="play_now")]
        ])
        await query.message.answer(
            f"📘 <b>RedLuck Lotto - Game Rules</b>\n\n"
            f"<b>🎮 How to Play:</b>\n"
            f"1. Create or import a Solana wallet\n"
            f"2. Deposit SOL to your wallet\n"
            f"3. Tap 'Play Now' and join a round\n"
            f"4. Ticket price: <b>{TICKET_PRICE} SOL</b> (Unlimited tickets!)\n"
            f"5. <b>Pick your 5 lucky numbers (1-40)</b>\n"
            f"6. Wait for the hourly draw!\n\n"
            f"<b>🎯 Round Schedule:</b>\n"
            f"• {ROUNDS_PER_DAY} rounds per day (one every hour)\n"
            f"• Each round lasts {ROUND_DURATION_MINUTES} minutes\n"
            f"• Draw happens automatically when round ends\n\n"
            f"<b>🏆 TIERED PRIZE SYSTEM:</b>\n"
            f"🏆 <b>5-Match (70%)</b> - Match all 5 = JACKPOT tier!\n"
            f"🥈 <b>4-Match (20%)</b> - Match 4 numbers\n"
            f"🥉 <b>3-Match (10%)</b> - Match 3 numbers\n\n"
            f"• Multiple winners in a tier split equally\n"
            f"• No winners in a tier? That allocation ROLLS OVER!\n"
            f"• Winning numbers shown after each round\n"
            f"• Winners announced in the public channel\n\n"
            f"<b>💰 Prize Pool:</b>\n"
            f"• 20% of each ticket goes to team\n"
            f"• 80% goes to prize pool\n"
            f"• Prize pool = 80% of sales + rollover\n"
            f"• Unclaimed tier prizes keep growing!\n\n"
            f"<b>🎖️ VIP Tiers (by SOL spent):</b>\n"
            f"Bronze → Silver → Gold → Platinum → Diamond\n\n"
            f"<b>🤝 Referral Bonus:</b>\n"
            f"Every time you invite 2 users who successfully buy a ticket, you earn 1 FREE ticket.\n"
            f"This reward repeats for every 2 successful referrals.\n\n"
            f"<b>🔐 Security:</b>\n"
            f"• Blockchain-based provably fair randomness\n"
            f"• All transactions on Solana\n"
            f"• Encrypted wallet keys",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    elif data == "support":
        support_username = os.getenv("SUPPORT_USERNAME", "")
        support_text = "🛠 <b>Support & Help</b>\n\n"
        support_text += "Need help? Here's how to reach us:\n\n"
        
        if support_username:
            support_text += f"📱 Contact: @{support_username}\n\n"
        
        support_text += "Common issues:\n"
        support_text += "• Wallet connection: Make sure you're using a valid Solana address\n"
        support_text += "• Balance not showing: Wait a few seconds for blockchain sync\n"
        support_text += "• Transaction failed: Check your wallet balance\n\n"
        support_text += "📧 For urgent matters, message our admin directly."
        
        keyboard = create_keyboard_with_nav([])
        await query.message.answer(support_text, reply_markup=keyboard, parse_mode="HTML")

    elif data == "settings":
        await query.answer()
        has_pin = has_user_pin(uid)
        has_sq = has_security_question(uid)
        
        text = "⚙️ <b>Settings</b>\n\n"
        text += "<b>Account Security:</b>\n"
        text += f"🔐 PIN: {'✅ Set' if has_pin else '❌ Not set'}\n"
        text += f"❓ Security Question: {'✅ Set' if has_sq else '❌ Not set'}\n\n"
        text += "Set up a security question to recover your PIN if you forget it."
        
        buttons = []
        if has_sq:
            buttons.append([InlineKeyboardButton(text="❓ Change Security Question", callback_data="change_security_question")])
            if has_pin:
                buttons.append([InlineKeyboardButton(text="🔐 Reset PIN", callback_data="reset_pin_security")])
        else:
            buttons.append([InlineKeyboardButton(text="❓ Set Security Question", callback_data="set_security_question")])
        
        if has_pin:
            buttons.append([InlineKeyboardButton(text="🔑 Change PIN", callback_data="change_pin")])
        
        keyboard = create_keyboard_with_nav(buttons)
        await query.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    elif data == "set_security_question":
        await query.answer()
        await show_security_question_picker(uid)
    
    elif data == "change_security_question":
        await query.answer()
        # Must verify current security answer before allowing change
        try:
            sq = get_security_question(uid)
            print(f"[SecurityQ] Change request from {uid}, has_question: {sq.get('has_question')}, question: {sq.get('question', 'N/A')[:30] if sq.get('question') else 'None'}")
            
            if sq.get('has_question'):
                # Set state BEFORE sending message
                user_states[uid] = {"action": "verify_security_for_change"}
                print(f"[SecurityQ] Set state for {uid}: verify_security_for_change")
                
                await query.message.answer(
                    f"🔐 <b>Verify Your Identity</b>\n\n"
                    f"To change your security question, please answer your current one:\n\n"
                    f"❓ <b>{sq['question']}</b>",
                    parse_mode="HTML"
                )
            else:
                print(f"[SecurityQ] No question found for {uid}, showing picker directly")
                await show_security_question_picker(uid)
        except Exception as e:
            print(f"[SecurityQ] Error in change_security_question: {e}")
            import traceback
            traceback.print_exc()
            await query.message.answer("❌ Error loading security question. Please try again.")

    elif data.startswith("sq_"):
        await query.answer()
        
        preset_questions = [
            "What is your mother's maiden name?",
            "What was the name of your first pet?",
            "What city were you born in?",
            "What is your favorite movie?",
            "What was your childhood nickname?"
        ]
        
        # Preserve next_action if this is first-time setup
        current_state = user_states.get(uid, {})
        next_action = current_state.get("next_action")
        
        if data == "sq_custom":
            new_state = {"action": "enter_custom_question"}
            if next_action:
                new_state["next_action"] = next_action
            user_states[uid] = new_state
            await query.message.answer(
                "✏️ <b>Custom Security Question</b>\n\n"
                "Please type your own security question:",
                parse_mode="HTML"
            )
        else:
            q_index = int(data.split("_")[1])
            question = preset_questions[q_index]
            new_state = {"action": "enter_security_answer", "question": question}
            if next_action:
                new_state["next_action"] = next_action
            user_states[uid] = new_state
            await query.message.answer(
                f"❓ <b>Your Question:</b>\n{question}\n\n"
                f"Please enter your answer:\n"
                f"(Remember this answer - you'll need it to reset your PIN!)",
                parse_mode="HTML"
            )

    elif data == "reset_pin_security":
        await query.answer()
        sq = get_security_question(uid)
        
        if not sq['has_question']:
            await query.message.answer(
                "❌ You need to set up a security question first.",
                reply_markup=create_keyboard_with_nav([
                    [InlineKeyboardButton(text="❓ Set Security Question", callback_data="set_security_question")]
                ])
            )
            return
        
        user_states[uid] = {"action": "verify_security_answer_for_reset"}
        await query.message.answer(
            f"🔐 <b>Reset PIN</b>\n\n"
            f"❓ <b>Security Question:</b>\n{sq['question']}\n\n"
            f"Please enter your answer:",
            parse_mode="HTML"
        )

    elif data == "change_pin":
        await query.answer()
        if has_user_pin(uid):
            user_states[uid] = {"action": "verify_current_pin_for_change"}
            await query.message.answer(
                "🔐 <b>Change PIN</b>\n\n"
                "Please enter your current PIN to continue:",
                parse_mode="HTML"
            )
        else:
            user_states[uid] = {"action": "set_new_pin"}
            await query.message.answer(
                "🔐 <b>Set PIN</b>\n\n"
                "Please enter a new 4-digit PIN:",
                parse_mode="HTML"
            )

    elif data == "leaderboard":
        await query.answer()
        keyboard = create_keyboard_with_nav([
            [InlineKeyboardButton(text="🏆 Top Winners", callback_data="leaderboard_winners"),
             InlineKeyboardButton(text="🎫 Top Players", callback_data="leaderboard_players")]
        ])
        await query.message.answer(
            "🏆 <b>Leaderboard</b>\n\n"
            "Choose a leaderboard to view:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    elif data == "leaderboard_winners":
        await query.answer()
        winners = get_top_winners(10)
        
        if not winners:
            text = "🏆 <b>Top Winners</b>\n\nNo winners yet! Be the first to win the jackpot!"
        else:
            text = "🏆 <b>Top Winners</b>\n\n"
            for i, w in enumerate(winners, 1):
                tier_emoji = ["🥉", "🥈", "🥇", "💎", "👑"][min(w['vip_tier'], 4)]
                text += f"{i}. {tier_emoji} @{w['username']}\n"
                text += f"   Won: <b>{w['total_won']} SOL</b> ({w['wins']} wins)\n\n"
        
        keyboard = create_keyboard_with_nav([
            [InlineKeyboardButton(text="🎫 Top Players", callback_data="leaderboard_players")]
        ])
        await query.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    elif data == "leaderboard_players":
        await query.answer()
        players = get_top_players(10)
        
        if not players:
            text = "🎫 <b>Top Players</b>\n\nNo players yet! Be the first to buy a ticket!"
        else:
            text = "🎫 <b>Top Players</b>\n\n"
            for i, p in enumerate(players, 1):
                tier_emoji = ["🥉", "🥈", "🥇", "💎", "👑"][min(p['vip_tier'], 4)]
                text += f"{i}. {tier_emoji} @{p['username']}\n"
                text += f"   Tickets: <b>{p['total_tickets']}</b> | Spent: {p['total_spent']} SOL\n\n"
        
        keyboard = create_keyboard_with_nav([
            [InlineKeyboardButton(text="🏆 Top Winners", callback_data="leaderboard_winners")]
        ])
        await query.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    elif data == "my_stats":
        await query.answer()
        stats = get_user_stats(uid)
        ref_stats = get_referral_stats(uid)
        tier = stats.get('vip_tier', 0)
        tier_info = VIP_TIERS.get(tier, VIP_TIERS[0])
        next_tier = VIP_TIERS.get(tier + 1)
        
        # Get free tickets earned from referrals
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT free_ticket_balance FROM users WHERE user_id = %s", (uid,))
        user_row = c.fetchone()
        free_tickets = user_row[0] if user_row else 0
        conn.close()
        
        text = f"📈 <b>Your Statistics</b>\n\n"
        text += f"🎖️ <b>VIP Tier:</b> {tier_info['name']}\n"
        if next_tier:
            tickets_needed = next_tier['min_tickets'] - stats.get('total_tickets', 0)
            text += f"   Next tier: {next_tier['name']} ({tickets_needed} more tickets)\n\n"
        else:
            text += f"   You're at the highest tier!\n\n"
        
        text += f"🎫 <b>Tickets Bought:</b> {stats.get('total_tickets', 0)}\n"
        text += f"💰 <b>Total Spent:</b> {stats.get('total_spent', 0)} SOL\n"
        text += f"🏆 <b>Total Won:</b> {stats.get('total_won', 0)} SOL\n"
        text += f"🎯 <b>Wins:</b> {stats.get('wins', 0)}\n"
        text += f"🌟 <b>Biggest Win:</b> {stats.get('biggest_win', 0)} SOL\n\n"
        
        text += f"🎁 <b>Referral System:</b>\n"
        text += f"👥 Friends Invited: <b>{ref_stats['total_referrals']}</b>\n"
        text += f"✅ Successful Purchases: <b>{ref_stats['total_tickets']}</b>\n"
        text += f"🎫 Free Tickets Earned: <b>{free_tickets}</b>\n"
        text += f"   (Every 2 successful referrals = 1 free ticket)\n"
        
        keyboard = create_keyboard_with_nav([
            [InlineKeyboardButton(text="🎁 Invite Friends", callback_data="referral")],
            [InlineKeyboardButton(text="🔔 Notifications", callback_data="toggle_notifications")]
        ])
        await query.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    elif data == "referral":
        await query.answer()
        ref_code = get_user_referral_code(uid)
        ref_stats = get_referral_stats(uid)
        
        # Check free tickets from referrals
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT free_ticket_balance FROM users WHERE user_id = %s", (uid,))
        user_row = c.fetchone()
        free_tickets = user_row[0] if user_row else 0
        conn.close()
        
        bot_info = await bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={ref_code}"
        
        text = f"🎁 <b>Invite Friends & Earn Free Tickets!</b>\n\n"
        text += f"<b>Your Referral Link:</b>\n"
        text += f"<code>{ref_link}</code>\n\n"
        text += f"<b>📊 Your Referral Stats:</b>\n"
        text += f"👥 Friends Invited: <b>{ref_stats['total_referrals']}</b>\n"
        text += f"✅ Successful Purchases: <b>{ref_stats['total_tickets']}</b>\n"
        text += f"🎫 Free Tickets Earned: <b>{free_tickets}</b>\n\n"
        text += f"<b>🎯 How It Works:</b>\n"
        text += f"1️⃣ Share your link with friends\n"
        text += f"2️⃣ When they buy their first ticket, you get credit\n"
        text += f"3️⃣ Every 2 successful referrals = 1 FREE TICKET\n"
        text += f"4️⃣ Use free tickets anytime to play without payment\n\n"
        text += f"<b>💡 Unlimited Earnings:</b>\n"
        text += f"The more you invite, the more free tickets you get!\n"
        text += f"This reward repeats for every 2 successful referrals."
        
        keyboard = create_keyboard_with_nav([
            [InlineKeyboardButton(text="📈 My Stats", callback_data="my_stats")],
            [InlineKeyboardButton(text="🎫 Buy Ticket", callback_data="buy_ticket")]
        ])
        await query.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    elif data == "toggle_notifications":
        await query.answer()
        new_state = toggle_notifications(uid)
        status = "enabled ✅" if new_state else "disabled ❌"
        await query.message.answer(
            f"🔔 Notifications are now <b>{status}</b>\n\n"
            f"You will {'receive' if new_state else 'not receive'} reminders when rounds are ending.",
            parse_mode="HTML"
        )

    elif data == "view_results":
        await query.answer("Loading transparency dashboard...")
        keyboard = create_keyboard_with_nav([
            [InlineKeyboardButton(text="📈 Live Stats", callback_data="transparency_live")],
            [InlineKeyboardButton(text="📜 Recent Draws", callback_data="transparency_history")],
            [InlineKeyboardButton(text="💰 Prize Pool Info", callback_data="transparency_pool")],
            [InlineKeyboardButton(text="🔗 Blockchain Links", callback_data="transparency_links")],
            [InlineKeyboardButton(text="🔐 Wallet Security", callback_data="wallet_security_info")]
        ])
        
        try:
            stats = get_transparency_stats()
            jackpot = await get_real_balance(OWNER_WALLET)
        except:
            stats = {"total_draws": 0, "total_distributed": Decimal("0"), "unique_players": 0, 
                     "tickets_today": 0, "draws_today": 0, "winners_today": 0, "volume_24h": Decimal("0"),
                     "biggest_jackpot": Decimal("0")}
            jackpot = Decimal("0")
        
        text = (
            "📊 <b>TRANSPARENCY DASHBOARD</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            
            "<b>TODAY'S ACTIVITY (24h Rolling)</b>\n"
            f"💰 <b>Current Jackpot:</b> {jackpot} SOL\n"
            f"🎫 <b>Tickets Today:</b> {stats.get('tickets_today', 0)}\n"
            f"🎲 <b>Draws Today:</b> {stats.get('draws_today', 0)}\n"
            f"🏆 <b>Winners Today:</b> {stats.get('winners_today', 0)}\n\n"
            
            "📈 <b>ALL-TIME STATS</b>\n"
            f"• Total Draws: {stats.get('total_draws', 0)}\n"
            f"• Total Distributed: {stats.get('total_distributed', Decimal('0')):.4f} SOL\n"
            f"• Unique Players: {stats.get('unique_players', 0)}\n"
            f"• Biggest Jackpot: {stats.get('biggest_jackpot', Decimal('0')):.4f} SOL\n"
            f"• 24h Volume: {stats.get('volume_24h', Decimal('0')):.4f} SOL\n\n"
            
            "🔒 <b>Provably Fair</b>\n"
            "All draws use SHA256 cryptographic hashing\n"
            "with on-chain transaction signatures.\n"
            "Anyone can verify any draw result!\n\n"
            
            "<i>Select an option below for more details</i>"
        )
        
        await bot.send_message(uid, text, reply_markup=keyboard, parse_mode="HTML")
    
    elif data == "transparency_live":
        await query.answer("Loading live stats...")
        
        try:
            live_rounds = get_live_round_stats()
            jackpot = await get_real_balance(OWNER_WALLET)
        except:
            live_rounds = []
            jackpot = Decimal("0")
        
        text = (
            "📈 <b>LIVE ROUND STATS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 <b>Current Jackpot:</b> {jackpot} SOL\n\n"
        )
        
        if live_rounds:
            for r in live_rounds:
                status_emoji = "🟢" if r["status"] == "open" else "🟡"
                text += (
                    f"{status_emoji} <b>Round {r['round_number']}</b>\n"
                    f"   🎫 Tickets Sold: {r['ticket_count']}\n"
                    f"   💵 Prize Pool: {jackpot:.4f} SOL\n"
                    f"   📊 Status: {r['status'].upper()}\n\n"
                )
        else:
            text += "<i>No active rounds at the moment</i>\n\n"
        
        text += (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💡 Prize pool = Owner wallet balance\n"
            "🔄 Updates in real-time!"
        )
        
        keyboard = create_keyboard_with_nav([
            [InlineKeyboardButton(text="🔄 Refresh", callback_data="transparency_live")],
            [InlineKeyboardButton(text="◀️ Back", callback_data="view_results")]
        ])
        
        await bot.send_message(uid, text, reply_markup=keyboard, parse_mode="HTML")
    
    elif data == "transparency_history":
        await query.answer("Loading draw history...")
        
        draws = get_recent_draws_with_verification(10)
        
        text = (
            "📜 <b>RECENT DRAW RESULTS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        )
        
        if draws:
            for d in draws:
                winning_nums_str = ", ".join(str(n) for n in d["winning_numbers"]) if d["winning_numbers"] else "N/A"
                winner_nums_str = ", ".join(str(n) for n in d["winner_numbers"]) if d["winner_numbers"] else "N/A"
                winner_text = f"Winner: User {d['winner_id']}" if d["winner_id"] else "No winner"
                prize_text = f"{d['prize_amount']:.4f} SOL" if d["prize_amount"] else "Rolled over"
                
                drawn_at = d["drawn_at"]
                if drawn_at:
                    if isinstance(drawn_at, str):
                        date_str = drawn_at[:10]
                    else:
                        date_str = drawn_at.strftime("%Y-%m-%d")
                else:
                    date_str = "N/A"
                
                round_num = get_round_number(d['round_id'])
                text += (
                    f"🎲 <b>Round {round_num}</b> ({date_str})\n"
                    f"   🎯 Winning Numbers: <code>{winning_nums_str}</code>\n"
                    f"   🎪 Winner Picked: <code>{winner_nums_str}</code>\n"
                    f"   {winner_text}\n"
                    f"   Prize: {prize_text}\n"
                    f"   Players: {d['player_count']}\n\n"
                )
        else:
            text += "<i>No draws completed yet</i>\n\n"
        
        keyboard = create_keyboard_with_nav([
            [InlineKeyboardButton(text="◀️ Back", callback_data="view_results")]
        ])
        
        await bot.send_message(uid, text, reply_markup=keyboard, parse_mode="HTML")
    
    elif data == "transparency_pool":
        await query.answer("Loading prize pool info...")
        
        try:
            jackpot = await get_real_balance(OWNER_WALLET)
            stats = get_transparency_stats()
            seeded = get_total_seeded()
        except:
            jackpot = Decimal("0")
            stats = {"total_distributed": Decimal("0"), "avg_pot": Decimal("0")}
            seeded = Decimal("0")
        
        text = (
            "💰 <b>PRIZE POOL BREAKDOWN</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            
            f"🏆 <b>Current Jackpot:</b> {jackpot} SOL\n\n"
            
            "<b>How the Pool Works:</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• Ticket Price: {TICKET_PRICE} SOL\n"
            "• 80% goes to prize pool\n"
            "• 20% goes to operations\n\n"
            
            "<b>Prize Tiers:</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🥇 5-Match: 70% of prize pool\n"
            "🥈 4-Match: 20% of prize pool\n"
            "🥉 3-Match: 10% of prize pool\n\n"
            
            "<b>Rollover System:</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "If no winner in a tier, that tier's\n"
            "allocation rolls to next round!\n\n"
            
            f"📊 <b>Stats:</b>\n"
            f"• Total Distributed: {stats['total_distributed']:.4f} SOL\n"
            f"• Admin Seeded: {seeded:.4f} SOL\n"
        )
        
        keyboard = create_keyboard_with_nav([
            [InlineKeyboardButton(text="🔗 View on Solscan", url=f"https://solscan.io/account/{OWNER_WALLET}")],
            [InlineKeyboardButton(text="◀️ Back", callback_data="view_results")]
        ])
        
        await bot.send_message(uid, text, reply_markup=keyboard, parse_mode="HTML")
    
    elif data == "transparency_links":
        await query.answer()
        
        text = (
            "🔗 <b>BLOCKCHAIN VERIFICATION</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            
            "<b>Owner Wallet (Prize Pool):</b>\n"
            f"<code>{OWNER_WALLET}</code>\n\n"
            
            "<b>Verify on Solscan:</b>\n"
            "View all transactions, balances, and\n"
            "prize distributions on the blockchain.\n\n"
            
            "<b>What You Can Verify:</b>\n"
            "✅ All ticket payments received\n"
            "✅ All prize payouts sent\n"
            "✅ Current pool balance\n"
            "✅ Transaction timestamps\n"
            "✅ Transaction signatures\n\n"
            
            "<i>Click below to view live blockchain data</i>"
        )
        
        keyboard = create_keyboard_with_nav([
            [InlineKeyboardButton(text="🔗 Solscan Explorer", url=f"https://solscan.io/account/{OWNER_WALLET}")],
            [InlineKeyboardButton(text="◀️ Back", callback_data="view_results")]
        ])
        
        await bot.send_message(uid, text, reply_markup=keyboard, parse_mode="HTML")
    
    elif data == "wallet_security_info":
        await query.answer("Loading wallet security info...")
        
        text = (
            "🔐 <b>HOW YOUR WALLET IS PROTECTED</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            
            "<b>🛡️ END-TO-END ENCRYPTION</b>\n"
            "Your private key is encrypted using military-grade encryption (Fernet) that scrambles your key into random-looking code. Only you can unscramble it with your unique encryption key.\n\n"
            
            "<b>🔑 What is a Private Key?</b>\n"
            "Think of it like a master password for your wallet. It proves you own the SOL. We NEVER see it or store it in plain text.\n\n"
            
            "<b>🗝️ How We Protect It:</b>\n"
            "1️⃣ You set a PIN (like a bank PIN)\n"
            "2️⃣ Your private key gets encrypted\n"
            "3️⃣ We store only the encrypted version\n"
            "4️⃣ To use your wallet, you enter your PIN\n"
            "5️⃣ PIN unlocks the encrypted key\n\n"
            
            "<b>🚫 What We DON'T Do:</b>\n"
            "❌ We never see your private key\n"
            "❌ We never store it unencrypted\n"
            "❌ We never share it with anyone\n"
            "❌ We can't access your funds without your PIN\n\n"
            
            "<b>💡 Simple Analogy:</b>\n"
            "Your private key is locked in a safe. The safe itself is locked in a vault. Even we can only access the vault, not the safe inside it. Only you have the safe key (your PIN).\n\n"
            
            "<b>✅ Your Security Checklist:</b>\n"
            "✓ Set a strong PIN (not 0000)\n"
            "✓ Never share your PIN\n"
            "✓ Backup your private key securely\n"
            "✓ Use 2-factor when available\n\n"
            
            "<i>Your funds are protected by Solana blockchain + our encryption</i>"
        )
        
        keyboard = create_keyboard_with_nav([
            [InlineKeyboardButton(text="📊 View Wallet on Solscan", url=f"https://solscan.io/account/{OWNER_WALLET}")],
            [InlineKeyboardButton(text="📜 Transaction History", url=f"https://solscan.io/account/{OWNER_WALLET}#txs")],
            [InlineKeyboardButton(text="◀️ Back", callback_data="view_results")]
        ])
        
        await bot.send_message(uid, text, reply_markup=keyboard, parse_mode="HTML")

    elif data == "check_active_rounds":
        await query.answer("Loading...")
        
        # Fast query - just get basic round info without heavy joins
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("""
            SELECT round_id, round_number, status, start_time
            FROM scheduled_rounds 
            WHERE status IN ('open', 'pending') 
            ORDER BY scheduled_time ASC LIMIT 3
        """)
        rounds = c.fetchall()
        conn.close()
        
        if not rounds:
            await bot.send_message(uid,
                "🎰 <b>No Active Rounds</b>\n\n"
                "There are no open rounds at the moment.\n"
                f"Rounds open daily at: {', '.join(ROUND_TIMES_UTC)} UTC\n\n"
                "Check back soon!",
                parse_mode="HTML"
            )
            return
        
        # Build quick summary without heavy database joins
        text = "🎰 <b>Active Rounds</b>\n\n"
        
        for round_id, round_number, status, start_time in rounds:
            # Display round_number (1-24) not round_id (auto-increment)
            text += f"<b>Round {round_number}</b> - {'🟢 OPEN' if status == 'open' else '🟡 Pending'}\n"
            
            if status == 'open' and start_time:
                try:
                    start_dt = datetime.fromisoformat(str(start_time).replace('Z', '+00:00'))
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=pytz.UTC)
                    now = datetime.now(pytz.UTC)
                    elapsed = (now - start_dt).total_seconds() / 60
                    remaining = max(0, ROUND_DURATION_MINUTES - elapsed)
                    text += f"⏰ {int(remaining)} min remaining\n"
                except:
                    pass
            text += "\n"
        
        text += f"🎫 Ticket: {TICKET_PRICE} SOL\n"
        text += "Pick 5 numbers (1-40) to win!\n"
        
        keyboard = create_keyboard_with_nav([
            [InlineKeyboardButton(text=f"🎫 Buy Ticket ({TICKET_PRICE} SOL)", callback_data="buy_ticket")],
            [InlineKeyboardButton(text="🔄 Refresh", callback_data="check_active_rounds")]
        ])
        
        await bot.send_message(uid, text, reply_markup=keyboard, parse_mode="HTML")
    
    elif data.startswith("check_round_"):
        await query.answer("Refreshing...")
        round_id = int(data.split("_")[2])
        
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT round_number, status, start_time FROM scheduled_rounds WHERE round_id = %s", (round_id,))
        round_data = c.fetchone()
        conn.close()
        
        if not round_data:
            await bot.send_message(uid, "❌ Round not found.", parse_mode="HTML")
            return
        
        round_number, status, start_time = round_data
        stakes = get_round_stakes_with_counts(round_id)
        
        text = f"🎰 <b>Round {round_number} - Updated</b>\n\n"
        
        if status == 'open' and start_time:
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=pytz.UTC)
            now = datetime.now(pytz.UTC)
            elapsed = (now - start_dt).total_seconds() / 60
            remaining = max(0, ROUND_DURATION_MINUTES - elapsed)
            text += f"⏰ Time Remaining: {int(remaining)} minutes\n\n"
        
        text += "💰 <b>Current Players:</b>\n"
        
        stake_buttons = []
        for stake_id, stake_amount, stake_status, player_count in stakes:
            needed = max(0, MIN_PLAYERS_PER_STAKE - player_count)
            status_icon = "✅" if player_count >= MIN_PLAYERS_PER_STAKE else "🎯"
            
            text += f"{status_icon} {stake_amount} SOL: {player_count}/{MIN_PLAYERS_PER_STAKE}"
            if needed > 0:
                text += f" ({needed} more needed)"
            text += "\n"
            
            if status == 'open':
                stake_buttons.append([InlineKeyboardButton(
                    text=f"{status_icon} Join {stake_amount} SOL",
                    callback_data=f"join_stake_{stake_id}"
                )])
        
        if status == 'open':
            stake_buttons.append([InlineKeyboardButton(
                text="🔄 Refresh Again",
                callback_data=f"check_round_{round_id}"
            )])
        
        stake_buttons.append([InlineKeyboardButton(
            text="🔙 Back",
            callback_data="check_active_rounds"
        )])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=stake_buttons)
        await bot.send_message(uid, text, reply_markup=keyboard, parse_mode="HTML")
    
    elif data.startswith("join_stake_"):
        await query.answer("Pick your lucky numbers!")
        stake_id = int(data.split("_")[2])
        
        wallet = get_active_wallet(uid)
        if not wallet:
            await bot.send_message(uid,
                "❌ <b>No Wallet Found</b>\n\n"
                "Please create or connect a wallet first!",
                parse_mode="HTML"
            )
            return
        
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT stake_amount, round_id FROM round_stakes WHERE id = %s", (stake_id,))
        stake_row = c.fetchone()
        conn.close()
        
        if not stake_row:
            await bot.send_message(uid, "❌ Stake not found.")
            return
        
        stake_amount = Decimal(str(stake_row[0]))
        round_id = stake_row[1]
        
        balance = await get_real_balance(wallet)
        if balance < stake_amount:
            await bot.send_message(uid,
                f"⚠️ <b>Insufficient Balance</b>\n\n"
                f"Your balance: {balance} SOL\n"
                f"Required: {stake_amount} SOL\n\n"
                f"Please deposit more SOL to your wallet:\n"
                f"<code>{wallet}</code>",
                parse_mode="HTML"
            )
            return
        
        private_key = get_wallet_private_key(uid, wallet)
        
        if not private_key:
            await bot.send_message(uid,
                f"⚠️ This is an external wallet.\n\n"
                f"Please send <b>{stake_amount} SOL</b> to:\n"
                f"<code>{OWNER_WALLET}</code>\n\n"
                f"Then reply with your transaction signature.",
                parse_mode="HTML"
            )
            return
        
        # Store stake info and start number selection
        import time
        user_selected_numbers[uid] = {
            "numbers": [],
            "stake_id": stake_id,
            "round_id": round_id,
            "stake_amount": stake_amount,
            "wallet": wallet,
            "private_key": private_key,
            "created_at": time.time()
        }
        
        keyboard = create_number_picker_keyboard([])
        await bot.send_message(uid,
            f"🎯 <b>Pick Your Lucky Numbers!</b>\n\n"
            f"Select <b>5 numbers</b> from 1-40\n"
            f"Stake: <b>{stake_amount} SOL</b>\n\n"
            f"Selected: None (0/5)\n\n"
            f"Tap numbers to select them:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    
    # Number picker callbacks
    elif data.startswith("pick_num_"):
        num = int(data.split("_")[2])
        
        if uid not in user_selected_numbers:
            # No active session - guide user to start fresh
            await query.answer("Please start a new ticket purchase.", show_alert=False)
            try:
                await query.message.edit_text(
                    "🎫 <b>Ready to Buy a Ticket?</b>\n\n"
                    "Your previous selection session has ended.\n"
                    "Use /start to return to the main menu and buy a new ticket.",
                    parse_mode="HTML"
                )
            except:
                pass
            return
        
        selected = user_selected_numbers[uid]["numbers"]
        
        if num in selected:
            # Deselect
            selected.remove(num)
            await query.answer(f"Removed {num}")
        elif len(selected) < 5:
            # Select
            selected.append(num)
            await query.answer(f"Added {num}!")
        else:
            await query.answer("Already selected 5 numbers! Tap one to remove it.", show_alert=True)
            return
        
        user_selected_numbers[uid]["numbers"] = selected
        stake_amount = user_selected_numbers[uid]["stake_amount"]
        
        selected_str = ", ".join(map(str, sorted(selected))) if selected else "None"
        keyboard = create_number_picker_keyboard(selected)
        
        try:
            await query.message.edit_text(
                f"🎯 <b>Pick Your Lucky Numbers!</b>\n\n"
                f"Select <b>5 numbers</b> from 1-40\n"
                f"Stake: <b>{stake_amount} SOL</b>\n\n"
                f"Selected: <b>{selected_str}</b> ({len(selected)}/5)\n\n"
                f"{'✅ Ready! Tap Confirm to proceed.' if len(selected) == 5 else 'Tap numbers to select them:'}",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except:
            pass
    
    # Handle delete button for individual selected numbers (supports both state mechanisms)
    elif data.startswith("delete_num_"):
        num = int(data.split("_")[2])
        
        # Check which state mechanism is being used
        if uid in user_selected_numbers:
            # Using user_selected_numbers flow
            selected = user_selected_numbers[uid]["numbers"]
            stake_amount = user_selected_numbers[uid]["stake_amount"]
            
            if num in selected:
                selected.remove(num)
                await query.answer(f"Removed {num}")
            else:
                await query.answer("Number not in selection")
                return
            
            user_selected_numbers[uid]["numbers"] = selected
            
        elif uid in user_states and user_states[uid].get("action") == "picking_numbers":
            # Using user_states flow
            selected = user_states[uid].get("selected_numbers", [])
            stake_amount = user_states[uid].get("stake_amount", TICKET_PRICE)
            
            if num in selected:
                selected.remove(num)
                await query.answer(f"Removed {num}")
            else:
                await query.answer("Number not in selection")
                return
            
            user_states[uid]["selected_numbers"] = selected
        else:
            # No active session - guide user to start fresh instead of showing error
            await query.answer("Please start a new ticket purchase.", show_alert=False)
            try:
                await query.message.edit_text(
                    "🎫 <b>Ready to Buy a Ticket?</b>\n\n"
                    "Your previous selection session has ended.\n"
                    "Use /start to return to the main menu and buy a new ticket.",
                    parse_mode="HTML"
                )
            except:
                pass
            return
        
        selected_str = ", ".join(map(str, sorted(selected))) if selected else "None"
        keyboard = create_number_picker_keyboard(selected)
        
        try:
            await query.message.edit_text(
                f"🎯 <b>Pick Your Lucky Numbers!</b>\n\n"
                f"Select <b>5 numbers</b> from 1-40\n"
                f"Stake: <b>{stake_amount} SOL</b>\n\n"
                f"Selected: <b>{selected_str}</b> ({len(selected)}/5)\n\n"
                f"{'✅ Ready! Tap Confirm to proceed.' if len(selected) == 5 else 'Tap numbers to select them:'}",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except:
            pass
    
    # Handle noop button (info labels)
    elif data == "noop":
        await query.answer()
    
    elif data == "clear_numbers":
        if uid in user_selected_numbers:
            user_selected_numbers[uid]["numbers"] = []
            stake_amount = user_selected_numbers[uid]["stake_amount"]
            keyboard = create_number_picker_keyboard([])
            
            try:
                await query.message.edit_text(
                    f"🎯 <b>Pick Your Lucky Numbers!</b>\n\n"
                    f"Select <b>5 numbers</b> from 1-40\n"
                    f"Stake: <b>{stake_amount} SOL</b>\n\n"
                    f"Selected: None (0/5)\n\n"
                    f"Tap numbers to select them:",
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            except:
                pass
            await query.answer("Cleared!")
        else:
            # No active session
            await query.answer("Please start a new ticket purchase.", show_alert=False)
            try:
                await query.message.edit_text(
                    "🎫 <b>Ready to Buy a Ticket?</b>\n\n"
                    "Your previous selection session has ended.\n"
                    "Use /start to return to the main menu and buy a new ticket.",
                    parse_mode="HTML"
                )
            except:
                pass
    
    elif data == "cancel_number_selection":
        if uid in user_selected_numbers:
            del user_selected_numbers[uid]
        await query.answer("Cancelled")
        await query.message.edit_text(
            "❌ <b>Number selection cancelled.</b>\n\n"
            "Use /start to return to the main menu.",
            parse_mode="HTML"
        )

    elif data == "back_to_main":
        await query.answer()
        await cmd_start(query.message)


@dp.message()
async def generic_message_handler(message: types.Message):
    uid = message.from_user.id
    text = message.text.strip()

    # Check if user is in a PIN operation state
    if uid in user_states:
        state = user_states[uid]
        action = state.get("action")
        
        # Handle draw verification
        if action == "awaiting_verify_round":
            del user_states[uid]
            
            try:
                round_id = int(text)
            except ValueError:
                await bot.send_message(uid, 
                    "❌ Please enter a valid round number (e.g., 42)",
                    parse_mode="HTML"
                )
                return
            
            draw = get_draw_for_verification(round_id)
            
            if not draw:
                keyboard = create_keyboard_with_nav([
                    [InlineKeyboardButton(text="📜 View Recent Draws", callback_data="transparency_history")],
                    [InlineKeyboardButton(text="◀️ Back", callback_data="view_results")]
                ])
                await bot.send_message(uid,
                    f"❌ <b>Round not found</b>\n\n"
                    "This round may not have been drawn yet, or doesn't exist.\n"
                    "Try viewing recent draws to find valid rounds.",
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
                return
            
            nums_str = ", ".join(str(n) for n in draw["winning_numbers"]) if draw["winning_numbers"] else "N/A"
            seed_display = draw["seed_data"][:64] + "..." if draw["seed_data"] and len(draw["seed_data"]) > 64 else (draw["seed_data"] or "N/A")
            round_num = get_round_number(round_id)
            
            verification_text = (
                f"🔍 <b>DRAW VERIFICATION - Round {round_num}</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                
                f"🎲 <b>Winning Numbers:</b> <code>{nums_str}</code>\n\n"
                
                f"👥 <b>Players:</b> {draw['player_count']}\n"
                f"💰 <b>Prize Pool:</b> {draw['total_pot']:.4f} SOL\n"
                f"🏆 <b>Prize Paid:</b> {draw['prize_amount']:.4f} SOL\n\n"
                
                "🔐 <b>VERIFICATION DATA</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Seed Hash:</b>\n<code>{seed_display}</code>\n\n"
            )
            
            if draw["tx_signature"]:
                verification_text += (
                    f"<b>TX Signature:</b>\n"
                    f"<code>{draw['tx_signature'][:32]}...</code>\n\n"
                )
            
            verification_text += (
                "📋 <b>HOW TO VERIFY</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "1. The seed includes blockchain tx signatures\n"
                "2. SHA256(seed) generates the winning numbers\n"
                "3. Process is deterministic - same seed = same numbers\n"
                "4. Anyone can reproduce this calculation!\n\n"
                
                "✅ <b>This draw is cryptographically verified</b>"
            )
            
            keyboard_buttons = []
            if draw["tx_signature"]:
                keyboard_buttons.append([InlineKeyboardButton(
                    text="🔗 View TX on Solscan", 
                    url=f"https://solscan.io/tx/{draw['tx_signature']}"
                )])
            keyboard_buttons.append([InlineKeyboardButton(text="◀️ Back", callback_data="view_results")])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            
            await bot.send_message(uid, verification_text, reply_markup=keyboard, parse_mode="HTML")
            return
        
        # Handle PIN setting
        if action == "set_pin_after_wallet":
            # Delete PIN message immediately for security
            try:
                await message.delete()
            except:
                pass
            
            if text.isdigit() and len(text) == 4:
                # First entry - store temporarily and ask for confirmation
                user_states[uid] = {
                    "action": "confirm_pin_after_wallet", 
                    "wallet_address": state.get("wallet_address"),
                    "first_pin": text
                }
                await bot.send_message(uid,
                    "🔐 <b>Confirm Your PIN</b>\n\n"
                    "Please enter your 4-digit PIN again to confirm:",
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(uid, "❌ PIN must be exactly 4 digits. Please try again:")
            return
        
        elif action == "confirm_pin_after_wallet":
            # Delete PIN message immediately for security
            try:
                await message.delete()
            except:
                pass
            
            first_pin = state.get("first_pin")
            if text == first_pin:
                if set_user_pin(uid, text):
                    wallet_address = state.get("wallet_address")
                    set_active_wallet(uid, wallet_address)
                    # Check if user already has a security question set up
                    if has_security_question(uid):
                        del user_states[uid]
                        await bot.send_message(uid,
                            "✅ <b>PIN Created Successfully!</b>\n\n"
                            "Your PIN has been securely saved.\n"
                            "You can now use your wallet!",
                            parse_mode="HTML"
                        )
                        await start_private_play(uid)
                    else:
                        # First-time PIN setup - require security question
                        user_states[uid] = {"action": "setup_first_security_question", "next_action": "play"}
                        await bot.send_message(uid,
                            "✅ <b>PIN Created!</b>\n\n"
                            "🔐 <b>Security Setup Required</b>\n\n"
                            "For your protection, you must set up a security question.\n"
                            "This will help you recover your PIN if you forget it.",
                            parse_mode="HTML"
                        )
                        await show_security_question_picker(uid)
                else:
                    await bot.send_message(uid, "❌ Failed to save PIN. Please try again with 4 digits.")
            else:
                # PINs don't match - start over
                user_states[uid] = {"action": "set_pin_after_wallet", "wallet_address": state.get("wallet_address")}
                await bot.send_message(uid,
                    "❌ <b>PINs don't match!</b>\n\n"
                    "Please enter a new 4-digit PIN:",
                    parse_mode="HTML"
                )
            return
        
        elif action == "set_pin_for_key_view":
            # Delete PIN message immediately for security
            try:
                await message.delete()
            except:
                pass
            
            if text.isdigit() and len(text) == 4:
                # First entry - store temporarily and ask for confirmation
                user_states[uid] = {"action": "confirm_pin_for_key_view", "first_pin": text}
                await bot.send_message(uid,
                    "🔐 <b>Confirm Your PIN</b>\n\n"
                    "Please enter your 4-digit PIN again to confirm:",
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(uid, "❌ PIN must be exactly 4 digits. Please try again:")
            return
        
        elif action == "confirm_pin_for_key_view":
            # Delete PIN message immediately for security
            try:
                await message.delete()
            except:
                pass
            
            first_pin = state.get("first_pin")
            if text == first_pin:
                if set_user_pin(uid, text):
                    # Check if user needs security question setup first
                    if has_security_question(uid):
                        # Now ask to verify PIN to view key
                        user_states[uid] = {"action": "verify_pin_for_key_view"}
                        await bot.send_message(uid,
                            "✅ PIN created!\n\n"
                            "Please enter your PIN to view private key:",
                            parse_mode="HTML"
                        )
                    else:
                        # First-time PIN - require security question
                        user_states[uid] = {"action": "setup_first_security_question", "next_action": "view_key"}
                        await bot.send_message(uid,
                            "✅ <b>PIN Created!</b>\n\n"
                            "🔐 <b>Security Setup Required</b>\n\n"
                            "For your protection, you must set up a security question.\n"
                            "This will help you recover your PIN if you forget it.",
                            parse_mode="HTML"
                        )
                        await show_security_question_picker(uid)
                else:
                    await bot.send_message(uid, "❌ Failed to save PIN. Please try again with 4 digits.")
            else:
                # PINs don't match - start over
                user_states[uid] = {"action": "set_pin_for_key_view"}
                await bot.send_message(uid,
                    "❌ <b>PINs don't match!</b>\n\n"
                    "Please enter a new 4-digit PIN:",
                    parse_mode="HTML"
                )
            return
        
        elif action == "verify_pin_for_key_view":
            # Delete PIN message immediately for security
            try:
                await message.delete()
            except:
                pass
            
            if verify_user_pin(uid, text):
                # PIN is now persistent - don't delete after verification
                del user_states[uid]
                pin_fail_counts.pop(uid, None)  # Reset fail count on success
                wallet = get_active_wallet(uid)
                private_key = get_wallet_private_key(uid, wallet)
                if private_key:
                    # Send private key message with auto-delete warning
                    key_message = await bot.send_message(uid,
                        f"🔑 <b>Private Key</b>\n\n"
                        f"Wallet: <code>{wallet[:8]}...{wallet[-8:]}</code>\n\n"
                        f"⚠️ <b>KEEP THIS SECRET!</b>\n"
                        f"Private Key:\n<code>{private_key}</code>\n\n"
                        f"🔐 Never share this with anyone!\n\n"
                        f"⏰ <b>This message will auto-delete in 30 seconds for security.</b>",
                        parse_mode="HTML"
                    )
                    # Schedule auto-deletion after 30 seconds
                    await schedule_private_key_deletion(uid, key_message, delay_seconds=30)
                else:
                    await bot.send_message(uid, "❌ Could not retrieve private key.")
            else:
                # Track failed PIN attempts
                pin_fail_counts[uid] = pin_fail_counts.get(uid, 0) + 1
                if pin_fail_counts[uid] >= 2 and has_security_question(uid):
                    # Show forgot PIN button after 2 failed attempts
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔑 Forgot PIN?", callback_data="reset_pin_security")],
                        [InlineKeyboardButton(text="🔙 Cancel", callback_data="back_to_main")]
                    ])
                    await bot.send_message(uid, 
                        "❌ <b>Incorrect PIN</b>\n\n"
                        "You've entered the wrong PIN multiple times.\n"
                        "Use your security question to reset your PIN.",
                        reply_markup=keyboard,
                        parse_mode="HTML"
                    )
                else:
                    await bot.send_message(uid, "❌ Incorrect PIN. Please try again:")
            return
        
        elif action == "import_wallet_private_key":
            # IMMEDIATELY delete the message containing the private key for security
            try:
                await message.delete()
            except Exception as e:
                print(f"Failed to delete private key message: {e}")
            
            del user_states[uid]
            private_key = text.strip()
            
            # Import the wallet using the private key
            result = import_wallet_from_private_key(uid, private_key)
            
            if result["success"]:
                wallet_address = result["address"]
                wallet_name = result["name"]
                
                # Get balance of imported wallet
                balance = await get_real_balance(wallet_address)
                
                keyboard = create_keyboard_with_nav([
                    [InlineKeyboardButton(text="🎲 Play Now", callback_data="play_now")],
                    [InlineKeyboardButton(text="💼 View Wallets", callback_data="my_wallets")]
                ])
                
                # Send success message (will auto-delete after 30 seconds)
                success_msg = await bot.send_message(uid,
                    f"✅ <b>Wallet Imported Successfully!</b>\n\n"
                    f"🔐 Your private key message was deleted for security.\n\n"
                    f"📍 <b>Wallet Address:</b>\n<code>{wallet_address}</code>\n\n"
                    f"💰 <b>Balance:</b> {balance} SOL\n\n"
                    f"⚠️ <b>PRIVATE KEY SAFETY:</b>\n"
                    f"• NEVER share your private key with ANYONE\n"
                    f"• RedLuck team will NEVER ask for your key\n"
                    f"• Anyone with your key can steal all funds\n\n"
                    f"⚠️ <b>This message will auto-delete in 30 seconds.</b>",
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
                
                # Schedule auto-deletion of success message for extra security
                await schedule_private_key_deletion(uid, success_msg, delay_seconds=30)
            else:
                error_msg = result.get("error", "Unknown error")
                keyboard = create_keyboard_with_nav([
                    [InlineKeyboardButton(text="🔄 Try Again", callback_data="import_wallet_confirm")]
                ], "my_wallets")
                
                await bot.send_message(uid,
                    f"❌ <b>Import Failed</b>\n\n"
                    f"🔐 Your message was deleted for security.\n\n"
                    f"Error: {error_msg}\n\n"
                    f"Please check your private key format and try again.",
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            return
        
        elif action == "set_pin_for_send":
            # Delete PIN message immediately for security
            try:
                await message.delete()
            except:
                pass
            
            if text.isdigit() and len(text) == 4:
                # First entry - store temporarily and ask for confirmation
                user_states[uid] = {"action": "confirm_pin_for_send", "first_pin": text}
                await bot.send_message(uid,
                    "🔐 <b>Confirm Your PIN</b>\n\n"
                    "Please enter your 4-digit PIN again to confirm:",
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(uid, "❌ PIN must be exactly 4 digits. Please try again:")
            return
        
        elif action == "confirm_pin_for_send":
            # Delete PIN message immediately for security
            try:
                await message.delete()
            except:
                pass
            
            first_pin = state.get("first_pin")
            if text == first_pin:
                if set_user_pin(uid, text):
                    # Check if user needs security question setup first
                    if has_security_question(uid):
                        user_states[uid] = {"action": "get_send_address"}
                        wallet = get_active_wallet(uid)
                        balance = await get_real_balance(wallet)
                        await bot.send_message(uid,
                            f"✅ PIN created!\n\n"
                            f"💸 <b>Send SOL</b>\n"
                            f"Current balance: <b>{balance} SOL</b>\n\n"
                            f"Please send the recipient's Solana address:",
                            parse_mode="HTML"
                        )
                    else:
                        # First-time PIN - require security question
                        user_states[uid] = {"action": "setup_first_security_question", "next_action": "send"}
                        await bot.send_message(uid,
                            "✅ <b>PIN Created!</b>\n\n"
                            "🔐 <b>Security Setup Required</b>\n\n"
                            "For your protection, you must set up a security question.\n"
                            "This will help you recover your PIN if you forget it.",
                            parse_mode="HTML"
                        )
                        await show_security_question_picker(uid)
                else:
                    await bot.send_message(uid, "❌ Failed to save PIN. Please try again with 4 digits.")
            else:
                # PINs don't match - start over
                user_states[uid] = {"action": "set_pin_for_send"}
                await bot.send_message(uid,
                    "❌ <b>PINs don't match!</b>\n\n"
                    "Please enter a new 4-digit PIN:",
                    parse_mode="HTML"
                )
            return
        
        elif action == "verify_pin_for_send":
            # Delete PIN message immediately for security
            try:
                await message.delete()
            except:
                pass
            
            if verify_user_pin(uid, text):
                # PIN is now persistent - don't delete after verification
                user_states[uid] = {"action": "get_send_address"}
                pin_fail_counts.pop(uid, None)  # Reset fail count on success
                wallet = get_active_wallet(uid)
                balance = await get_real_balance(wallet)
                await bot.send_message(uid,
                    f"✅ PIN verified!\n\n"
                    f"💸 <b>Send SOL</b>\n"
                    f"Current balance: <b>{balance} SOL</b>\n\n"
                    f"Please send the recipient's Solana address:",
                    parse_mode="HTML"
                )
            else:
                # Track failed PIN attempts
                pin_fail_counts[uid] = pin_fail_counts.get(uid, 0) + 1
                if pin_fail_counts[uid] >= 2 and has_security_question(uid):
                    # Show forgot PIN button after 2 failed attempts
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔑 Forgot PIN?", callback_data="reset_pin_security")],
                        [InlineKeyboardButton(text="🔙 Cancel", callback_data="back_to_main")]
                    ])
                    await bot.send_message(uid, 
                        "❌ <b>Incorrect PIN</b>\n\n"
                        "You've entered the wrong PIN multiple times.\n"
                        "Use your security question to reset your PIN.",
                        reply_markup=keyboard,
                        parse_mode="HTML"
                    )
                else:
                    await bot.send_message(uid, "❌ Incorrect PIN. Please try again:")
            return
        
        elif action == "get_send_address":
            # Validate Solana address
            if 32 <= len(text) <= 44 and text.isalnum():
                user_states[uid] = {"action": "get_send_amount", "recipient": text}
                wallet = get_active_wallet(uid)
                balance = await get_real_balance(wallet)
                await message.answer(
                    f"💸 <b>Send to:</b>\n<code>{text}</code>\n\n"
                    f"Your balance: <b>{balance} SOL</b>\n\n"
                    f"How much SOL do you want to send?\n"
                    f"(Example: 0.5 or 1.25)",
                    parse_mode="HTML"
                )
            else:
                await message.answer("❌ Invalid Solana address. Please send a valid address:")
            return
        
        elif action == "get_send_amount":
            try:
                amount = Decimal(text)
                if amount <= 0:
                    await message.answer("❌ Amount must be greater than 0. Please try again:")
                    return
                
                wallet = get_active_wallet(uid)
                balance = await get_real_balance(wallet)
                
                if amount > balance:
                    await message.answer(
                        f"❌ <b>Insufficient Balance</b>\n\n"
                        f"You have: {balance} SOL\n"
                        f"Trying to send: {amount} SOL\n\n"
                        f"Please enter a smaller amount:",
                        parse_mode="HTML"
                    )
                    return
                
                recipient = state.get("recipient")
                private_key = get_wallet_private_key(uid, wallet)
                
                if not private_key:
                    del user_states[uid]
                    await message.answer("❌ Could not access wallet private key.")
                    return
                
                await message.answer("⏳ Sending transaction...")
                
                result = await send_sol(wallet, recipient, amount, private_key)
                
                del user_states[uid]
                
                if result["success"]:
                    await message.answer(
                        f"✅ <b>Transaction Successful!</b>\n\n"
                        f"Sent: <b>{amount} SOL</b>\n"
                        f"To: <code>{recipient[:8]}...{recipient[-8:]}</code>\n"
                        f"Transaction: <code>{result['signature'][:16]}...</code>\n\n"
                        f"View on Solscan:\n"
                        f"https://solscan.io/tx/{result['signature']}",
                        parse_mode="HTML"
                    )
                else:
                    await message.answer(
                        f"❌ <b>Transaction Failed</b>\n\n"
                        f"Error: {result.get('error', 'Unknown error')}\n\n"
                        f"Please try again later.",
                        parse_mode="HTML"
                    )
            except (ValueError, decimal.InvalidOperation):
                await message.answer("❌ Invalid amount. Please enter a number (e.g., 0.5):")
            return
        
        elif action == "enter_custom_question":
            question = text.strip()
            if len(question) < 10:
                await message.answer("❌ Question is too short. Please enter a longer question:")
                return
            if len(question) > 200:
                await message.answer("❌ Question is too long. Please keep it under 200 characters:")
                return
            
            # Preserve next_action if first-time setup
            next_action = state.get("next_action")
            new_state = {"action": "enter_security_answer", "question": question}
            if next_action:
                new_state["next_action"] = next_action
            user_states[uid] = new_state
            await bot.send_message(uid,
                f"❓ <b>Your Question:</b>\n{question}\n\n"
                f"Please enter your answer:\n"
                f"(Remember this answer - you'll need it to reset your PIN!)",
                parse_mode="HTML"
            )
            return
        
        elif action == "enter_security_answer":
            answer = text.strip()
            
            # Validate length first before deleting
            if len(answer) < 2:
                # Delete message even on error (security)
                try:
                    await message.delete()
                except:
                    pass
                await bot.send_message(uid, "❌ Answer is too short. Please enter a longer answer:")
                return
            
            # Delete answer message for security (like PIN)
            try:
                await message.delete()
            except:
                pass
            
            # First time entering - ask to confirm (like PIN double entry)
            question = state.get("question")
            next_action = state.get("next_action")
            new_state = {"action": "confirm_security_answer", "question": question, "first_answer": answer}
            if next_action:
                new_state["next_action"] = next_action
            user_states[uid] = new_state
            await bot.send_message(uid,
                f"🔐 <b>Confirm Your Answer</b>\n\n"
                f"❓ Question: {question}\n\n"
                f"Please enter your answer again to confirm:",
                parse_mode="HTML"
            )
            return
        
        elif action == "confirm_security_answer":
            answer = text.strip()
            first_answer = state.get("first_answer")
            question = state.get("question")
            next_action = state.get("next_action")
            
            # Delete answer message for security (like PIN)
            try:
                await message.delete()
            except:
                pass
            
            if answer == first_answer:
                if save_security_question(uid, question, answer):
                    # Check if this was first-time setup and redirect accordingly
                    if next_action:
                        del user_states[uid]
                        await bot.send_message(uid,
                            "✅ <b>Security Setup Complete!</b>\n\n"
                            "Your security question has been saved.\n"
                            "You can now use your wallet!",
                            parse_mode="HTML"
                        )
                        # Redirect to original action
                        if next_action == "play":
                            await start_private_play(uid)
                        elif next_action == "view_key":
                            # Ask for PIN to view key
                            user_states[uid] = {"action": "verify_pin_for_key_view"}
                            await bot.send_message(uid,
                                "🔐 Please enter your PIN to view private key:",
                                parse_mode="HTML"
                            )
                        elif next_action == "send":
                            # Continue to send flow
                            user_states[uid] = {"action": "get_send_address"}
                            wallet = get_active_wallet(uid)
                            balance = await get_real_balance(wallet)
                            await bot.send_message(uid,
                                f"💸 <b>Send SOL</b>\n"
                                f"Current balance: <b>{balance} SOL</b>\n\n"
                                f"Please send the recipient's Solana address:",
                                parse_mode="HTML"
                            )
                    else:
                        del user_states[uid]
                        await bot.send_message(uid,
                            "✅ <b>Security Question Saved!</b>\n\n"
                            "You can now use this to reset your PIN if you forget it.\n\n"
                            f"❓ Question: {question}\n"
                            f"💡 Tip: Remember your answer!",
                            reply_markup=create_keyboard_with_nav([]),
                            parse_mode="HTML"
                        )
                else:
                    del user_states[uid]
                    await bot.send_message(uid,
                        "❌ Failed to save security question. Please try again.",
                        reply_markup=create_keyboard_with_nav([])
                    )
            else:
                # Answers don't match - start over (preserve next_action)
                new_state = {"action": "enter_security_answer", "question": question}
                if next_action:
                    new_state["next_action"] = next_action
                user_states[uid] = new_state
                await bot.send_message(uid,
                    f"❌ <b>Answers don't match!</b>\n\n"
                    f"❓ Question: {question}\n\n"
                    f"Please enter your answer again:",
                    parse_mode="HTML"
                )
            return
        
        elif action == "verify_security_answer_for_reset":
            # Delete answer message immediately for security (like PIN)
            try:
                await message.delete()
            except:
                pass
            
            answer = text.strip()
            
            if verify_security_answer(uid, answer):
                delete_user_pin(uid)
                user_states[uid] = {"action": "set_new_pin_after_reset"}
                await bot.send_message(uid,
                    "✅ <b>Answer Correct!</b>\n\n"
                    "Please enter a new 4-digit PIN:",
                    parse_mode="HTML"
                )
            else:
                del user_states[uid]
                await bot.send_message(uid,
                    "❌ <b>Incorrect Answer</b>\n\n"
                    "The answer doesn't match. You can try again from Settings.",
                    reply_markup=create_keyboard_with_nav([]),
                    parse_mode="HTML"
                )
            return
        
        elif action == "verify_security_for_change":
            # Delete answer message immediately for security (like PIN)
            try:
                await message.delete()
            except:
                pass
            
            answer = text.strip()
            
            if verify_security_answer(uid, answer):
                del user_states[uid]
                await bot.send_message(uid,
                    "✅ <b>Answer Correct!</b>\n\n"
                    "You can now set a new security question.",
                    parse_mode="HTML"
                )
                await show_security_question_picker(uid)
            else:
                del user_states[uid]
                await bot.send_message(uid,
                    "❌ <b>Incorrect Answer</b>\n\n"
                    "The answer doesn't match. You can try again from Settings.",
                    reply_markup=create_keyboard_with_nav([]),
                    parse_mode="HTML"
                )
            return
        
        elif action == "set_new_pin_after_reset":
            try:
                await message.delete()
            except:
                pass
            
            if text.isdigit() and len(text) == 4:
                user_states[uid] = {"action": "confirm_new_pin_after_reset", "first_pin": text}
                await bot.send_message(uid,
                    "🔐 <b>Confirm Your PIN</b>\n\n"
                    "Please enter your new PIN again to confirm:",
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(uid, "❌ PIN must be exactly 4 digits. Please try again:")
            return
        
        elif action == "confirm_new_pin_after_reset":
            try:
                await message.delete()
            except:
                pass
            
            first_pin = state.get("first_pin")
            if text == first_pin:
                if set_user_pin(uid, text):
                    del user_states[uid]
                    await bot.send_message(uid,
                        "✅ <b>PIN Reset Successfully!</b>\n\n"
                        "Your new PIN has been saved.",
                        reply_markup=create_keyboard_with_nav([]),
                        parse_mode="HTML"
                    )
                else:
                    await bot.send_message(uid, "❌ Failed to save PIN. Please try again with 4 digits.")
            else:
                user_states[uid] = {"action": "set_new_pin_after_reset"}
                await bot.send_message(uid,
                    "❌ <b>PINs don't match!</b>\n\n"
                    "Please enter a new 4-digit PIN:",
                    parse_mode="HTML"
                )
            return
        
        elif action == "verify_current_pin_for_change":
            try:
                await message.delete()
            except:
                pass
            
            if verify_user_pin(uid, text):
                user_states[uid] = {"action": "set_new_pin"}
                await bot.send_message(uid,
                    "✅ PIN verified!\n\n"
                    "Please enter your new 4-digit PIN:",
                    parse_mode="HTML"
                )
            else:
                del user_states[uid]
                await bot.send_message(uid,
                    "❌ Incorrect PIN.\n\n"
                    "You can try again from Settings.",
                    reply_markup=create_keyboard_with_nav([])
                )
            return
        
        elif action == "set_new_pin":
            try:
                await message.delete()
            except:
                pass
            
            if text.isdigit() and len(text) == 4:
                user_states[uid] = {"action": "confirm_new_pin", "first_pin": text}
                await bot.send_message(uid,
                    "🔐 <b>Confirm Your PIN</b>\n\n"
                    "Please enter your new PIN again to confirm:",
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(uid, "❌ PIN must be exactly 4 digits. Please try again:")
            return
        
        elif action == "confirm_new_pin":
            try:
                await message.delete()
            except:
                pass
            
            first_pin = state.get("first_pin")
            if text == first_pin:
                if set_user_pin(uid, text):
                    del user_states[uid]
                    await bot.send_message(uid,
                        "✅ <b>PIN Changed Successfully!</b>\n\n"
                        "Your new PIN has been saved.",
                        reply_markup=create_keyboard_with_nav([]),
                        parse_mode="HTML"
                    )
                else:
                    await bot.send_message(uid, "❌ Failed to save PIN. Please try again with 4 digits.")
            else:
                user_states[uid] = {"action": "set_new_pin"}
                await bot.send_message(uid,
                    "❌ <b>PINs don't match!</b>\n\n"
                    "Please enter a new 4-digit PIN:",
                    parse_mode="HTML"
                )
            return
        
        elif action == "enter_stake_amount":
            try:
                amount = Decimal(text.strip())
                wallet = state.get("wallet")
                stored_balance = Decimal(state.get("balance", "0"))
                
                # Get fresh balance
                current_balance = await get_real_balance(wallet)
                
                # Validate minimum stake
                if amount < STAKE_MIN:
                    await message.answer(
                        f"❌ <b>Below Minimum Stake</b>\n\n"
                        f"You entered: <b>{amount} SOL</b>\n"
                        f"Minimum stake: <b>{STAKE_MIN} SOL</b>\n\n"
                        f"Please enter an amount of at least {STAKE_MIN} SOL:",
                        parse_mode="HTML"
                    )
                    return
                
                # Validate maximum stake
                if amount > STAKE_MAX:
                    await message.answer(
                        f"❌ <b>Above Maximum Stake</b>\n\n"
                        f"You entered: <b>{amount} SOL</b>\n"
                        f"Maximum stake: <b>{STAKE_MAX} SOL</b>\n\n"
                        f"Please enter an amount up to {STAKE_MAX} SOL:",
                        parse_mode="HTML"
                    )
                    return
                
                # Validate balance
                if amount > current_balance:
                    await message.answer(
                        f"❌ <b>Insufficient Balance</b>\n\n"
                        f"You have: <b>{current_balance} SOL</b>\n"
                        f"Trying to stake: <b>{amount} SOL</b>\n\n"
                        f"Please deposit more SOL to your wallet:\n"
                        f"<code>{wallet}</code>\n\n"
                        f"Or enter a smaller amount:",
                        parse_mode="HTML"
                    )
                    return
                
                # Get private key for bot-managed wallets
                private_key = get_wallet_private_key(uid, wallet)
                
                if not private_key:
                    # External wallet - set state to wait for transaction signature
                    user_states[uid] = {
                        "action": "stake_external_tx", 
                        "wallet": wallet, 
                        "stake_amount": str(amount)
                    }
                    await message.answer(
                        f"⚠️ <b>External Wallet - Manual Payment Required</b>\n\n"
                        f"Please send <b>{amount} SOL</b> to:\n"
                        f"<code>{OWNER_WALLET}</code>\n\n"
                        f"After sending, reply with your transaction signature\n"
                        f"(the long alphanumeric code from your wallet).",
                        parse_mode="HTML"
                    )
                    return
                
                # Clear state for bot-managed wallets
                del user_states[uid]
                
                # Process stake for bot-managed wallets
                owner_amt = amount * Decimal("0.8")
                team_amt = amount * Decimal("0.2")
                
                await message.answer("⏳ Processing your stake payment...")
                
                # Send to owner wallet
                result = await send_sol(wallet, OWNER_WALLET, owner_amt, private_key)
                
                if not result["success"]:
                    await message.answer(
                        f"❌ <b>Transaction Failed!</b>\n\n"
                        f"Error: {result.get('error', 'Unknown error')}\n\n"
                        f"Please try again or contact support.",
                        parse_mode="HTML"
                    )
                    return
                
                tx_signature = result["signature"]
                
                # Send to team wallet (if different from owner)
                if TEAM_WALLET and TEAM_WALLET != OWNER_WALLET:
                    await send_sol(wallet, TEAM_WALLET, team_amt, private_key)
                
                # Get or create active round stake for this amount
                stake_id, round_id = get_or_create_active_round_stake(amount)
                
                if not stake_id:
                    await message.answer(
                        f"❌ <b>No Active Round</b>\n\n"
                        f"There is no active lottery round at the moment.\n"
                        f"Your payment was processed. Contact support with TX:\n"
                        f"<code>{tx_signature}</code>",
                        parse_mode="HTML"
                    )
                    return
                
                # Generate lottery numbers deterministically from transaction signature
                number_seed = generate_provable_seed(uid, stake_id, tx_signature, "player_numbers")
                lottery_numbers = generate_lottery_numbers(number_seed, count=5, min_val=1, max_val=40)
                
                # Add to round_participants (this is what the draw system uses)
                add_result = add_round_participant(stake_id, uid, lottery_numbers, tx_signature)
                
                if not add_result["success"]:
                    await message.answer(
                        f"❌ <b>Failed to Join Round</b>\n\n"
                        f"Error: {add_result.get('error', 'Unknown error')}\n\n"
                        f"Payment was processed. Contact support with TX:\n"
                        f"<code>{tx_signature}</code>",
                        parse_mode="HTML"
                    )
                    return
                
                participant_id = add_result["participant_id"]
                ticket_count = add_result.get("ticket_count", 1)
                
                # Also add to entries table for legacy/backup tracking
                add_entry(uid, get_current_round(), lottery_numbers, float(amount), tx_signature, paid=1)
                
                try:
                    jackpot = await get_real_balance(OWNER_WALLET)
                except:
                    jackpot = Decimal("0")
                
                await message.answer(
                    f"✅ <b>Payment confirmed</b>\n"
                    f"🎟 <b>Ticket successfully added</b>\n\n"
                    f"🎫 <b>Ticket ID:</b> #{participant_id}\n"
                    f"🎲 <b>Your Numbers:</b> {numbers_to_str(lottery_numbers)}\n"
                    f"💰 <b>Stake:</b> {amount} SOL\n"
                    f"🏆 <b>Current Jackpot:</b> {jackpot} SOL\n\n"
                    f"📝 Transaction:\n<code>{tx_signature[:20]}...</code>\n\n"
                    f"🎯 You can buy multiple tickets for this round.\n"
                    f"🍀 <b>Good luck!</b> Winner will be announced in the channel.",
                    parse_mode="HTML"
                )
                
                # Announce the new ticket to channel
                await announce_new_ticket(uid, participant_id, amount, lottery_numbers, round_id, ticket_count)
                
            except (ValueError, decimal.InvalidOperation):
                await message.answer(
                    f"❌ <b>Invalid Amount</b>\n\n"
                    f"Please enter a valid number.\n"
                    f"Examples: 0.1, 0.5, 1.25, 2.5\n\n"
                    f"Stake range: {STAKE_MIN} - {STAKE_MAX} SOL",
                    parse_mode="HTML"
                )
            return
        
        elif action == "stake_external_tx":
            # Handle external wallet transaction signature
            tx_signature = text.strip()
            wallet = state.get("wallet")
            stake_amount = Decimal(state.get("stake_amount", "0"))
            
            # Validate transaction signature format (base58, typically 88 chars)
            if len(tx_signature) < 60 or not tx_signature.isalnum():
                await message.answer(
                    f"❌ <b>Invalid Transaction Signature</b>\n\n"
                    f"Please enter a valid Solana transaction signature.\n"
                    f"It should be a long alphanumeric string (like: 5wBYsK...)\n\n"
                    f"Or send /cancel to cancel.",
                    parse_mode="HTML"
                )
                return
            
            await message.answer("⏳ Verifying your transaction on the blockchain...")
            
            # Verify the transaction on-chain
            verification = await verify_solana_transaction(
                tx_signature=tx_signature,
                expected_recipient=OWNER_WALLET,
                expected_amount=stake_amount,
                sender_wallet=wallet
            )
            
            if not verification["valid"]:
                await message.answer(
                    f"❌ <b>Transaction Verification Failed</b>\n\n"
                    f"Error: {verification.get('error', 'Unknown error')}\n\n"
                    f"Please ensure you:\n"
                    f"1. Sent {stake_amount} SOL to <code>{OWNER_WALLET}</code>\n"
                    f"2. The transaction is confirmed on the blockchain\n"
                    f"3. You entered the correct transaction signature\n\n"
                    f"Try again with the correct signature:",
                    parse_mode="HTML"
                )
                return
            
            # Clear state after successful verification
            del user_states[uid]
            
            # Get or create active round stake for this amount
            stake_id, round_id = get_or_create_active_round_stake(stake_amount)
            
            if not stake_id:
                await message.answer(
                    f"❌ <b>No Active Round</b>\n\n"
                    f"There is no active lottery round at the moment.\n"
                    f"Your payment was verified. Contact support with TX:\n"
                    f"<code>{tx_signature}</code>",
                    parse_mode="HTML"
                )
                return
            
            # Generate lottery numbers deterministically from transaction signature
            number_seed = generate_provable_seed(uid, stake_id, tx_signature, "player_numbers")
            lottery_numbers = generate_lottery_numbers(number_seed, count=5, min_val=1, max_val=40)
            
            # Add to round_participants (this is what the draw system uses)
            add_result = add_round_participant(stake_id, uid, lottery_numbers, tx_signature)
            
            if not add_result["success"]:
                await message.answer(
                    f"❌ <b>Failed to Join Round</b>\n\n"
                    f"Error: {add_result.get('error', 'Unknown error')}\n\n"
                    f"Payment was verified. Contact support with TX:\n"
                    f"<code>{tx_signature}</code>",
                    parse_mode="HTML"
                )
                return
            
            participant_id = add_result["participant_id"]
            ticket_count = add_result.get("ticket_count", 1)
            
            # Also add to entries table for legacy/backup tracking
            add_entry(uid, get_current_round(), lottery_numbers, float(stake_amount), tx_signature, paid=1)
            
            try:
                jackpot = await get_real_balance(OWNER_WALLET)
            except:
                jackpot = Decimal("0")
            
            await message.answer(
                f"✅ <b>Payment confirmed</b>\n"
                f"🎟 <b>Ticket successfully added</b>\n\n"
                f"🎫 <b>Ticket ID:</b> #{participant_id}\n"
                f"🎲 <b>Your Numbers:</b> {numbers_to_str(lottery_numbers)}\n"
                f"💰 <b>Stake:</b> {stake_amount} SOL\n"
                f"🏆 <b>Current Jackpot:</b> {jackpot} SOL\n\n"
                f"📝 Transaction verified:\n<code>{tx_signature[:20]}...</code>\n\n"
                f"🎯 You can buy multiple tickets for this round.\n"
                f"🍀 <b>Good luck!</b> Winner will be announced in the channel.",
                parse_mode="HTML"
            )
            
            # Announce the new ticket to channel
            await announce_new_ticket(uid, participant_id, stake_amount, lottery_numbers, round_id, ticket_count)
            return

    # Check if it's a Solana wallet address (32-44 chars, alphanumeric)
    if 32 <= len(text) <= 44 and text.isalnum():
        wallet_count = get_user_wallet_count(uid)

        if wallet_count >= MAX_WALLETS_PER_USER:
            await bot.send_message(uid,
                "⚠️ You already have a wallet. Delete it first to add a new one."
            )
            return

        # Try to save as external wallet
        if save_external_wallet(uid, text, "external"):
            set_active_wallet(uid, text)
            balance = await get_real_balance(text)
            await bot.send_message(uid,
                f"✅ <b>Wallet connected successfully!</b>\n\n"
                f"Address: <code>{text}</code>\n"
                f"Balance: {balance} SOL\n\n"
                f"You can now play!",
                parse_mode="HTML"
            )
            await start_private_play(uid)
        else:
            await bot.send_message(uid,
                "❌ Failed to connect wallet. It may already be connected."
            )
# ==========================
# 🔹 Wallet Buttons Handlers
# ==========================


# ---------------------------
# Admin commands
# ---------------------------

# DISABLED: Manual draw command replaced by automatic draws
# Automatic draws now trigger when:
#  1. 10 players join a stake, OR
#  2. 30 minutes pass since first participant
#
# @dp.message(Command("admin_draw"))
# async def cmd_admin_draw(message: types.Message):
#     if not is_admin(message.from_user.id):
#         await message.reply("⛔ Not authorized.")
#         return
#     await message.reply("⚠️ Manual draws are disabled. The system now uses automatic draws based on player count or time elapsed.")


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("⛔ Not authorized.")
        return
    
    rounds = get_active_rounds()
    
    text = "📊 <b>System Status</b>\n\n"
    text += f"⏰ Next rounds: {', '.join(ROUND_TIMES_UTC)} UTC\n"
    text += f"👥 Min players: {MIN_PLAYERS_PER_STAKE}\n"
    text += f"⏱ Round duration: {ROUND_DURATION_MINUTES} min\n\n"
    
    if not rounds:
        text += "🎰 No active rounds\n"
    else:
        text += f"🎰 <b>Active Rounds: {len(rounds)}</b>\n\n"
        
        for round_id, round_number, scheduled_time, start_time, end_time, status in rounds:
            text += f"<b>Round {round_number}</b>\n"
            text += f"Status: {status}\n"
            
            if start_time:
                start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=pytz.UTC)
                now = datetime.now(pytz.UTC)
                elapsed = (now - start_dt).total_seconds() / 60
                remaining = max(0, ROUND_DURATION_MINUTES - elapsed)
                text += f"Time remaining: {int(remaining)} min\n"
            
            stakes = get_round_stakes_with_counts(round_id)
            total_players = sum(player_count for _, _, _, player_count in stakes)
            text += f"Total players: {total_players}\n"
            
            text += "Stakes:\n"
            for stake_id, stake_amount, stake_status, player_count in stakes:
                text += f"  • {stake_amount} SOL: {player_count} players\n"
            
            text += "\n"
    
    await message.reply(text, parse_mode="HTML")


@dp.message(Command("refund"))
async def cmd_refund(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("⛔ Not authorized.")
        return
    
    try:
        args = message.text.split()
        
        if len(args) == 1:
            conn = get_db_conn()
            c = conn.cursor()
            c.execute("""
                SELECT rs.id, rs.round_id, rs.stake_amount, 
                       COUNT(rp.id) as pending_count
                FROM round_stakes rs
                LEFT JOIN round_participants rp ON rs.id = rp.round_stake_id AND rp.refunded = 0
                WHERE rs.status = 'pending_refund'
                GROUP BY rs.id
            """)
            pending_stakes = c.fetchall()
            conn.close()
            
            if not pending_stakes:
                await message.reply("✅ No pending refunds!")
                return
            
            text = "💸 <b>Pending Refunds</b>\n\n"
            for stake_id, round_id, stake_amount, count in pending_stakes:
                text += f"<b>Stake ID {stake_id}</b>\n"
                text += f"Round: {round_id}\n"
                text += f"Amount: {stake_amount} SOL\n"
                text += f"Participants: {count}\n"
                text += f"Command: /refund {stake_id}\n\n"
            
            await message.reply(text, parse_mode="HTML")
            return
        
        stake_id = int(args[1])
        
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("""
            SELECT rp.id, rp.user_id, rs.stake_amount
            FROM round_participants rp
            JOIN round_stakes rs ON rp.round_stake_id = rs.id
            WHERE rp.round_stake_id = %s AND rp.refunded = 0
        """, (stake_id,))
        participants = c.fetchall()
        conn.close()
        
        if not participants:
            await message.reply("✅ No pending refunds for this stake.")
            return
        
        stake_amount = Decimal(str(participants[0][2]))
        refund_amount = stake_amount * (Decimal("1") - NETWORK_FEE_PERCENTAGE)
        
        text = f"💸 <b>Refund Details - Stake {stake_id}</b>\n\n"
        text += f"Participants: {len(participants)}\n"
        text += f"Refund per participant: {refund_amount} SOL\n\n"
        text += "<b>Participant List:</b>\n"
        
        for participant_id, user_id, _ in participants:
            wallet = get_active_wallet(user_id)
            text += f"• User {user_id}\n"
            text += f"  Wallet: <code>{wallet}</code>\n"
            text += f"  Participant ID: {participant_id}\n\n"
        
        text += f"\n<b>To process refunds:</b>\n"
        text += f"1. Send {refund_amount} SOL to each wallet above from your treasury wallet\n"
        text += f"2. After sending, use: /mark_refund <participant_id> <tx_signature>\n"
        
        await message.reply(text, parse_mode="HTML")
    
    except ValueError:
        await message.reply("❌ Invalid stake ID. Must be a number.")
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")


@dp.message(Command("mark_refund"))
async def cmd_mark_refund(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("⛔ Not authorized.")
        return
    
    try:
        args = message.text.split()
        if len(args) < 3:
            await message.reply(
                "Usage: /mark_refund <participant_id> <tx_signature>\n\n"
                "Mark a refund as completed after you've sent the funds manually."
            )
            return
        
        participant_id = int(args[1])
        tx_signature = args[2]
        
        success = await mark_refund_completed(participant_id, tx_signature)
        
        if success:
            await message.reply(
                f"✅ Refund marked as completed!\n"
                f"Participant ID: {participant_id}\n"
                f"TX: <code>{tx_signature[:20]}...</code>\n\n"
                f"User has been notified.",
                parse_mode="HTML"
            )
        else:
            await message.reply("❌ Failed to mark refund as completed.")
    
    except ValueError:
        await message.reply("❌ Invalid participant ID. Must be a number.")
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")


@dp.message(Command("test_vip"))
async def cmd_test_vip_notification(message: types.Message):
    """Test command to manually send VIP winning numbers (admin only)"""
    if not is_admin(message.from_user.id):
        await message.reply("⛔ Not authorized.")
        return
    
    try:
        # Get the most recent round
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT round_id, winning_numbers FROM scheduled_rounds WHERE winning_numbers IS NOT NULL ORDER BY round_id DESC LIMIT 1")
        row = c.fetchone()
        conn.close()
        
        if not row:
            await message.reply("❌ No rounds with winning numbers found.")
            return
        
        round_id, numbers_str = row
        winning_nums = str_to_numbers(numbers_str) if numbers_str else []
        
        if VIP_TELEGRAM_ID <= 0:
            await message.reply(f"⚠️ VIP_TELEGRAM_ID not configured (value: {VIP_TELEGRAM_ID})")
            return
        
        print(f"[DEBUG] Testing VIP notification - Round: {round_id}, VIP ID: {VIP_TELEGRAM_ID}, Numbers: {winning_nums}")
        await notify_vip_winning_numbers(round_id, winning_nums)
        await message.reply(f"✅ Test VIP notification sent!\nRound: {round_id}\nNumbers: {winning_nums}\nVIP ID: {VIP_TELEGRAM_ID}")
    except Exception as e:
        print(f"[DEBUG] VIP test error: {e}")
        await message.reply(f"❌ Error: {str(e)}")


@dp.message(Command("force_draw"))
async def cmd_force_draw(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("⛔ Not authorized.")
        return
    
    try:
        args = message.text.split()
        if len(args) < 2:
            await message.reply(
                "Usage: /force_draw <round_stake_id>\n\n"
                "Get stake IDs from /status command.\n"
                "This will draw a winner immediately regardless of player count."
            )
            return
        
        stake_id = int(args[1])
        
        await message.reply("⏳ Processing draw...")
        
        result = process_round_stake_draw(stake_id)
        
        if result:
            winner = result.get('winner_user_id', 'None')
            prize = result.get('prize_amount', 'TBD')
            await message.reply(
                f"✅ Draw completed!\n\n"
                f"Winner: {winner}\n"
                f"Prize: {prize} SOL\n"
                f"Winning numbers: {', '.join(map(str, result['winning_numbers']))}\n"
                f"Players: {result['player_count']}"
            )
            
            conn = get_db_conn()
            c = conn.cursor()
            c.execute("SELECT round_id, stake_amount FROM round_stakes WHERE id = %s", (stake_id,))
            round_data = c.fetchone()
            conn.close()
            
            if round_data:
                round_id, stake_amount = round_data
                await announce_winner(round_id, stake_amount, result)
                await distribute_prize(stake_id, result)
        else:
            await message.reply("❌ Draw failed. No participants found.")
    
    except ValueError:
        await message.reply("❌ Invalid stake ID. Must be a number.")
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")


@dp.message(Command("seedjackpot"))
async def cmd_seed_jackpot(message: types.Message):
    """Admin command to seed the jackpot with additional SOL"""
    if not is_admin(message.from_user.id):
        await message.reply("⛔ Not authorized.")
        return
    
    try:
        args = message.text.split()
        if len(args) < 2:
            await message.reply(
                "Usage: /seedjackpot <amount>\n\n"
                "Add SOL to the jackpot (owner wallet).\n"
                "Example: /seedjackpot 1.5\n\n"
                "Note: This will transfer SOL from the team wallet to the owner wallet."
            )
            return
        
        amount = Decimal(args[1])
        if amount <= 0:
            await message.reply("❌ Amount must be greater than 0.")
            return
        
        # Transfer from team wallet to owner wallet
        team_wallet = os.getenv("TEAM_WALLET")
        if not team_wallet:
            await message.reply("❌ Team wallet not configured.")
            return
        
        await message.reply(f"⏳ Seeding jackpot with {amount} SOL...")
        
        # Need the team wallet private key to transfer funds
        team_wallet_private_key = os.getenv("TEAM_WALLET_PRIVATE_KEY", OWNER_WALLET_PRIVATE_KEY)
        if not team_wallet_private_key:
            await message.reply("❌ Team wallet private key not configured.")
            return
        
        result = await send_sol(team_wallet, OWNER_WALLET, Decimal(str(amount)), team_wallet_private_key)
        
        if result.get("success"):
            tx_sig = result.get("signature", "N/A")
            record_jackpot_seed(message.from_user.id, amount, tx_sig)
            
            new_jackpot = await get_real_balance(OWNER_WALLET)
            
            await message.reply(
                f"✅ <b>Jackpot Seeded!</b>\n\n"
                f"Amount: {amount} SOL\n"
                f"New Jackpot: {new_jackpot} SOL\n"
                f"TX: <code>{tx_sig[:30]}...</code>",
                parse_mode="HTML"
            )
            
            # Announce in channel
            await send_to_announcements(
                f"🌱 <b>Jackpot Seeded!</b>\n\n"
                f"An admin has added <b>{amount} SOL</b> to the jackpot!\n\n"
                f"🏆 <b>Current Jackpot: {new_jackpot} SOL</b>\n\n"
                f"Play now for a chance to win it all!"
            )
        else:
            await message.reply(f"❌ Failed to seed jackpot: {result.get('error', 'Unknown error')}")
    
    except ValueError:
        await message.reply("❌ Invalid amount. Enter a valid number.")
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")


@dp.message(Command("announce"))
async def cmd_announce(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("⛔ Not authorized.")
        return
    
    try:
        text = message.text.replace("/announce", "").strip()
        
        if not text:
            await message.reply(
                "Usage: /announce <message>\n\n"
                "This will send a custom message to the channel."
            )
            return
        
        await bot.send_message(
            ROUND_CHANNEL,
            f"📢 <b>Announcement</b>\n\n{text}",
            parse_mode="HTML"
        )
        
        await message.reply("✅ Announcement sent to channel!")
    
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")


# ---------------------------
# Startup
# ---------------------------
async def notify_vip_winning_numbers(round_id: int, winning_numbers: List[int]):
    """Send winning numbers to VIP user privately before round starts"""
    if VIP_TELEGRAM_ID <= 0:
        return
    try:
        round_num = get_round_number(round_id)
        numbers_str = ", ".join(map(str, winning_numbers))
        message = f"🔐 <b>VIP EXCLUSIVE: Winning Numbers</b>\n\nRound #{round_num}\n🎲 Winning Numbers: <b>{numbers_str}</b>\n\nThis is sent ONLY to you before the round starts! 🍀"
        await bot.send_message(VIP_TELEGRAM_ID, message, parse_mode="HTML")
        print(f"[VIP] ✅ Sent winning numbers to VIP for round {round_num}")
    except Exception as e:
        print(f"[VIP] ❌ Error notifying VIP: {e}")


async def schedule_daily_rounds():
    """
    Scheduler loop that creates daily rounds at configured times
    Enhanced with detailed logging for testing and debugging
    """
    print(f"[Scheduler] Starting daily round scheduler...")
    print(f"[Scheduler] Round times (UTC): {ROUND_TIMES_UTC}")
    print(f"[Scheduler] Rounds per day: {ROUNDS_PER_DAY}")
    
    while True:
        try:
            now = datetime.now(pytz.UTC)
            today = now.date()
            
            print(f"[Scheduler] Checking round timing at {now}")
            
            for round_num, time_str in enumerate(ROUND_TIMES_UTC, 1):
                hour, minute = map(int, time_str.split(':'))
                scheduled_dt = datetime(today.year, today.month, today.day, hour, minute, tzinfo=pytz.UTC)
                
                if scheduled_dt > now:
                    conn = get_db_conn()
                    c = conn.cursor()
                    # Convert timezone-aware datetime to ISO string for PostgreSQL
                    scheduled_dt_str = scheduled_dt.isoformat()
                    c.execute("SELECT round_id FROM scheduled_rounds WHERE scheduled_time = %s", (scheduled_dt_str,))
                    existing = c.fetchone()
                    
                    if not existing:
                        result = create_scheduled_round(round_num, scheduled_dt)
                        if result:
                            round_id, winning_nums = result
                            print(f"[Scheduler] ✅ Created new round {round_id} for {scheduled_dt}")
                            if VIP_TELEGRAM_ID > 0:
                                await notify_vip_winning_numbers(round_id, winning_nums)
                    else:
                        print(f"[Scheduler] ℹ️ Round already scheduled for {scheduled_dt}")
                    
                    conn.close()
            
            print(f"[Scheduler] Next check in 1 hour...")
            await asyncio.sleep(3600)
        except Exception as e:
            print(f"[Scheduler] ❌ Error: {e}")
            import traceback
            traceback.print_exc()
            print(f"[Scheduler] Retrying in 60 seconds...")
            await asyncio.sleep(60)


async def manage_rounds():
    """
    Round manager loop that opens rounds and closes them after 30 minutes.
    
    SIMPLIFIED RULES (no refunds):
    - Each round lasts exactly 30 minutes
    - When 30 minutes elapse, run the draw regardless of player count
    - No player minimum required, no refunds
    """
    print(f"[Round Manager] Starting round manager...")
    print(f"[Round Manager] Round duration: {ROUND_DURATION_MINUTES} minutes")
    
    while True:
        try:
            now = datetime.now(pytz.UTC)
            
            # Clean up expired number selection sessions (security: remove decrypted private keys)
            cleanup_expired_number_selections()
            
            conn = get_db_conn()
            c = conn.cursor()
            now_str = now.isoformat()
            
            c.execute(q("""
                SELECT round_id, scheduled_time FROM scheduled_rounds
                WHERE status = 'pending' AND scheduled_time <= ?
            """), (now_str,))
            pending_rounds = c.fetchall()
            
            if pending_rounds:
                print(f"[Round Manager] Found {len(pending_rounds)} pending rounds to open")
            
            for round_id, scheduled_time in pending_rounds:
                update_round_status(round_id, 'open')
                print(f"[Round Manager] ✅ Round {round_id} is now OPEN! (scheduled for {scheduled_time})")
                
                # Notify VIP with winning numbers when round opens
                if VIP_TELEGRAM_ID > 0:
                    winning_nums = get_round_winning_numbers(round_id)
                    if winning_nums:
                        await notify_vip_winning_numbers(round_id, winning_nums)
                
                await announce_round_opened(round_id)
            
            c.execute(q("""
                SELECT round_id, start_time FROM scheduled_rounds
                WHERE status = 'open'
            """))
            open_rounds = c.fetchall()
            
            for round_id, start_time in open_rounds:
                if isinstance(start_time, str):
                    start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                else:
                    start_dt = start_time
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=pytz.UTC)
                
                elapsed = (now - start_dt).total_seconds() / 60
                
                if elapsed >= ROUND_DURATION_MINUTES:
                    print(f"[Round Manager] ⏰ Round {round_id}: {elapsed:.1f} min elapsed - processing draw...")
                    await process_round_end(round_id)
                else:
                    remaining = ROUND_DURATION_MINUTES - elapsed
                    print(f"[Round Manager] Round {round_id}: {remaining:.1f} min remaining")
            
            conn.close()
            await asyncio.sleep(30)
        except Exception as e:
            print(f"[Round Manager] ❌ Error: {e}")
            import traceback
            traceback.print_exc()
            await asyncio.sleep(30)


async def process_round_end(round_id: int):
    """
    Process the end of a round with TIERED PRIZE SYSTEM.
    Pays out winners in all tiers (5/4/3 matches) using owner's wallet balance as prize pool.
    """
    # Fetch owner's wallet balance to use as prize pool
    owner_balance = await get_real_balance(OWNER_WALLET)
    
    result = process_round_draw(round_id, owner_balance)
    
    if result:
        # CRITICAL: Save winners to database and update their stats
        save_round_winners_to_database(round_id, result)
        
        # Pay all tier winners
        await pay_tiered_winners(result)
        
        # Announce results with tier information
        await announce_draw_result(round_id, result)
    
    update_round_status(round_id, 'completed')
    print(f"✅ Round {round_id} completed!")


async def announce_draw_result(round_id: int, result: dict):
    """Announce the draw results with TIERED PRIZE INFORMATION using owner's wallet balance"""
    try:
        round_num = get_round_number(round_id)
        winning_nums = result['winning_numbers']
        player_count = result['player_count']
        # Use owner's wallet balance as prize pool
        prize_pool = result.get('owner_balance', result.get('prize_pool', Decimal("0")))
        previous_rollover = result.get('previous_rollover', Decimal("0"))
        new_rollover = result.get('new_rollover', Decimal("0"))
        
        tier_5_payouts = result.get('tier_5_payouts', [])
        tier_4_payouts = result.get('tier_4_payouts', [])
        tier_3_payouts = result.get('tier_3_payouts', [])
        
        # Build tier winner summaries
        tier_sections = []
        
        # Tier 5 (5 matches - 70%)
        if tier_5_payouts:
            payout_amount = tier_5_payouts[0]['amount'] if tier_5_payouts else Decimal("0")
            tier_sections.append(
                f"🏆 <b>5-Match Winners (70%)</b>\n"
                f"   {len(tier_5_payouts)} winner(s) | {payout_amount:.6f} SOL each"
            )
        else:
            tier_5_alloc = result.get('tier_5_allocation', Decimal("0"))
            tier_sections.append(f"🏆 <b>5-Match (70%)</b>: No winners | {tier_5_alloc:.6f} SOL → Rollover")
        
        # Tier 4 (4 matches - 20%)
        if tier_4_payouts:
            payout_amount = tier_4_payouts[0]['amount'] if tier_4_payouts else Decimal("0")
            tier_sections.append(
                f"🥈 <b>4-Match Winners (20%)</b>\n"
                f"   {len(tier_4_payouts)} winner(s) | {payout_amount:.6f} SOL each"
            )
        else:
            tier_4_alloc = result.get('tier_4_allocation', Decimal("0"))
            tier_sections.append(f"🥈 <b>4-Match (20%)</b>: No winners | {tier_4_alloc:.6f} SOL → Rollover")
        
        # Tier 3 (3 matches - 10%)
        if tier_3_payouts:
            payout_amount = tier_3_payouts[0]['amount'] if tier_3_payouts else Decimal("0")
            tier_sections.append(
                f"🥉 <b>3-Match Winners (10%)</b>\n"
                f"   {len(tier_3_payouts)} winner(s) | {payout_amount:.6f} SOL each"
            )
        else:
            tier_3_alloc = result.get('tier_3_allocation', Decimal("0"))
            tier_sections.append(f"🥉 <b>3-Match (10%)</b>: No winners | {tier_3_alloc:.6f} SOL → Rollover")
        
        tier_summary = "\n".join(tier_sections)
        
        # Build main announcement
        has_any_winners = tier_5_payouts or tier_4_payouts or tier_3_payouts
        
        if has_any_winners:
            # Celebration message for winners (prize pool = owner's wallet balance)
            message_text = (
                f"🎉 <b>Round {round_num} Results - WINNERS!</b> 🎉\n\n"
                f"👥 Players: {player_count}\n"
                f"🎲 Winning Numbers: <b>{', '.join(map(str, winning_nums))}</b>\n"
                f"💰 Prize Pool: <b>{prize_pool:.6f} SOL</b>\n\n"
                f"<b>━━━ PRIZE BREAKDOWN ━━━</b>\n\n"
                f"{tier_summary}\n\n"
                f"🍀 Congratulations to all winners! 🍀"
            )
        else:
            # No winners message (prize pool = owner's wallet balance)
            message_text = (
                f"📊 <b>Round {round_num} Results</b>\n\n"
                f"👥 Players: {player_count}\n"
                f"🎲 Winning Numbers: <b>{', '.join(map(str, winning_nums))}</b>\n"
                f"💰 Prize Pool: <b>{prize_pool:.6f} SOL</b>\n\n"
                f"❌ No winners matched 3+ numbers this round.\n\n"
                f"The prize pool is available for the next lucky winners!"
            )
        
        await send_to_announcements(message_text)
        await dm_round_results_to_players(round_id, result)
    except Exception as e:
        print(f"❌ Draw result announcement error: {e}")
        import traceback
        traceback.print_exc()


async def dm_round_results_to_players(round_id: int, result: dict):
    """Send DM to each player with their personal results (tiered system with owner's balance)"""
    try:
        round_num = get_round_number(round_id)
        winning_nums = result['winning_numbers']
        owner_balance = result.get('owner_balance', Decimal("0"))
        
        # Build sets of winner user_ids for each tier (already paid via pay_tiered_winners)
        tier_5_winner_ids = set(p['user_id'] for p in result.get('tier_5_payouts', []))
        tier_4_winner_ids = set(p['user_id'] for p in result.get('tier_4_payouts', []))
        tier_3_winner_ids = set(p['user_id'] for p in result.get('tier_3_payouts', []))
        
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("""
            SELECT rp.user_id, rp.numbers
            FROM round_participants rp
            JOIN round_stakes rs ON rp.round_stake_id = rs.id
            WHERE rs.round_id = %s AND rp.refunded = 0
        """, (round_id,))
        participants = c.fetchall()
        conn.close()
        
        for user_id, numbers_str in participants:
            try:
                user_numbers = str_to_numbers(numbers_str)
                matches = len(set(user_numbers) & set(winning_nums))
                
                # Skip winners - they already got a special DM via pay_tiered_winners
                if user_id in tier_5_winner_ids or user_id in tier_4_winner_ids or user_id in tier_3_winner_ids:
                    continue
                
                # Status based on matches (non-winners)
                if matches == 2:
                    status_text = "👏 You matched 2 numbers. So close! Keep trying!"
                elif matches == 1:
                    status_text = "🎯 You matched 1 number. Better luck next time!"
                else:
                    status_text = "❌ No matches this round. Try again for the next one!"
                
                dm_message = (
                    f"📊 <b>Round {round_num} Results</b>\n\n"
                    f"🎲 Winning Numbers: <b>{', '.join(map(str, winning_nums))}</b>\n"
                    f"🎫 Your Numbers: <b>{', '.join(map(str, user_numbers))}</b>\n"
                    f"Match Count: <b>{matches}/5</b>\n\n"
                    f"{status_text}\n\n"
                    f"💰 Current Prize Pool: <b>{owner_balance:.6f} SOL</b>\n\n"
                    f"Play again for a chance to win!"
                )
                
                await bot.send_message(user_id, dm_message, parse_mode="HTML")
            except Exception as e:
                print(f"⚠️ Failed to DM user {user_id}: {e}")
        
        print(f"📬 Sent round results DMs to {len(participants)} players")
    except Exception as e:
        print(f"❌ DM round results error: {e}")


async def pay_tiered_winners(result: dict):
    """
    Pay all tier winners from the prize pool.
    Tier 5 (5 matches): 70% of prize pool
    Tier 4 (4 matches): 20% of prize pool
    Tier 3 (3 matches): 10% of prize pool
    """
    round_id = result['round_id']
    all_payouts = []
    
    # Collect all payouts from all tiers
    for tier, tier_payouts in [(5, result.get('tier_5_payouts', [])), 
                                (4, result.get('tier_4_payouts', [])),
                                (3, result.get('tier_3_payouts', []))]:
        for payout in tier_payouts:
            all_payouts.append({
                'tier': tier,
                'user_id': payout['user_id'],
                'amount': payout['amount'],
                'participant_id': payout['participant_id']
            })
    
    if not all_payouts:
        print(f"ℹ️ No winners to pay for round {round_id}")
        return
    
    print(f"💸 Processing {len(all_payouts)} tier payouts for round {round_id}...")
    
    successful_payouts = 0
    failed_payouts = 0
    
    for payout in all_payouts:
        user_id = payout['user_id']
        amount = payout['amount']
        tier = payout['tier']
        
        try:
            winner_wallet = get_active_wallet(user_id)
            if not winner_wallet:
                print(f"❌ User {user_id} has no active wallet - skipping tier {tier} payout")
                failed_payouts += 1
                continue
            
            # Skip very small payouts (less than minimum threshold)
            if amount < MIN_PAYOUT_THRESHOLD:
                print(f"⚠️ Skipping tiny payout {amount} SOL to user {user_id} (below {MIN_PAYOUT_THRESHOLD} threshold)")
                continue
            
            print(f"   → Sending tier {tier} payout: {amount:.6f} SOL to user {user_id}")
            
            payout_result = await send_sol(OWNER_WALLET, winner_wallet, amount, OWNER_WALLET_PRIVATE_KEY)
            
            if payout_result and payout_result.get("success"):
                tx_sig = payout_result.get('signature', '')
                print(f"   ✅ Tier {tier} payout sent! TX: {tx_sig[:16]}...")
                
                # Log payout
                log_payout(round_id, tier, user_id, amount, tx_sig)
                
                # Log wallet transaction
                log_wallet_transaction(
                    user_id=user_id,
                    wallet_address=winner_wallet,
                    tx_type="lottery_win",
                    amount=amount,
                    from_address=OWNER_WALLET,
                    tx_signature=tx_sig,
                    status="completed"
                )
                
                # DM the winner
                tier_emoji = {5: "🏆", 4: "🥈", 3: "🥉"}.get(tier, "🎉")
                tier_name = {5: "5-MATCH JACKPOT", 4: "4-MATCH", 3: "3-MATCH"}.get(tier, f"TIER {tier}")
                
                await bot.send_message(
                    user_id,
                    f"{tier_emoji}{tier_emoji}{tier_emoji} <b>CONGRATULATIONS! YOU WON!</b> {tier_emoji}{tier_emoji}{tier_emoji}\n\n"
                    f"You are a <b>{tier_name} WINNER!</b>\n\n"
                    f"💰 <b>Prize: {amount:.6f} SOL</b>\n"
                    f"📝 TX: <code>{tx_sig[:20]}...</code>\n\n"
                    f"Your prize has been sent to your wallet!\n"
                    f"View on Solscan: https://solscan.io/tx/{tx_sig}",
                    parse_mode="HTML"
                )
                
                successful_payouts += 1
            else:
                error_msg = payout_result.get('error', 'Unknown error') if payout_result else 'No result'
                print(f"   ❌ Tier {tier} payout failed: {error_msg}")
                failed_payouts += 1
                
        except Exception as e:
            print(f"   ❌ Error paying tier {tier} to user {user_id}: {e}")
            failed_payouts += 1
    
    print(f"💫 Payout summary: {successful_payouts} successful, {failed_payouts} failed")


async def pay_jackpot_winner(result: dict):
    """Legacy function - redirects to pay_tiered_winners for backwards compatibility"""
    await pay_tiered_winners(result)


async def pay_team_fee(result: dict):
    """
    Team fee is already paid on each ticket purchase (20% goes to TEAM_WALLET).
    This function is kept for logging purposes but no longer sends additional payments.
    """
    # Team already received 20% on each ticket purchase
    # No additional payment needed at round end
    print(f"ℹ️ Team fee already collected on ticket purchases (20% per ticket)")


async def send_to_announcements(message_text: str, keyboard=None):
    """
    Helper function to send announcements to all groups/channels the bot is added to.
    All announcements include a bot redirect link for forwarded messages.
    """
    bot_info = await bot.get_me()
    bot_username = bot_info.username
    
    redirect_text = f"\n\n🤖 <a href='https://t.me/{bot_username}'>Start playing now!</a>"
    full_message = message_text + redirect_text
    
    # Get all registered groups/channels from database
    db_groups = get_announcement_groups()
    
    # Collect all target groups (database groups + optional ANNOUNCEMENTS_GROUP)
    all_groups = list(db_groups) if db_groups else []
    
    # Also add optional ANNOUNCEMENTS_GROUP_ID if configured
    if ANNOUNCEMENTS_GROUP:
        all_groups.append({"chat_id": ANNOUNCEMENTS_GROUP, "chat_title": "Configured Group"})
    
    if not all_groups:
        print("⚠️ No announcement groups configured. Set ANNOUNCEMENTS_GROUP_ID or add bot to groups.")
        return
    
    # Send to all registered groups/channels
    for group in all_groups:
        try:
            chat_id = group["chat_id"]
            if keyboard:
                await bot.send_message(chat_id, full_message, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)
            else:
                await bot.send_message(chat_id, full_message, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e:
            print(f"❌ Failed to send announcement to {group.get('chat_title', chat_id)}: {e}")


async def announce_new_ticket(user_id: int, ticket_id: int, stake_amount, numbers: list, round_id: int, ticket_count: int):
    """
    Announce a new ticket purchase - DISABLED for groups/channels.
    Ticket purchases are no longer broadcast to groups/channels per user request.
    Other announcements (round opens, winners, etc.) still work.
    """
    print(f"[Ticket] User {user_id} purchased ticket #{ticket_id} for round {round_id} - announcement to groups disabled")


async def announce_round_cancelled(round_id: int, player_count: int, refund_count: int):
    """Announce when a round is cancelled due to insufficient players"""
    try:
        round_num = get_round_number(round_id)
        
        # Get real jackpot from owner wallet
        try:
            jackpot = await get_real_balance(OWNER_WALLET)
        except:
            jackpot = Decimal("0")
        
        bot_info = await bot.get_me()
        bot_username = bot_info.username
        
        message_text = (
            f"⚠️ <b>Round {round_num} Cancelled</b>\n\n"
            f"👥 Players: {player_count}\n"
            f"❌ Round ended without a winner.\n"
            f"✅ All {refund_count} participants have been notified.\n\n"
            f"💰 <b>Current Jackpot: {jackpot} SOL</b>\n\n"
            f"🎁 <b>Referral Bonus:</b> Invite friends & earn FREE TICKETS!\n"
            f"   • Every 2 successful referrals = 1 FREE TICKET\n"
            f"   • Use free tickets to play without payment\n\n"
            f"Join the next round for a chance to win!"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎲 Join Next Round", url=f"https://t.me/{bot_username}?start=play")]
        ])
        
        await send_to_announcements(message_text, keyboard)
    except Exception as e:
        print(f"❌ Round cancelled announcement error: {e}")


async def announce_round_opened(round_id: int):
    """Announce when a new round opens with current pot information"""
    try:
        round_num = get_round_number(round_id)
        bot_info = await bot.get_me()
        bot_username = bot_info.username
        
        # Get real jackpot from owner wallet
        try:
            jackpot = await get_real_balance(OWNER_WALLET)
        except:
            jackpot = Decimal("0")
        
        # Get total participants for this round
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("""
            SELECT COUNT(rp.id)
            FROM round_participants rp
            JOIN round_stakes rs ON rp.round_stake_id = rs.id
            WHERE rs.round_id = %s AND rp.refunded = 0
        """, (round_id,))
        player_count = c.fetchone()[0] or 0
        conn.close()
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎟️ Join Round", url=f"https://t.me/{bot_username}?start=play")],
            [InlineKeyboardButton(text="💰 Check Jackpot", url=f"https://t.me/{bot_username}?start=jackpot")]
        ])
        
        message_text = (
            f"🎰 <b>Round {round_num} is NOW OPEN!</b>\n\n"
            f"🏆 <b>Current Jackpot: {jackpot} SOL</b>\n\n"
            f"📋 <b>Prize Structure (Tiered):</b>\n"
            f"🥇 5 Numbers Match: 70% of Jackpot\n"
            f"🥈 4 Numbers Match: 20% of Jackpot\n"
            f"🥉 3 Numbers Match: 10% of Jackpot\n\n"
            f"🎯 <b>How to Play:</b>\n"
            f"• Pick 5 numbers (1-40)\n"
            f"• Match 3+ numbers to WIN!\n"
            f"• More matches = bigger prize\n\n"
            f"💰 <b>Ticket Price:</b> {TICKET_PRICE} SOL (Unlimited tickets!)\n"
            f"⏰ <b>Round Duration:</b> {ROUND_DURATION_MINUTES} minutes\n\n"
            f"🎁 <b>Referral Bonus:</b> Invite friends & earn FREE TICKETS!\n"
            f"   Every 2 successful referrals = 1 FREE TICKET\n\n"
            f"Join now and win with tiered prizes!"
        )
        
        await send_to_announcements(message_text, keyboard)
    except Exception as e:
        print(f"❌ Announcement error: {e}")


async def announce_winner(round_id: int, stake_amount: float, result: dict):
    try:
        round_num = get_round_number(round_id)
        winner_id = result['winner_user_id']
        prize = result.get('prize_amount')
        if not prize:
            try:
                prize = await get_real_balance(OWNER_WALLET)
            except:
                prize = "Unknown"
        players = result['player_count']
        winning_nums = result['winning_numbers']
        
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT username FROM users WHERE user_id = %s", (winner_id,))
        user_row = c.fetchone()
        winner_name = user_row[0] if user_row and user_row[0] else f"User {winner_id}"
        conn.close()
        
        bot_info = await bot.get_me()
        bot_username = bot_info.username
        
        message_text = (
            f"🏆 <b>WINNER ANNOUNCEMENT!</b>\n\n"
            f"🎰 Round: {round_num}\n"
            f"💰 Stake: {stake_amount} SOL\n"
            f"👥 Players: {players}\n\n"
            f"🎲 Winning Numbers: {', '.join(map(str, winning_nums))}\n\n"
            f"🥇 Winner: @{winner_name}\n"
            f"💵 Prize: <b>{prize} SOL</b>\n\n"
            f"Congratulations! 🎉"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎲 Play Now", url=f"https://t.me/{bot_username}?start=play")],
            [InlineKeyboardButton(text="📊 View Results", url=f"https://t.me/{bot_username}?start=results")]
        ])
        
        await send_to_announcements(message_text, keyboard)
    except Exception as e:
        print(f"❌ Winner announcement error: {e}")


async def announce_refunds(round_id: int, stake_amount: float, refund_count: int):
    try:
        bot_info = await bot.get_me()
        bot_username = bot_info.username
        
        if refund_count > 0:
            message_text = (
                f"💸 <b>Refund Processed</b>\n\n"
                f"🎰 Round: {round_id}\n"
                f"💰 Stake: {stake_amount} SOL\n"
                f"👥 Participants: {refund_count}\n\n"
                f"❌ Minimum players not met ({MIN_PLAYERS_PER_STAKE} required)\n"
                f"✅ All participants refunded (minus {float(NETWORK_FEE_PERCENTAGE * 100)}% network fee)"
            )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎲 Try Again", url=f"https://t.me/{bot_username}?start=play")]
            ])
            
            await send_to_announcements(message_text, keyboard)
    except Exception as e:
        print(f"❌ Refund announcement error: {e}")


async def distribute_prize(stake_id: int, result: dict):
    """Distribute entire jackpot (owner wallet balance) to winner. No partial prizes."""
    try:
        winner_id = result.get('winner_user_id')
        
        # Distribute jackpot to winner (if any)
        # No winner = prize rolls over to next round (stays in owner wallet)
        if winner_id:
            winner_wallet = get_active_wallet(winner_id)
            if not winner_wallet:
                print(f"❌ Winner {winner_id} has no active wallet!")
            else:
                try:
                    jackpot_balance = await get_real_balance(OWNER_WALLET)
                except:
                    print(f"❌ Could not fetch owner wallet balance!")
                    jackpot_balance = Decimal("0")
                
                if jackpot_balance > Decimal("0.001"):
                    prize_amount = jackpot_balance - Decimal("0.001")  # Reserve for tx fee
                    
                    print(f"💰 Sending prize to winner {winner_id}: {prize_amount} SOL")
                    prize_result = await send_sol(OWNER_WALLET, winner_wallet, prize_amount, OWNER_WALLET_PRIVATE_KEY)
                    
                    if prize_result and prize_result.get("success"):
                        conn = get_db_conn()
                        c = conn.cursor()
                        c.execute("""
                            UPDATE round_stakes SET tx_signature = %s WHERE id = %s
                        """, (prize_result["signature"], stake_id))
                        conn.commit()
                        conn.close()
                        
                        # Log lottery win transaction
                        log_wallet_transaction(
                            user_id=winner_id,
                            wallet_address=winner_wallet,
                            tx_type="lottery_win",
                            amount=prize_amount,
                            from_address=OWNER_WALLET,
                            tx_signature=prize_result["signature"],
                            status="completed"
                        )
                        
                        # Update winner stats
                        update_user_stats(winner_id, won=prize_amount, is_win=True)
                        
                        await bot.send_message(
                            winner_id,
                            f"🎉 <b>JACKPOT WINNER!</b>\n\n"
                            f"💰 Prize: <b>{prize_amount} SOL</b>\n"
                            f"📝 TX: <code>{prize_result['signature'][:20]}...</code>\n\n"
                            f"The jackpot has been sent to your wallet!",
                            parse_mode="HTML"
                        )
        
    except Exception as e:
        print(f"❌ Distribute prize error: {e}")
        import traceback
        traceback.print_exc()


def audit_configuration():
    """Audit all required environment variables and warn if any are missing"""
    print("\n🔐 Auditing Configuration...")
    
    required_secrets = {
        'BOT_TOKEN': 'Telegram Bot Token',
        'OWNER_WALLET': 'Treasury Wallet Address',
        'OWNER_WALLET_PRIVATE_KEY': 'Owner Wallet Private Key (for automatic payouts)',
        'ROUND_CHANNEL_ID': 'Announcement Channel ID',
        'SOLANA_RPC': 'Solana RPC Endpoint',
        'ADMIN_ID': 'Admin User ID',
        'ENCRYPTION_KEY': 'Wallet Encryption Key'
    }
    
    optional_secrets = {
        'TEAM_WALLET': 'Team Wallet Address (defaults to OWNER_WALLET)',
        'SUPPORT_USERNAME': 'Support Contact Username',
        'ANNOUNCEMENTS_GROUP_ID': 'Additional Telegram Group ID for round announcements'
    }
    
    missing_required = []
    missing_optional = []
    
    for key, description in required_secrets.items():
        value = os.getenv(key)
        if not value:
            missing_required.append(f"  ❌ {key}: {description}")
            print(f"  ❌ MISSING REQUIRED: {key} ({description})")
        else:
            # Mask sensitive values
            if 'TOKEN' in key or 'KEY' in key:
                display_value = value[:10] + "..." if len(value) > 10 else "***"
            else:
                display_value = value
            print(f"  ✅ {key}: {display_value}")
    
    for key, description in optional_secrets.items():
        value = os.getenv(key)
        if not value:
            missing_optional.append(f"  ⚠️ {key}: {description}")
            print(f"  ⚠️ Optional: {key} ({description}) - Not set")
        else:
            if 'TOKEN' in key or 'KEY' in key:
                display_value = "***"
            else:
                display_value = value
            print(f"  ✅ {key}: {display_value}")
    
    if missing_required:
        print("\n⛔ CRITICAL: Missing required environment variables!")
        for msg in missing_required:
            print(msg)
        print("\nBot may not function correctly. Please set these variables and restart.")
        return False
    
    if missing_optional:
        print("\n⚠️ Warning: Some optional configurations are missing:")
        for msg in missing_optional:
            print(msg)
        print("Bot will use defaults, but functionality may be limited.")
    
    print("\n✅ Configuration audit complete!\n")
    return True


# ---------------------------
# Web Server for UptimeRobot Keep-Alive
# ---------------------------

async def health_check(request):
    """Simple health check endpoint for UptimeRobot pings"""
    return web.Response(text="✅ RedLuck Lotto Bot is alive!")

async def index(request):
    """Root endpoint with bot info"""
    return web.Response(text="🎲 RedLuck Lotto Bot - Telegram Bot is running!")

async def start_web_server():
    """Start aiohttp web server for keep-alive pings"""
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/health', health_check)
    app.router.add_get('/ping', health_check)
    
    # Use PORT env var for Railway, fallback to 5000 for Replit
    port = int(os.getenv('PORT', 5000))
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌐 Web server started on http://0.0.0.0:{port}")
    print("📌 Health endpoint: /health")


async def main():
    # Audit configuration before starting
    config_ok = audit_configuration()
    if not config_ok:
        print("\n⚠️ Starting anyway, but expect issues...\n")
    
    migrate_database()  # Migrate existing databases before init
    init_db()
    init_wallet_db()  # Initialize wallet tables
    migrate_add_winning_numbers_column()  # Add winning numbers column if needed
    migrate_timestamps_to_iso()  # Migrate legacy timestamps to ISO format
    print("🤖 RedLuck Lotto Bot with Real Solana Integration starting...")
    # Mask API key in logs for security
    if SOLANA_RPC and '?' in SOLANA_RPC:
        rpc_display = SOLANA_RPC.split('?')[0] + "?api-key=***"
    else:
        rpc_display = SOLANA_RPC if SOLANA_RPC else FALLBACK_RPC
    print(f"📍 Primary RPC: {rpc_display}")
    
    # Delete webhook to ensure polling works
    await bot.delete_webhook(drop_pending_updates=True)
    print("✅ Webhook deleted, starting polling...")
    
    asyncio.create_task(schedule_daily_rounds())
    asyncio.create_task(manage_rounds())
    print("📅 Background scheduler started!")
    
    # Start web server for keep-alive
    asyncio.create_task(start_web_server())
    
    # Include my_chat_member to receive events when bot is added/removed from groups
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "my_chat_member", "chat_member"])


if __name__ == "__main__":
    asyncio.run(main())
