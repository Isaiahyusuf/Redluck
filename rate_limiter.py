"""
Per-user Rate Limiting for Telegram Bot.

Features:
- Per-user rate limits for different actions
- Silent rejection of spam requests
- Abuse logging
- Sliding window algorithm
"""

import time
import threading
from typing import Dict, Optional
from dataclasses import dataclass, field
from collections import defaultdict
from enum import Enum

class RateLimitAction(Enum):
    BUTTON_CLICK = "button_click"
    TICKET_PURCHASE = "ticket_purchase"
    WALLET_CREATE = "wallet_create"
    BALANCE_CHECK = "balance_check"
    COMMAND = "command"
    MESSAGE = "message"

@dataclass
class RateLimitConfig:
    max_requests: int
    window_seconds: float
    cooldown_seconds: float = 0

@dataclass
class UserRateState:
    requests: list = field(default_factory=list)
    blocked_until: float = 0
    abuse_count: int = 0

class RateLimiter:
    """
    Per-user rate limiter with sliding window.
    
    Limits:
    - Button clicks: 10 per 5 seconds
    - Ticket purchases: 5 per 30 seconds
    - Commands: 20 per 10 seconds
    - Messages: 30 per 10 seconds
    """
    
    DEFAULT_LIMITS: Dict[RateLimitAction, RateLimitConfig] = {
        RateLimitAction.BUTTON_CLICK: RateLimitConfig(
            max_requests=10,
            window_seconds=5,
            cooldown_seconds=3
        ),
        RateLimitAction.TICKET_PURCHASE: RateLimitConfig(
            max_requests=5,
            window_seconds=30,
            cooldown_seconds=10
        ),
        RateLimitAction.WALLET_CREATE: RateLimitConfig(
            max_requests=3,
            window_seconds=60,
            cooldown_seconds=30
        ),
        RateLimitAction.BALANCE_CHECK: RateLimitConfig(
            max_requests=10,
            window_seconds=30,
            cooldown_seconds=5
        ),
        RateLimitAction.COMMAND: RateLimitConfig(
            max_requests=20,
            window_seconds=10,
            cooldown_seconds=2
        ),
        RateLimitAction.MESSAGE: RateLimitConfig(
            max_requests=30,
            window_seconds=10,
            cooldown_seconds=1
        ),
    }
    
    ABUSE_THRESHOLD = 10
    ABUSE_BLOCK_DURATION = 300
    
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
        
        self._user_states: Dict[int, Dict[RateLimitAction, UserRateState]] = defaultdict(
            lambda: defaultdict(UserRateState)
        )
        self._state_lock = threading.RLock()
        self._cleanup_interval = 300
        self._last_cleanup = time.time()
        self._initialized = True
        print("[RateLimiter] Initialized")
    
    def _cleanup_old_data(self):
        """Periodically clean up old rate limit data."""
        current_time = time.time()
        if current_time - self._last_cleanup < self._cleanup_interval:
            return
        
        with self._state_lock:
            users_to_remove = []
            for user_id, actions in self._user_states.items():
                all_empty = True
                for action, state in actions.items():
                    state.requests = [
                        t for t in state.requests
                        if current_time - t < 300
                    ]
                    if state.requests or state.blocked_until > current_time:
                        all_empty = False
                
                if all_empty:
                    users_to_remove.append(user_id)
            
            for user_id in users_to_remove:
                del self._user_states[user_id]
            
            self._last_cleanup = current_time
    
    def is_allowed(
        self,
        user_id: int,
        action: RateLimitAction,
        custom_config: Optional[RateLimitConfig] = None
    ) -> bool:
        """
        Check if action is allowed for user.
        Returns True if allowed, False if rate limited.
        """
        self._cleanup_old_data()
        
        config = custom_config or self.DEFAULT_LIMITS.get(action)
        if not config:
            return True
        
        current_time = time.time()
        
        with self._state_lock:
            state = self._user_states[user_id][action]
            
            if state.blocked_until > current_time:
                return False
            
            state.requests = [
                t for t in state.requests
                if current_time - t < config.window_seconds
            ]
            
            if len(state.requests) >= config.max_requests:
                state.abuse_count += 1
                
                if state.abuse_count >= self.ABUSE_THRESHOLD:
                    state.blocked_until = current_time + self.ABUSE_BLOCK_DURATION
                    print(f"[RateLimiter] User {user_id} blocked for {self.ABUSE_BLOCK_DURATION}s due to abuse ({action.value})")
                elif config.cooldown_seconds > 0:
                    state.blocked_until = current_time + config.cooldown_seconds
                
                print(f"[RateLimiter] User {user_id} rate limited for {action.value} ({len(state.requests)} requests)")
                return False
            
            state.requests.append(current_time)
            return True
    
    def record_action(self, user_id: int, action: RateLimitAction):
        """Record an action without checking limits (for tracking)."""
        with self._state_lock:
            state = self._user_states[user_id][action]
            state.requests.append(time.time())
    
    def reset_user(self, user_id: int):
        """Reset all rate limits for a user (admin action)."""
        with self._state_lock:
            if user_id in self._user_states:
                del self._user_states[user_id]
    
    def get_user_status(self, user_id: int) -> dict:
        """Get rate limit status for a user."""
        current_time = time.time()
        
        with self._state_lock:
            if user_id not in self._user_states:
                return {"status": "clean", "actions": {}}
            
            actions = {}
            for action, state in self._user_states[user_id].items():
                config = self.DEFAULT_LIMITS.get(action)
                if not config:
                    continue
                
                valid_requests = [
                    t for t in state.requests
                    if current_time - t < config.window_seconds
                ]
                
                actions[action.value] = {
                    "requests_in_window": len(valid_requests),
                    "max_requests": config.max_requests,
                    "is_blocked": state.blocked_until > current_time,
                    "blocked_until": state.blocked_until if state.blocked_until > current_time else None,
                    "abuse_count": state.abuse_count
                }
            
            return {"status": "tracked", "actions": actions}
    
    def get_stats(self) -> dict:
        """Get overall rate limiter statistics."""
        with self._state_lock:
            total_users = len(self._user_states)
            blocked_users = sum(
                1 for user_actions in self._user_states.values()
                for state in user_actions.values()
                if state.blocked_until > time.time()
            )
            
            return {
                "tracked_users": total_users,
                "blocked_users": blocked_users
            }


_callback_dedup_cache: Dict[str, float] = {}
_callback_lock = threading.Lock()

def is_duplicate_callback(callback_id: str, window_seconds: float = 2.0) -> bool:
    """
    Check if a callback is a duplicate within the time window.
    Used to prevent double-clicks from processing twice.
    
    Args:
        callback_id: Unique identifier (e.g., f"{user_id}:{callback_data}")
        window_seconds: Time window to consider as duplicate
    
    Returns:
        True if this is a duplicate, False if it should be processed
    """
    current_time = time.time()
    
    with _callback_lock:
        if len(_callback_dedup_cache) > 10000:
            old_keys = [
                k for k, v in _callback_dedup_cache.items()
                if current_time - v > 60
            ]
            for k in old_keys:
                del _callback_dedup_cache[k]
        
        last_time = _callback_dedup_cache.get(callback_id)
        
        if last_time and current_time - last_time < window_seconds:
            return True
        
        _callback_dedup_cache[callback_id] = current_time
        return False


def get_rate_limiter() -> RateLimiter:
    """Get the singleton rate limiter instance."""
    return RateLimiter()
