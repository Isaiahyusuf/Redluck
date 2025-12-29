"""
PostgreSQL-only database layer for RedLuck Lotto.

SQLite fully removed. PostgreSQL only.

This module connects exclusively via DATABASE_URL environment variable.
No local file-based database. No SQLite fallback.
"""

import os
from contextlib import contextmanager
import threading
import hashlib

# ==============================================================================
# POSTGRESQL ONLY - NO SQLITE
# ==============================================================================
# SQLite fully removed. PostgreSQL only.
# This bot requires DATABASE_URL to be set. No fallback to local files.

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("RAILWAY_DATABASE_URL")

if not DATABASE_URL:
    raise ValueError(
        "DATABASE_URL environment variable is required. "
        "PostgreSQL is mandatory - no SQLite fallback."
    )

# Validate DATABASE_URL format
if not (DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://")):
    raise ValueError(
        f"DATABASE_URL must start with postgresql:// or postgres://. "
        f"Got: {DATABASE_URL[:20]}..."
    )

# PostgreSQL only - always True
USE_POSTGRES = True

# Import PostgreSQL driver
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

# Print connection info (hide password)
safe_url = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else "configured"
print(f"[Database] PostgreSQL only mode: ...@{safe_url}")
print("[Database] SQLite fully removed. PostgreSQL only.")

# Connection pool for PostgreSQL
_pg_pool = None
_pool_lock = threading.Lock()

# Flag to track if constraint has been fixed
_constraint_fixed = False


def init_pg_pool():
    """Initialize PostgreSQL connection pool"""
    global _pg_pool
    if _pg_pool is None:
        with _pool_lock:
            if _pg_pool is None:
                try:
                    _pg_pool = pool.ThreadedConnectionPool(
                        minconn=2,
                        maxconn=10,
                        dsn=DATABASE_URL
                    )
                    print("[Database] Connection pool initialized (2-10 connections)")
                except Exception as e:
                    print(f"[Database] Failed to create pool: {e}")
                    _pg_pool = None


class PostgresCursor:
    """Wrapper that handles query placeholders for PostgreSQL"""
    def __init__(self, cursor):
        self._cursor = cursor
    
    def execute(self, query, params=None):
        # Convert ? placeholders to %s for PostgreSQL
        converted = query.replace("?", "%s")
        if params:
            return self._cursor.execute(converted, params)
        return self._cursor.execute(converted)
    
    def fetchone(self):
        return self._cursor.fetchone()
    
    def fetchall(self):
        return self._cursor.fetchall()
    
    @property
    def lastrowid(self):
        return None  # PostgreSQL doesn't have lastrowid
    
    @property
    def rowcount(self):
        return self._cursor.rowcount
    
    @property
    def description(self):
        return self._cursor.description


class PostgresConnection:
    """Wrapper for psycopg2 connection"""
    def __init__(self, conn, from_pool=False):
        self.conn = conn
        self._from_pool = from_pool
    
    def cursor(self):
        return PostgresCursor(self.conn.cursor())
    
    def commit(self):
        self.conn.commit()
    
    def rollback(self):
        self.conn.rollback()
    
    def close(self):
        if self._from_pool and _pg_pool:
            try:
                _pg_pool.putconn(self.conn)
            except:
                self.conn.close()
        else:
            self.conn.close()
    
    @property
    def rowcount(self):
        return self.conn.cursor().rowcount
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def get_db_conn():
    """Get PostgreSQL database connection from pool"""
    # Try to use pool if available
    if _pg_pool:
        try:
            conn = _pg_pool.getconn()
            return PostgresConnection(conn, from_pool=True)
        except Exception as e:
            print(f"[Database] Pool error, using direct connection: {e}")
    
    # Fallback to direct connection
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return PostgresConnection(conn, from_pool=False)
    except Exception as e:
        print(f"[Database] PostgreSQL connection error: {e}")
        raise


def q(query: str) -> str:
    """
    Convert query placeholders for PostgreSQL.
    Handles: ? placeholders, INSERT OR IGNORE, INSERT OR REPLACE
    
    SQLite fully removed. PostgreSQL only.
    """
    result = query.replace("?", "%s")
    
    # Handle INSERT OR IGNORE -> INSERT ... ON CONFLICT DO NOTHING
    if "INSERT OR IGNORE" in result.upper():
        result = result.replace("INSERT OR IGNORE", "INSERT")
        result = result.replace("insert or ignore", "INSERT")
        if "ON CONFLICT" not in result.upper():
            result = result.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    
    # Handle INSERT OR REPLACE
    if "INSERT OR REPLACE" in result.upper():
        result = result.replace("INSERT OR REPLACE", "INSERT")
        result = result.replace("insert or replace", "INSERT")
        if "ON CONFLICT" not in result.upper():
            result = result.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    
    return result


def insert_ignore(table: str, columns: list, values: tuple) -> str:
    """Generate INSERT ON CONFLICT query for PostgreSQL"""
    cols = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(values))
    return f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"


def drop_all_tables():
    """
    Drop all existing tables for a clean reset.
    WARNING: This deletes all data permanently.
    """
    print("[Database] Dropping all tables for clean reset...")
    conn = get_db_conn()
    c = conn.cursor()
    
    tables = [
        'announcement_groups',
        'security_questions',
        'email_verification_codes',
        'user_emails',
        'wallet_transactions',
        'jackpot_seeds',
        'draw_history',
        'referrals',
        'user_stats',
        'round_participants',
        'round_stakes',
        'scheduled_rounds',
        'entries',
        'users',
        'meta',
        'user_pins',
        'user_active_wallet',
        'wallets'
    ]
    
    for table in tables:
        try:
            c.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            print(f"[Database] Dropped table: {table}")
        except Exception as e:
            print(f"[Database] Could not drop {table}: {e}")
    
    conn.commit()
    conn.close()
    print("[Database] All tables dropped")


def init_all_tables():
    """
    Initialize all database tables with PostgreSQL syntax.
    
    SQLite fully removed. PostgreSQL only.
    
    IMPORTANT: round_participants table has NO UNIQUE constraint on (user_id, round_stake_id)
    to allow users to buy multiple tickets per round. Each ticket is a separate row.
    """
    # Initialize connection pool
    init_pg_pool()
    
    conn = get_db_conn()
    c = conn.cursor()
    
    # Wallets table
    c.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            wallet_address TEXT NOT NULL,
            wallet_type TEXT NOT NULL,
            wallet_name TEXT,
            private_key TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, wallet_address)
        )
    """)
    
    # User active wallet
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_active_wallet (
            user_id BIGINT PRIMARY KEY,
            active_wallet_address TEXT
        )
    """)
    
    # User PINs
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_pins (
            user_id BIGINT PRIMARY KEY,
            pin_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Meta/settings
    c.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    # Users
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Entries (legacy)
    c.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            round INTEGER,
            numbers TEXT,
            stake_amount REAL,
            tx_signature TEXT,
            paid INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Scheduled rounds
    c.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_rounds (
            round_id SERIAL PRIMARY KEY,
            round_number INTEGER NOT NULL,
            scheduled_time TIMESTAMP NOT NULL,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            status TEXT DEFAULT 'pending',
            winning_numbers TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Round stakes
    c.execute("""
        CREATE TABLE IF NOT EXISTS round_stakes (
            id SERIAL PRIMARY KEY,
            round_id INTEGER NOT NULL,
            stake_amount REAL NOT NULL,
            status TEXT DEFAULT 'open',
            winner_user_id BIGINT,
            prize_amount REAL,
            tx_signature TEXT,
            first_stake_time TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Round participants - NO UNIQUE on (user_id, round_stake_id) to allow multiple tickets
    # Each ticket purchase creates a new row with its own primary key
    c.execute("""
        CREATE TABLE IF NOT EXISTS round_participants (
            id SERIAL PRIMARY KEY,
            ticket_id TEXT,
            round_stake_id INTEGER NOT NULL,
            user_id BIGINT NOT NULL,
            numbers TEXT NOT NULL,
            tx_signature TEXT NOT NULL,
            refunded INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # User stats
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id BIGINT PRIMARY KEY,
            total_tickets INTEGER DEFAULT 0,
            total_spent REAL DEFAULT 0,
            total_won REAL DEFAULT 0,
            wins INTEGER DEFAULT 0,
            biggest_win REAL DEFAULT 0,
            referral_earnings REAL DEFAULT 0,
            vip_tier INTEGER DEFAULT 0,
            notification_enabled INTEGER DEFAULT 1
        )
    """)
    
    # Referrals
    c.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id SERIAL PRIMARY KEY,
            referrer_id BIGINT NOT NULL,
            referred_id BIGINT NOT NULL UNIQUE,
            referral_code TEXT,
            bonus_earned REAL DEFAULT 0,
            tickets_from_referral INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Draw history
    c.execute("""
        CREATE TABLE IF NOT EXISTS draw_history (
            id SERIAL PRIMARY KEY,
            round_id INTEGER NOT NULL,
            winning_numbers TEXT NOT NULL,
            seed_data TEXT,
            player_count INTEGER,
            total_pot REAL,
            winner_id BIGINT,
            prize_amount REAL,
            tx_signature TEXT,
            drawn_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Jackpot seeds
    c.execute("""
        CREATE TABLE IF NOT EXISTS jackpot_seeds (
            id SERIAL PRIMARY KEY,
            admin_id BIGINT NOT NULL,
            amount REAL NOT NULL,
            tx_signature TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Wallet transactions
    c.execute("""
        CREATE TABLE IF NOT EXISTS wallet_transactions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            wallet_address TEXT NOT NULL,
            tx_type TEXT NOT NULL,
            amount REAL NOT NULL,
            to_address TEXT,
            from_address TEXT,
            tx_signature TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # User emails
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_emails (
            user_id BIGINT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            verified INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Email verification codes
    c.execute("""
        CREATE TABLE IF NOT EXISTS email_verification_codes (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            email TEXT NOT NULL,
            code TEXT NOT NULL,
            purpose TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Security questions
    c.execute("""
        CREATE TABLE IF NOT EXISTS security_questions (
            user_id BIGINT PRIMARY KEY,
            question TEXT NOT NULL,
            answer_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Announcement groups
    c.execute("""
        CREATE TABLE IF NOT EXISTS announcement_groups (
            chat_id BIGINT PRIMARY KEY,
            chat_type TEXT NOT NULL,
            chat_title TEXT,
            added_by BIGINT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # AI Chat History - stores conversations for 3 days
    c.execute("""
        CREATE TABLE IF NOT EXISTS ai_chat_history (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # User Profiles - permanent storage for user info AI should remember
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id BIGINT PRIMARY KEY,
            display_name TEXT,
            preferred_name TEXT,
            notes TEXT,
            first_interaction TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_interaction TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Payout logs - for transparency and auditing of prize payouts
    c.execute("""
        CREATE TABLE IF NOT EXISTS payout_logs (
            id SERIAL PRIMARY KEY,
            round_id INTEGER NOT NULL,
            tier INTEGER NOT NULL,
            user_id BIGINT NOT NULL,
            amount REAL NOT NULL,
            tx_signature TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Rollover logs - for transparency and auditing of rollover amounts
    c.execute("""
        CREATE TABLE IF NOT EXISTS rollover_logs (
            id SERIAL PRIMARY KEY,
            round_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            reason TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Initialize meta values
    try:
        c.execute("INSERT INTO meta (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING", ('current_round', '1'))
        c.execute("INSERT INTO meta (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING", ('current_pot', '0'))
    except Exception as e:
        print(f"[Database] Meta init note: {e}")
    
    # Create indexes for performance optimization
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_wallets_user ON wallets(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_entries_user ON entries(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_entries_round ON entries(round)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_status ON scheduled_rounds(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_round_stakes ON round_stakes(round_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_participants_stake ON round_participants(round_stake_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_participants_user ON round_participants(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_participants_user_stake ON round_participants(user_id, round_stake_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_participants_tx ON round_participants(tx_signature)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_draw_history_round ON draw_history(round_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_email_codes_user ON email_verification_codes(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_wallet_tx_user ON wallet_transactions(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_wallet_tx_sig ON wallet_transactions(tx_signature)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ai_chat_user ON ai_chat_history(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ai_chat_created ON ai_chat_history(created_at)")
    except Exception as e:
        print(f"[Database] Index creation note: {e}")
    
    conn.commit()
    conn.close()
    print("[Database] All tables initialized - PostgreSQL only")
    print("[Database] SQLite fully removed. PostgreSQL only.")


def migrate_remove_unique_constraint():
    """
    Migration: Remove any UNIQUE constraint from round_participants 
    on (round_stake_id, user_id) to allow unlimited tickets per user.
    """
    print("[Migration] Checking for problematic UNIQUE constraints on round_participants...")
    
    try:
        conn = get_db_conn()
        c = conn.cursor()
        
        # Find all unique constraints on round_participants
        c.execute("""
            SELECT conname, pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_class t ON c.conrelid = t.oid
            WHERE t.relname = 'round_participants' AND c.contype = 'u'
        """)
        constraints = c.fetchall()
        
        for row in constraints:
            constraint_name = row[0]
            constraint_def = str(row[1]) if row[1] else ''
            
            # Skip tx_signature and ticket_id constraints - those are valid
            if 'tx_signature' in constraint_def or 'ticket_id' in constraint_def:
                continue
            
            # Drop any constraint involving round_stake_id AND user_id
            if 'round_stake_id' in constraint_def or 'user_id' in constraint_def:
                try:
                    c.execute(f"ALTER TABLE round_participants DROP CONSTRAINT IF EXISTS {constraint_name}")
                    print(f"[Migration] Dropped constraint: {constraint_name}")
                except Exception as e:
                    print(f"[Migration] Could not drop {constraint_name}: {e}")
        
        # Also check for unique indexes
        c.execute("""
            SELECT indexname, indexdef FROM pg_indexes 
            WHERE tablename = 'round_participants'
        """)
        indexes = c.fetchall()
        
        for row in indexes:
            idx_name = row[0]
            idx_def = str(row[1]) if row[1] else ''
            
            # Skip valid unique indexes
            if 'tx_signature' in idx_name or 'ticket_id' in idx_name:
                continue
            
            # Drop any unique index on (round_stake_id, user_id)
            if 'UNIQUE' in idx_def.upper() and 'round_stake_id' in idx_def and 'user_id' in idx_def:
                try:
                    c.execute(f"DROP INDEX IF EXISTS {idx_name}")
                    print(f"[Migration] Dropped unique index: {idx_name}")
                except Exception as e:
                    print(f"[Migration] Could not drop index {idx_name}: {e}")
        
        conn.commit()
        conn.close()
        print("[Migration] Constraint check complete - users can buy unlimited tickets")
    except Exception as e:
        print(f"[Migration] Error: {e}")


def force_fix_participants_constraint():
    """
    FORCE FIX: Aggressively remove any unique constraint on (round_stake_id, user_id).
    This runs every time and ensures users can buy unlimited tickets.
    """
    global _constraint_fixed
    if _constraint_fixed:
        return
    
    print("[ForceFix] Checking for problematic constraints on round_participants...")
    
    try:
        conn = get_db_conn()
        c = conn.cursor()
        
        # Check for and drop all unique constraints except tx_signature and ticket_id
        c.execute("""
            SELECT conname, pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_class t ON c.conrelid = t.oid
            WHERE t.relname = 'round_participants' AND c.contype = 'u'
        """)
        constraints = c.fetchall()
        
        for row in constraints:
            constraint_name = row[0]
            constraint_def = str(row[1]) if row[1] else ''
            if 'tx_signature' in constraint_def or 'ticket_id' in constraint_def:
                continue
            if 'round_stake_id' in constraint_def or 'user_id' in constraint_def:
                try:
                    c.execute(f"ALTER TABLE round_participants DROP CONSTRAINT IF EXISTS {constraint_name}")
                    print(f"[ForceFix] Dropped constraint: {constraint_name}")
                except Exception as e:
                    print(f"[ForceFix] Could not drop {constraint_name}: {e}")
        
        # Also check for unique indexes
        c.execute("""
            SELECT indexname, indexdef FROM pg_indexes 
            WHERE tablename = 'round_participants'
        """)
        indexes = c.fetchall()
        
        for row in indexes:
            idx_name = row[0]
            idx_def = str(row[1]) if row[1] else ''
            if 'tx_signature' in idx_name or 'ticket_id' in idx_name or 'idx_participants_stake' in idx_name:
                continue
            if 'UNIQUE' in idx_def.upper() and ('round_stake_id' in idx_def or 'user_id' in idx_def):
                if 'round_stake_id' in idx_def and 'user_id' in idx_def:
                    try:
                        c.execute(f"DROP INDEX IF EXISTS {idx_name}")
                        print(f"[ForceFix] Dropped unique index: {idx_name}")
                    except Exception as e:
                        print(f"[ForceFix] Could not drop index {idx_name}: {e}")
        
        conn.commit()
        conn.close()
        _constraint_fixed = True
        print("[ForceFix] Constraint check complete - users can now buy unlimited tickets")
    except Exception as e:
        print(f"[ForceFix] Error: {e}")
        import traceback
        traceback.print_exc()


def migrate_add_ticket_id_column():
    """Migration: Add ticket_id column to round_participants for multiple tickets support"""
    try:
        conn = get_db_conn()
        c = conn.cursor()
        
        # Check if ticket_id column exists
        c.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'round_participants' AND column_name = 'ticket_id'
        """)
        if not c.fetchone():
            c.execute("ALTER TABLE round_participants ADD COLUMN ticket_id TEXT")
            print("[Migration] Added ticket_id column to round_participants table")
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Migration] Error adding ticket_id column: {e}")


def migrate_add_referral_column():
    """Migration: Add tickets_from_referral column to referrals table if missing"""
    try:
        conn = get_db_conn()
        c = conn.cursor()
        
        c.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'referrals' AND column_name = 'tickets_from_referral'
        """)
        if not c.fetchone():
            c.execute("ALTER TABLE referrals ADD COLUMN tickets_from_referral INTEGER DEFAULT 0")
            print("[Migration] Added tickets_from_referral column to referrals table")
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Migration] Error adding tickets_from_referral column: {e}")


def migrate_add_referral_reward_columns():
    """Migration: Add has_bought_ticket, free_ticket_balance, and referral_count columns for referral rewards"""
    try:
        conn = get_db_conn()
        c = conn.cursor()
        
        # Check and add has_bought_ticket
        c.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'users' AND column_name = 'has_bought_ticket'
        """)
        if not c.fetchone():
            c.execute("ALTER TABLE users ADD COLUMN has_bought_ticket INTEGER DEFAULT 0")
            print("[Migration] Added has_bought_ticket column to users table")
        
        # Check and add free_ticket_balance
        c.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'users' AND column_name = 'free_ticket_balance'
        """)
        if not c.fetchone():
            c.execute("ALTER TABLE users ADD COLUMN free_ticket_balance INTEGER DEFAULT 0")
            print("[Migration] Added free_ticket_balance column to users table")
        
        # Check and add referral_count (counter for awarded free tickets)
        c.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'user_stats' AND column_name = 'referral_count'
        """)
        if not c.fetchone():
            c.execute("ALTER TABLE user_stats ADD COLUMN referral_count INTEGER DEFAULT 0")
            print("[Migration] Added referral_count column to user_stats table")
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Migration] Error adding referral reward columns: {e}")


def migrate_add_vip_claim_column():
    """Migration: Add free_ticket_last_claim column to users table for VIP daily bonus tracking"""
    try:
        conn = get_db_conn()
        c = conn.cursor()
        
        # Check and add free_ticket_last_claim (UNIX timestamp of last VIP bonus claim)
        c.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'users' AND column_name = 'free_ticket_last_claim'
        """)
        if not c.fetchone():
            c.execute("ALTER TABLE users ADD COLUMN free_ticket_last_claim INTEGER")
            print("[Migration] Added free_ticket_last_claim column to users table (VIP bonus tracking)")
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Migration] Error adding VIP claim column: {e}")


# ==============================================================================
# Security Question Functions
# ==============================================================================

def hash_answer(answer: str) -> str:
    """Hash security question answer (case-insensitive, trimmed)"""
    normalized = answer.strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()


def save_security_question(user_id: int, question: str, answer: str) -> bool:
    """Save or update user's security question"""
    try:
        print(f"[SecurityQ] Saving for user {user_id}, question: {question[:30] if question else 'None'}...")
        conn = get_db_conn()
        c = conn.cursor()
        answer_hash = hash_answer(answer)
        
        c.execute("""
            INSERT INTO security_questions (user_id, question, answer_hash)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET question = EXCLUDED.question, answer_hash = EXCLUDED.answer_hash
        """, (int(user_id), question, answer_hash))
        
        conn.commit()
        conn.close()
        print(f"[SecurityQ] Successfully saved for user {user_id}")
        return True
    except Exception as e:
        print(f"[DB] Error saving security question: {e}")
        import traceback
        traceback.print_exc()
        return False


def get_security_question(user_id: int) -> dict:
    """Get user's security question (not the answer)"""
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT question FROM security_questions WHERE user_id = %s", (int(user_id),))
        row = c.fetchone()
        conn.close()
        if row:
            return {"question": row[0], "has_question": True}
        return {"has_question": False}
    except Exception as e:
        print(f"[DB] Error getting security question: {e}")
        return {"has_question": False}


def verify_security_answer(user_id: int, answer: str) -> bool:
    """Verify user's security question answer"""
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT answer_hash FROM security_questions WHERE user_id = %s", (int(user_id),))
        row = c.fetchone()
        conn.close()
        if row:
            return row[0] == hash_answer(answer)
        return False
    except Exception as e:
        print(f"[DB] Error verifying security answer: {e}")
        return False


def has_security_question(user_id: int) -> bool:
    """Check if user has a security question set"""
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT 1 FROM security_questions WHERE user_id = %s", (int(user_id),))
        result = c.fetchone() is not None
        conn.close()
        return result
    except:
        return False


# ==============================================================================
# Announcement Groups Functions
# ==============================================================================

def add_announcement_group(chat_id: int, chat_type: str, chat_title: str = None, added_by: int = None) -> bool:
    """Add a group/channel to receive announcements"""
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("""
            INSERT INTO announcement_groups (chat_id, chat_type, chat_title, added_by, is_active)
            VALUES (%s, %s, %s, %s, 1)
            ON CONFLICT (chat_id) DO UPDATE SET 
                chat_title = EXCLUDED.chat_title,
                is_active = 1
        """, (chat_id, chat_type, chat_title, added_by))
        conn.commit()
        conn.close()
        print(f"[DB] Added announcement group: {chat_id} ({chat_title})")
        return True
    except Exception as e:
        print(f"[DB] Error adding announcement group: {e}")
        return False


def remove_announcement_group(chat_id: int) -> bool:
    """Deactivate a group/channel from announcements (soft delete)"""
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("UPDATE announcement_groups SET is_active = 0 WHERE chat_id = %s", (chat_id,))
        conn.commit()
        conn.close()
        print(f"[DB] Removed announcement group: {chat_id}")
        return True
    except Exception as e:
        print(f"[DB] Error removing announcement group: {e}")
        return False


def get_announcement_groups() -> list:
    """Get all active announcement groups/channels"""
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT chat_id, chat_type, chat_title FROM announcement_groups WHERE is_active = 1")
        rows = c.fetchall()
        conn.close()
        return [{"chat_id": row[0], "chat_type": row[1], "chat_title": row[2]} for row in rows]
    except Exception as e:
        print(f"[DB] Error getting announcement groups: {e}")
        return []


def is_announcement_group(chat_id: int) -> bool:
    """Check if a chat is an active announcement group"""
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT 1 FROM announcement_groups WHERE chat_id = %s AND is_active = 1", (chat_id,))
        result = c.fetchone() is not None
        conn.close()
        return result
    except:
        return False


# ==============================================================================
# Legacy compatibility - DB_PATH removed, no SQLite
# ==============================================================================
# SQLite fully removed. PostgreSQL only.
DB_PATH = None  # No local database file
