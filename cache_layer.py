"""
Cache Layer for reducing database and RPC reads.

Features:
- In-memory cache with TTL
- Thread-safe operations
- Automatic expiration
- Cache invalidation helpers
"""

import time
import threading
from typing import Optional, Any, Dict, Callable
from dataclasses import dataclass
from decimal import Decimal

@dataclass
class CacheEntry:
    value: Any
    expires_at: float
    created_at: float

class CacheLayer:
    """
    In-memory cache with TTL support.
    
    Cache TTLs:
    - Round info: 10-30 seconds
    - Ticket price: 60 seconds
    - Prize pool: 15-30 seconds
    - User ticket count: 10 seconds
    - Balance: 30 seconds
    """
    
    TTL_ROUND_INFO = 15
    TTL_TICKET_PRICE = 60
    TTL_PRIZE_POOL = 20
    TTL_USER_TICKETS = 10
    TTL_BALANCE = 30
    TTL_DEFAULT = 30
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._cache: Dict[str, CacheEntry] = {}
        self._cache_lock = threading.RLock()
        self._initialized = True
        self._cleanup_interval = 60
        self._last_cleanup = time.time()
        print("[Cache] In-memory cache initialized")
    
    def _cleanup_expired(self):
        """Remove expired entries periodically."""
        current_time = time.time()
        if current_time - self._last_cleanup < self._cleanup_interval:
            return
        
        with self._cache_lock:
            expired_keys = [
                key for key, entry in self._cache.items()
                if entry.expires_at < current_time
            ]
            for key in expired_keys:
                del self._cache[key]
            
            if expired_keys:
                print(f"[Cache] Cleaned up {len(expired_keys)} expired entries")
            
            self._last_cleanup = current_time
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired."""
        self._cleanup_expired()
        
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            
            if entry.expires_at < time.time():
                del self._cache[key]
                return None
            
            return entry.value
    
    def set(self, key: str, value: Any, ttl: float = None):
        """Set value in cache with TTL."""
        ttl = ttl or self.TTL_DEFAULT
        current_time = time.time()
        
        with self._cache_lock:
            self._cache[key] = CacheEntry(
                value=value,
                expires_at=current_time + ttl,
                created_at=current_time
            )
    
    def delete(self, key: str):
        """Delete a specific key from cache."""
        with self._cache_lock:
            self._cache.pop(key, None)
    
    def invalidate_pattern(self, pattern: str):
        """Invalidate all keys matching pattern (prefix match)."""
        with self._cache_lock:
            keys_to_delete = [
                key for key in self._cache.keys()
                if key.startswith(pattern)
            ]
            for key in keys_to_delete:
                del self._cache[key]
            
            if keys_to_delete:
                print(f"[Cache] Invalidated {len(keys_to_delete)} keys matching '{pattern}'")
    
    def clear(self):
        """Clear all cache entries."""
        with self._cache_lock:
            self._cache.clear()
        print("[Cache] All entries cleared")
    
    def get_or_set(self, key: str, factory: Callable, ttl: float = None) -> Any:
        """Get from cache or compute and cache the value."""
        value = self.get(key)
        if value is not None:
            return value
        
        value = factory()
        self.set(key, value, ttl)
        return value
    
    async def get_or_set_async(self, key: str, factory: Callable, ttl: float = None) -> Any:
        """Async version of get_or_set."""
        value = self.get(key)
        if value is not None:
            return value
        
        value = await factory()
        self.set(key, value, ttl)
        return value
    
    def get_round_info(self, round_id: int) -> Optional[dict]:
        """Get cached round info."""
        return self.get(f"round:{round_id}")
    
    def set_round_info(self, round_id: int, info: dict):
        """Cache round info."""
        self.set(f"round:{round_id}", info, self.TTL_ROUND_INFO)
    
    def get_active_round(self) -> Optional[dict]:
        """Get cached active round."""
        return self.get("active_round")
    
    def set_active_round(self, info: dict):
        """Cache active round."""
        self.set("active_round", info, self.TTL_ROUND_INFO)
    
    def invalidate_round(self, round_id: int = None):
        """Invalidate round cache."""
        if round_id:
            self.delete(f"round:{round_id}")
        self.delete("active_round")
    
    def get_ticket_price(self) -> Optional[Decimal]:
        """Get cached ticket price."""
        return self.get("ticket_price")
    
    def set_ticket_price(self, price: Decimal):
        """Cache ticket price."""
        self.set("ticket_price", price, self.TTL_TICKET_PRICE)
    
    def get_prize_pool(self) -> Optional[Decimal]:
        """Get cached prize pool."""
        return self.get("prize_pool")
    
    def set_prize_pool(self, amount: Decimal):
        """Cache prize pool."""
        self.set("prize_pool", amount, self.TTL_PRIZE_POOL)
    
    def invalidate_prize_pool(self):
        """Invalidate prize pool cache."""
        self.delete("prize_pool")
    
    def get_user_ticket_count(self, user_id: int, round_stake_id: int) -> Optional[int]:
        """Get cached user ticket count for a round."""
        return self.get(f"tickets:{user_id}:{round_stake_id}")
    
    def set_user_ticket_count(self, user_id: int, round_stake_id: int, count: int):
        """Cache user ticket count."""
        self.set(f"tickets:{user_id}:{round_stake_id}", count, self.TTL_USER_TICKETS)
    
    def invalidate_user_tickets(self, user_id: int):
        """Invalidate all ticket counts for a user."""
        self.invalidate_pattern(f"tickets:{user_id}:")
    
    def get_balance(self, wallet_address: str) -> Optional[Decimal]:
        """Get cached wallet balance."""
        return self.get(f"balance:{wallet_address}")
    
    def set_balance(self, wallet_address: str, balance: Decimal):
        """Cache wallet balance."""
        self.set(f"balance:{wallet_address}", balance, self.TTL_BALANCE)
    
    def invalidate_balance(self, wallet_address: str):
        """Invalidate balance cache."""
        self.delete(f"balance:{wallet_address}")
    
    def get_stats(self) -> dict:
        """Get cache statistics."""
        with self._cache_lock:
            current_time = time.time()
            valid_entries = sum(
                1 for entry in self._cache.values()
                if entry.expires_at > current_time
            )
            return {
                "total_entries": len(self._cache),
                "valid_entries": valid_entries,
                "expired_entries": len(self._cache) - valid_entries
            }


def get_cache() -> CacheLayer:
    """Get the singleton cache instance."""
    return CacheLayer()
