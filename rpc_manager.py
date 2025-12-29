"""
Centralized RPC Manager for Solana blockchain operations.

Features:
- Round-robin load balancing for READ operations
- Primary endpoint for WRITE operations
- Automatic failover with retry logic
- Configurable timeouts
"""

import os
import asyncio
import time
from typing import Optional, List, Any, Callable
from dataclasses import dataclass
from enum import Enum

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed

class OperationType(Enum):
    READ = "read"
    WRITE = "write"

@dataclass
class RPCEndpoint:
    url: str
    name: str
    is_primary: bool = False
    is_healthy: bool = True
    last_error_time: float = 0
    error_count: int = 0

class RPCManager:
    """
    Centralized RPC manager with load balancing and failover.
    
    - Primary (Helius): Used for WRITE operations
    - Secondary endpoints: Used for READ operations with round-robin
    - Fallback: Public Solana RPC
    """
    
    FALLBACK_RPC = "https://api.mainnet-beta.solana.com"
    DEFAULT_TIMEOUT = 10
    MAX_RETRIES = 2
    HEALTH_RECOVERY_TIME = 60
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._endpoints: List[RPCEndpoint] = []
        self._read_index = 0
        self._lock = asyncio.Lock()
        
        self._setup_endpoints()
        self._initialized = True
        print(f"[RPC Manager] Initialized with {len(self._endpoints)} endpoints")
    
    def _setup_endpoints(self):
        """Configure RPC endpoints from environment variables."""
        
        solana_rpc = os.getenv("SOLANA_RPC", "")
        helius_rpc = os.getenv("HELIUS_RPC", "")
        
        if helius_rpc:
            self._endpoints.append(RPCEndpoint(
                url=helius_rpc,
                name="Helius (Primary)",
                is_primary=True
            ))
            print(f"[RPC Manager] Primary: Helius configured")
        
        if solana_rpc and solana_rpc != self.FALLBACK_RPC:
            is_primary = len(self._endpoints) == 0
            self._endpoints.append(RPCEndpoint(
                url=solana_rpc,
                name="SOLANA_RPC",
                is_primary=is_primary
            ))
            if is_primary:
                print(f"[RPC Manager] Primary: SOLANA_RPC configured")
            else:
                print(f"[RPC Manager] Secondary: SOLANA_RPC configured")
        
        self._endpoints.append(RPCEndpoint(
            url=self.FALLBACK_RPC,
            name="Solana Public",
            is_primary=len(self._endpoints) == 0
        ))
        print(f"[RPC Manager] Fallback: Solana Public RPC")
    
    def _get_healthy_endpoints(self, for_write: bool = False) -> List[RPCEndpoint]:
        """Get list of healthy endpoints, optionally filtered for writes."""
        current_time = time.time()
        healthy = []
        
        for ep in self._endpoints:
            if not ep.is_healthy:
                if current_time - ep.last_error_time > self.HEALTH_RECOVERY_TIME:
                    ep.is_healthy = True
                    ep.error_count = 0
                    print(f"[RPC Manager] {ep.name} recovered, marking healthy")
            
            if ep.is_healthy:
                if for_write and ep.is_primary:
                    healthy.insert(0, ep)
                elif not for_write:
                    healthy.append(ep)
        
        if not healthy:
            for ep in self._endpoints:
                ep.is_healthy = True
                ep.error_count = 0
            healthy = self._endpoints.copy()
            print("[RPC Manager] All endpoints were unhealthy, resetting")
        
        return healthy
    
    def _mark_endpoint_error(self, endpoint: RPCEndpoint):
        """Mark an endpoint as having an error."""
        endpoint.error_count += 1
        endpoint.last_error_time = time.time()
        
        if endpoint.error_count >= 3:
            endpoint.is_healthy = False
            print(f"[RPC Manager] {endpoint.name} marked unhealthy after {endpoint.error_count} errors")
    
    def _mark_endpoint_success(self, endpoint: RPCEndpoint):
        """Mark endpoint as successful."""
        endpoint.error_count = 0
        endpoint.is_healthy = True
    
    async def _get_next_read_endpoint(self) -> RPCEndpoint:
        """Get next endpoint for read operations using round-robin."""
        async with self._lock:
            endpoints = self._get_healthy_endpoints(for_write=False)
            if not endpoints:
                return self._endpoints[-1]
            
            self._read_index = (self._read_index + 1) % len(endpoints)
            return endpoints[self._read_index]
    
    async def execute_read(
        self,
        operation: Callable,
        timeout: float = None,
        log_prefix: str = "RPC"
    ) -> Any:
        """
        Execute a READ operation with load balancing and failover.
        
        Args:
            operation: Async function that takes AsyncClient as argument
            timeout: Request timeout in seconds
            log_prefix: Prefix for log messages
        
        Returns:
            Result from the operation
        
        Raises:
            Exception: If all endpoints fail
        """
        timeout = timeout or self.DEFAULT_TIMEOUT
        last_error = None
        tried_endpoints = set()
        
        for attempt in range(self.MAX_RETRIES + 1):
            endpoint = await self._get_next_read_endpoint()
            
            if endpoint.url in tried_endpoints:
                endpoints = self._get_healthy_endpoints(for_write=False)
                for ep in endpoints:
                    if ep.url not in tried_endpoints:
                        endpoint = ep
                        break
            
            tried_endpoints.add(endpoint.url)
            
            try:
                async with AsyncClient(endpoint.url) as client:
                    result = await asyncio.wait_for(
                        operation(client),
                        timeout=timeout
                    )
                    self._mark_endpoint_success(endpoint)
                    return result
                    
            except asyncio.TimeoutError:
                last_error = TimeoutError(f"Timeout after {timeout}s")
                self._mark_endpoint_error(endpoint)
                print(f"[{log_prefix}] {endpoint.name} timeout, attempt {attempt + 1}/{self.MAX_RETRIES + 1}")
                
            except Exception as e:
                last_error = e
                self._mark_endpoint_error(endpoint)
                print(f"[{log_prefix}] {endpoint.name} error: {e}, attempt {attempt + 1}/{self.MAX_RETRIES + 1}")
        
        raise last_error or Exception("All RPC endpoints failed")
    
    async def execute_write(
        self,
        operation: Callable,
        timeout: float = None,
        log_prefix: str = "RPC"
    ) -> Any:
        """
        Execute a WRITE operation using primary endpoint with failover.
        
        Args:
            operation: Async function that takes AsyncClient as argument
            timeout: Request timeout in seconds
            log_prefix: Prefix for log messages
        
        Returns:
            Result from the operation
        
        Raises:
            Exception: If all endpoints fail
        """
        timeout = timeout or self.DEFAULT_TIMEOUT * 2
        last_error = None
        
        endpoints = self._get_healthy_endpoints(for_write=True)
        
        for endpoint in endpoints:
            try:
                async with AsyncClient(endpoint.url) as client:
                    result = await asyncio.wait_for(
                        operation(client),
                        timeout=timeout
                    )
                    self._mark_endpoint_success(endpoint)
                    print(f"[{log_prefix}] Write successful via {endpoint.name}")
                    return result
                    
            except asyncio.TimeoutError:
                last_error = TimeoutError(f"Timeout after {timeout}s")
                self._mark_endpoint_error(endpoint)
                print(f"[{log_prefix}] {endpoint.name} write timeout")
                
            except Exception as e:
                last_error = e
                self._mark_endpoint_error(endpoint)
                print(f"[{log_prefix}] {endpoint.name} write error: {e}")
        
        raise last_error or Exception("All RPC endpoints failed for write operation")
    
    async def get_balance(self, wallet_address: str) -> int:
        """Get wallet balance in lamports."""
        from solders.pubkey import Pubkey
        
        async def _get_balance(client: AsyncClient):
            pubkey = Pubkey.from_string(wallet_address)
            response = await client.get_balance(pubkey, commitment=Confirmed)
            return response.value if response.value is not None else 0
        
        return await self.execute_read(_get_balance, log_prefix="Balance")
    
    async def get_latest_blockhash(self):
        """Get latest blockhash for transaction building."""
        async def _get_blockhash(client: AsyncClient):
            response = await client.get_latest_blockhash()
            return response.value
        
        return await self.execute_read(_get_blockhash, log_prefix="Blockhash")
    
    async def send_transaction(self, transaction) -> str:
        """Send a signed transaction and return signature."""
        async def _send_tx(client: AsyncClient):
            response = await client.send_transaction(transaction)
            return str(response.value)
        
        return await self.execute_write(_send_tx, log_prefix="SendTx")
    
    async def confirm_transaction(self, signature: str, timeout: float = 30) -> bool:
        """Confirm a transaction with the given signature."""
        async def _confirm_tx(client: AsyncClient):
            response = await client.confirm_transaction(signature, commitment=Confirmed)
            return response.value is not None
        
        return await self.execute_read(_confirm_tx, timeout=timeout, log_prefix="ConfirmTx")
    
    async def get_transaction(self, signature: str):
        """Get transaction details by signature."""
        async def _get_tx(client: AsyncClient):
            response = await client.get_transaction(signature)
            return response.value
        
        return await self.execute_read(_get_tx, log_prefix="GetTx")
    
    def get_status(self) -> dict:
        """Get current status of all endpoints."""
        return {
            "endpoints": [
                {
                    "name": ep.name,
                    "is_primary": ep.is_primary,
                    "is_healthy": ep.is_healthy,
                    "error_count": ep.error_count
                }
                for ep in self._endpoints
            ],
            "read_index": self._read_index
        }


def get_rpc_manager() -> RPCManager:
    """Get the singleton RPC manager instance."""
    return RPCManager()
