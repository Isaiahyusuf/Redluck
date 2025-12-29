"""
Async Transaction Verification Queue.

Features:
- Background verification of Solana transactions
- Immediate user response, async ticket creation
- Duplicate signature detection
- Retry logic for failed verifications
"""

import asyncio
import time
from typing import Optional, Dict, Callable, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import threading

class TxStatus(Enum):
    PENDING = "pending"
    VERIFYING = "verifying"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    DUPLICATE = "duplicate"

@dataclass
class TxVerificationTask:
    signature: str
    user_id: int
    round_stake_id: int
    numbers: str
    amount: float
    created_at: float = field(default_factory=time.time)
    attempts: int = 0
    status: TxStatus = TxStatus.PENDING
    error: Optional[str] = None
    callback_data: Optional[Dict] = None

class TxVerificationQueue:
    """
    Async queue for transaction verification.
    
    Flow:
    1. User submits transaction
    2. Bot responds immediately with "verifying" message
    3. Transaction signature is queued
    4. Background worker verifies transaction
    5. On success: tickets inserted to database
    6. On failure: user notified
    """
    
    MAX_ATTEMPTS = 3
    VERIFICATION_TIMEOUT = 60
    WORKER_INTERVAL = 2
    MAX_QUEUE_SIZE = 1000
    
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
        
        self._queue: deque = deque(maxlen=self.MAX_QUEUE_SIZE)
        self._processing: Dict[str, TxVerificationTask] = {}
        self._completed: Dict[str, TxVerificationTask] = {}
        self._signatures_seen: set = set()
        
        self._queue_lock = asyncio.Lock()
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        
        self._on_success_callback: Optional[Callable] = None
        self._on_failure_callback: Optional[Callable] = None
        self._verify_callback: Optional[Callable] = None
        
        self._initialized = True
        print("[TxQueue] Transaction verification queue initialized")
    
    def set_callbacks(
        self,
        on_success: Callable,
        on_failure: Callable,
        verify_tx: Callable
    ):
        """
        Set callback functions for verification results.
        
        Args:
            on_success: Called when tx confirmed - (task, tx_data) -> None
            on_failure: Called when tx fails - (task, error) -> None
            verify_tx: Called to verify tx - (signature) -> tx_data or None
        """
        self._on_success_callback = on_success
        self._on_failure_callback = on_failure
        self._verify_callback = verify_tx
        print("[TxQueue] Callbacks configured")
    
    async def enqueue(
        self,
        signature: str,
        user_id: int,
        round_stake_id: int,
        numbers: str,
        amount: float,
        callback_data: Optional[Dict] = None
    ) -> bool:
        """
        Add a transaction to the verification queue.
        
        Returns:
            True if added, False if duplicate or queue full
        """
        signature = signature.strip()
        
        if signature in self._signatures_seen:
            print(f"[TxQueue] Duplicate signature ignored: {signature[:16]}...")
            return False
        
        async with self._queue_lock:
            if len(self._queue) >= self.MAX_QUEUE_SIZE:
                print("[TxQueue] Queue full, rejecting new transaction")
                return False
            
            task = TxVerificationTask(
                signature=signature,
                user_id=user_id,
                round_stake_id=round_stake_id,
                numbers=numbers,
                amount=amount,
                callback_data=callback_data
            )
            
            self._signatures_seen.add(signature)
            self._queue.append(task)
            
            print(f"[TxQueue] Queued tx {signature[:16]}... for user {user_id}")
            return True
    
    async def start_worker(self):
        """Start the background verification worker."""
        if self._running:
            return
        
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        print("[TxQueue] Background worker started")
    
    async def stop_worker(self):
        """Stop the background worker."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        print("[TxQueue] Background worker stopped")
    
    async def _worker_loop(self):
        """Main worker loop that processes the queue."""
        while self._running:
            try:
                await self._process_next()
                await asyncio.sleep(self.WORKER_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[TxQueue] Worker error: {e}")
                await asyncio.sleep(5)
    
    async def _process_next(self):
        """Process the next transaction in queue."""
        task = None
        
        async with self._queue_lock:
            if not self._queue:
                return
            
            task = self._queue.popleft()
            task.status = TxStatus.VERIFYING
            self._processing[task.signature] = task
        
        if not task:
            return
        
        try:
            await self._verify_transaction(task)
        except Exception as e:
            print(f"[TxQueue] Verification error for {task.signature[:16]}: {e}")
            task.error = str(e)
            await self._handle_failure(task)
        finally:
            if task.signature in self._processing:
                del self._processing[task.signature]
    
    async def _verify_transaction(self, task: TxVerificationTask):
        """Verify a single transaction."""
        if not self._verify_callback:
            print("[TxQueue] No verify callback configured!")
            task.error = "Verification not configured"
            await self._handle_failure(task)
            return
        
        task.attempts += 1
        print(f"[TxQueue] Verifying tx {task.signature[:16]}... (attempt {task.attempts})")
        
        try:
            tx_data = await asyncio.wait_for(
                self._verify_callback(task.signature),
                timeout=self.VERIFICATION_TIMEOUT
            )
            
            if tx_data:
                task.status = TxStatus.CONFIRMED
                await self._handle_success(task, tx_data)
            else:
                if task.attempts < self.MAX_ATTEMPTS:
                    print(f"[TxQueue] Tx {task.signature[:16]} not found, requeueing")
                    async with self._queue_lock:
                        self._queue.append(task)
                else:
                    task.error = "Transaction not found after max attempts"
                    await self._handle_failure(task)
                    
        except asyncio.TimeoutError:
            if task.attempts < self.MAX_ATTEMPTS:
                print(f"[TxQueue] Tx {task.signature[:16]} timeout, requeueing")
                async with self._queue_lock:
                    self._queue.append(task)
            else:
                task.error = "Verification timeout"
                await self._handle_failure(task)
    
    async def _handle_success(self, task: TxVerificationTask, tx_data: Any):
        """Handle successful verification."""
        print(f"[TxQueue] Tx {task.signature[:16]} CONFIRMED for user {task.user_id}")
        
        self._completed[task.signature] = task
        
        if len(self._completed) > 1000:
            oldest = list(self._completed.keys())[:500]
            for k in oldest:
                del self._completed[k]
        
        if self._on_success_callback:
            try:
                await self._on_success_callback(task, tx_data)
            except Exception as e:
                print(f"[TxQueue] Success callback error: {e}")
    
    async def _handle_failure(self, task: TxVerificationTask):
        """Handle verification failure."""
        task.status = TxStatus.FAILED
        print(f"[TxQueue] Tx {task.signature[:16]} FAILED: {task.error}")
        
        self._completed[task.signature] = task
        
        if self._on_failure_callback:
            try:
                await self._on_failure_callback(task, task.error)
            except Exception as e:
                print(f"[TxQueue] Failure callback error: {e}")
    
    def get_status(self, signature: str) -> Optional[TxVerificationTask]:
        """Get the status of a transaction."""
        if signature in self._completed:
            return self._completed[signature]
        if signature in self._processing:
            return self._processing[signature]
        for task in self._queue:
            if task.signature == signature:
                return task
        return None
    
    def is_duplicate(self, signature: str) -> bool:
        """Check if a signature has already been seen."""
        return signature in self._signatures_seen
    
    def get_stats(self) -> dict:
        """Get queue statistics."""
        return {
            "queued": len(self._queue),
            "processing": len(self._processing),
            "completed": len(self._completed),
            "signatures_seen": len(self._signatures_seen),
            "worker_running": self._running
        }


def get_tx_queue() -> TxVerificationQueue:
    """Get the singleton transaction queue instance."""
    return TxVerificationQueue()
