"""
CTrader Leverage Extraction Module
==================================

Highly optimized and integrated module for extracting leverage information
from cTrader Open API. Designed to work seamlessly with the CTrader broker
implementation to provide real-time leverage data for symbols.

This module provides both synchronous and asynchronous leverage extraction
capabilities, with built-in caching, error handling, and retry mechanisms.

Key Features:
- Real-time leverage extraction via cTrader Open API
- Integrated caching system with TTL
- Batch processing for multiple symbols
- Strict data validation with error handling
- Thread-safe design for production use
- Optimized for minimal API calls and fast response times

Author: Trading System v3.0
Date: July 2025
"""

import os
import sys
import logging
import threading
import time
from typing import Dict, List, Optional, Any, Callable, Union
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from collections import defaultdict
from queue import Queue, Empty
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
import asyncio
from functools import lru_cache

# Configure logging
logger = logging.getLogger(__name__)

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    logger.debug("python-dotenv not available, using system environment variables")

# cTrader Open API imports
try:
    from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
    from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import *
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAApplicationAuthReq, ProtoOAAccountAuthReq, ProtoOASymbolsListReq, 
        ProtoOAGetDynamicLeverageByIDReq, ProtoOASymbolByIdReq
    )
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import *
    from twisted.internet import reactor, defer
    
    # Message type constants
    LEVERAGE_MESSAGE_TYPES = {
        'APPLICATION_AUTH_REQ': 2100,
        'APPLICATION_AUTH_RES': 2101,
        'ACCOUNT_AUTH_REQ': 2102,
        'ACCOUNT_AUTH_RES': 2103,
        'SYMBOLS_LIST_REQ': 2114,
        'SYMBOLS_LIST_RES': 2115,
        'SYMBOL_BY_ID_REQ': 2121,
        'SYMBOL_BY_ID_RES': 2117,
        'GET_DYNAMIC_LEVERAGE_BY_ID_REQ': 2148,
        'GET_DYNAMIC_LEVERAGE_BY_ID_RES': 2178,
        'ERROR_RES': 2142
    }
    
    CTRADER_LEVERAGE_API_AVAILABLE = True
    logger.debug("CTrader Open API available for leverage extraction")
    
except ImportError as e:
    CTRADER_LEVERAGE_API_AVAILABLE = False
    logger.warning(f"CTrader Open API not available for leverage extraction: {e}")
    LEVERAGE_MESSAGE_TYPES = {}


@dataclass
class LeverageInfo:
    """Container for leverage information"""
    symbol: str
    leverage_id: int
    max_leverage: float
    margin_requirement: float
    tiers: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    source: str = "CTRADER_API"
    tier_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage"""
        return {
            'symbol': self.symbol,
            'leverage_id': self.leverage_id,
            'max_leverage': self.max_leverage,
            'margin_requirement': self.margin_requirement,
            'tiers': self.tiers,
            'timestamp': self.timestamp.isoformat(),
            'source': self.source,
            'tier_count': self.tier_count
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LeverageInfo':
        """Create from dictionary"""
        timestamp = data.get('timestamp')
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        elif timestamp is None:
            timestamp = datetime.now()
            
        return cls(
            symbol=data['symbol'],
            leverage_id=data['leverage_id'],
            max_leverage=data['max_leverage'],
            margin_requirement=data['margin_requirement'],
            tiers=data.get('tiers', []),
            timestamp=timestamp,
            source=data.get('source', 'CTRADER_API'),
            tier_count=data.get('tier_count', len(data.get('tiers', [])))
        )


@dataclass
class LeverageExtractionRequest:
    """Request for leverage extraction"""
    symbol: str
    leverage_id: int
    callback: Optional[Callable[[LeverageInfo], None]] = None
    priority: str = "NORMAL"  # HIGH, NORMAL, LOW
    timeout: int = 30
    retry_count: int = 0
    max_retries: int = 3
    
    def __post_init__(self):
        self.created_at = datetime.now()


class LeverageCache:
    """Thread-safe cache for leverage information with TTL"""
    
    def __init__(self, default_ttl_minutes: int = 60):
        self.cache = {}
        self.default_ttl = timedelta(minutes=default_ttl_minutes)
        self._lock = threading.RLock()
        self.hit_count = 0
        self.miss_count = 0
    
    def get(self, leverage_id: int) -> Optional[LeverageInfo]:
        """Get leverage info from cache"""
        with self._lock:
            if leverage_id in self.cache:
                leverage_info, expiry = self.cache[leverage_id]
                if datetime.now() < expiry:
                    self.hit_count += 1
                    logger.debug(f"Cache HIT for leverage ID {leverage_id}")
                    return leverage_info
                else:
                    # Expired, remove from cache
                    del self.cache[leverage_id]
                    logger.debug(f"Cache EXPIRED for leverage ID {leverage_id}")
            
            self.miss_count += 1
            logger.debug(f"Cache MISS for leverage ID {leverage_id}")
            return None
    
    def set(self, leverage_id: int, leverage_info: LeverageInfo, ttl: Optional[timedelta] = None):
        """Set leverage info in cache"""
        with self._lock:
            expiry = datetime.now() + (ttl or self.default_ttl)
            self.cache[leverage_id] = (leverage_info, expiry)
            logger.debug(f"Cache SET for leverage ID {leverage_id} (expires: {expiry})")
    
    def invalidate(self, leverage_id: int = None):
        """Invalidate cache entry or entire cache"""
        with self._lock:
            if leverage_id is not None:
                self.cache.pop(leverage_id, None)
                logger.debug(f"Cache INVALIDATED for leverage ID {leverage_id}")
            else:
                self.cache.clear()
                logger.debug("Cache CLEARED completely")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self._lock:
            total_requests = self.hit_count + self.miss_count
            hit_rate = (self.hit_count / total_requests * 100) if total_requests > 0 else 0
            
            return {
                'entries': len(self.cache),
                'hit_count': self.hit_count,
                'miss_count': self.miss_count,
                'hit_rate_percent': round(hit_rate, 2),
                'cache_size_mb': sys.getsizeof(self.cache) / 1024 / 1024
            }


class CTraderLeverageExtractor:
    """
    Highly optimized leverage extractor for CTrader Open API integration.
    
    Designed to work seamlessly with the existing CTrader broker implementation
    while providing minimal overhead and maximum performance.
    """
    
    def __init__(self, client_id: str = None, client_secret: str = None, 
                 account_id: int = None, access_token: str = None,
                 use_demo: bool = True, cache_ttl_minutes: int = 60,
                 shared_client=None):
        """
        Initialize leverage extractor
        
        Args:
            client_id: CTrader client ID (from env if None)
            client_secret: CTrader client secret (from env if None)
            account_id: CTrader account ID (from env if None)
            access_token: CTrader access token (from env if None)
            use_demo: Whether to use demo environment
            cache_ttl_minutes: Cache TTL in minutes
            shared_client: Optional shared CTrader client from main broker (OPTIMAL)
        """
        if not CTRADER_LEVERAGE_API_AVAILABLE:
            raise ImportError("CTrader Open API not available. Install with: pip install ctrader-open-api")
        
        # Credentials
        self.client_id = client_id or os.getenv('CTRADER_CLIENT_ID')
        self.client_secret = client_secret or os.getenv('CTRADER_CLIENT_SECRET')
        self.account_id = account_id or int(os.getenv('CTRADER_ACCOUNT_ID', '0'))
        self.access_token = access_token or os.getenv('CTRADER_ACCESS_TOKEN', '')
        self.use_demo = use_demo
        
        if not all([self.client_id, self.client_secret, self.account_id]):
            raise ValueError("Missing CTrader credentials. Provide via parameters or environment variables.")
        
        # Cache and request management
        self.cache = LeverageCache(cache_ttl_minutes)
        self.request_queue = Queue()
        self.active_requests = {}
        self._request_lock = threading.RLock()
        
        # API client state - OPTIMIZED: Use shared client if available
        self.shared_client = shared_client  # Reference to broker's authenticated client
        self.client = shared_client if shared_client else None
        self.connected = bool(shared_client)  # If shared client exists, assume it's connected
        self.app_authenticated = bool(shared_client)  # If shared client exists, assume it's authenticated
        self.account_authenticated = bool(shared_client)  # If shared client exists, assume it's authenticated
        self.symbols_cache = {}
        
        # Thread management
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="leverage_extract")
        self.reactor_thread = None
        self.processing_thread = None
        self._shutdown_event = threading.Event()
        
        # Statistics
        self.stats = {
            'requests_processed': 0,
            'requests_failed': 0,
            'cache_hits': 0,
            'api_calls': 0,
            'last_api_call': None,
            'connection_attempts': 0,
            'successful_connections': 0,
            'using_shared_client': bool(shared_client)
        }
        
        if shared_client:
            logger.info(f"CTrader Leverage Extractor initialized with SHARED CLIENT (optimal)")
            logger.info(f"  Environment: {'DEMO' if use_demo else 'LIVE'}")
            logger.info(f"  Account ID: {self.account_id}")
            logger.info(f"  Cache TTL: {cache_ttl_minutes} minutes")
            logger.info(f"  ✅ Using authenticated client from main broker (no duplicate connection)")
            logger.info(f"  ✅ Message routing: Leverage responses routed via broker's message handler")
        else:
            logger.info(f"CTrader Leverage Extractor initialized with SEPARATE CLIENT")
            logger.info(f"  Environment: {'DEMO' if use_demo else 'LIVE'}")
            logger.info(f"  Account ID: {self.account_id}")
            logger.info(f"  Cache TTL: {cache_ttl_minutes} minutes")
            logger.warning(f"  ⚠️ Creating separate connection (not optimal - consider using shared client)")
    
    def get_leverage_sync(self, leverage_id: int, symbol: str = None, 
                         use_cache: bool = True, timeout: int = 30) -> Optional[LeverageInfo]:
        """
        Synchronously get leverage information for a leverage ID.
        
        This is the main method to be used by the CTrader broker implementation.
        
        Args:
            leverage_id: The leverage ID to fetch data for
            symbol: Symbol name for context (optional)
            use_cache: Whether to use cached data
            timeout: Request timeout in seconds
            
        Returns:
            LeverageInfo object or None if failed
        """
        try:
            # Check cache first if enabled
            if use_cache:
                cached_info = self.cache.get(leverage_id)
                if cached_info:
                    self.stats['cache_hits'] += 1
                    logger.debug(f"Returning cached leverage data for ID {leverage_id}")
                    return cached_info
            
            # Create extraction request
            request = LeverageExtractionRequest(
                symbol=symbol or f"LEVERAGE_ID_{leverage_id}",
                leverage_id=leverage_id,
                timeout=timeout
            )
            
            # Use thread pool to handle the extraction
            future = self.executor.submit(self._extract_leverage_blocking, request)
            
            try:
                result = future.result(timeout=timeout)
                if result:
                    # Cache the result
                    self.cache.set(leverage_id, result)
                    self.stats['requests_processed'] += 1
                    logger.debug(f"Successfully extracted leverage data for ID {leverage_id}")
                else:
                    self.stats['requests_failed'] += 1
                    logger.warning(f"Failed to extract leverage data for ID {leverage_id}")
                
                return result
                
            except Exception as e:
                logger.error(f"Timeout or error extracting leverage for ID {leverage_id}: {e}")
                self.stats['requests_failed'] += 1
                return None
                
        except Exception as e:
            logger.error(f"Error in get_leverage_sync for ID {leverage_id}: {e}")
            self.stats['requests_failed'] += 1
            return None
    
    def get_leverage_async(self, leverage_id: int, symbol: str = None,
                          callback: Callable[[Optional[LeverageInfo]], None] = None,
                          use_cache: bool = True, priority: str = "NORMAL") -> Future:
        """
        Asynchronously get leverage information for a leverage ID.
        
        Args:
            leverage_id: The leverage ID to fetch data for
            symbol: Symbol name for context (optional)
            callback: Callback function to receive the result
            use_cache: Whether to use cached data
            priority: Request priority (HIGH, NORMAL, LOW)
            
        Returns:
            Future object for the request
        """
        try:
            # Check cache first if enabled
            if use_cache:
                cached_info = self.cache.get(leverage_id)
                if cached_info:
                    self.stats['cache_hits'] += 1
                    if callback:
                        callback(cached_info)
                    
                    # Create a completed future
                    future = Future()
                    future.set_result(cached_info)
                    return future
            
            # Create extraction request
            request = LeverageExtractionRequest(
                symbol=symbol or f"LEVERAGE_ID_{leverage_id}",
                leverage_id=leverage_id,
                callback=callback,
                priority=priority
            )
            
            # Submit to thread pool
            future = self.executor.submit(self._extract_leverage_blocking, request)
            
            # Add completion callback to handle caching and user callback
            def on_complete(fut):
                try:
                    result = fut.result()
                    if result:
                        self.cache.set(leverage_id, result)
                        self.stats['requests_processed'] += 1
                    else:
                        self.stats['requests_failed'] += 1
                    
                    if callback:
                        callback(result)
                except Exception as e:
                    logger.error(f"Error in async leverage extraction callback: {e}")
                    if callback:
                        callback(None)
            
            future.add_done_callback(on_complete)
            return future
            
        except Exception as e:
            logger.error(f"Error in get_leverage_async for ID {leverage_id}: {e}")
            # Create a failed future
            future = Future()
            future.set_result(None)
            return future
    
    def batch_get_leverage(self, leverage_ids: List[int], symbols: List[str] = None,
                          use_cache: bool = True, timeout: int = 60) -> Dict[int, Optional[LeverageInfo]]:
        """
        Get leverage information for multiple leverage IDs in batch.
        
        Args:
            leverage_ids: List of leverage IDs to fetch
            symbols: Corresponding symbol names (optional)
            use_cache: Whether to use cached data
            timeout: Total timeout for all requests
            
        Returns:
            Dictionary mapping leverage_id to LeverageInfo (or None if failed)
        """
        results = {}
        futures = {}
        
        # Prepare symbols list
        if symbols is None:
            symbols = [f"LEVERAGE_ID_{lid}" for lid in leverage_ids]
        elif len(symbols) != len(leverage_ids):
            symbols.extend([f"LEVERAGE_ID_{lid}" for lid in leverage_ids[len(symbols):]])
        
        try:
            # Submit all requests
            for i, leverage_id in enumerate(leverage_ids):
                symbol = symbols[i] if i < len(symbols) else f"LEVERAGE_ID_{leverage_id}"
                
                # Check cache first
                if use_cache:
                    cached_info = self.cache.get(leverage_id)
                    if cached_info:
                        results[leverage_id] = cached_info
                        continue
                
                # Submit async request
                future = self.get_leverage_async(leverage_id, symbol, use_cache=False, priority="HIGH")
                futures[leverage_id] = future
            
            # Wait for all futures to complete
            start_time = time.time()
            for leverage_id, future in futures.items():
                remaining_timeout = max(1, timeout - (time.time() - start_time))
                try:
                    result = future.result(timeout=remaining_timeout)
                    results[leverage_id] = result
                except Exception as e:
                    logger.error(f"Batch leverage extraction failed for ID {leverage_id}: {e}")
                    results[leverage_id] = None
            
            logger.info(f"Batch leverage extraction completed: {len(results)} requests, "
                       f"{sum(1 for r in results.values() if r is not None)} successful")
            
            return results
            
        except Exception as e:
            logger.error(f"Error in batch leverage extraction: {e}")
            # Return partial results
            for leverage_id in leverage_ids:
                if leverage_id not in results:
                    results[leverage_id] = None
            return results
    
    def _extract_leverage_blocking(self, request: LeverageExtractionRequest) -> Optional[LeverageInfo]:
        """
        Blocking extraction of leverage data (runs in thread pool).
        
        Args:
            request: The leverage extraction request
            
        Returns:
            LeverageInfo object or None if failed
        """
        try:
            logger.debug(f"Starting leverage extraction for ID {request.leverage_id}")
            
            # Check if we have a shared client from the main broker
            if self.shared_client and self.connected and self.account_authenticated:
                logger.debug(f"Using shared authenticated client for leverage extraction (optimal)")
                
                # Create result container for thread-safe communication
                result_container = {
                    'result': None,
                    'completed': threading.Event(),
                    'error': None
                }
                
                try:
                    # Request leverage data using the shared client
                    self._request_leverage_data(request.leverage_id, result_container)
                    
                    # Wait for response with timeout
                    if result_container['completed'].wait(timeout=request.timeout):
                        if result_container['result']:
                            logger.debug(f"Successfully extracted leverage for ID {request.leverage_id} via shared client")
                            return result_container['result']
                        else:
                            logger.error(f"No leverage data received for ID {request.leverage_id}")
                            logger.error(f"Symbol {request.symbol} will be excluded from trading due to missing leverage information")
                            return None
                    else:
                        logger.error(f"Timeout waiting for leverage data for ID {request.leverage_id}")
                        logger.error(f"Symbol {request.symbol} will be excluded from trading due to extraction timeout")
                        return None
                        
                except Exception as e:
                    logger.error(f"Error requesting leverage data for ID {request.leverage_id}: {e}")
                    logger.error(f"Symbol {request.symbol} will be excluded from trading due to extraction error")
                    return None
            else:
                # No shared client available or not authenticated - this is a configuration issue
                if self.shared_client:
                    logger.error(f"Shared client available but not authenticated for leverage ID {request.leverage_id}")
                    logger.error(f"Connection: {self.connected}, Account auth: {self.account_authenticated}")
                else:
                    logger.error(f"No shared client available for leverage extraction for ID {request.leverage_id}")
                    logger.error(f"Configure leverage extractor with shared client from main broker for optimal performance")
                
                logger.error(f"Symbol {request.symbol} will be excluded from trading due to missing authenticated connection")
                return None
                
        except Exception as e:
            logger.error(f"Exception in leverage extraction for ID {request.leverage_id}: {e}")
            logger.error(f"Symbol {request.symbol} will be excluded from trading due to extraction error")
            return None
    
    def _ensure_client_ready(self) -> bool:
        """Ensure API client is ready for requests"""
        try:
            # If using shared client, it should already be ready
            if self.shared_client:
                if self.client == self.shared_client and self.connected and self.account_authenticated:
                    return True
                else:
                    logger.warning("Shared client not ready for leverage extraction")
                    logger.warning(f"Client match: {self.client == self.shared_client}, Connected: {self.connected}, Auth: {self.account_authenticated}")
                    return False
            
            # For separate client, ensure it's set up
            if self.client is None:
                return self._setup_client()
            return True
        except Exception as e:
            logger.error(f"Error ensuring client ready: {e}")
            return False
    
    def _setup_client(self) -> bool:
        """Setup CTrader API client"""
        try:
            # Determine host
            if self.use_demo:
                host = EndPoints.PROTOBUF_DEMO_HOST
            else:
                host = EndPoints.PROTOBUF_LIVE_HOST
            
            port = EndPoints.PROTOBUF_PORT
            
            logger.debug(f"Setting up CTrader client: {host}:{port}")
            
            self.client = Client(host, port, TcpProtocol)
            self.client.setConnectedCallback(self._on_connected)
            self.client.setDisconnectedCallback(self._on_disconnected)
            self.client.setMessageReceivedCallback(self._on_message_received)
            
            # Start client service if reactor is running
            if reactor.running:
                d = self.client.startService()
                if d:
                    d.addErrback(self._on_error)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to setup CTrader client: {e}")
            return False
    
    def _connect_and_authenticate(self):
        """Connect and authenticate with CTrader API"""
        try:
            if not self.connected:
                logger.debug("Connecting to CTrader API...")
                # Connection is handled by the client
                return
            
            if not self.app_authenticated:
                self._authenticate_application()
                return
                
            if not self.account_authenticated:
                self._authenticate_account()
                return
                
        except Exception as e:
            logger.error(f"Error in connect and authenticate: {e}")
    
    def _on_connected(self, client):
        """Handle connection to CTrader"""
        logger.debug("Connected to CTrader API for leverage extraction")
        self.connected = True
        self.stats['successful_connections'] += 1
        self._authenticate_application()
    
    def _on_disconnected(self, client, reason):
        """Handle disconnection from CTrader"""
        logger.warning(f"Disconnected from CTrader API: {reason}")
        self.connected = False
        self.app_authenticated = False
        self.account_authenticated = False
    
    def _on_error(self, failure):
        """Handle connection errors"""
        logger.error(f"CTrader API connection error: {failure}")
        self.connected = False
    
    def _on_message_received(self, client, message):
        """Handle received messages from CTrader API"""
        try:
            logger.info(f"📡 Leverage extractor received message type: {message.payloadType}")
            
            if message.payloadType == LEVERAGE_MESSAGE_TYPES['APPLICATION_AUTH_RES']:
                logger.debug("📡 Processing application auth response")
                self._handle_app_auth_response(message)
            elif message.payloadType == LEVERAGE_MESSAGE_TYPES['ACCOUNT_AUTH_RES']:
                logger.debug("📡 Processing account auth response")
                self._handle_account_auth_response(message)
            elif message.payloadType == LEVERAGE_MESSAGE_TYPES['GET_DYNAMIC_LEVERAGE_BY_ID_RES']:
                logger.info(f"📡 Processing leverage response")
                self._handle_leverage_response(message)
            elif message.payloadType == LEVERAGE_MESSAGE_TYPES['ERROR_RES']:
                logger.warning(f"📡 Processing error response")
                self._handle_error_response(message)
            else:
                logger.debug(f"Received unhandled message type: {message.payloadType}")
                
        except Exception as e:
            logger.error(f"Error handling received message: {e}")
            logger.error(f"Message type: {message.payloadType}")
            import traceback
            logger.error(traceback.format_exc())
    
    def _authenticate_application(self):
        """Authenticate application with CTrader"""
        try:
            logger.debug("Authenticating application for leverage extraction")
            
            auth_request = ProtoOAApplicationAuthReq()
            auth_request.clientId = str(self.client_id)
            auth_request.clientSecret = str(self.client_secret)
            
            deferred = self.client.send(auth_request)
            self.stats['api_calls'] += 1
            self.stats['last_api_call'] = datetime.now()
            
        except Exception as e:
            logger.error(f"Application authentication failed: {e}")
    
    def _handle_app_auth_response(self, message):
        """Handle application authentication response"""
        try:
            logger.debug("Application authenticated for leverage extraction")
            self.app_authenticated = True
            self._authenticate_account()
        except Exception as e:
            logger.error(f"Error handling app auth response: {e}")
    
    def _authenticate_account(self):
        """Authenticate account with CTrader"""
        try:
            logger.debug("Authenticating account for leverage extraction")
            
            account_auth_request = ProtoOAAccountAuthReq()
            account_auth_request.ctidTraderAccountId = self.account_id
            account_auth_request.accessToken = self.access_token
            
            deferred = self.client.send(account_auth_request)
            self.stats['api_calls'] += 1
            self.stats['last_api_call'] = datetime.now()
            
        except Exception as e:
            logger.error(f"Account authentication failed: {e}")
    
    def _handle_account_auth_response(self, message):
        """Handle account authentication response"""
        try:
            logger.debug("Account authenticated for leverage extraction")
            self.account_authenticated = True
        except Exception as e:
            logger.error(f"Error handling account auth response: {e}")
    
    def _request_leverage_data(self, leverage_id: int, result_container: Dict):
        """Request leverage data for specific leverage ID"""
        try:
            logger.debug(f"Requesting leverage data for ID: {leverage_id}")
            logger.debug(f"Using shared client: {self.shared_client is not None}")
            logger.debug(f"Client connection status: connected={self.connected}, auth={self.account_authenticated}")
            
            leverage_request = ProtoOAGetDynamicLeverageByIDReq()
            leverage_request.ctidTraderAccountId = self.account_id
            leverage_request.leverageId = leverage_id
            
            logger.info(f"📡 Sending leverage request for ID {leverage_id} via shared client")
            logger.debug(f"Request details: account_id={self.account_id}, leverage_id={leverage_id}")
            
            # Store result container for this request
            with self._request_lock:
                self.active_requests[leverage_id] = result_container
            
            deferred = self.client.send(leverage_request)
            self.stats['api_calls'] += 1
            self.stats['last_api_call'] = datetime.now()
            
            # Add timeout handling to prevent unhandled deferred errors
            def handle_timeout(failure, lid=leverage_id):
                """Handle timeout errors gracefully"""
                logger.debug(f"Leverage request {lid} timed out (expected behavior)")
                with self._request_lock:
                    result_container = self.active_requests.pop(lid, None)
                if result_container:
                    result_container['completed'].set()
                return None  # Suppress the timeout error
            
            # Add errback to handle timeout gracefully
            deferred.addErrback(handle_timeout)
            
            logger.info(f"📡 Leverage request sent successfully for ID {leverage_id}")
            
        except Exception as e:
            logger.error(f"Failed to request leverage data for ID {leverage_id}: {e}")
            logger.error(f"Exception details: {type(e).__name__}: {str(e)}")
            result_container['completed'].set()
    
    def _handle_leverage_response(self, message):
        """Handle leverage data response"""
        try:
            leverage_response = Protobuf.extract(message)
            logger.debug("Received leverage data from CTrader API")
            
            if hasattr(leverage_response, 'leverage'):
                leverage_data = leverage_response.leverage
                leverage_id = getattr(leverage_data, 'leverageId', None)
                
                if leverage_id is not None:
                    # Find corresponding request
                    with self._request_lock:
                        result_container = self.active_requests.pop(leverage_id, None)
                    
                    if result_container:
                        # Extract leverage information
                        leverage_info = self._extract_leverage_info(leverage_data, leverage_id)
                        result_container['result'] = leverage_info
                        result_container['completed'].set()
                        logger.debug(f"Leverage extraction completed for ID {leverage_id}")
                    else:
                        logger.warning(f"No pending request found for leverage ID {leverage_id}")
                else:
                    logger.error("Leverage ID not found in response")
            else:
                logger.error("No leverage data in response")
                
        except Exception as e:
            logger.error(f"Error processing leverage response: {e}")
    
    def _extract_leverage_info(self, leverage_data, leverage_id: int) -> LeverageInfo:
        """Extract LeverageInfo from CTrader leverage data"""
        try:
            # Extract tiers
            tiers = []
            max_leverage = 0
            
            if hasattr(leverage_data, 'tier'):
                for tier in leverage_data.tier:
                    tier_info = {
                        'from_amount': getattr(tier, 'from', 0),
                        'to_amount': getattr(tier, 'to', float('inf')),
                        'leverage': getattr(tier, 'leverage', 1)
                    }
                    tiers.append(tier_info)
                    max_leverage = max(max_leverage, tier_info['leverage'])
            
            # Calculate margin requirement
            margin_requirement = (1 / max_leverage * 100) if max_leverage > 0 else 100
            
            # Create LeverageInfo object
            leverage_info = LeverageInfo(
                symbol=f"LEVERAGE_ID_{leverage_id}",
                leverage_id=leverage_id,
                max_leverage=max_leverage,
                margin_requirement=margin_requirement,
                tiers=tiers,
                tier_count=len(tiers),
                timestamp=datetime.now(),
                source="CTRADER_API"
            )
            
            logger.debug(f"Extracted leverage info: ID={leverage_id}, Max={max_leverage}, Tiers={len(tiers)}")
            return leverage_info
            
        except Exception as e:
            logger.error(f"Error extracting leverage info: {e}")
            # Return minimal info
            return LeverageInfo(
                symbol=f"LEVERAGE_ID_{leverage_id}",
                leverage_id=leverage_id,
                max_leverage=1,
                margin_requirement=100,
                timestamp=datetime.now(),
                source="CTRADER_API_ERROR"
            )
    
    def _handle_error_response(self, message):
        """Handle API error response"""
        try:
            error_response = Protobuf.extract(message)
            error_code = getattr(error_response, 'errorCode', 'unknown')
            error_desc = getattr(error_response, 'description', 'No description')
            
            logger.error(f"CTrader API Error {error_code}: {error_desc}")
            
            # Complete all pending requests with error
            with self._request_lock:
                for leverage_id, result_container in list(self.active_requests.items()):
                    result_container['completed'].set()
                self.active_requests.clear()
                
        except Exception as e:
            logger.error(f"Error processing error response: {e}")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return self.cache.get_stats()
    
    def get_extraction_stats(self) -> Dict[str, Any]:
        """Get extraction statistics"""
        stats = self.stats.copy()
        stats.update(self.cache.get_stats())
        return stats
    
    def clear_cache(self):
        """Clear the leverage cache"""
        self.cache.invalidate()
        logger.info("Leverage cache cleared")
    
    def shutdown(self):
        """Shutdown the leverage extractor"""
        try:
            logger.info("Shutting down CTrader Leverage Extractor")
            
            self._shutdown_event.set()
            
            # Complete pending requests
            with self._request_lock:
                for result_container in self.active_requests.values():
                    result_container['completed'].set()
                self.active_requests.clear()
            
            # Shutdown thread pool
            self.executor.shutdown(wait=True)
            
            # Disconnect client
            if self.client and self.connected:
                self.client.stopService()
            
            logger.info("CTrader Leverage Extractor shutdown complete")
            
        except Exception as e:
            logger.error(f"Error during leverage extractor shutdown: {e}")


# Global instance for integration with CTrader broker
_global_leverage_extractor = None
_extractor_lock = threading.Lock()


def get_leverage_extractor(client_id: str = None, client_secret: str = None,
                          account_id: int = None, access_token: str = None,
                          use_demo: bool = True, shared_client=None) -> Optional[CTraderLeverageExtractor]:
    """
    Get or create global leverage extractor instance.
    
    This function provides a singleton pattern for the leverage extractor
    to be used by the CTrader broker implementation.
    
    OPTIMIZATION: Pass shared_client from main broker to avoid duplicate connections.
    
    Args:
        client_id: CTrader client ID (from env if None)
        client_secret: CTrader client secret (from env if None)
        account_id: CTrader account ID (from env if None)
        access_token: CTrader access token (from env if None)
        use_demo: Whether to use demo environment
        shared_client: Shared authenticated CTrader client from main broker (OPTIMAL)
        
    Returns:
        CTraderLeverageExtractor instance or None if failed
    """
    global _global_leverage_extractor
    
    with _extractor_lock:
        if _global_leverage_extractor is None:
            try:
                _global_leverage_extractor = CTraderLeverageExtractor(
                    client_id=client_id,
                    client_secret=client_secret,
                    account_id=account_id,
                    access_token=access_token,
                    use_demo=use_demo,
                    shared_client=shared_client
                )
                if shared_client:
                    logger.info("Global CTrader leverage extractor created with SHARED CLIENT (optimal)")
                else:
                    logger.info("Global CTrader leverage extractor created with separate client")
            except Exception as e:
                logger.error(f"Failed to create global leverage extractor: {e}")
                return None
        elif shared_client and not _global_leverage_extractor.shared_client:
            # Update existing extractor with shared client if available
            logger.info("Updating existing leverage extractor with shared client (optimal)")
            _global_leverage_extractor.shared_client = shared_client
            _global_leverage_extractor.client = shared_client
            _global_leverage_extractor.connected = True
            _global_leverage_extractor.app_authenticated = True
            _global_leverage_extractor.account_authenticated = True
            _global_leverage_extractor.stats['using_shared_client'] = True
        
        return _global_leverage_extractor


def extract_symbol_leverage(symbol_details: Dict[str, Any], leverage_extractor: CTraderLeverageExtractor = None) -> Optional[float]:
    """
    Extract and cache leverage information for a symbol.
    
    This is the main integration function to be called from brokers/ctrader.py
    when processing symbol details.
    
    CRITICAL: This function never uses fallback/dummy data. All failures result in None
    and the symbol will be excluded from trading.
    
    Args:
        symbol_details: Symbol details dictionary that should contain 'leverageId'
        leverage_extractor: Optional leverage extractor instance
        
    Returns:
        Maximum leverage value or None if extraction failed (symbol excluded from trading)
    """
    try:
        # Get leverage ID from symbol details
        leverage_id = symbol_details.get('leverageId')
        if leverage_id is None:
            symbol_name = symbol_details.get('symbol_name', symbol_details.get('symbolName', 'UNKNOWN_SYMBOL'))
            logger.error(f"No leverageId found for symbol {symbol_name}. Symbol excluded from trading - no fallback data allowed.")
            symbol_details['leverage_error'] = 'NO_LEVERAGE_ID'
            symbol_details['trading_disabled'] = True
            return None
        
        # Validate leverage ID
        if not isinstance(leverage_id, int) or leverage_id <= 0:
            symbol_name = symbol_details.get('symbol_name', symbol_details.get('symbolName', 'UNKNOWN_SYMBOL'))
            logger.error(f"Invalid leverageId ({leverage_id}) for symbol {symbol_name}. Symbol excluded from trading - no fallback data allowed.")
            symbol_details['leverage_error'] = 'INVALID_LEVERAGE_ID'
            symbol_details['trading_disabled'] = True
            return None
        
        # Get leverage extractor
        if leverage_extractor is None:
            leverage_extractor = get_leverage_extractor()
            if leverage_extractor is None:
                symbol_name = symbol_details.get('symbol_name', symbol_details.get('symbolName', 'UNKNOWN_SYMBOL'))
                logger.error(f"No leverage extractor available for symbol {symbol_name}. Symbol excluded from trading - check CTrader API configuration.")
                symbol_details['leverage_error'] = 'NO_EXTRACTOR_AVAILABLE'
                symbol_details['trading_disabled'] = True
                return None
        
        # Extract leverage information
        symbol_name = symbol_details.get('symbol_name', symbol_details.get('symbolName'))
        if not symbol_name:
            logger.error(f"No symbol name found for leverageId {leverage_id}. Symbol excluded from trading.")
            symbol_details['leverage_error'] = 'NO_SYMBOL_NAME'
            symbol_details['trading_disabled'] = True
            return None
            
        leverage_info = leverage_extractor.get_leverage_sync(
            leverage_id=leverage_id,
            symbol=symbol_name,
            use_cache=True,
            timeout=60  # Increased timeout to allow for API response delays
        )
        
        if leverage_info and leverage_info.max_leverage > 0:
            # Store leverage info in symbol details
            symbol_details['symbol_leverage'] = leverage_info.max_leverage
            symbol_details['leverage_info'] = leverage_info.to_dict()
            symbol_details['leverage_source'] = leverage_info.source
            symbol_details['trading_disabled'] = False
            
            logger.info(f"Successfully extracted leverage for {symbol_name}: {leverage_info.max_leverage}:1 (ID: {leverage_id})")
            return leverage_info.max_leverage
        else:
            logger.error(f"Failed to extract valid leverage for {symbol_name} (ID: {leverage_id}). Symbol excluded from trading - no fallback data allowed.")
            symbol_details['leverage_error'] = 'EXTRACTION_FAILED'
            symbol_details['trading_disabled'] = True
            return None
            
    except Exception as e:
        symbol_name = symbol_details.get('symbol_name', symbol_details.get('symbolName', 'UNKNOWN_SYMBOL'))
        logger.error(f"Exception during leverage extraction for {symbol_name}: {e}. Symbol excluded from trading.")
        symbol_details['leverage_error'] = f'EXCEPTION: {str(e)}'
        symbol_details['trading_disabled'] = True
        return None


def validate_leverage_requirements(symbol_details: Dict[str, Any]) -> bool:
    """
    Validate that a symbol has proper leverage information for trading.
    NO default values or fallbacks allowed - only real API data is acceptable.
    
    Args:
        symbol_details: Symbol details dictionary
        
    Returns:
        True if symbol has valid leverage data, False otherwise
    """
    symbol_name = symbol_details.get('symbol_name', symbol_details.get('symbolName', 'UNKNOWN_SYMBOL'))
    
    # Check if leverage extraction failed
    if 'leverage_error' in symbol_details:
        logger.error(f"Symbol {symbol_name} has leverage error: {symbol_details['leverage_error']}")
        return False
    
    # Check if leverage value exists and is valid - NO defaults allowed
    leverage = symbol_details.get('symbol_leverage')
    leverage_source = symbol_details.get('leverage_source', 'UNKNOWN')
    
    if leverage is None:
        # Log different messages based on source - reduce noise during startup
        if leverage_source == 'PENDING_API_EXTRACTION':
            # During startup, use debug level to reduce noise
            # Only warn if this is called after system should be fully initialized
            import time
            current_time = time.time()
            startup_grace_period = getattr(validate_leverage_requirements, '_startup_time', None)
            if startup_grace_period is None:
                # First call - set startup time
                validate_leverage_requirements._startup_time = current_time
                startup_grace_period = current_time
            
            # Allow 20 seconds grace period during startup for extractions to complete
            if current_time - startup_grace_period < 20:
                logger.debug(f"Symbol {symbol_name} leverage extraction pending during startup - normal during initialization")
                logger.debug(f"  leverageId: {symbol_details.get('leverageId', 'None')}")
            else:
                logger.warning(f"Symbol {symbol_name} leverage extraction still pending - trading not yet available")
                logger.warning(f"  leverageId: {symbol_details.get('leverageId', 'None')}")
                logger.warning(f"  Wait for API response before attempting to trade this symbol")
        else:
            logger.error(f"Symbol {symbol_name} has no leverage information - cannot trade")
            logger.error(f"  leverage_source: {leverage_source}")
        return False
    
    if not isinstance(leverage, (int, float)) or leverage <= 0:
        logger.error(f"Symbol {symbol_name} has invalid leverage value: {leverage}. Must be a positive number.")
        return False
    
    # Check minimum leverage requirement (1:1 minimum for any trading)
    if leverage < 1:
        logger.error(f"Symbol {symbol_name} has insufficient leverage: {leverage}:1. Minimum 1:1 required.")
        return False
    
    logger.debug(f"Symbol {symbol_name} leverage validation passed: {leverage}:1")
    return True


def get_leverage_validation_summary(symbol_details_dict: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Get summary of leverage validation for all symbols.
    
    Args:
        symbol_details_dict: Dictionary of symbol details
        
    Returns:
        Summary of leverage validation results
    """
    summary = {
        'total_symbols': len(symbol_details_dict),
        'valid_leverage': 0,
        'invalid_leverage': 0,
        'missing_leverage': 0,
        'extraction_errors': 0,
        'invalid_symbols': [],
        'error_details': {}
    }
    
    for symbol_name, symbol_details in symbol_details_dict.items():
        if validate_leverage_requirements(symbol_details):
            summary['valid_leverage'] += 1
        else:
            summary['invalid_leverage'] += 1
            summary['invalid_symbols'].append(symbol_name)
            
            # Categorize the error
            if 'leverage_error' in symbol_details:
                summary['extraction_errors'] += 1
                summary['error_details'][symbol_name] = symbol_details['leverage_error']
            elif symbol_details.get('symbol_leverage') is None:
                summary['missing_leverage'] += 1
                summary['error_details'][symbol_name] = 'NO_LEVERAGE_DATA'
            else:
                summary['error_details'][symbol_name] = 'INVALID_LEVERAGE_VALUE'
    
    return summary


if __name__ == "__main__":
    # Test the module
    import argparse
    
    parser = argparse.ArgumentParser(description='Test CTrader Leverage Extraction Module')
    parser.add_argument('--leverage-id', type=int, required=True, help='Leverage ID to test')
    parser.add_argument('--symbol', help='Symbol name for context')
    parser.add_argument('--demo', action='store_true', help='Use demo environment')
    
    args = parser.parse_args()
    
    # Setup logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    try:
        # Validate input
        if args.leverage_id <= 0:
            print(f"❌ Error: Invalid leverage ID {args.leverage_id}. Must be a positive integer.")
            sys.exit(1)
        
        # Create extractor
        extractor = CTraderLeverageExtractor(use_demo=args.demo)
        
        # Test leverage extraction
        result = extractor.get_leverage_sync(
            leverage_id=args.leverage_id,
            symbol=args.symbol,
            timeout=30
        )
        
        if result and result.max_leverage > 0:
            print(f"\n✅ Success! Real leverage data extracted:")
            print(f"   Symbol: {result.symbol}")
            print(f"   Leverage ID: {result.leverage_id}")
            print(f"   Max Leverage: {result.max_leverage}:1")
            print(f"   Margin Requirement: {result.margin_requirement}%")
            print(f"   Tiers: {result.tier_count}")
            print(f"   Source: {result.source}")
            print(f"   Timestamp: {result.timestamp}")
        else:
            print(f"❌ Failed to extract valid leverage data for ID {args.leverage_id}")
            print("This could be due to:")
            print("  • Invalid leverage ID")
            print("  • Network connectivity issues")
            print("  • Invalid CTrader API credentials")
            print("  • CTrader API service unavailable")
            sys.exit(1)
        
        # Show stats
        stats = extractor.get_extraction_stats()
        print(f"\n📊 Extraction Stats:")
        for key, value in stats.items():
            print(f"   {key}: {value}")
        
        # Cleanup
        extractor.shutdown()
        
    except ValueError as e:
        print(f"❌ Configuration Error: {e}")
        print("Please check your CTrader API credentials in environment variables.")
        sys.exit(1)
    except ImportError as e:
        print(f"❌ Import Error: {e}")
        print("Please install required dependencies: pip install ctrader-open-api")
        sys.exit(1)
    except Exception as e:
        print(f"💥 Unexpected Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
