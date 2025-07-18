"""
CTrader Real-Time Trading Engine (Strategy-Agnostic)
===================================================

High-performance real-time trading engine for cTrader Open API
that works with any strategy implementing the BaseStrategy interface.

This engine promotes separation of concerns by delegating all strategy
decisions to the provided strategy instance while handling broker-specific
functionality like API communication, order execution, and risk management.

Author: Trading System v3.0
Date: July 2025
"""

import os
import time
import logging
import threading
import traceback
import pandas as pd
from collections import deque, defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Union, Any

from config import TradingConfig
from strategies.base_strategy import BaseStrategy, PairsStrategyInterface

logger = logging.getLogger(__name__)

# cTrader Open API imports
try:
    from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
    from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import *
    from ctrader_open_api.messages.OpenApiMessages_pb2 import *
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import *
    from twisted.internet import reactor, defer
    
    # Import specific classes to handle import issues
    try:
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOAApplicationAuthReq, ProtoOAApplicationAuthRes,
            ProtoOAAccountAuthReq, ProtoOAAccountAuthRes,
            ProtoOASymbolsListReq, ProtoOASymbolsListRes,
            ProtoOASymbolByIdReq, ProtoOASymbolByIdRes,
            ProtoOANewOrderReq, ProtoOASubscribeSpotsReq, ProtoOAUnsubscribeSpotsReq,
            ProtoOASpotEvent, ProtoOAExecutionEvent, ProtoOAGetTrendbarsRes,
            ProtoOASubscribeSpotsRes
        )
        from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
            ProtoOAOrderType, ProtoOATradeSide
        )
    except ImportError as specific_import_error:
        logger.warning(f"Some specific cTrader classes not available: {specific_import_error}")
    
    # Define message type constants as fallbacks
    # cTrader Open API message types - official values from cTrader documentation
    # CRITICAL FIX: Updated to handle ProtoOAExecutionEvent properly
    # According to cTrader docs, execution events contain order/deal/position objects
    MESSAGE_TYPES = {
        'APPLICATION_AUTH_RES': 2101,  # PROTO_OA_APPLICATION_AUTH_RES
        'ACCOUNT_AUTH_RES': 2103,     # PROTO_OA_ACCOUNT_AUTH_RES
        'SYMBOLS_LIST_RES': 2115,     # PROTO_OA_SYMBOLS_LIST_RES
        'SYMBOL_BY_ID_RES': 2117,     # PROTO_OA_SYMBOL_BY_ID_RES
        'SPOT_EVENT': 2131,           # PROTO_OA_SPOT_EVENT
        'EXECUTION_EVENT': 2126,      # PROTO_OA_EXECUTION_EVENT (critical for order confirmations!)
        'ORDER_ERROR_EVENT': 2132,    # PROTO_OA_ORDER_ERROR_EVENT
        'SUBSCRIBE_SPOTS_RES': 2128,  # PROTO_OA_SUBSCRIBE_SPOTS_RES
        'TRENDBAR_RES': 2138          # PROTO_OA_GET_TRENDBARS_RES
    }
    
    CTRADER_API_AVAILABLE = True
except ImportError as e:
    CTRADER_API_AVAILABLE = False
    logger.warning(f"cTrader Open API not available: {e}")
    MESSAGE_TYPES = {}


class CTraderRealTimeTrader:
    """
    Strategy-agnostic real-time trading engine for cTrader Open API.
    
    This class handles all broker-specific functionality while delegating
    strategy decisions to the provided strategy instance. Supports any
    strategy that implements the BaseStrategy interface.
    """
    
    def __init__(self, config: TradingConfig, data_manager, strategy: BaseStrategy = None):
        if not CTRADER_API_AVAILABLE:
            raise ImportError("cTrader Open API not available. Install with: pip install ctrader-open-api")
        
        self.config = config
        self.data_manager = data_manager
        
        # Strategy handling - backwards compatibility
        if strategy is None:
            # Import here to avoid circular dependency
            from strategies.pairs_trading import OptimizedPairsStrategy
            self.strategy = OptimizedPairsStrategy(config, data_manager)
            logger.warning("No strategy provided, using default OptimizedPairsStrategy for backwards compatibility")
        else:
            if not isinstance(strategy, BaseStrategy):
                raise TypeError("Strategy must implement BaseStrategy interface")
            self.strategy = strategy
            # Ensure the strategy has access to the data manager
            if hasattr(self.strategy, 'data_manager') and self.strategy.data_manager is None:
                self.strategy.data_manager = data_manager
                logger.info("Updated strategy with data manager for cost calculations")
        
        # Detect strategy type for optimized handling
        self.is_pairs_strategy = isinstance(self.strategy, PairsStrategyInterface)
        
        # cTrader API setup
        self.client = None
        self.account_id = int(os.getenv('CTRADER_ACCOUNT_ID', '0'))
        self.client_id = os.getenv('CTRADER_CLIENT_ID')
        self.client_secret = os.getenv('CTRADER_CLIENT_SECRET')
        self.access_token = os.getenv('CTRADER_ACCESS_TOKEN')
        
        # Trading state
        self.active_positions = {}
        self.pair_states = {}
        self.is_trading = False
        self.last_update = {}
        self.symbols_map = {}
        self.symbol_id_to_name_map = {}
        self.symbol_details = {}
        
        # Performance optimization
        self._price_buffer = defaultdict(lambda: deque(maxlen=1000))
        self._update_lock = threading.Lock()
        
        # Real-time data state
        self.spot_prices = {}
        self.price_history = defaultdict(lambda: deque(maxlen=500))
        self.subscribed_symbols = set()
        self.execution_requests = {}
        self.pending_pair_trades = {}  # Track pending pair trades awaiting execution confirmation
        self.next_order_id = 1
        
        # Data throttling to reduce duplicate processing
        self._last_strategy_check = {}  # Track last strategy check time per pair
        self._min_check_interval = 0.5  # Minimum 500ms between strategy checks per pair
        self._price_update_buffer = defaultdict(lambda: deque(maxlen=10))  # Buffer recent updates
        
        # Trading threads
        self.trading_thread = None
        self.symbols_request_time = None
        self.symbols_initialized = False
        self.trading_started = False  # Track if trading loop has started
        self.authentication_in_progress = False
        self._degraded_mode = False  # Flag for degraded mode operation
        
        # Drawdown tracking (consistent with MT5 implementation)
        self.portfolio_peak_value = config.initial_portfolio_value
        self.pair_peak_values = {}
        self.suspended_pairs = set()
        self.portfolio_trading_suspended = False
        
        # Get account currency
        self.account_currency = "USD"  # Default, will be updated from account info
        
        # Log strategy information
        strategy_info = self.strategy.get_strategy_info()
        logger.info(f"Strategy-Agnostic CTrader Trader initialized")
        logger.info(f"  Strategy: {strategy_info['name']}")
        logger.info(f"  Type: {strategy_info['type']}")
        logger.info(f"  Required symbols: {len(strategy_info['required_symbols'])}")
        logger.info(f"  Tradeable instruments: {len(strategy_info['tradeable_instruments'])}")
    
    def _create_protobuf_request(self, request_type: str, **kwargs):
        """Safely create protobuf requests with fallback handling"""
        try:
            if request_type == 'APPLICATION_AUTH':
                if globals().get('ProtoOAApplicationAuthReq'):
                    request = ProtoOAApplicationAuthReq()
                    request.clientId = kwargs.get('client_id')
                    request.clientSecret = kwargs.get('client_secret')
                    return request
            elif request_type == 'ACCOUNT_AUTH':
                if globals().get('ProtoOAAccountAuthReq'):
                    request = ProtoOAAccountAuthReq()
                    request.ctidTraderAccountId = kwargs.get('account_id')
                    request.accessToken = kwargs.get('access_token')
                    return request
            elif request_type == 'SYMBOLS_LIST':
                if globals().get('ProtoOASymbolsListReq'):
                    request = ProtoOASymbolsListReq()
                    request.ctidTraderAccountId = kwargs.get('account_id')
                    request.includeArchivedSymbols = kwargs.get('include_archived', False)
                    return request
            elif request_type == 'SYMBOL_BY_ID':
                if globals().get('ProtoOASymbolByIdReq'):
                    request = ProtoOASymbolByIdReq()
                    request.ctidTraderAccountId = kwargs.get('account_id')
                    request.symbolId.extend(kwargs.get('symbol_ids', []))
                    return request
            elif request_type == 'SUBSCRIBE_SPOTS':
                if globals().get('ProtoOASubscribeSpotsReq'):
                    request = ProtoOASubscribeSpotsReq()
                    request.ctidTraderAccountId = kwargs.get('account_id')
                    request.symbolId.extend(kwargs.get('symbol_ids', []))
                    return request
            elif request_type == 'NEW_ORDER':
                if globals().get('ProtoOANewOrderReq'):
                    request = ProtoOANewOrderReq()
                    request.ctidTraderAccountId = kwargs.get('account_id')
                    request.symbolId = kwargs.get('symbol_id')
                    if globals().get('ProtoOAOrderType'):
                        request.orderType = ProtoOAOrderType.Value("MARKET")
                    request.tradeSide = kwargs.get('trade_side')
                    request.clientOrderId = kwargs.get('client_order_id')
                    request.volume = kwargs.get('volume')
                    return request
            elif request_type == 'UNSUBSCRIBE_SPOTS':
                if globals().get('ProtoOAUnsubscribeSpotsReq'):
                    request = ProtoOAUnsubscribeSpotsReq()
                    request.ctidTraderAccountId = kwargs.get('account_id')
                    request.symbolId.extend(kwargs.get('symbol_ids', []))
                    return request
                    
        except Exception as e:
            logger.error(f"Error creating {request_type} request: {e}")
            
        return None
    
    def _get_trade_side_value(self, side_name: str):
        """Safely get trade side value with fallback"""
        try:
            if globals().get('ProtoOATradeSide'):
                return ProtoOATradeSide.Value(side_name)
            else:
                # Fallback numeric values for trade sides
                side_values = {'BUY': 1, 'SELL': 2}
                return side_values.get(side_name, 1)
        except Exception as e:
            logger.error(f"Error getting trade side for {side_name}: {e}")
            return 1 if side_name == 'BUY' else 2
    
    def initialize(self) -> bool:
        """Initialize cTrader real-time trading system"""
        logger.info("Initializing Strategy-Agnostic CTrader trading system...")
        
        # Log environment configuration for debugging
        logger.info("🔍 CTRADER CONFIGURATION:")
        logger.info(f"   Account ID: {self.account_id}")
        logger.info(f"   Client ID: {self.client_id[:8] + '...' if self.client_id else 'NOT SET'}")
        logger.info(f"   Client Secret: {'SET' if self.client_secret else 'NOT SET'}")
        logger.info(f"   Access Token: {'SET' if self.access_token else 'NOT SET'}")
        logger.info(f"   Host Type: {os.getenv('CTRADER_HOST_TYPE', 'Live')}")
        
        # Validate credentials
        if not all([self.client_id, self.client_secret, self.access_token]):
            logger.error("Missing cTrader credentials. Check CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET, and CTRADER_ACCESS_TOKEN")
            return False
        
        if self.account_id == 0:
            logger.error("Missing or invalid CTRADER_ACCOUNT_ID. Please set a valid account ID.")
            return False
        
        # Initialize client
        if not self._setup_client():
            logger.error("Failed to setup cTrader client")
            return False
        
        # Initialize data (this will be handled by connection callback)
        self.is_trading = True
        logger.info("CTrader real-time trading initialized")
        return True
    
    def _setup_client(self) -> bool:
        """Setup cTrader API client with proper callbacks"""
        try:
            # Get host based on environment variable
            host_type = os.getenv('CTRADER_HOST_TYPE', 'Live').lower()
            if host_type == 'demo':
                host = EndPoints.PROTOBUF_DEMO_HOST
                logger.info(f"🔧 Using DEMO server: {host}:{EndPoints.PROTOBUF_PORT}")
            else:
                host = EndPoints.PROTOBUF_LIVE_HOST
                logger.info(f"🔧 Using LIVE server: {host}:{EndPoints.PROTOBUF_PORT}")
            
            port = EndPoints.PROTOBUF_PORT
            
            logger.info(f"Connecting to cTrader API at {host}:{port}")
            
            self.client = Client(host, port, TcpProtocol)
            self.client.setConnectedCallback(self._on_connected)
            self.client.setDisconnectedCallback(self._on_disconnected)
            self.client.setMessageReceivedCallback(self._on_message_received)
            
            # Start the client - handle case where startService returns None
            logger.info("Starting cTrader client service...")
            d = self.client.startService()
            if d is not None:
                d.addErrback(self._on_error)
            
            return True
            
        except Exception as e:
            logger.error(f"Error setting up cTrader client: {e}")
            return False
    
    def _on_connected(self, client):
        """Callback when client connects to cTrader"""
        logger.info("Connected to cTrader API")
        self._authenticate_application()
    
    def _on_disconnected(self, client, reason):
        """Callback when client disconnects"""
        logger.warning(f"Disconnected from cTrader API: {reason}")
        
        # Attempt reconnection if trading is active
        if self.is_trading:
            threading.Timer(5.0, self._retry_connection).start()
    
    def _on_error(self, failure):
        """Handle connection errors"""
        logger.error(f"cTrader API error: {failure}")
    
    def _retry_connection(self):
        """Retry connection to cTrader API"""
        logger.info("Attempting to reconnect to cTrader API...")
        try:
            self._setup_client()
        except Exception as e:
            logger.error(f"Reconnection failed: {e}")
            if self.is_trading:
                threading.Timer(10.0, self._retry_connection).start()
    
    def _authenticate_application(self):
        """Authenticate the application with cTrader"""
        logger.info("Authenticating application with cTrader...")
        request = self._create_protobuf_request('APPLICATION_AUTH', 
                                               client_id=self.client_id, 
                                               client_secret=self.client_secret)
        
        if request is None:
            logger.error("Failed to create application authentication request")
            return
            
        deferred = self.client.send(request)
        deferred.addErrback(self._on_error)
    
    def _authenticate_account(self):
        """Authenticate the trading account"""
        logger.info("Authenticating account with cTrader...")
        request = self._create_protobuf_request('ACCOUNT_AUTH',
                                               account_id=self.account_id,
                                               access_token=self.access_token)
        
        if request is None:
            logger.error("Failed to create account authentication request")
            return
            
        deferred = self.client.send(request)
        deferred.addErrback(self._on_error)
    
    def _get_symbols_list(self):
        """Get the list of available symbols from cTrader"""
        if self.symbols_initialized:
            logger.debug("Symbols already initialized, skipping request")
            return
            
        logger.info("Requesting symbols list from cTrader...")
        self.symbols_request_time = datetime.now()
        
        try:
            request = self._create_protobuf_request('SYMBOLS_LIST',
                                                   account_id=self.account_id,
                                                   include_archived=False)
            
            if request is None:
                logger.error("Failed to create symbols list request")
                return
                
            deferred = self.client.send(request)
            deferred.addErrback(self._on_error)
            deferred.addTimeout(30, reactor)
            
        except Exception as e:
            logger.error(f"Error requesting symbols: {e}")
            raise
    
    def _request_symbol_details(self, symbol_ids: List[int]):
        """Request detailed symbol information using ProtoOASymbolByIdReq"""
        try:
            # Split into chunks if we have too many symbols (API may have limits)
            chunk_size = 50  # Conservative chunk size
            symbol_chunks = [symbol_ids[i:i + chunk_size] for i in range(0, len(symbol_ids), chunk_size)]
            
            for chunk_idx, chunk in enumerate(symbol_chunks):
                logger.info(f"Requesting symbol details chunk {chunk_idx + 1}/{len(symbol_chunks)} ({len(chunk)} symbols)")
                
                request = self._create_protobuf_request('SYMBOL_BY_ID',
                                                       account_id=self.account_id,
                                                       symbol_ids=chunk)
                
                if request is None:
                    logger.error(f"Failed to create symbol details request for chunk {chunk_idx + 1}")
                    continue
                
                deferred = self.client.send(request)
                deferred.addErrback(self._on_symbol_details_error)
                deferred.addTimeout(30, reactor)
                
        except Exception as e:
            logger.error(f"Error requesting symbol details: {e}")
            raise
    
    def _on_symbol_details_error(self, failure):
        """Handle symbol details request errors"""
        logger.error(f"Symbol details request error: {failure}")
        # Continue with basic symbol information if detailed request fails
        logger.warning("Continuing with basic symbol information only")
    
    def set_historical_data_cache(self, historical_data_cache: Dict[str, pd.Series]):
        """
        Set pre-fetched historical data cache to avoid reactor conflicts
        
        Args:
            historical_data_cache: Dict mapping symbol -> pandas Series of price data
        """
        logger.info(f"Setting historical data cache with {len(historical_data_cache)} symbols")
        
        self._historical_data_cache = historical_data_cache
        
        # Pre-populate pair states with cached data if pairs are already initialized
        if hasattr(self, 'pair_states') and self.pair_states:
            self._initialize_pairs_with_cached_data()
        
        logger.info("✅ Historical data cache set successfully")
    
    def _initialize_pairs_with_cached_data(self):
        """Initialize existing pair states with cached historical data"""
        logger.info("Initializing pair states with cached historical data...")
        
        if not hasattr(self, '_historical_data_cache'):
            logger.warning("No historical data cache available")
            return
        
        initialized_pairs = 0
        
        for pair_str, pair_state in self.pair_states.items():
            try:
                # Extract symbols from pair
                s1 = pair_state.get('symbol1')
                s2 = pair_state.get('symbol2')
                
                if not s1 or not s2:
                    logger.warning(f"Invalid symbols for pair {pair_str}: {s1}, {s2}")
                    continue
                
                # Get cached data
                data1 = self._historical_data_cache.get(s1)
                data2 = self._historical_data_cache.get(s2)
                
                # Check if both data sources exist and have content
                data1_valid = data1 is not None and (
                    (hasattr(data1, '__len__') and len(data1) > 0) or 
                    (hasattr(data1, 'empty') and not data1.empty)
                )
                data2_valid = data2 is not None and (
                    (hasattr(data2, '__len__') and len(data2) > 0) or 
                    (hasattr(data2, 'empty') and not data2.empty)
                )
                
                if data1_valid and data2_valid:
                    # Convert to deques for real-time processing
                    min_data_points = self.strategy.get_minimum_data_points()
                    price_deque1 = deque(maxlen=min_data_points * 2)
                    price_deque2 = deque(maxlen=min_data_points * 2)
                    
                    # Populate deques with cached data (handle different formats)
                    try:
                        if hasattr(data1, 'items'):  # Series with index
                            for timestamp, price in data1.items():
                                price_deque1.append((timestamp, price))
                        elif hasattr(data1, 'iterrows'):  # DataFrame
                            for timestamp, row in data1.iterrows():
                                price = row['close'] if 'close' in row else row.iloc[0]
                                price_deque1.append((timestamp, price))
                        else:  # Assume list or array-like
                            current_time = datetime.now()
                            for i, price in enumerate(data1):
                                timestamp = current_time - timedelta(minutes=15*i)
                                price_deque1.append((timestamp, price))
                        
                        if hasattr(data2, 'items'):  # Series with index
                            for timestamp, price in data2.items():
                                price_deque2.append((timestamp, price))
                        elif hasattr(data2, 'iterrows'):  # DataFrame
                            for timestamp, row in data2.iterrows():
                                price = row['close'] if 'close' in row else row.iloc[0]
                                price_deque2.append((timestamp, price))
                        else:  # Assume list or array-like
                            current_time = datetime.now()
                            for i, price in enumerate(data2):
                                timestamp = current_time - timedelta(minutes=15*i)
                                price_deque2.append((timestamp, price))
                        
                        # Update pair state
                        with self._update_lock:
                            pair_state['price1'] = price_deque1
                            pair_state['price2'] = price_deque2
                            
                            # Set timestamps based on data format
                            if hasattr(data1, 'index') and len(data1.index) > 0:
                                pair_state['last_candle_time'] = data1.index[-1]
                            else:
                                pair_state['last_candle_time'] = datetime.now()
                            
                            pair_state['last_update'] = datetime.now()
                            pair_state['historical_data_loaded'] = True
                        
                        initialized_pairs += 1
                        logger.info(f"✅ Initialized {pair_str} with cached data: {len(price_deque1)} bars")
                        
                    except Exception as e:
                        logger.error(f"Error processing cached data for {pair_str}: {e}")
                        logger.warning(f"⚠️ Failed to use cached data for pair {pair_str}")
                    
                else:
                    logger.warning(f"⚠️ No valid cached data for pair {pair_str} ({s1}, {s2})")
                    
            except Exception as e:
                logger.error(f"Error initializing pair {pair_str} with cached data: {e}")
        
        logger.info(f"✅ Initialized {initialized_pairs}/{len(self.pair_states)} pairs with cached historical data")

    
    def _on_message_received(self, client, message):
        """Handle incoming messages from cTrader API"""
        try:
            # Count message types for debugging
            if not hasattr(self, '_message_type_counts'):
                self._message_type_counts = defaultdict(int)
            self._message_type_counts[message.payloadType] += 1
            
            # Log message types periodically for debugging
            if sum(self._message_type_counts.values()) % 100 == 0:  # Back to normal frequency
                logger.info(f"📨 Message stats (last 100): {dict(self._message_type_counts)}")
                # Reset counts to avoid memory buildup
                if sum(self._message_type_counts.values()) > 500:
                    self._message_type_counts.clear()
            
            logger.debug(f"Received message type: {message.payloadType}")
            
            # Use both protobuf class checks and numeric fallbacks for message type detection
            if (hasattr(message, 'payloadType') and 
                (message.payloadType == MESSAGE_TYPES.get('APPLICATION_AUTH_RES', 2101) or
                 message.payloadType == 2101)):
                logger.info("Application authenticated with cTrader")
                self._authenticate_account()
                
            elif (message.payloadType == MESSAGE_TYPES.get('ACCOUNT_AUTH_RES', 2103) or
                  message.payloadType == 2103):
                logger.info("Account authenticated with cTrader")
                # Extract account details for verification
                response = Protobuf.extract(message)
                logger.info(f"🔍 ACCOUNT VERIFICATION:")
                logger.info(f"   Account ID: {getattr(response, 'ctidTraderAccountId', 'Unknown')}")
                logger.info(f"   Live Account: {getattr(response, 'liveAccount', 'Unknown')}")
                logger.info(f"   Account Type: {'LIVE' if getattr(response, 'liveAccount', False) else 'DEMO'}")
                
                if not self.symbols_initialized:  # Only request once
                    self._get_symbols_list()
                    
            elif (message.payloadType == MESSAGE_TYPES.get('SYMBOLS_LIST_RES', 2115) or
                  message.payloadType == 2115):
                logger.info("Received symbols list response")
                self._process_symbols_list(message)
                
            elif (message.payloadType == MESSAGE_TYPES.get('SYMBOL_BY_ID_RES', 2117) or
                  message.payloadType == 2117):
                logger.info("Received symbol details response")
                self._process_symbol_details(message)
                
            elif (message.payloadType == MESSAGE_TYPES.get('TRENDBAR_RES', 2138) or
                  message.payloadType == 2138):
                self._process_trendbar_data(message)
                
            elif (message.payloadType == MESSAGE_TYPES.get('SPOT_EVENT', 2131) or
                  message.payloadType == 2131):
                # Extract the spot event properly
                event = Protobuf.extract(message)
                self._process_spot_event(event)
                
            elif (message.payloadType == MESSAGE_TYPES.get('EXECUTION_EVENT', 2126) or
                  message.payloadType == 2126):  # Direct check for correct EXECUTION_EVENT type
                # Extract the execution event properly  
                logger.info(f"🎯 RECEIVED EXECUTION EVENT - payload type: {message.payloadType}")
                try:
                    event = Protobuf.extract(message)
                    logger.info(f"🎯 Successfully extracted execution event: {type(event)}")
                    self._process_execution_event(event)
                except Exception as e:
                    logger.error(f"🎯 Error extracting/processing execution event: {e}")
                    logger.error(f"🎯 Raw message: {message}")
                    traceback.print_exc()
                
            elif (message.payloadType == MESSAGE_TYPES.get('SUBSCRIBE_SPOTS_RES', 2128) or
                  message.payloadType == 2128):
                # Extract the subscription response to see if it was successful
                response = Protobuf.extract(message)
                logger.info(f"📡 Spot subscription response received: {response}")
                logger.debug("Spot subscription confirmed")
                
            elif (message.payloadType == MESSAGE_TYPES.get('ORDER_ERROR_EVENT', 2132) or
                  message.payloadType == 2132):
                # Handle order error events
                logger.error(f"❌ RECEIVED ORDER ERROR EVENT - payload type: {message.payloadType}")
                try:
                    event = Protobuf.extract(message)
                    logger.error(f"❌ Order error details: {event}")
                    # TODO: Add specific order error handling
                except Exception as e:
                    logger.error(f"❌ Error extracting order error event: {e}")
                
            else:
                # Log unhandled message types to help debug missing execution events
                if not hasattr(self, '_unhandled_messages_logged'):
                    self._unhandled_messages_logged = set()
                
                # Log each unhandled message type only once to avoid spam
                if message.payloadType not in self._unhandled_messages_logged:
                    logger.info(f"🔍 UNHANDLED MESSAGE TYPE: {message.payloadType}")
                    # Try to identify if this could be an execution event with different type
                    if message.payloadType in [2130, 2131, 2132, 2133, 2134, 2135]:  # Range around expected execution event
                        logger.warning(f"⚠️ Message type {message.payloadType} might be an execution event - check cTrader API docs")
                        # Since we see 2131 frequently, try processing it as execution event
                        if message.payloadType == 2131:
                            logger.warning(f"🚨 PROCESSING MESSAGE TYPE 2131 AS EXECUTION EVENT")
                            try:
                                event = Protobuf.extract(message)
                                self._process_execution_event(event)
                                return  # Exit early after processing
                            except Exception as e:
                                logger.debug(f"Failed to process 2131 as execution event: {e}")
                    self._unhandled_messages_logged.add(message.payloadType)
                
                # Always log execution-related messages for debugging
                if 'execution' in str(message).lower() or 'order' in str(message).lower():
                    logger.warning(f"🚨 POTENTIAL EXECUTION MESSAGE: type={message.payloadType}, content preview: {str(message)[:200]}")
                    
                    # Try to extract and process as execution event regardless of type
                    try:
                        event = Protobuf.extract(message)
                        if (hasattr(event, 'clientOrderId') or hasattr(event, 'orderStatus') or 
                            hasattr(event, 'executionType') or hasattr(event, 'dealId')):
                            logger.warning(f"🚨 PROCESSING AS EXECUTION EVENT: {message.payloadType}")
                            self._process_execution_event(event)
                            return  # Exit early after processing
                    except Exception as e:
                        logger.debug(f"Failed to process as execution event: {e}")
                
                logger.debug(f"Unhandled message type: {message.payloadType}")  # Changed from info to debug
                
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def _process_symbols_list(self, message):
        """Process the symbols list and initialize trading pairs"""
        logger.info("Processing symbols list...")
        
        try:
            # Extract the message content properly using Protobuf.extract
            response = Protobuf.extract(message)
            
            # Check if we have symbols in the response
            if not hasattr(response, 'symbol') or len(response.symbol) == 0:
                logger.error("No symbols received from cTrader API")
                raise ValueError("Empty symbols list received from cTrader")
            
            logger.info(f"Received symbols list: {len(response.symbol)} symbols")
            
            # Get required symbols from strategy before processing
            required_symbols = self.strategy.get_required_symbols()
            logger.info(f"Strategy requires {len(required_symbols)} symbols: {required_symbols}")
            
            # Collect symbol IDs only for symbols our strategy needs
            required_symbol_ids = []
            found_symbols = []
            
            for symbol in response.symbol:
                symbol_name = symbol.symbolName
                symbol_id = symbol.symbolId
                
                # Only store mappings for symbols our strategy needs
                if symbol_name in required_symbols:
                    self.symbols_map[symbol_name] = symbol_id
                    self.symbol_id_to_name_map[symbol_id] = symbol_name
                    required_symbol_ids.append(symbol_id)
                    found_symbols.append(symbol_name)
            
            # Identify missing symbols
            missing_symbols = set(required_symbols) - set(found_symbols)
            
            logger.info(f"Found {len(found_symbols)} required symbols in cTrader: {found_symbols}")
            if missing_symbols:
                logger.warning(f"Missing {len(missing_symbols)} required symbols: {missing_symbols}")
                logger.info("Trading system will continue with available symbols only")
            
            # Only request detailed symbol information for symbols we actually need
            if required_symbol_ids:
                logger.info(f"Requesting detailed symbol information for {len(required_symbol_ids)} required symbols...")
                self._request_symbol_details(required_symbol_ids)
            else:
                logger.error("No required symbols found in cTrader symbol list!")
                # Still finalize initialization to prevent hanging, but with empty symbol details
                if not hasattr(self, '_pair_states_initialized'):
                    self._finalize_initialization()
            
            logger.info(f"Successfully retrieved {len(self.symbols_map)} symbols from cTrader")
            self.symbols_initialized = True
            
            # Note: Do NOT initialize pair states here - wait for detailed symbol information
            # The initialization will be completed in _process_symbol_details() or _finalize_initialization()
            
        except Exception as e:
            logger.error(f"Error processing symbols list: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def _process_symbol_details(self, message):
        """Process detailed symbol information from ProtoOASymbolByIdRes"""
        logger.info("Processing detailed symbol information for required symbols...")
        
        try:
            # Extract the message content properly using Protobuf.extract
            response = Protobuf.extract(message)
            
            # Check if we have symbols in the response
            if not hasattr(response, 'symbol') or len(response.symbol) == 0:
                logger.warning("No detailed symbols received from cTrader API")
                return
                
            symbols_processed = 0
            required_symbols = self.strategy.get_required_symbols()
            
            for symbol in response.symbol:
                symbol_name = self.symbol_id_to_name_map.get(symbol.symbolId)
                
                if not symbol_name:
                    logger.warning(f"Symbol ID {symbol.symbolId} not found in name mapping")
                    continue
                
                # Verify this is a symbol we actually need
                if symbol_name not in required_symbols:
                    logger.debug(f"Skipping symbol {symbol_name} - not required by strategy")
                    continue
                
                # Extract detailed symbol information with proper error handling
                symbol_details = {}
                
                # Required fields - these should always be present according to API docs
                try:
                    symbol_details['digits'] = symbol.digits
                    symbol_details['pip_position'] = symbol.pipPosition
                except AttributeError as e:
                    logger.error(f"Missing required field for symbol {symbol_name}: {e}")
                    continue
                
                # Optional fields - handle missing values appropriately
                optional_fields = {
                    'min_volume': 'minVolume',
                    'max_volume': 'maxVolume', 
                    'step_volume': 'stepVolume',
                    'lot_size': 'lotSize',
                    'max_exposure': 'maxExposure',
                    'commission': 'commission',
                    'commission_type': 'commissionType',
                    'sl_distance': 'slDistance',
                    'tp_distance': 'tpDistance',
                    'gsl_distance': 'gslDistance',
                    'gsl_charge': 'gslCharge',
                    'min_commission': 'minCommission',
                    'min_commission_type': 'minCommissionType',
                    'min_commission_asset': 'minCommissionAsset',
                    'rollover_commission': 'rolloverCommission',
                    'swap_long': 'swapLong',
                    'swap_short': 'swapShort',
                    'swap_calculation_type': 'swapCalculationType',
                    'trading_mode': 'tradingMode',
                    'enable_short_selling': 'enableShortSelling',
                    'guaranteed_stop_loss': 'guaranteedStopLoss',
                    'precise_trading_commission_rate': 'preciseTradingCommissionRate',
                    'precise_min_commission': 'preciseMinCommission',
                    'measurement_units': 'measurementUnits'
                }
                
                for local_name, api_name in optional_fields.items():
                    if hasattr(symbol, api_name):
                        value = getattr(symbol, api_name)
                        
                        # Convert volume-related fields from centilots to standard lots
                        # According to cTrader API, volume fields are in centilots (lots * 100)
                        if local_name in ['min_volume', 'max_volume','lot_size','step_volume']:
                            # Convert from centilots to lots (divide by 100)
                            converted_value = value / 100.0
                            
                            # Additional validation and correction for unrealistic values
                            if local_name == 'min_volume' and converted_value >= 1:
                            #     # If min_volume > 10 lots after conversion, likely needs more division
                            #     # Try dividing by 1000 instead (some brokers use micro-lots)
                                converted_value = 0.01
                                logger.info(f"Applied lot conversion for {symbol_name} {local_name}: {value} -> {converted_value}")
                            elif local_name == 'step_volume' and converted_value >= 1:
                            #     # If step_volume > 1 lot after conversion, likely needs more division
                                converted_value = 0.01
                                logger.info(f"Applied lot conversion for {symbol_name} {local_name}: {value} -> {converted_value}")
                            
                            # # Ensure reasonable minimums
                            # if local_name == 'min_volume' and converted_value < 0.001:
                            #     converted_value = 0.01  # Default to 0.01 lots minimum
                            # elif local_name == 'step_volume' and converted_value < 0.001:
                            #     converted_value = 0.01  # Default to 0.01 lots step
                            
                            symbol_details[local_name] = converted_value
                            logger.info(f"Converted {local_name} from {value} centilots to {converted_value} lots")
                        # elif local_name == 'lot_size':
                        #     converted_value = value / 100.0
                        #     # lot_size represents contract size and should remain large
                        #     # Only convert if it seems to be in centilots format
                        #     if value < 1000:  # If lot_size is suspiciously small, it might be in wrong units
                        #         converted_value = value * 100.0  # Convert up
                        #         logger.info(f"Applied contract size conversion for {symbol_name}: {value} -> {converted_value}")
                        #     else:
                        #         converted_value = value  # Keep as-is if reasonable
                            
                        #     symbol_details[local_name] = converted_value
                        #     logger.debug(f"Lot size for {symbol_name}: {value} -> {converted_value}")
                        # else:
                        #     symbol_details[local_name] = value
                    else:
                        logger.debug(f"Optional field {api_name} not available for symbol {symbol_name}")
                        # Set reasonable defaults for missing volume constraints
                        # if local_name == 'min_volume':
                        #     symbol_details[local_name] = 0.01  # 0.01 lots minimum
                        # elif local_name == 'max_volume':
                        #     symbol_details[local_name] = 1000.0  # 1000 lots maximum  
                        # elif local_name == 'step_volume':
                        #     symbol_details[local_name] = 0.01  # 0.01 lots step
                        # elif local_name == 'lot_size':
                        #     symbol_details[local_name] = 100000.0  # Standard forex lot size
                logger.info(f"📊 Raw symbol details for {symbol_name}:")
                logger.info(f"   digits={symbol.digits if hasattr(symbol, 'digits') else 'N/A'}")
                logger.info(f"   pipPosition={symbol.pipPosition if hasattr(symbol, 'pipPosition') else 'N/A'}")
                logger.info(f"   minVolume={getattr(symbol, 'minVolume', 'N/A')} (raw)")
                logger.info(f"   maxVolume={getattr(symbol, 'maxVolume', 'N/A')} (raw)")
                logger.info(f"   stepVolume={getattr(symbol, 'stepVolume', 'N/A')} (raw)")
                logger.info(f"   lotSize={getattr(symbol, 'lotSize', 'N/A')} (raw)")
                
                logger.info(f"📊 Processed symbol details for {symbol_name}: {symbol_details}")
                # Store the detailed symbol information
                self.symbol_details[symbol_name] = symbol_details
                symbols_processed += 1
                
                logger.info(f"✅ Processed detailed info for {symbol_name}: "
                          f"digits={symbol_details.get('digits')}, "
                          f"min_volume={symbol_details.get('min_volume')}, "
                          f"max_volume={symbol_details.get('max_volume')}, "
                          f"lot_size={symbol_details.get('lot_size')}")
            
            logger.info(f"✅ Successfully processed detailed information for {symbols_processed}/{len(required_symbols)} required symbols")
            
            # Check if we have processed all required symbols
            if symbols_processed < len(required_symbols):
                missing_count = len(required_symbols) - symbols_processed
                logger.warning(f"⚠️ Missing detailed info for {missing_count} required symbols")
            else:
                logger.info("🎯 All required symbols have detailed information")
            
            # Now we can safely proceed with initialization since we have detailed symbol info
            if not hasattr(self, '_pair_states_initialized'):
                self._finalize_initialization()
                
        except Exception as e:
            logger.error(f"Error processing symbol details: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Continue with basic initialization even if detailed symbol info fails
            if not hasattr(self, '_pair_states_initialized'):
                logger.warning("Proceeding with basic symbol information due to detailed info processing error")
                self._finalize_initialization()
    
    def _finalize_initialization(self):
        """Finalize initialization after symbol details are processed"""
        logger.info("Finalizing trading system initialization...")
        
        # Verify we have symbol details for all required symbols
        required_symbols = self.strategy.get_required_symbols()
        missing_details = []
        
        for symbol in required_symbols:
            if symbol not in self.symbol_details:
                missing_details.append(symbol)
        
        if missing_details:
            logger.warning(f"⚠️ Missing symbol details for required symbols: {missing_details}")
            logger.warning("Some trading functionality may be limited")
        else:
            logger.info("✅ All required symbols have detailed information")
        
        # Mark that we've processed symbol details
        self._pair_states_initialized = True
        
        # Initialize pair states
        self._initialize_pair_states()
        
        # Initialize pairs with cached data if available (after pairs are created)
        if hasattr(self, '_historical_data_cache'):
            logger.info("Applying cached historical data to initialized pairs...")
            self._initialize_pairs_with_cached_data()
        
        # Subscribe to real-time data
        self._subscribe_to_data()
        
        # Start the trading loop now that we have real symbols
        if not self.trading_started:
            logger.info("🚀 Starting real trading loop with live cTrader data")
            threading.Thread(target=self._trading_loop, daemon=True).start()
            self.trading_started = True
    
    def _initialize_pair_states(self):
        """Initialize trading states based on strategy requirements"""
        logger.info("Initializing trading states...")
        
        # Get tradeable instruments from strategy
        tradeable_instruments = self.strategy.get_tradeable_instruments()
        required_symbols = self.strategy.get_required_symbols()
        min_data_points = self.strategy.get_minimum_data_points()
        
        all_symbols = set()
        valid_pairs = []
        
        # For pairs strategies
        if self.is_pairs_strategy:
            for instrument in tradeable_instruments:
                if isinstance(instrument, (tuple, list)) and len(instrument) == 2:
                    s1, s2 = instrument
                    pair_str = f"{s1}-{s2}"
                    
                    if s1 in self.symbols_map and s2 in self.symbols_map:
                        all_symbols.add(s1)
                        all_symbols.add(s2)
                        valid_pairs.append(pair_str)
                    else:
                        logger.debug(f"Pair {pair_str} skipped - symbols not available")
                elif isinstance(instrument, str) and '-' in instrument:
                    # Handle string format pairs
                    try:
                        s1, s2 = instrument.split('-')
                        if s1 in self.symbols_map and s2 in self.symbols_map:
                            all_symbols.add(s1)
                            all_symbols.add(s2)
                            valid_pairs.append(instrument)
                        else:
                            logger.debug(f"Pair {instrument} skipped - symbols not available")
                    except ValueError:
                        logger.warning(f"Invalid pair format: {instrument}")
        else:
            # For backwards compatibility, try config.pairs if strategy doesn't provide pairs
            for pair_str in getattr(self.config, 'pairs', []):
                try:
                    s1, s2 = pair_str.split('-')
                    if s1 in self.symbols_map and s2 in self.symbols_map:
                        all_symbols.add(s1)
                        all_symbols.add(s2)
                        valid_pairs.append(pair_str)
                except ValueError:
                    logger.warning(f"Invalid pair format: {pair_str}")
        
        logger.info(f"Initializing {len(valid_pairs)} valid pairs")
        
        # Calculate optimal lookback period based on enabled strategy features
        lookback_periods = [self.config.z_period]  # Z-score period is always needed
        
        # Only include correlation period if correlation test is enabled
        if getattr(self.config, 'enable_correlation', False):
            lookback_periods.append(getattr(self.config, 'corr_period', 100))
        
        # Only include ADF period if ADF test is enabled
        if getattr(self.config, 'enable_adf', False):
            lookback_periods.append(getattr(self.config, 'adf_period', 100))
        
        # Only include Johansen period if Johansen test is enabled
        if getattr(self.config, 'enable_johansen', False):
            lookback_periods.append(getattr(self.config, 'adf_period', 100))  # Johansen uses same period as ADF
        
        # Calculate optimal lookback bars (2x the maximum period for buffer)
        lookback_bars = max(lookback_periods) * 2
        
        logger.info(f"Using lookback period of {lookback_bars} bars (based on enabled features)")
        logger.info(f"  Z-score period: {self.config.z_period}")
        if getattr(self.config, 'enable_correlation', False):
            logger.info(f"  Correlation period: {getattr(self.config, 'corr_period', 100)} (enabled)")
        if getattr(self.config, 'enable_adf', False):
            logger.info(f"  ADF period: {getattr(self.config, 'adf_period', 100)} (enabled)")
        if getattr(self.config, 'enable_johansen', False):
            logger.info(f"  Johansen period: {getattr(self.config, 'adf_period', 100)} (enabled)")
        
        # Initialize pair states with historical data from data manager
        logger.info("Initializing pair states with historical data...")
        
        for pair_str in valid_pairs:
            s1, s2 = pair_str.split('-')
            
            try:
                # Initialize with empty deques first (historical data fetch conflicts with running reactor)
                self.pair_states[pair_str] = {
                    'symbol1': s1,
                    'symbol2': s2,
                    'price1': deque(maxlen=min_data_points * 2),
                    'price2': deque(maxlen=min_data_points * 2),
                    'position': None,
                    'last_signal': None,
                    'cooldown': 0,
                    'last_trade_time': None,
                    'entry_time': None,
                    'last_update': None
                }
                
                # Always initialize historical data - use threading to avoid reactor conflicts
                if hasattr(self.data_manager, 'get_historical_data'):
                    # Schedule historical data fetch in a separate thread to avoid reactor conflicts
                    threading.Thread(
                        target=self._fetch_historical_data_for_pair,
                        args=(pair_str, s1, s2, lookback_bars, min_data_points),
                        daemon=True
                    ).start()
                else:
                    logger.warning(f"Data manager does not support historical data retrieval for {pair_str}")
                    
            except Exception as e:
                logger.error(f"Error initializing {pair_str}: {e}")
                # Ensure we always have a valid pair state even if everything fails
                self.pair_states[pair_str] = {
                    'symbol1': s1,
                    'symbol2': s2,
                    'price1': deque(maxlen=min_data_points * 2),
                    'price2': deque(maxlen=min_data_points * 2),
                    'position': None,
                    'last_signal': None,
                    'cooldown': 0,
                    'last_trade_time': None,
                    'entry_time': None,
                    'last_update': None
                }
        
        logger.info(f"Initialized {len(self.pair_states)} pair states - historical data will be fetched asynchronously")
        
        # If no valid pairs, log a warning
        if len(valid_pairs) == 0:
            logger.warning("No valid pairs found! Check symbol availability in cTrader")
    
    def _fetch_historical_data_for_pair(self, pair_str: str, s1: str, s2: str, lookback_bars: int, min_data_points: int):
        """Fetch historical data for a specific pair - use cache if available, otherwise fetch from API"""
        try:
            logger.info(f"Fetching historical data for {pair_str}...")
            
            # Check if we have cached data available
            if hasattr(self, '_historical_data_cache'):
                data1 = self._historical_data_cache.get(s1)
                data2 = self._historical_data_cache.get(s2)
                
                # Check if both data sources exist and have content
                data1_valid = data1 is not None and (
                    (hasattr(data1, '__len__') and len(data1) > 0) or 
                    (hasattr(data1, 'empty') and not data1.empty)
                )
                data2_valid = data2 is not None and (
                    (hasattr(data2, '__len__') and len(data2) > 0) or 
                    (hasattr(data2, 'empty') and not data2.empty)
                )
                
                if data1_valid and data2_valid:
                    logger.info(f"✅ Using cached historical data for {pair_str}")
                    logger.info(f"   {s1}: {len(data1)} data points, {s2}: {len(data2)} data points")
                    
                    # Convert to deques for real-time processing
                    price_deque1 = deque(maxlen=min_data_points * 2)
                    price_deque2 = deque(maxlen=min_data_points * 2)
                    
                    # Populate deques with cached data (handle different data formats)
                    try:
                        if hasattr(data1, 'items'):  # Series with index
                            for timestamp, price in data1.items():
                                price_deque1.append((timestamp, price))
                        elif hasattr(data1, 'iterrows'):  # DataFrame
                            for timestamp, row in data1.iterrows():
                                price = row['close'] if 'close' in row else row.iloc[0]
                                price_deque1.append((timestamp, price))
                        else:  # Assume list or array-like
                            current_time = datetime.now()
                            for i, price in enumerate(data1):
                                timestamp = current_time - timedelta(minutes=15*i)  # Assume 15min intervals
                                price_deque1.append((timestamp, price))
                        
                        if hasattr(data2, 'items'):  # Series with index
                            for timestamp, price in data2.items():
                                price_deque2.append((timestamp, price))
                        elif hasattr(data2, 'iterrows'):  # DataFrame
                            for timestamp, row in data2.iterrows():
                                price = row['close'] if 'close' in row else row.iloc[0]
                                price_deque2.append((timestamp, price))
                        else:  # Assume list or array-like
                            current_time = datetime.now()
                            for i, price in enumerate(data2):
                                timestamp = current_time - timedelta(minutes=15*i)  # Assume 15min intervals
                                price_deque2.append((timestamp, price))
                        
                        # Thread-safe update of pair state with cached data
                        with self._update_lock:
                            if pair_str in self.pair_states:  # Ensure pair still exists
                                self.pair_states[pair_str]['price1'] = price_deque1
                                self.pair_states[pair_str]['price2'] = price_deque2
                                
                                # Set timestamps based on data format
                                if hasattr(data1, 'index') and len(data1.index) > 0:
                                    self.pair_states[pair_str]['last_candle_time'] = data1.index[-1]
                                else:
                                    self.pair_states[pair_str]['last_candle_time'] = datetime.now()
                                
                                self.pair_states[pair_str]['last_update'] = datetime.now()
                                self.pair_states[pair_str]['historical_data_loaded'] = True
                                
                                logger.info(f"✅ Successfully initialized {pair_str} with {len(price_deque1)} cached bars")
                                
                                # Get latest prices for logging
                                if price_deque1 and price_deque2:
                                    latest_price1 = price_deque1[-1][1]
                                    latest_price2 = price_deque2[-1][1]
                                    logger.info(f"   Latest prices: {s1}=${latest_price1:.5f}, {s2}=${latest_price2:.5f}")
                        
                        return  # Successfully initialized with cached data
                        
                    except Exception as cache_error:
                        logger.error(f"Error processing cached data for {pair_str}: {cache_error}")
                        logger.info(f"Will try to fetch from API instead")
                else:
                    if not data1_valid:
                        logger.info(f"No valid cached data for {s1}")
                    if not data2_valid:
                        logger.info(f"No valid cached data for {s2}")
                    logger.info(f"No cached data available for {pair_str}, will try to fetch from API")
            
            # Fallback to API fetch if no cache or cached data unavailable
            logger.info(f"Fetching historical data for {pair_str} from API in background thread...")
            
            # Calculate date range for historical data
            interval_mapping = {'D1': 1, 'H1': 1/24, 'M15': 1/(24*4), 'M5': 1/(24*12), 'M1': 1/(24*60)}
            days_per_bar = interval_mapping.get(self.config.interval, 1/24)
            lookback_days = int(lookback_bars * days_per_bar) + 10
            
            end_date_dt = datetime.now()
            start_date_dt = end_date_dt - timedelta(days=lookback_days)
            start_date_str = start_date_dt.strftime('%Y-%m-%d')
            end_date_str = end_date_dt.strftime('%Y-%m-%d')
            
            logger.info(f"Historical data range for {pair_str}: {start_date_str} to {end_date_str} ({lookback_days} days)")
            
            # Fetch historical data regardless of reactor status (this method runs in its own thread)
            try:
                # Use data manager's method without count parameter (CTrader compatible)
                # The data manager should handle reactor conflicts internally
                logger.info(f"Attempting to fetch historical data for {pair_str}...")
                data1 = self.data_manager.get_historical_data(
                    [s1], self.config.interval, start_date_str, end_date_str
                )
                data2 = self.data_manager.get_historical_data(
                    [s2], self.config.interval, start_date_str, end_date_str
                )
                
                # Extract the single symbol data if returned as dict
                if isinstance(data1, dict) and s1 in data1:
                    data1 = data1[s1]
                if isinstance(data2, dict) and s2 in data2:
                    data2 = data2[s2]
                
                # Check if data is valid (could be dict or DataFrame)
                data1_valid = False
                data2_valid = False
                
                if hasattr(data1, 'empty'):
                    data1_valid = not data1.empty
                elif isinstance(data1, dict):
                    data1_valid = len(data1) > 0
                elif data1 is not None:
                    data1_valid = len(data1) > 0
                
                if hasattr(data2, 'empty'):
                    data2_valid = not data2.empty
                elif isinstance(data2, dict):
                    data2_valid = len(data2) > 0
                elif data2 is not None:
                    data2_valid = len(data2) > 0
                
                if data1_valid and data2_valid:
                    # Convert DataFrame to deque of (timestamp, price) tuples for real-time processing
                    price_deque1 = deque(maxlen=min_data_points * 2)
                    price_deque2 = deque(maxlen=min_data_points * 2)
                    
                    # Handle DataFrame format
                    if hasattr(data1, 'iterrows'):
                        # Populate deques with historical data
                        for timestamp, row in data1.iterrows():
                            price_deque1.append((timestamp, row['close'] if 'close' in row else row.iloc[0]))
                        
                        for timestamp, row in data2.iterrows():
                            price_deque2.append((timestamp, row['close'] if 'close' in row else row.iloc[0]))
                    
                        # Thread-safe update of pair state with historical data
                        with self._update_lock:
                            if pair_str in self.pair_states:  # Ensure pair still exists
                                self.pair_states[pair_str]['price1'] = price_deque1
                                self.pair_states[pair_str]['price2'] = price_deque2
                                self.pair_states[pair_str]['last_candle_time'] = data1.index[-1] if len(data1) > 0 else None
                                self.pair_states[pair_str]['last_update'] = datetime.now()
                                
                                logger.info(f"✅ Successfully initialized {pair_str} with {len(data1)} historical bars")
                                logger.info(f"   Data range: {data1.index[0]} to {data1.index[-1]}")
                                logger.info(f"   Latest prices: {s1}=${data1.iloc[-1]['close']:.5f}, {s2}=${data2.iloc[-1]['close']:.5f}")
                    else:
                        logger.warning(f"❌ Historical data format not supported for {pair_str}")
                        logger.info(f"   Will accumulate real-time data instead")
                else:
                    logger.warning(f"❌ No valid historical data received for {pair_str}")
                    logger.info(f"   Will accumulate real-time data instead")
                    
            except Exception as data_error:
                logger.warning(f"❌ Data manager fetch failed for {pair_str}: {data_error}")
                logger.info(f"   Will accumulate real-time data instead")
                # Don't re-raise - allow trading to continue with real-time data only
                
        except Exception as e:
            logger.warning(f"❌ Historical data fetch failed for {pair_str}: {e}")
            logger.info(f"   Pair will start with empty data and accumulate real-time data")
            import traceback
            logger.debug(traceback.format_exc())
    
    def _subscribe_to_data(self):
        """Subscribe to real-time data for all symbols required by the strategy"""
        # Get required symbols from strategy
        required_symbols = self.strategy.get_required_symbols()
        
        # Filter to only available symbols
        available_symbols = {symbol for symbol in required_symbols if symbol in self.symbols_map}
        unavailable_symbols = set(required_symbols) - available_symbols
        
        if unavailable_symbols:
            logger.warning(f"Some required symbols not available: {unavailable_symbols}")
        
        logger.info(f"🔔 Starting spot price subscriptions for {len(available_symbols)} symbols...")
        logger.info(f"🔔 Available symbols: {sorted(available_symbols)}")
        
        # Subscribe to spot prices for available symbols
        successful_subscriptions = 0
        for symbol in available_symbols:
            if self._subscribe_to_spot_prices(symbol):
                successful_subscriptions += 1
        
        logger.info(f"✅ Real-time data subscriptions: {successful_subscriptions}/{len(available_symbols)} symbols")
        logger.info(f"✅ Trading system ready with {self.strategy.__class__.__name__}")
        if unavailable_symbols:
            logger.info("📝 Note: Some symbols were unavailable and skipped")
        
        # Add debug info about current spot prices
        logger.info(f"🔍 Current spot prices count: {len(self.spot_prices)}")
        if len(self.spot_prices) > 0:
            logger.info(f"🔍 Available spot prices: {list(self.spot_prices.keys())}")
        else:
            logger.warning("⚠️ No spot prices available yet - waiting for cTrader price updates...")
    
    def _subscribe_to_spot_prices(self, symbol):
        """Subscribe to real-time price updates for a symbol"""
        if symbol in self.symbols_map and symbol not in self.subscribed_symbols:
            symbol_id = self.symbols_map[symbol]
            
            logger.info(f"🔔 Subscribing to spot prices for {symbol} (ID: {symbol_id})")
            
            request = self._create_protobuf_request('SUBSCRIBE_SPOTS',
                                                   account_id=self.account_id,
                                                   symbol_ids=[symbol_id])
            
            if request is None:
                logger.error(f"Failed to create subscription request for {symbol}")
                return False
            
            try:
                deferred = self.client.send(request)
                deferred.addErrback(self._on_subscription_error, symbol)
                self.subscribed_symbols.add(symbol)
                logger.debug(f"Subscription request sent for {symbol}")
                return True
            except Exception as e:
                logger.error(f"Error subscribing to {symbol}: {e}")
                return False
        else:
            if symbol not in self.symbols_map:
                logger.warning(f"Symbol {symbol} not found in symbols_map")
            elif symbol in self.subscribed_symbols:
                logger.debug(f"Already subscribed to {symbol}")
        return False
    
    def _on_subscription_error(self, failure, symbol=None):
        """Handle subscription errors"""
        # Check if this is a timeout error (common with cTrader API when subscribing to many symbols)
        if hasattr(failure, 'value') and 'TimeoutError' in str(failure.value):
            # Timeout errors are expected behavior when subscribing to many symbols
            logger.debug(f"Subscription timeout for {symbol or 'unknown symbol'} (normal cTrader API behavior)")
        else:
            # Log actual errors that aren't timeouts
            logger.warning(f"Subscription error for {symbol or 'unknown symbol'}: {failure}")
    
    def _process_trendbar_data(self, message):
        """Process trendbar data (not used for real-time trading but needed for handler)"""
        logger.debug("Received trendbar data - ignored in real-time trading mode")
    
    def _get_price_from_relative(self, symbol_details, relative_price):
        """
        Convert relative price to actual price according to cTrader documentation
        
        According to cTrader API documentation:
        - All prices (bid, ask, trendbar prices) are returned in relative format
        - To get actual price: divide by 100000 and round to symbol digits
        - This applies to spot events, historical trendbars, and depth quotes
        
        Args:
            symbol_details: Dictionary containing symbol information including 'digits'
            relative_price: Raw price value from cTrader API
            
        Returns:
            Properly formatted price rounded to symbol digits
        """
        # Divide by 100000 and round to symbol digits as per cTrader documentation
        digits = symbol_details.get('digits', 5)  # Default to 5 digits if not available
        actual_price = relative_price / 100000.0
        return round(actual_price, digits)
    
    def _process_spot_event(self, event):
        """Process real-time price updates according to cTrader documentation"""
        symbol_id = event.symbolId
        symbol_name = self.symbol_id_to_name_map.get(symbol_id)
        
        if not symbol_name:
            # Log the first few unknown symbol IDs for debugging
            if not hasattr(self, '_unknown_symbol_debug_count'):
                self._unknown_symbol_debug_count = 0
            self._unknown_symbol_debug_count += 1
            if self._unknown_symbol_debug_count <= 10:
                logger.debug(f"Received spot event for unknown symbol ID: {symbol_id}")
                logger.debug(f"  Available mappings: {list(self.symbol_id_to_name_map.items())[:5]}...")
            return
        
        # Get symbol details for price conversion
        details = self.symbol_details.get(symbol_name)
        if not details:
            if not hasattr(self, '_missing_details_debug_count'):
                self._missing_details_debug_count = 0
            self._missing_details_debug_count += 1
            if self._missing_details_debug_count <= 10:
                logger.debug(f"No symbol details available for {symbol_name}")
                logger.debug(f"  Available details: {list(self.symbol_details.keys())[:5]}...")
            return
        
        # Extract bid/ask prices and convert according to cTrader documentation
        # Divide by 100000 and round to symbol digits
        bid = None
        ask = None
        
        if hasattr(event, 'bid') and event.bid is not None and event.bid > 0:
            bid = self._get_price_from_relative(details, event.bid)
            
        if hasattr(event, 'ask') and event.ask is not None and event.ask > 0:
            ask = self._get_price_from_relative(details, event.ask)
        
        timestamp = datetime.fromtimestamp(event.timestamp / 1000)
        
        # Handle incomplete price data - use last known good price or skip
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            # Try to use previous price if available
            if symbol_name in self.spot_prices:
                # Keep existing price if we have partial update
                existing_price = self.spot_prices[symbol_name]
                if not hasattr(self, '_incomplete_price_debug_count'):
                    self._incomplete_price_debug_count = {}
                if symbol_name not in self._incomplete_price_debug_count:
                    self._incomplete_price_debug_count[symbol_name] = 0
                self._incomplete_price_debug_count[symbol_name] += 1
                
                if self._incomplete_price_debug_count[symbol_name] <= 5:
                    logger.debug(f"Incomplete price data for {symbol_name}: bid={bid}, ask={ask}, keeping existing price=${existing_price:.5f}")
                return
            else:
                # No previous price available, skip this update
                if not hasattr(self, '_missing_prices_debug_count'):
                    self._missing_prices_debug_count = 0
                self._missing_prices_debug_count += 1
                if self._missing_prices_debug_count <= 10:
                    logger.debug(f"Missing or invalid bid/ask for {symbol_name}: bid={bid}, ask={ask}")
                return
        
        # Store mid price
        price = (bid + ask) / 2
        self.spot_prices[symbol_name] = price
        
        # Log first few price updates to confirm data flow
        if not hasattr(self, '_spot_debug_count'):
            self._spot_debug_count = 0
        self._spot_debug_count += 1
        
        if self._spot_debug_count <= 50:  # Log first 50 spot events for debugging
            logger.info(f"🔥 SPOT EVENT #{self._spot_debug_count}: {symbol_name} = ${price:.5f} (bid={bid}, ask={ask})")
            if self._spot_debug_count == 50:
                logger.info("🔥 Spot event debugging complete - future price updates will be throttled")
        
        # Log price updates periodically to prove data flow
        if not hasattr(self, '_price_log_counter'):
            self._price_log_counter = {}
        if symbol_name not in self._price_log_counter:
            self._price_log_counter[symbol_name] = 0
        
        self._price_log_counter[symbol_name] += 1
        
        # Log every 100th price update to show data flow without spam
        if self._price_log_counter[symbol_name] % 100 == 0:
            logger.info(f"📈 PRICE DATA FLOW: {symbol_name} = ${price:.5f} (updates: {self._price_log_counter[symbol_name]})")
        
        # Add to price history with duplicate prevention
        current_time = time.time()
        
        # Only add if price has changed significantly or enough time has passed
        price_history = self.price_history[symbol_name]
        should_add = True
        
        if price_history:
            last_timestamp, last_price = price_history[-1]
            price_diff = abs(price - last_price) / last_price if last_price > 0 else 1
            time_diff = (timestamp - last_timestamp).total_seconds()
            
            # Skip if price change is tiny and time difference is small
            if price_diff < 0.0001 and time_diff < 0.1:  # Less than 0.01% change in 100ms
                should_add = False
        
        if should_add:
            price_history.append((timestamp, price))
            
            # Update pair states
            self._update_pair_prices(symbol_name, price, timestamp)
            
            # Throttled trading signal check - only check every 500ms per symbol
            if symbol_name not in self._last_strategy_check:
                self._last_strategy_check[symbol_name] = 0
            
            if current_time - self._last_strategy_check[symbol_name] >= self._min_check_interval:
                self._last_strategy_check[symbol_name] = current_time
                # Check for trading signals (but throttled)
                self._check_trading_signals_throttled()
    
    def _check_trading_signals_throttled(self):
        """Throttled version of trading signal check to reduce computational load"""
        try:
            # Only check one pair per call to distribute load
            pair_keys = list(self.pair_states.keys())
            if not pair_keys:
                return
            
            # Round-robin through pairs
            if not hasattr(self, '_current_pair_index'):
                self._current_pair_index = 0
            
            pair_str = pair_keys[self._current_pair_index % len(pair_keys)]
            self._current_pair_index += 1
            
            # Log signal checking process every 20th check to prove it's working
            # if not hasattr(self, '_signal_check_counter'):
            #     self._signal_check_counter = 0
            # self._signal_check_counter += 1
            
            # if self._signal_check_counter % 20 == 0:
            #     logger.info(f"🧠 SIGNAL CHECKING: Processing {pair_str} (check #{self._signal_check_counter})")
            
            # Check this specific pair
            self._check_pair_trading_signals(pair_str)
            
        except Exception as e:
            logger.error(f"Error in throttled trading signal check: {e}")
    
    def _check_pair_trading_signals(self, pair_str: str):
        """Check trading signals for a specific pair"""
        try:
            state = self.pair_states.get(pair_str)
            if not state:
                return
                
            # Skip if in cooldown
            if state['cooldown'] > 0:
                state['cooldown'] -= 1
                return
            
            # Check if we have enough data for strategy calculation
            try:
                min_data_points = self.strategy.get_minimum_data_points()
            except Exception as e:
                logger.error(f"Error getting minimum data points from strategy: {e}")
                min_data_points = 50  # Fallback default
            
            if len(state['price1']) < min_data_points or len(state['price2']) < min_data_points:
                # Log data accumulation progress
                if not hasattr(self, '_data_accumulation_log'):
                    self._data_accumulation_log = {}
                if pair_str not in self._data_accumulation_log:
                    self._data_accumulation_log[pair_str] = 0
                
                self._data_accumulation_log[pair_str] += 1
                if self._data_accumulation_log[pair_str] % 50 == 0:
                    logger.info(f"📊 DATA ACCUMULATION: {pair_str} has {len(state['price1'])}/{min_data_points} points for {state['symbol1']}, {len(state['price2'])}/{min_data_points} for {state['symbol2']}")
                return
            
            # Extract recent prices for strategy calculation
            recent_length = max(min_data_points, getattr(self.config, 'z_period', 50))
            recent_prices1 = list(state['price1'])[-recent_length:]
            recent_prices2 = list(state['price2'])[-recent_length:]
            
            # Extract timestamps and prices separately
            timestamps1 = [p[0] for p in recent_prices1]
            timestamps2 = [p[0] for p in recent_prices2] 
            prices1 = [p[1] for p in recent_prices1]
            prices2 = [p[1] for p in recent_prices2]
            
            if len(prices1) != len(prices2) or len(prices1) < min_data_points:
                return
            
            # Create proper time-indexed pandas series with duplicate handling
            try:
                # Use timestamps as index for proper alignment
                series1 = pd.Series(prices1, index=pd.to_datetime(timestamps1), name=state['symbol1'])
                series2 = pd.Series(prices2, index=pd.to_datetime(timestamps2), name=state['symbol2'])
                
                # Remove duplicates before passing to strategy (pre-filtering)
                if series1.index.has_duplicates:
                    series1 = series1[~series1.index.duplicated(keep='last')]
                if series2.index.has_duplicates:
                    series2 = series2[~series2.index.duplicated(keep='last')]
                
                # Prepare market data for strategy
                market_data = {
                    'price1': series1,
                    'price2': series2
                }
            except Exception as e:
                logger.error(f"Error creating time series for {pair_str}: {e}")
                return
            
            # Validate market data with strategy
            try:
                if not self.strategy.validate_market_data(market_data):
                    return
            except Exception as e:
                logger.error(f"Error validating market data with strategy: {e}")
                return
            
            # Log strategy calculation process
            if not hasattr(self, '_strategy_calc_log'):
                self._strategy_calc_log = {}
            if pair_str not in self._strategy_calc_log:
                self._strategy_calc_log[pair_str] = 0
            self._strategy_calc_log[pair_str] += 1
            
            # Use strategy to calculate indicators
            try:
                if self._strategy_calc_log[pair_str] % 10 == 0:
                    logger.info(f"🔍 STRATEGY CALCULATION: Computing indicators for {pair_str} (calc #{self._strategy_calc_log[pair_str]})")
                    logger.info(f"   Data points: {len(series1)} x {len(series2)}")
                    logger.info(f"   Latest prices: {state['symbol1']}=${series1.iloc[-1]:.5f}, {state['symbol2']}=${series2.iloc[-1]:.5f}")
                
                indicators = self.strategy.calculate_indicators(market_data)
                if not indicators:
                    if self._strategy_calc_log[pair_str] % 10 == 0:
                        logger.warning(f"❌ STRATEGY CALCULATION: No indicators returned for {pair_str}")
                    return
                    
                # Log indicator values periodically with enhanced debugging
                if self._strategy_calc_log[pair_str] % 10 == 0:
                    logger.info(f"✅ STRATEGY CALCULATION: Indicators calculated for {pair_str}")
                    logger.info(f"   Available indicators: {list(indicators.keys())}")
                    
                    if 'zscore' in indicators:
                        zscore_val = indicators['zscore'].iloc[-1] if hasattr(indicators['zscore'], 'iloc') else indicators['zscore']
                        logger.info(f"   💡 Z-Score: {zscore_val:.6f}")
                        logger.info(f"   💡 Z-Entry Threshold: ±{self.config.z_entry:.6f}")
                        logger.info(f"   💡 Z-Exit Threshold: ±{self.config.z_exit:.6f}")
                        if abs(zscore_val) > self.config.z_entry:
                            logger.info(f"   🎯 Z-Score {zscore_val:.6f} EXCEEDS entry threshold {self.config.z_entry:.6f}!")
                    else:
                        logger.warning(f"   ❌ No Z-Score in indicators")
                        
                    if 'ratio' in indicators:
                        ratio_val = indicators['ratio'].iloc[-1] if hasattr(indicators['ratio'], 'iloc') else indicators['ratio']
                        logger.info(f"   💡 Price Ratio: {ratio_val:.6f}")
                        
                    if 'suitable' in indicators:
                        suitable_val = indicators['suitable'].iloc[-1] if hasattr(indicators['suitable'], 'iloc') else indicators['suitable']
                        logger.info(f"   💡 Suitable for Trading: {suitable_val}")
                        
                    if 'spread' in indicators:
                        spread_val = indicators['spread'].iloc[-1] if hasattr(indicators['spread'], 'iloc') else indicators['spread']
                        logger.info(f"   💡 Spread: {spread_val:.5f}")
                        
            except Exception as e:
                logger.error(f"Error calculating indicators with strategy for {pair_str}: {type(e).__name__}: {e}")
                return
            
            # Use strategy to generate signals
            symbol1, symbol2 = pair_str.split('-')
            try:
                signals = self.strategy.generate_signals(indicators, symbol1=symbol1, symbol2=symbol2)
                if signals.empty:
                    return
                    
                # Log signal generation periodically
                if self._strategy_calc_log[pair_str] % 10 == 0:
                    latest_signal = signals.iloc[-1]
                    signal_info = []
                    if getattr(latest_signal, 'long_entry', False):
                        signal_info.append("LONG_ENTRY")
                    if getattr(latest_signal, 'short_entry', False):
                        signal_info.append("SHORT_ENTRY")
                    if getattr(latest_signal, 'long_exit', False):
                        signal_info.append("LONG_EXIT")
                    if getattr(latest_signal, 'short_exit', False):
                        signal_info.append("SHORT_EXIT")
                    if getattr(latest_signal, 'suitable', True):
                        signal_info.append("SUITABLE")
                    else:
                        signal_info.append("NOT_SUITABLE")
                    
                    logger.info(f"🎯 SIGNAL GENERATED: {pair_str} -> {', '.join(signal_info) if signal_info else 'NO_SIGNAL'}")
                    
            except Exception as e:
                logger.error(f"Error generating signals with strategy: {e}")
                return
            
            # Get latest signal
            latest_signal = signals.iloc[-1]
            
            current_position = state['position']
            
            # Thread-safe check of active positions AND pending trades
            with self._update_lock:
                has_active_position = pair_str in self.active_positions
                current_position_count = len(self.active_positions)
                
                # CRITICAL FIX: Also check if there are pending trades for this pair
                # This prevents multiple trades being started for the same pair before completion
                has_pending_trade = False
                if hasattr(self, 'pending_pair_trades'):
                    for pending_key, pending_trade in self.pending_pair_trades.items():
                        if pending_trade['pair_str'] == pair_str:
                            has_pending_trade = True
                            break
            
            # Check if trading conditions are suitable
            if not getattr(latest_signal, 'suitable', True):
                return
            
            # CRITICAL FIX: Block new trades if pair already has active position OR pending trade
            if current_position is not None or has_active_position or has_pending_trade:
                if has_pending_trade and current_position is None and not has_active_position:
                    # Only log occasionally to avoid spam
                    if not hasattr(self, '_pending_trade_log'):
                        self._pending_trade_log = {}
                    if pair_str not in self._pending_trade_log:
                        self._pending_trade_log[pair_str] = 0
                    self._pending_trade_log[pair_str] += 1
                    
                    if self._pending_trade_log[pair_str] % 20 == 1:  # Log every 20th occurrence
                        # Determine direction from signals to provide better logging
                        signal_direction = "entry"
                        if getattr(latest_signal, 'long_entry', False):
                            signal_direction = "LONG"
                        elif getattr(latest_signal, 'short_entry', False):
                            signal_direction = "SHORT"
                        logger.info(f"⏳ PENDING TRADE: Skipping new {signal_direction} signal for {pair_str} - already has pending trade")
                
                # Skip to exit signal processing if this is an active position
                if current_position is not None or has_active_position:
                    # Process exit signals (existing logic)
                    should_exit = False
                    if current_position == 'LONG' and getattr(latest_signal, 'long_exit', False):
                        should_exit = True
                    elif current_position == 'SHORT' and getattr(latest_signal, 'short_exit', False):
                        should_exit = True
                    
                    if should_exit:
                        # Get additional info for logging if available
                        info_str = ""
                        if 'zscore' in indicators:
                            zscore_val = indicators['zscore'].iloc[-1] if hasattr(indicators['zscore'], 'iloc') else indicators['zscore']
                            info_str = f", z-score: {zscore_val:.2f}"
                        
                        logger.info(f"[{current_position} EXIT] Signal for {pair_str}{info_str}")
                        self._close_pair_position(pair_str)
                return
            
            # Check portfolio limits before entry (only for new positions)
            if current_position_count >= self.config.max_open_positions:
                return
            
            # Process entry signals using strategy output
            # CRITICAL FIX: This section should only be reached if no active/pending positions exist
            if getattr(latest_signal, 'short_entry', False):
                logger.info(f"[SHORT ENTRY] Signal for {pair_str} - Positions: {current_position_count}/{self.config.max_open_positions}")
                self._execute_pair_trade(pair_str, 'SHORT')
            elif getattr(latest_signal, 'long_entry', False):
                logger.info(f"[LONG ENTRY] Signal for {pair_str} - Positions: {current_position_count}/{self.config.max_open_positions}")
                self._execute_pair_trade(pair_str, 'LONG')
                    
        except Exception as e:
            logger.error(f"Error checking trading signals for {pair_str}: {e}")
    
    def _update_pair_prices(self, symbol_name: str, price: float, timestamp: datetime):
        """Update pair state prices and indicators"""
        with self._update_lock:
            for pair_str, state in self.pair_states.items():
                if state['symbol1'] == symbol_name:
                    state['price1'].append((timestamp, price))
                elif state['symbol2'] == symbol_name:
                    state['price2'].append((timestamp, price))
    
    def _check_trading_signals(self):
        """Check for trading signals using the configured strategy - now delegates to throttled version"""
        self._check_trading_signals_throttled()
    
    def _execute_pair_trade(self, pair_str: str, direction: str) -> bool:
        """Execute a pairs trade"""
        # Use lock to prevent race conditions when checking/updating positions
        with self._update_lock:
            # Double-check position limits to prevent race conditions
            if len(self.active_positions) >= self.config.max_open_positions:
                logger.warning(f"[PORTFOLIO LIMIT] Cannot open {direction} position for {pair_str} - already at max positions: {len(self.active_positions)}/{self.config.max_open_positions}")
                return False
            
            # CRITICAL FIX: Check if pair already has an active position
            if pair_str in self.active_positions:
                logger.warning(f"[DUPLICATE PREVENTION] Cannot open {direction} position for {pair_str} - pair already has active position")
                return False
            
            # CRITICAL FIX: Check if pair already has pending trades to prevent duplicates
            if hasattr(self, 'pending_pair_trades'):
                for pending_key, pending_trade in self.pending_pair_trades.items():
                    if pending_trade['pair_str'] == pair_str:
                        logger.warning(f"[DUPLICATE PREVENTION] Cannot open {direction} position for {pair_str} - pair already has pending trade (ID: {pending_key})")
                        return False
        
        if not self._check_drawdown_limits(pair_str):
            logger.error(f"[ERROR] Trade blocked by drawdown limits for {pair_str}")
            return False
        
        state = self.pair_states[pair_str]
        s1, s2 = state['symbol1'], state['symbol2']
        
        # Get current prices
        if s1 not in self.spot_prices or s2 not in self.spot_prices:
            logger.warning(f"Missing spot prices for {pair_str}")
            logger.warning(f"  Required: {s1}, {s2}")
            logger.warning(f"  Available spot prices ({len(self.spot_prices)}): {list(self.spot_prices.keys())}")
            logger.warning(f"  Missing: {[s for s in [s1, s2] if s not in self.spot_prices]}")
            logger.warning(f"  Subscribed symbols: {self.subscribed_symbols}")
            return False
        
        price1 = self.spot_prices[s1]
        price2 = self.spot_prices[s2]
        
        # Calculate volumes
        volumes = self._calculate_balanced_volumes(s1, s2, price1, price2)
        if volumes is None:
            logger.error(f"Cannot calculate balanced volumes for {pair_str}")
            return False
        
        volume1, volume2, monetary1, monetary2 = volumes
        
        # Validate monetary values are within tolerance (this is now also checked in _calculate_balanced_volumes)
        value_diff_pct = abs(monetary1 - monetary2) / max(monetary1, monetary2)
        if value_diff_pct > self.config.monetary_value_tolerance:
            logger.error(f"Monetary value difference ({value_diff_pct:.4f}) exceeds tolerance ({self.config.monetary_value_tolerance:.4f}) for {pair_str}")
            return False
        
        # Check position size limits
        total_monetary = monetary1 + monetary2
        if total_monetary > self.config.max_position_size:
            logger.error(f"Position size for {pair_str} ({total_monetary:.2f}) exceeds max_position_size ({self.config.max_position_size})")
            return False
        
        # Determine trade directions
        if direction == 'LONG':
            side1 = self._get_trade_side_value("BUY")   # Buy first symbol
            side2 = self._get_trade_side_value("SELL")  # Sell second symbol
        else:
            side1 = self._get_trade_side_value("SELL")  # Sell first symbol
            side2 = self._get_trade_side_value("BUY")   # Buy second symbol
        
        # Execute trades
        order1 = self._send_market_order(s1, side1, volume1)
        order2 = self._send_market_order(s2, side2, volume2)
        
        # Only proceed if both orders were successfully sent
        if order1 and order2:
            logger.info(f"📨 Both orders sent for {direction} trade on {pair_str}")
            logger.info(f"   Order 1: {s1} {('BUY' if side1 == self._get_trade_side_value('BUY') else 'SELL')} {volume1:.5f} lots (ID: {order1})")
            logger.info(f"   Order 2: {s2} {('BUY' if side2 == self._get_trade_side_value('BUY') else 'SELL')} {volume2:.5f} lots (ID: {order2})")
            logger.info(f"⏳ Waiting for execution confirmations from cTrader before confirming trade success...")
            
            # Store pending pair trade for execution tracking
            pending_trade = {
                'pair_str': pair_str,
                'direction': direction,
                'symbol1': s1,
                'symbol2': s2,
                'volume1': volume1,
                'volume2': volume2,
                'entry_price1': price1,
                'entry_price2': price2,
                'order1_id': order1,
                'order2_id': order2,
                'order1_filled': False,
                'order2_filled': False,
                'timestamp': datetime.now()
            }
            
            # Track pending pair trade
            if not hasattr(self, 'pending_pair_trades'):
                self.pending_pair_trades = {}
            self.pending_pair_trades[f"{order1}_{order2}"] = pending_trade
            
            # Note: The trade will be confirmed as successful only after both orders are FILLED
            # This is handled in _process_execution_event when we receive ORDER_STATUS_FILLED
            return True
        else:
            failed_orders = []
            if not order1:
                failed_orders.append(f"{s1}")
            if not order2:
                failed_orders.append(f"{s2}")
            logger.error(f"[ERROR] Failed to send orders for {direction} trade on {pair_str}: {', '.join(failed_orders)}")
        
        return False
    
    def _close_pair_position(self, pair_str: str) -> bool:
        """Close a pair position"""
        if pair_str not in self.active_positions:
            return False
        
        position = self.active_positions[pair_str]
        state = self.pair_states[pair_str]
        
        s1, s2 = position['symbol1'], position['symbol2']
        direction = position['direction']
        volume1, volume2 = position['volume1'], position['volume2']
        
        # Determine closing sides (opposite of opening)
        if direction == 'LONG':
            close_side1 = self._get_trade_side_value("SELL")  # Sell to close long position
            close_side2 = self._get_trade_side_value("BUY")   # Buy to close short position
        else:
            close_side1 = self._get_trade_side_value("BUY")   # Buy to close short position  
            close_side2 = self._get_trade_side_value("SELL")  # Sell to close long position
        
        # Close positions with the same volumes as opening
        self._send_market_order(s1, close_side1, volume1, is_close=True)
        self._send_market_order(s2, close_side2, volume2, is_close=True)
        
        # Clean up position state with thread safety
        with self._update_lock:
            if pair_str in self.active_positions:
                del self.active_positions[pair_str]
            state['position'] = None
            state['cooldown'] = self.config.cooldown_bars
        
        logger.info(f"Closed {direction} position for {pair_str} - Portfolio now: {len(self.active_positions)}/{self.config.max_open_positions}")
        return True
    
    def _send_market_order(self, symbol: str, side, volume: Optional[float] = None, is_close: bool = False) -> Optional[str]:
        """Send a market order to cTrader"""
        if symbol not in self.symbols_map:
            logger.error(f"Symbol {symbol} not found")
            return None
        
        symbol_id = self.symbols_map[symbol]
        client_order_id = f"PT_{datetime.now().strftime('%Y%m%d%H%M%S')}_{self.next_order_id}"
        self.next_order_id += 1
        
        # Log account and trading details for debugging
        logger.info(f"🔍 TRADING DEBUG - Account ID: {self.account_id}")
        logger.info(f"🔍 TRADING DEBUG - Symbol: {symbol} (ID: {symbol_id})")
        logger.info(f"🔍 TRADING DEBUG - Client Order ID: {client_order_id}")
        
        # Validate volume is provided and reasonable
        if volume is None:
            logger.error(f"Volume is required for all orders")
            return None
            
        if volume <= 0:
            logger.error(f"Invalid volume for {symbol}: {volume}")
            return None
        
        # Get symbol details for validation
        symbol_details = self.symbol_details.get(symbol, {})
        min_volume_lots = symbol_details.get('min_volume', 0.01)
        max_volume_lots = symbol_details.get('max_volume', 1000.0)
        # Validate volume is within constraints (in lots)
        if volume < min_volume_lots:
            logger.warning(f"Volume {volume} below minimum {min_volume_lots} for {symbol}, adjusting")
            volume = min_volume_lots
            
        if volume > max_volume_lots:
            logger.warning(f"Volume {volume} above maximum {max_volume_lots} for {symbol}, adjusting")
            volume = max_volume_lots
        
        # Convert volume to cTrader centilots format for the API
        # According to cTrader API: volume in centilots = volume in lots * 100
        # Special case for XRPUSD which requires different volume conversion
        if symbol == 'XRPUSD':
            broker_volume = int(round(volume * 10000))
            logger.info(f"Special volume conversion for {symbol}: {volume:.5f} lots -> {broker_volume} (x10000)")
        else:
            broker_volume = int(round(volume * 100))
            logger.info(f"Volume conversion for {symbol}: {volume:.5f} lots -> {broker_volume} centilots")
        
        # Ensure minimum volume (at least 1 unit)
        broker_volume = max(broker_volume, 1)
        
        request = self._create_protobuf_request('NEW_ORDER',
                               account_id=self.account_id,
                               symbol_id=symbol_id,
                               trade_side=side,
                               client_order_id=client_order_id,
                               volume=broker_volume)
        
        if request is None:
            logger.error(f"Failed to create market order request for {symbol}")
            return None
        
        if is_close:
            # For closing positions, we need to use ProtoOAClosePositionReq instead
            # For now, we'll create a regular market order in the opposite direction
            # TODO: Implement proper position ID tracking for ProtoOAClosePositionReq
            logger.debug(f"Using market order to close position for {symbol} (fallback method)")
        
        # Convert side to readable string for logging
        buy_side_value = self._get_trade_side_value("BUY")
        side_name = "BUY" if side == buy_side_value else "SELL"
        action_type = "Closing" if is_close else "Opening"
        logger.info(f"{action_type} {side_name} order for {symbol}: {volume:.5f} lots ({broker_volume} centilots)")
        
        try:
            deferred = self.client.send(request)
            deferred.addErrback(self._on_order_error)
            
            # Store order request
            self.execution_requests[client_order_id] = {
                'symbol': symbol,
                'side': side,
                'volume': volume,
                'timestamp': datetime.now(),
                'is_close': is_close
            }
            
            logger.info(f"📨 ORDER SENT - ID: {client_order_id}, Symbol: {symbol}, Side: {side_name}, Volume: {volume:.5f}")
            logger.info(f"📨 Awaiting execution confirmation from cTrader...")
            logger.info(f"📨 Total pending orders: {len(self.execution_requests)}")
            logger.info(f"📨 Pending order IDs: {list(self.execution_requests.keys())}")
            
            return client_order_id
            
        except Exception as e:
            logger.error(f"Error sending order for {symbol}: {e}")
            return None
    
    def _on_order_error(self, failure):
        """Handle order execution errors"""
        logger.error(f"Order execution error: {failure}")
    
    def _process_execution_event(self, event):
        """Process order execution events according to cTrader documentation"""
        
        # According to cTrader docs, ProtoOAExecutionEvent contains:
        # - executionType: Type of operation (ACCEPTED, FILLED, etc.)
        # - order: Reference to the initial order (contains clientOrderId, orderStatus)
        # - deal: Reference to the deal (execution details)
        # - position: Reference to the position
        
        execution_type = getattr(event, 'executionType', None)
        order = getattr(event, 'order', None)
        deal = getattr(event, 'deal', None)
        position = getattr(event, 'position', None)
        
        # Extract order details from the order object (not directly from event)
        client_order_id = None
        order_status = None
        
        if order:
            client_order_id = getattr(order, 'clientOrderId', None)
            order_status = getattr(order, 'orderStatus', None)
        
        # Log all execution events for debugging
        logger.info(f"🎯 EXECUTION EVENT RECEIVED:")
        logger.info(f"   Execution Type: {execution_type}")
        logger.info(f"   Client Order ID: {client_order_id}")
        logger.info(f"   Order Status: {order_status} ({self._get_order_status_name(order_status) if order_status else 'None'})")
        logger.info(f"   Has Order Object: {order is not None}")
        logger.info(f"   Has Deal Object: {deal is not None}")
        logger.info(f"   Has Position Object: {position is not None}")
        logger.info(f"   Current pending orders: {list(self.execution_requests.keys())}")
        
        # Handle cases where clientOrderId might be directly on event (fallback)
        if not client_order_id:
            client_order_id = getattr(event, 'clientOrderId', None)
            logger.info(f"   Fallback Client Order ID: {client_order_id}")
        
        if not client_order_id:
            logger.warning(f"⚠️ No client order ID found in execution event")
            # Log raw event structure for debugging
            logger.info(f"🔍 Raw execution event attributes:")
            for attr in dir(event):
                if not attr.startswith('_'):
                    try:
                        value = getattr(event, attr)
                        if not callable(value):
                            logger.info(f"🔍   {attr}: {value}")
                    except:
                        pass
            return
        
        if client_order_id in self.execution_requests:
            order_data = self.execution_requests[client_order_id]
            
            # Process execution type (primary indicator)
            if execution_type:
                logger.info(f"✅ Order {client_order_id} execution type: {execution_type}")
                
                # Handle different execution types
                if execution_type == 'ORDER_ACCEPTED':
                    logger.info(f"📝 Order {client_order_id} ACCEPTED for {order_data['symbol']} - awaiting fill...")
                elif execution_type == 'ORDER_FILLED':
                    logger.info(f"✅ Order {client_order_id} FILLED successfully for {order_data['symbol']}")
                    self._handle_order_filled(client_order_id, event)
                elif execution_type == 'ORDER_REJECTED':
                    logger.error(f"❌ Order {client_order_id} REJECTED for {order_data['symbol']}")
                    self._handle_order_failed(client_order_id, 'REJECTED')
                elif execution_type == 'ORDER_EXPIRED':
                    logger.error(f"❌ Order {client_order_id} EXPIRED for {order_data['symbol']}")
                    self._handle_order_failed(client_order_id, 'EXPIRED')
                elif execution_type == 'ORDER_CANCELLED':
                    logger.warning(f"⚠️ Order {client_order_id} CANCELLED for {order_data['symbol']}")
                    self._handle_order_failed(client_order_id, 'CANCELLED')
                else:
                    logger.info(f"🔍 Order {client_order_id} execution type: {execution_type}")
            
            # Also process order status as secondary indicator (if available)
            if order_status:
                if order_status == 2:  # ORDER_STATUS_FILLED
                    logger.info(f"✅ Order {client_order_id} status FILLED for {order_data['symbol']}")
                    if execution_type != 'ORDER_FILLED':  # Only handle if not already handled by execution type
                        self._handle_order_filled(client_order_id, event)
                elif order_status == 3:  # ORDER_STATUS_REJECTED
                    logger.error(f"❌ Order {client_order_id} status REJECTED for {order_data['symbol']}")
                    if execution_type != 'ORDER_REJECTED':
                        self._handle_order_failed(client_order_id, 'REJECTED')
                elif order_status == 4:  # ORDER_STATUS_EXPIRED
                    logger.error(f"❌ Order {client_order_id} status EXPIRED for {order_data['symbol']}")
                    if execution_type != 'ORDER_EXPIRED':
                        self._handle_order_failed(client_order_id, 'EXPIRED')
                elif order_status == 5:  # ORDER_STATUS_CANCELLED
                    logger.warning(f"⚠️ Order {client_order_id} status CANCELLED for {order_data['symbol']}")
                    if execution_type != 'ORDER_CANCELLED':
                        self._handle_order_failed(client_order_id, 'CANCELLED')
                elif order_status == 1:  # ORDER_STATUS_ACCEPTED
                    logger.info(f"📝 Order {client_order_id} status ACCEPTED for {order_data['symbol']}")
            
            # Log deal details if available
            if deal:
                deal_volume = getattr(deal, 'volume', 0)
                deal_price = getattr(deal, 'executionPrice', 0)
                deal_id = getattr(deal, 'dealId', 'Unknown')
                logger.info(f"💰 Deal details - ID: {deal_id}, Volume: {deal_volume}, Price: {deal_price}")
            
            # Clean up completed orders
            if execution_type in ['ORDER_FILLED', 'ORDER_REJECTED', 'ORDER_EXPIRED', 'ORDER_CANCELLED'] or order_status in [2, 3, 4, 5]:
                logger.info(f"🏁 Order {client_order_id} completed - removing from pending orders")
                if client_order_id in self.execution_requests:
                    del self.execution_requests[client_order_id]
        else:
            logger.warning(f"⚠️ Received execution event for unknown order: {client_order_id}")
            logger.warning(f"⚠️ Known pending orders: {list(self.execution_requests.keys())}")
            logger.warning(f"⚠️ Total pending orders: {len(self.execution_requests)}")
            
            # Check if this might be a case sensitivity or format issue
            if client_order_id:
                similar_orders = [oid for oid in self.execution_requests.keys() if client_order_id.lower() in oid.lower() or oid.lower() in client_order_id.lower()]
                if similar_orders:
                    logger.warning(f"⚠️ Similar order IDs found: {similar_orders}")
    
    def _get_order_status_name(self, status_code):
        """Convert order status code to readable name"""
        status_names = {
            1: "ACCEPTED",
            2: "FILLED", 
            3: "REJECTED",
            4: "EXPIRED",
            5: "CANCELLED"
        }
        return status_names.get(status_code, f"UNKNOWN({status_code})")
    
    def _handle_order_filled(self, order_id: str, event):
        """Handle a successfully filled order and check if pair trade is complete"""
        logger.info(f"🎉 Processing FILLED order: {order_id}")
        
        # Monitor emergency hedges first
        self._monitor_emergency_hedges()
        
        # Check if this is an emergency hedge order
        if hasattr(self, 'emergency_hedges') and order_id in self.emergency_hedges:
            hedge_info = self.emergency_hedges[order_id]
            logger.info(f"✅ EMERGENCY HEDGE ORDER FILLED - RISK NEUTRALIZED!")
            logger.info(f"   Hedged symbol: {hedge_info['symbol']}, Volume: {hedge_info['volume']:.5f}")
            logger.info(f"   Original pair: {hedge_info['original_pair']}")
            
            # Suspend the pair if not already suspended (in case hedge completed before pair was suspended)
            if 'failed_trade_data' in hedge_info:
                pair_to_suspend = hedge_info['original_pair']
                self._suspend_problematic_pair(pair_to_suspend, "Emergency hedge completed - pair requires review")
            
            # Remove from tracking once filled
            del self.emergency_hedges[order_id]
            return  # Don't process as pair trade order
        
        if not hasattr(self, 'pending_pair_trades'):
            logger.warning(f"⚠️ No pending_pair_trades attribute - initializing")
            self.pending_pair_trades = {}
            return
        
        if not self.pending_pair_trades:
            logger.warning(f"⚠️ No pending pair trades to process for order {order_id}")
            return
        
        # Find which pending pair trade this order belongs to
        for trade_key, pending_trade in list(self.pending_pair_trades.items()):
            if pending_trade['order1_id'] == order_id:
                pending_trade['order1_filled'] = True
                logger.info(f"✅ First leg of pair trade filled: {pending_trade['symbol1']} (Order: {order_id})")
                logger.info(f"   Pair: {pending_trade['pair_str']}, Direction: {pending_trade['direction']}")
            elif pending_trade['order2_id'] == order_id:
                pending_trade['order2_filled'] = True
                logger.info(f"✅ Second leg of pair trade filled: {pending_trade['symbol2']} (Order: {order_id})")
                logger.info(f"   Pair: {pending_trade['pair_str']}, Direction: {pending_trade['direction']}")
            else:
                continue  # This order doesn't belong to this pending trade
            
            # Check if both orders are now filled
            if pending_trade['order1_filled'] and pending_trade['order2_filled']:
                logger.info(f"🎉 BOTH LEGS FILLED - Completing pair trade for {pending_trade['pair_str']}")
                self._complete_pair_trade(pending_trade)
                del self.pending_pair_trades[trade_key]
            else:
                remaining_orders = []
                if not pending_trade['order1_filled']:
                    remaining_orders.append(f"{pending_trade['symbol1']} ({pending_trade['order1_id']})")
                if not pending_trade['order2_filled']:
                    remaining_orders.append(f"{pending_trade['symbol2']} ({pending_trade['order2_id']})")
                logger.info(f"⏳ Pair trade {pending_trade['pair_str']} waiting for: {', '.join(remaining_orders)}")
            break
        else:
            logger.warning(f"⚠️ Order {order_id} not found in any pending pair trade")
            logger.info(f"   Current pending trades: {list(self.pending_pair_trades.keys())}")
            for trade_key, pending_trade in self.pending_pair_trades.items():
                logger.info(f"   {trade_key}: {pending_trade['order1_id']}, {pending_trade['order2_id']}")
    
    def _handle_order_failed(self, order_id: str, reason: str):
        """Handle a failed order and cancel the entire pair trade if needed"""
        logger.error(f"💥 Processing FAILED order: {order_id}, Reason: {reason}")
        
        # Monitor emergency hedges first
        self._monitor_emergency_hedges()
        
        # Check if this is an emergency hedge order that failed
        if hasattr(self, 'emergency_hedges') and order_id in self.emergency_hedges:
            hedge_info = self.emergency_hedges[order_id]
            logger.critical(f"🚨 EMERGENCY HEDGE ORDER FAILED!")
            logger.critical(f"   Failed hedge symbol: {hedge_info['symbol']}, Volume: {hedge_info['volume']:.5f}")
            logger.critical(f"   Original pair: {hedge_info['original_pair']}")
            logger.critical(f"   ONE-LEGGED POSITION STILL EXISTS - MANUAL INTERVENTION REQUIRED!")
            
            # Send alert but keep tracking the failed hedge
            self._send_emergency_alert(
                f"CRITICAL: Emergency hedge order FAILED for {hedge_info['symbol']} "
                f"({hedge_info['volume']:.5f} shares). One-legged position still exists!"
            )
            return  # Don't process as pair trade order
        
        if not hasattr(self, 'pending_pair_trades'):
            logger.warning(f"⚠️ No pending_pair_trades attribute for failed order {order_id}")
            return
        
        if not self.pending_pair_trades:
            logger.warning(f"⚠️ No pending pair trades to process for failed order {order_id}")
            return
        
        # Find which pending pair trade this order belongs to
        for trade_key, pending_trade in list(self.pending_pair_trades.items()):
            if pending_trade['order1_id'] == order_id or pending_trade['order2_id'] == order_id:
                failed_symbol = pending_trade['symbol1'] if pending_trade['order1_id'] == order_id else pending_trade['symbol2']
                other_order_id = pending_trade['order2_id'] if pending_trade['order1_id'] == order_id else pending_trade['order1_id']
                other_symbol = pending_trade['symbol2'] if pending_trade['order1_id'] == order_id else pending_trade['symbol1']
                
                logger.error(f"❌ PAIR TRADE FAILED for {pending_trade['pair_str']}: {failed_symbol} order {reason}")
                logger.error(f"   Failed order: {order_id} ({failed_symbol})")
                logger.error(f"   Other order: {other_order_id} ({other_symbol})")
                logger.error(f"   Direction: {pending_trade['direction']}")
                
                # CRITICAL: Check if the other order was already filled - IMMEDIATE HEDGING REQUIRED
                if pending_trade['order1_filled'] or pending_trade['order2_filled']:
                    logger.critical(f"🚨 CRITICAL: ONE-LEGGED POSITION DETECTED!")
                    logger.critical(f"   Order 1 ({pending_trade['symbol1']}) filled: {pending_trade['order1_filled']}")
                    logger.critical(f"   Order 2 ({pending_trade['symbol2']}) filled: {pending_trade['order2_filled']}")
                    logger.critical(f"   IMMEDIATE HEDGING REQUIRED TO PREVENT UNCONTROLLED RISK!")
                    
                    # Determine which leg is filled and needs immediate closing
                    if pending_trade['order1_filled'] and not pending_trade['order2_filled']:
                        # Order 1 (symbol1) is filled, order 2 failed
                        filled_symbol = pending_trade['symbol1']
                        filled_volume = pending_trade['volume1']
                        filled_side = "BUY" if pending_trade['direction'] == 'LONG' else "SELL"
                        logger.critical(f"   Filled leg: {filled_symbol} {filled_side} {filled_volume:.5f} lots")
                        self._emergency_hedge_position(filled_symbol, filled_side, filled_volume, pending_trade)
                        
                    elif pending_trade['order2_filled'] and not pending_trade['order1_filled']:
                        # Order 2 (symbol2) is filled, order 1 failed
                        filled_symbol = pending_trade['symbol2']
                        filled_volume = pending_trade['volume2']
                        filled_side = "SELL" if pending_trade['direction'] == 'LONG' else "BUY"
                        logger.critical(f"   Filled leg: {filled_symbol} {filled_side} {filled_volume:.5f} lots")
                        self._emergency_hedge_position(filled_symbol, filled_side, filled_volume, pending_trade)
                else:
                    logger.info(f"✅ Both orders failed/cancelled - no imbalanced position created")
                
                # Remove the pending trade regardless
                logger.info(f"🗑️ Removing failed pair trade: {trade_key}")
                del self.pending_pair_trades[trade_key]
                break
        else:
            logger.warning(f"⚠️ Failed order {order_id} not found in any pending pair trade")
            logger.info(f"   Current pending trades: {list(self.pending_pair_trades.keys())}")
            for trade_key, pending_trade in self.pending_pair_trades.items():
                logger.info(f"   {trade_key}: {pending_trade['order1_id']}, {pending_trade['order2_id']}")
    
    def _emergency_hedge_position(self, symbol: str, original_side: str, volume: float, failed_trade: dict):
        """Emergency hedging function - first retry failed leg, then close filled leg if needed"""
        logger.critical(f"🚨 EXECUTING EMERGENCY HEDGE PROTOCOL for {symbol}")
        logger.critical(f"   Original side: {original_side}, Volume: {volume:.5f}")
        logger.critical(f"   Failed pair: {failed_trade['pair_str']}")
        
        try:
            # First, attempt to retry the failed leg up to 3 times
            retry_success = self._retry_failed_leg(failed_trade)
            
            if retry_success:
                logger.info(f"✅ RETRY SUCCESSFUL - Original trade completed, no hedging needed")
                return
            
            # If retries failed, proceed with closing the filled leg
            logger.critical(f"🚨 ALL RETRIES FAILED - Proceeding to close filled leg: {symbol}")
            
            # Determine the opposite side to close the position
            if original_side == "BUY":
                hedge_side = self._get_trade_side_value("SELL")
                hedge_side_name = "SELL"
            else:
                hedge_side = self._get_trade_side_value("BUY")
                hedge_side_name = "BUY"
            
            logger.critical(f"   Hedge action: {hedge_side_name} {volume:.5f} lots of {symbol}")
            
            # Send immediate market order to close the position
            hedge_order_id = self._send_market_order(symbol, hedge_side, volume, is_close=True)
            
            if hedge_order_id:
                logger.critical(f"✅ EMERGENCY HEDGE ORDER SENT: {hedge_order_id}")
                logger.critical(f"   Symbol: {symbol}, Side: {hedge_side_name}, Volume: {volume:.5f}")
                logger.critical(f"   This should close the one-legged position")
                
                # Mark this as an emergency hedge in tracking
                if not hasattr(self, 'emergency_hedges'):
                    self.emergency_hedges = {}
                
                self.emergency_hedges[hedge_order_id] = {
                    'symbol': symbol,
                    'volume': volume,
                    'hedge_side': hedge_side_name,
                    'original_pair': failed_trade['pair_str'],
                    'timestamp': datetime.now(),
                    'reason': 'one_legged_position_after_retries',
                    'failed_trade_data': failed_trade  # Store for pair suspension
                }
                
                logger.critical(f"   Emergency hedge tracked under ID: {hedge_order_id}")
                
                # Suspend the pair immediately to prevent further issues
                self._suspend_problematic_pair(failed_trade['pair_str'], "Emergency hedge required after failed retries")
                
            else:
                logger.critical(f"❌ FAILED TO SEND EMERGENCY HEDGE ORDER!")
                logger.critical(f"   MANUAL INTERVENTION REQUIRED IMMEDIATELY!")
                logger.critical(f"   Action needed: {hedge_side_name} {volume:.5f} lots of {symbol}")
                
                # Still suspend the pair even if hedge failed
                self._suspend_problematic_pair(failed_trade['pair_str'], "Emergency hedge failed to send")
                
                # Send alert/notification (implement as needed)
                self._send_emergency_alert(symbol, original_side, volume, failed_trade)
                
        except Exception as e:
            logger.critical(f"❌ EXCEPTION IN EMERGENCY HEDGE: {e}")
            logger.critical(f"   MANUAL INTERVENTION REQUIRED IMMEDIATELY!")
            logger.critical(f"   Action needed: Close {volume:.5f} lots of {symbol} (opposite to {original_side})")
            
            # Suspend pair even on exception
            self._suspend_problematic_pair(failed_trade['pair_str'], f"Emergency hedge exception: {e}")
            
            # Send emergency alert
            self._send_emergency_alert(symbol, original_side, volume, failed_trade)
    
    def _retry_failed_leg(self, failed_trade: dict, max_retries: int = 3) -> bool:
        """Retry the failed leg of a pair trade to complete the original trade"""
        logger.critical(f"🔄 ATTEMPTING TO RETRY FAILED LEG for {failed_trade['pair_str']}")
        
        # Determine which leg failed and needs to be retried
        failed_symbol = None
        failed_side = None
        failed_volume = None
        failed_order_id = None
        
        if failed_trade['order1_filled'] and not failed_trade['order2_filled']:
            # Order 2 failed, retry it
            failed_symbol = failed_trade['symbol2']
            failed_volume = failed_trade['volume2']
            failed_side = "SELL" if failed_trade['direction'] == 'LONG' else "BUY"
            failed_order_id = failed_trade['order2_id']
            logger.critical(f"   Retrying Order 2: {failed_symbol} {failed_side} {failed_volume:.5f}")
            
        elif failed_trade['order2_filled'] and not failed_trade['order1_filled']:
            # Order 1 failed, retry it
            failed_symbol = failed_trade['symbol1']
            failed_volume = failed_trade['volume1']
            failed_side = "BUY" if failed_trade['direction'] == 'LONG' else "SELL"
            failed_order_id = failed_trade['order1_id']
            logger.critical(f"   Retrying Order 1: {failed_symbol} {failed_side} {failed_volume:.5f}")
            
        else:
            logger.error(f"❌ Invalid failed trade state - cannot determine which leg to retry")
            return False
        
        if not failed_symbol:
            logger.error(f"❌ Could not determine failed leg details")
            return False
        
        # Convert side to cTrader format
        side_value = self._get_trade_side_value(failed_side)
        
        # Attempt retries
        for retry_attempt in range(1, max_retries + 1):
            logger.critical(f"🔄 RETRY ATTEMPT {retry_attempt}/{max_retries} for {failed_symbol}")
            
            try:
                # Generate new order ID for retry
                retry_order_id = self._send_market_order(failed_symbol, side_value, failed_volume)
                
                if retry_order_id:
                    logger.critical(f"✅ RETRY ORDER SENT: {retry_order_id}")
                    logger.critical(f"   Waiting for execution confirmation...")
                    
                    # Wait for order execution (with timeout)
                    retry_success = self._wait_for_order_execution(retry_order_id, timeout_seconds=30)
                    
                    if retry_success:
                        logger.critical(f"🎉 RETRY SUCCESSFUL! Original pair trade completed")
                        logger.critical(f"   Completed pair: {failed_trade['pair_str']}")
                        
                        # Update the pending trade to mark both legs as filled
                        if failed_trade['order1_filled'] and not failed_trade['order2_filled']:
                            failed_trade['order2_filled'] = True
                            failed_trade['order2_id'] = retry_order_id  # Update with new order ID
                        elif failed_trade['order2_filled'] and not failed_trade['order1_filled']:
                            failed_trade['order1_filled'] = True
                            failed_trade['order1_id'] = retry_order_id  # Update with new order ID
                        
                        # Complete the pair trade normally
                        self._complete_pair_trade(failed_trade)
                        
                        return True  # Success!
                    else:
                        logger.error(f"❌ RETRY ATTEMPT {retry_attempt} FAILED - Order did not execute within timeout")
                        
                else:
                    logger.error(f"❌ RETRY ATTEMPT {retry_attempt} FAILED - Could not send order")
                    
            except Exception as e:
                logger.error(f"❌ RETRY ATTEMPT {retry_attempt} FAILED with exception: {e}")
            
            # Wait before next retry (except on last attempt)
            if retry_attempt < max_retries:
                import time
                wait_time = retry_attempt * 2  # Increasing wait time: 2s, 4s, 6s
                logger.critical(f"   Waiting {wait_time}s before next retry...")
                time.sleep(wait_time)
        
        logger.critical(f"❌ ALL {max_retries} RETRY ATTEMPTS FAILED for {failed_symbol}")
        return False
    
    def _wait_for_order_execution(self, order_id: str, timeout_seconds: int = 30) -> bool:
        """Wait for an order to be executed or rejected within timeout"""
        import time
        
        start_time = time.time()
        check_interval = 0.5  # Check every 500ms
        
        while time.time() - start_time < timeout_seconds:
            # Check if order is no longer pending (executed or failed)
            if order_id not in self.execution_requests:
                # Order was processed, check if it was successful
                # We'll consider it successful if it's not in pending anymore
                # (the actual success/failure handling is done in _process_execution_event)
                logger.debug(f"Order {order_id} processed (no longer pending)")
                return True
            
            time.sleep(check_interval)
        
        # Timeout reached
        logger.warning(f"Order {order_id} execution timeout after {timeout_seconds}s")
        return False
    
    def _suspend_problematic_pair(self, pair_str: str, reason: str):
        """Add a pair to the suspended list to prevent further trading"""
        if not hasattr(self, 'suspended_pairs'):
            self.suspended_pairs = set()
        
        if pair_str not in self.suspended_pairs:
            self.suspended_pairs.add(pair_str)
            logger.critical(f"🚫 PAIR SUSPENDED: {pair_str}")
            logger.critical(f"   Reason: {reason}")
            logger.critical(f"   This pair will not be traded until manually re-enabled")
            logger.critical(f"   Total suspended pairs: {len(self.suspended_pairs)}")
            
            # Log all suspended pairs for visibility
            if len(self.suspended_pairs) > 1:
                logger.critical(f"   All suspended pairs: {', '.join(sorted(self.suspended_pairs))}")
        else:
            logger.warning(f"⚠️ Pair {pair_str} already suspended")
    
    def _unsuspend_pair(self, pair_str: str, reason: str = "Manual re-enable"):
        """Remove a pair from the suspended list to re-enable trading"""
        if not hasattr(self, 'suspended_pairs'):
            self.suspended_pairs = set()
            
        if pair_str in self.suspended_pairs:
            self.suspended_pairs.remove(pair_str)
            logger.info(f"✅ PAIR RE-ENABLED: {pair_str}")
            logger.info(f"   Reason: {reason}")
            logger.info(f"   Remaining suspended pairs: {len(self.suspended_pairs)}")
            
            if self.suspended_pairs:
                logger.info(f"   Still suspended: {', '.join(sorted(self.suspended_pairs))}")
            else:
                logger.info(f"   No pairs currently suspended")
        else:
            logger.warning(f"⚠️ Pair {pair_str} was not suspended")
    
    def get_suspended_pairs_status(self) -> dict:
        """Get status of all suspended pairs"""
        if not hasattr(self, 'suspended_pairs'):
            self.suspended_pairs = set()
            
        return {
            'suspended_pairs': list(self.suspended_pairs),
            'count': len(self.suspended_pairs),
            'active_pairs': [pair for pair in self.pair_states.keys() if pair not in self.suspended_pairs]
        }
    
    def _send_emergency_alert(self, symbol: str, original_side: str, volume: float, failed_trade: dict):
        """Send emergency alert for manual intervention (implement based on your alert system)"""
        alert_message = f"""
        🚨 EMERGENCY: ONE-LEGGED POSITION DETECTED! 🚨
        
        Pair: {failed_trade['pair_str']}
        Filled Symbol: {symbol}
        Original Side: {original_side}
        Volume: {volume:.5f} lots
        
        IMMEDIATE ACTION REQUIRED:
        - Manually close this position by executing opposite trade
        - Required action: {'SELL' if original_side == 'BUY' else 'BUY'} {volume:.5f} lots of {symbol}
        
        Timestamp: {datetime.now()}
        """
        
        logger.critical(alert_message)
        
        # TODO: Implement your preferred alert mechanism:
        # - Email notification
        # - SMS alert
        # - Webhook to monitoring system
        # - Dashboard alert
        # - etc.
        
        # For now, log prominently
        logger.critical("=" * 80)
        logger.critical("MANUAL INTERVENTION REQUIRED - ONE-LEGGED POSITION")
        logger.critical("=" * 80)
    
    def _complete_pair_trade(self, pending_trade):
        """Complete a successful pair trade by updating position tracking"""
        pair_str = pending_trade['pair_str']
        direction = pending_trade['direction']
        
        # Store position with thread safety
        with self._update_lock:
            self.active_positions[pair_str] = {
                'direction': direction,
                'symbol1': pending_trade['symbol1'],
                'symbol2': pending_trade['symbol2'],
                'volume1': pending_trade['volume1'],
                'volume2': pending_trade['volume2'],
                'entry_price1': pending_trade['entry_price1'],
                'entry_price2': pending_trade['entry_price2'],
                'entry_time': pending_trade['timestamp'],
                'order_ids': (pending_trade['order1_id'], pending_trade['order2_id'])
            }
            
            # Update pair state
            if pair_str in self.pair_states:
                state = self.pair_states[pair_str]
                state['position'] = direction
                state['entry_time'] = pending_trade['timestamp']
                state['entry_price1'] = pending_trade['entry_price1']
                state['entry_price2'] = pending_trade['entry_price2']
        
        logger.info(f"🎉 PAIR TRADE COMPLETED SUCCESSFULLY!")
        logger.info(f"   Pair: {pair_str}")
        logger.info(f"   Direction: {direction}")
        logger.info(f"   {pending_trade['symbol1']}: {'BUY' if direction == 'LONG' else 'SELL'} {pending_trade['volume1']:.5f} lots")
        logger.info(f"   {pending_trade['symbol2']}: {'SELL' if direction == 'LONG' else 'BUY'} {pending_trade['volume2']:.5f} lots")
        logger.info(f"   Portfolio now: {len(self.active_positions)}/{self.config.max_open_positions} positions")
    
    def _calculate_balanced_volumes(self, symbol1: str, symbol2: str, price1: float, price2: float) -> Optional[Tuple[float, float, float, float]]:
        """Calculate volumes for equal monetary exposure between two symbols with comprehensive validation"""
        
        # Get symbol information with strict validation and fallbacks
        details1 = self.symbol_details.get(symbol1)
        details2 = self.symbol_details.get(symbol2)
        
        if not details1:
            logger.error(f"No symbol details available for {symbol1}")
            logger.error(f"Available symbol details: {list(self.symbol_details.keys())}")
            return None
        
        if not details2:
            logger.error(f"No symbol details available for {symbol2}")
            logger.error(f"Available symbol details: {list(self.symbol_details.keys())}")
            return None
        
        # Ensure required fields are available with proper fallbacks
        required_fields = ['digits', 'min_volume', 'step_volume', 'lot_size']
        
        for symbol, details in [(symbol1, details1), (symbol2, details2)]:
            for field in required_fields:
                if field not in details or details[field] is None:
                    logger.warning(f"Missing or null field '{field}' for symbol {symbol}")
        
        logger.info(f"Volume calculation for {symbol1}-{symbol2}:")
        logger.info(f"  {symbol1}: price={price1:.5f}, min_volume={details1.get('min_volume', 'MISSING')}, step_volume={details1.get('step_volume', 'MISSING')}")
        logger.info(f"  {symbol2}: price={price2:.5f}, min_volume={details2.get('min_volume', 'MISSING')}, step_volume={details2.get('step_volume', 'MISSING')}")
        
        # Log lot sizes for debugging
        lot_size1_raw = details1.get('lot_size', 'MISSING')
        lot_size2_raw = details2.get('lot_size', 'MISSING')
        logger.info(f"  Lot sizes: {symbol1}={lot_size1_raw}, {symbol2}={lot_size2_raw}")
        
        # Calculate target monetary value per leg (half of max position size for each leg)
        target_monetary_value = self.config.max_position_size / 2
        
        logger.info(f"  Target monetary value per leg: ${target_monetary_value:.2f}")
        
        # Get lot sizes from symbol details (this is the contract size in cTrader)
        # According to cTrader API documentation, lotSize is in centilots initially
        # After conversion, it represents the actual contract size
        lot_size1 = details1.get('lot_size')
        lot_size2 = details2.get('lot_size')
        
        if lot_size1 is None:
            logger.error(f"No lot size information available for {symbol1}")
            logger.error(f"Available details for {symbol1}: {list(details1.keys())}")
            return None
            
        if lot_size2 is None:
            logger.error(f"No lot size information available for {symbol2}")
            logger.error(f"Available details for {symbol2}: {list(details2.keys())}")
            return None
        
        # Use lot sizes directly - they have been converted from centilots to standard units
        # For Forex pairs, this should be around 100000 (1 standard lot)
        # For CFDs and other instruments, this varies by instrument
        contract_size1 = lot_size1
        contract_size2 = lot_size2
        
        logger.info(f"  Contract sizes: {symbol1}={contract_size1}, {symbol2}={contract_size2}")
        
        # Calculate required volumes for target monetary value
        # Formula: volume = target_value / (price * contract_size)
        volume1_raw = target_monetary_value / (price1 * contract_size1)
        volume2_raw = target_monetary_value / (price2 * contract_size2)
        
        logger.info(f"  Raw volumes: {symbol1}={volume1_raw:.6f}, {symbol2}={volume2_raw:.6f}")
        
        # Apply volume constraints
        volume1 = self._normalize_ctrader_volume(symbol1, volume1_raw, details1)
        volume2 = self._normalize_ctrader_volume(symbol2, volume2_raw, details2)
        
        if volume1 is None or volume2 is None:
            logger.error(f"Failed to normalize volumes for {symbol1}-{symbol2}")
            return None
        
        logger.info(f"  Normalized volumes: {symbol1}={volume1:.6f}, {symbol2}={volume2:.6f}")
        
        # Calculate actual monetary values with normalized volumes and contract sizes
        monetary_value1 = volume1 * price1 * contract_size1
        monetary_value2 = volume2 * price2 * contract_size2
        
        logger.info(f"  Initial monetary values: {symbol1}=${monetary_value1:.2f}, {symbol2}=${monetary_value2:.2f}")
        
        # Try iterative adjustment for better balance (similar to MT5 implementation)
        best_volume1, best_volume2 = volume1, volume2
        best_monetary1, best_monetary2 = monetary_value1, monetary_value2
        best_diff = abs(monetary_value1 - monetary_value2) / max(monetary_value1, monetary_value2)
        
        # Try small adjustments to improve balance - enhanced logic from MT5
        for multiplier in [0.95, 0.98, 1.02, 1.05]:
            try:
                # Adjust the larger volume down or smaller volume up for better balance
                if monetary_value1 > monetary_value2:
                    test_volume1 = self._normalize_ctrader_volume(symbol1, volume1_raw * multiplier, details1)
                    test_volume2 = volume2
                else:
                    test_volume1 = volume1
                    test_volume2 = self._normalize_ctrader_volume(symbol2, volume2_raw * multiplier, details2)
                
                if test_volume1 is not None and test_volume2 is not None:
                    test_monetary1 = test_volume1 * price1 * contract_size1
                    test_monetary2 = test_volume2 * price2 * contract_size2
                    test_diff = abs(test_monetary1 - test_monetary2) / max(test_monetary1, test_monetary2)
                    
                    if test_diff < best_diff:
                        best_volume1, best_volume2 = test_volume1, test_volume2
                        best_monetary1, best_monetary2 = test_monetary1, test_monetary2
                        best_diff = test_diff
                        logger.info(f"  Improved balance with multiplier {multiplier}: diff={test_diff:.4f}")
            except Exception as e:
                logger.debug(f"  Adjustment failed for multiplier {multiplier}: {e}")
                continue
        
        volume1, volume2 = best_volume1, best_volume2
        monetary_value1, monetary_value2 = best_monetary1, best_monetary2
        
        # Final validation against monetary value tolerance
        final_diff_pct = abs(monetary_value1 - monetary_value2) / max(monetary_value1, monetary_value2)
        
        logger.info(f"  Final monetary values: {symbol1}=${monetary_value1:.2f}, {symbol2}=${monetary_value2:.2f}")
        logger.info(f"  Final difference: {final_diff_pct:.4f} vs tolerance: {self.config.monetary_value_tolerance:.4f}")
        
        if final_diff_pct > self.config.monetary_value_tolerance:
            logger.warning(f"Monetary value difference ({final_diff_pct:.4f}) exceeds tolerance ({self.config.monetary_value_tolerance:.4f}) for {symbol1}-{symbol2}")
            logger.warning(f"  Consider increasing monetary_value_tolerance or adjusting max_position_size")
            return None
        
        logger.info(f"  Successfully calculated balanced volumes with {final_diff_pct:.4f} difference")
        return volume1, volume2, monetary_value1, monetary_value2
    
    def _normalize_ctrader_volume(self, symbol: str, volume_raw: float, symbol_details: Dict) -> Optional[float]:
        """
        Normalize volume to valid increments and constraints for cTrader
        
        According to cTrader API documentation:
        - Volume constraints (minVolume, maxVolume, stepVolume) are in centilots initially
        - After conversion, they represent standard lots (0.01, 0.1, 1.0, etc.)
        - Volume for trading orders should be in lots (e.g., 0.01 = 0.01 lots)
        
        Args:
            symbol: Symbol name
            volume_raw: Raw calculated volume in lots
            symbol_details: Symbol details including volume constraints (already converted)
            
        Returns:
            Normalized volume in lots, or None if normalization fails
        """
        
        # Validate that required volume constraints are available
        required_volume_fields = ['min_volume', 'step_volume']
        for field in required_volume_fields:
            if field not in symbol_details:
                logger.error(f"Missing required volume field '{field}' for symbol {symbol}")
                return None
        
        min_vol = symbol_details['min_volume']  # Already converted from centilots
        max_vol = symbol_details.get('max_volume')  # Already converted from centilots
        step = symbol_details['step_volume']  # Already converted from centilots
        
        # These values are now in standard lots (already converted)
        min_vol_standard = min_vol
        max_vol_standard = max_vol if max_vol is not None else None
        step_standard = step
        
        # Validate the converted values are reasonable
        # if min_vol_standard >= 1:
        #     logger.warning(f"Suspicious min_volume for {symbol}: {min_vol_standard}, using default 0.01")
        #     min_vol_standard = 0.01
            
        # if step_standard >= 1:
        #     logger.warning(f"Suspicious step_volume for {symbol}: {step_standard}, using default 0.01")
        #     step_standard = 0.01
        
        logger.debug(f"Normalizing volume for {symbol}: raw={volume_raw:.6f}, min={min_vol_standard}, max={max_vol_standard or 'None'}, step={step_standard}")
        
        # Handle edge case where raw volume is 0 or negative
        if volume_raw <= 0:
            logger.warning(f"Invalid raw volume for {symbol}: {volume_raw}")
            return None
        
        # Round to valid step increments
        if step_standard > 0:
            volume = round(volume_raw / step_standard) * step_standard
        else:
            volume = volume_raw
        
        # Apply minimum constraint
        volume = max(min_vol_standard, volume)
        
        # Apply maximum constraint if available
        if max_vol_standard is not None:
            volume = min(max_vol_standard, volume)
        
        # Validate minimum volume requirement
        if volume < min_vol_standard:
            logger.warning(f"Volume {volume:.6f} below minimum {min_vol_standard} for {symbol}")
            return None
        
        # Validate maximum volume requirement if available
        if max_vol_standard is not None and volume > max_vol_standard:
            logger.warning(f"Volume {volume:.6f} exceeds maximum {max_vol_standard} for {symbol}")
            volume = max_vol_standard
        
        # Additional validation: ensure volume is not zero after rounding
        if volume == 0:
            logger.warning(f"Volume became zero after normalization for {symbol} (raw: {volume_raw:.6f})")
            return None
        
        logger.debug(f"Normalized volume for {symbol}: {volume:.6f}")
        return volume
    
    def _check_drawdown_limits(self, pair_str: str = None) -> bool:
        """Check if trading should be allowed based on drawdown limits"""
        # Simplified implementation - expand based on requirements
        if self.portfolio_trading_suspended:
            return False
        
        # Check if this specific pair is suspended
        if pair_str:
            if not hasattr(self, 'suspended_pairs'):
                self.suspended_pairs = set()
            
            if pair_str in self.suspended_pairs:
                logger.debug(f"Trading blocked for suspended pair: {pair_str}")
                return False
        
        return True
    
    def start_trading(self):
        """Start the real-time trading loop"""
        if not self.is_trading:
            logger.error("Trader not initialized")
            return
        
        logger.info("Starting cTrader real-time trading loop...")
        
        # Start continuous trading loop in separate thread
        self.trading_thread = threading.Thread(target=self._trading_loop, daemon=True)
        self.trading_thread.start()
        
        # The reactor will be started by the main thread or callback
        # Just ensure the client is ready to receive messages
        logger.info("cTrader trader ready for real-time trading")
    
    def _trading_loop(self):
        """Continuous trading loop to actively check for signals and manage positions"""
        logger.info("Starting cTrader continuous trading loop...")
        
        status_counter = 0
        symbols_check_counter = 0
        spot_price_check_counter = 0
        
        while self.is_trading:
            try:
                # Check if symbols need to be retried (less frequently to avoid spam)
                symbols_check_counter += 1
                if symbols_check_counter % 60 == 0:  # Check every 60 seconds instead of 30
                    self._check_symbols_timeout()
                    symbols_check_counter = 0
                
                # Check spot price availability periodically
                spot_price_check_counter += 1
                if spot_price_check_counter % 30 == 0:  # Check every 30 seconds
                    if len(self.spot_prices) == 0:
                        logger.warning(f"⚠️ No spot prices received yet after {spot_price_check_counter} seconds")
                        logger.warning(f"   Subscribed to {len(self.subscribed_symbols)} symbols: {self.subscribed_symbols}")
                    # else:
                        # logger.info(f"✅ Spot prices available for {len(self.spot_prices)} symbols")
                    spot_price_check_counter = 0
                
                # Only proceed with trading if symbols are available or in degraded mode
                if self.symbols_initialized or self._degraded_mode:
                    # Only check trading signals if we have some spot prices
                    if len(self.spot_prices) > 0:
                        # Check trading signals for all pairs
                        self._check_trading_signals()
                        
                        # Monitor existing positions
                        self._monitor_positions()
                    else:
                        # Wait for spot prices to start arriving
                        if spot_price_check_counter % 10 == 0:
                            logger.debug("Waiting for spot price data to arrive from cTrader...")
                else:
                    # Log status periodically while waiting for symbols (less frequently)
                    if symbols_check_counter % 30 == 0:
                        logger.debug("Waiting for symbols initialization before starting trading...")
                
                # Log portfolio status every 5 minutes (300 seconds)
                status_counter += 1
                if status_counter % 60 == 0:
                    self._log_portfolio_status()
                    self._cleanup_stale_pending_trades()
                    status_counter = 0
                
                # Sleep for a short interval to prevent excessive CPU usage
                time.sleep(1)  # Check every second
                
            except Exception as e:
                # Don't spam the logs with timeout errors
                if "timeout" not in str(e).lower():
                    logger.error(f"Error in cTrader trading loop: {e}")
                    traceback.print_exc()
                else:
                    logger.debug(f"Timeout in trading loop (normal): {e}")
                time.sleep(5)  # Wait longer on error before retrying
        
        logger.info("cTrader trading loop stopped")
    
    def _check_symbols_timeout(self):
        """Check if symbols request timed out and retry if needed"""
        if not self.symbols_initialized and self.symbols_request_time:
            time_since_request = datetime.now() - self.symbols_request_time
            if time_since_request.total_seconds() > 30:  # 30 second timeout
                logger.error("Symbols request timed out - this indicates a connection issue")
                logger.error("Please check your cTrader API credentials and account access")
                
                # Instead of raising an exception, reset and try again
                logger.warning("Resetting symbols request for retry...")
                self.symbols_request_time = None  # Reset to allow retry
                
                # Set a flag to indicate degraded mode
                self._degraded_mode = True
                logger.warning("Trading system entering degraded mode due to symbols timeout")
                
                # Don't raise exception - let trading continue without full symbol data
    
    def _cleanup_stale_pending_trades(self):
        """Clean up pending trades that have been waiting too long"""
        if not hasattr(self, 'pending_pair_trades') or not self.pending_pair_trades:
            return
        
        current_time = datetime.now()
        stale_timeout = timedelta(minutes=5)  # 5 minutes timeout
        
        stale_trades = []
        for trade_key, pending_trade in self.pending_pair_trades.items():
            if current_time - pending_trade['timestamp'] > stale_timeout:
                stale_trades.append((trade_key, pending_trade))
        
        for trade_key, pending_trade in stale_trades:
            logger.warning(f"⏰ Cleaning up stale pending trade for {pending_trade['pair_str']} (waited {current_time - pending_trade['timestamp']})")
            logger.warning(f"   Order 1 filled: {pending_trade['order1_filled']}, Order 2 filled: {pending_trade['order2_filled']}")
            
            # CRITICAL: Check for one-legged positions before cleanup
            if pending_trade['order1_filled'] and not pending_trade['order2_filled']:
                logger.critical(f"🚨 STALE TRADE WITH ONE-LEGGED POSITION DETECTED!")
                filled_symbol = pending_trade['symbol1']
                filled_volume = pending_trade['volume1']
                filled_side = "BUY" if pending_trade['direction'] == 'LONG' else "SELL"
                self._emergency_hedge_position(filled_symbol, filled_side, filled_volume, pending_trade)
                
            elif pending_trade['order2_filled'] and not pending_trade['order1_filled']:
                logger.critical(f"🚨 STALE TRADE WITH ONE-LEGGED POSITION DETECTED!")
                filled_symbol = pending_trade['symbol2']
                filled_volume = pending_trade['volume2']
                filled_side = "SELL" if pending_trade['direction'] == 'LONG' else "BUY"
                self._emergency_hedge_position(filled_symbol, filled_side, filled_volume, pending_trade)
            
            del self.pending_pair_trades[trade_key]
    
    def _monitor_emergency_hedges(self):
        """Monitor emergency hedge orders to ensure they complete successfully"""
        if not hasattr(self, 'emergency_hedges'):
            return
        
        current_time = datetime.now()
        
        for hedge_order_id, hedge_info in list(self.emergency_hedges.items()):
            # Check if the hedge order is still pending after too long
            time_elapsed = current_time - hedge_info['timestamp']
            
            if hedge_order_id in self.execution_requests and time_elapsed.total_seconds() > 30:
                logger.critical(f"🚨 EMERGENCY HEDGE ORDER {hedge_order_id} STILL PENDING AFTER {time_elapsed.total_seconds():.0f} seconds!")
                logger.critical(f"   Symbol: {hedge_info['symbol']}, Volume: {hedge_info['volume']:.5f}")
                logger.critical(f"   Original pair: {hedge_info['original_pair']}")
                logger.critical(f"   MANUAL INTERVENTION MAY BE REQUIRED!")
    
    def _get_emergency_hedges_status(self):
        """Get status of all emergency hedges for reporting"""
        if not hasattr(self, 'emergency_hedges'):
            return []
        
        hedge_status = []
        current_time = datetime.now()
        
        for hedge_order_id, hedge_info in self.emergency_hedges.items():
            status = "PENDING" if hedge_order_id in self.execution_requests else "COMPLETED"
            elapsed = current_time - hedge_info['timestamp']
            
            hedge_status.append({
                'order_id': hedge_order_id,
                'symbol': hedge_info['symbol'],
                'volume': hedge_info['volume'],
                'hedge_side': hedge_info['hedge_side'],
                'original_pair': hedge_info['original_pair'],
                'status': status,
                'elapsed_seconds': elapsed.total_seconds()
            })
        
        return hedge_status
    
    def get_risk_status_report(self):
        """Get comprehensive risk status report for monitoring"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'pending_pair_trades': len(self.pending_pair_trades) if hasattr(self, 'pending_pair_trades') else 0,
            'emergency_hedges': self._get_emergency_hedges_status(),
            'one_legged_positions': 0,  # Will be calculated below
            'suspended_pairs': self.get_suspended_pairs_status()
        }
        
        # Count one-legged positions in pending trades
        if hasattr(self, 'pending_pair_trades'):
            for pending_trade in self.pending_pair_trades.values():
                if (pending_trade['order1_filled'] and not pending_trade['order2_filled']) or \
                   (pending_trade['order2_filled'] and not pending_trade['order1_filled']):
                    report['one_legged_positions'] += 1
        
        # Add detailed pending trades info
        report['pending_trades_detail'] = []
        if hasattr(self, 'pending_pair_trades'):
            for trade_key, pending_trade in self.pending_pair_trades.items():
                current_time = datetime.now()
                elapsed = current_time - pending_trade['timestamp']
                
                report['pending_trades_detail'].append({
                    'pair': pending_trade['pair_str'],
                    'direction': pending_trade['direction'],
                    'order1_filled': pending_trade['order1_filled'],
                    'order2_filled': pending_trade['order2_filled'],
                    'elapsed_seconds': elapsed.total_seconds(),
                    'is_one_legged': (pending_trade['order1_filled'] and not pending_trade['order2_filled']) or 
                                   (pending_trade['order2_filled'] and not pending_trade['order1_filled'])
                })
        
        return report
    
    def _log_portfolio_status(self):
        """Log current portfolio status"""
        try:
            status = self.get_portfolio_status()
            
            logger.info("")
            logger.info("=" * 80)
            logger.info("PORTFOLIO STATUS")
            logger.info("-" * 80)
            logger.info(f"Active Pairs     : {len(self.pair_states)}")
            logger.info(f"Open Positions   : {status['position_count']}/{self.config.max_open_positions}")
            logger.info(f"Current Value    : ${status['portfolio_value']:,.2f}")
            logger.info(f"Exposure         : ${status['total_exposure']:,.2f}/{self.config.max_monetary_exposure:,.2f} ({status['total_exposure']/self.config.max_monetary_exposure*100:.1f}%)")
            
            # Calculate drawdown
            drawdown_pct = max(0, (self.portfolio_peak_value - status['portfolio_value']) / self.portfolio_peak_value * 100)
            logger.info(f"Drawdown         : {drawdown_pct:.2f}%")
            logger.info(f"Trading Status   : {'ACTIVE' if self.is_trading else 'STOPPED'}")
            
            # Handle suspended pairs safely
            suspended_count = len(self.suspended_pairs) if hasattr(self, 'suspended_pairs') else 0
            logger.info(f"Suspended Pairs  : {suspended_count}")
            
            # Show suspended pairs if any
            if suspended_count > 0 and hasattr(self, 'suspended_pairs'):
                suspended_list = ', '.join(sorted(self.suspended_pairs))
                logger.info(f"  Suspended: {suspended_list}")
            
            logger.info(f"Open P&L        : ${status['unrealized_pnl']:,.2f} ({status['unrealized_pnl']/self.config.initial_portfolio_value*100:+.2f}%)")
            
            # Log pending trades awaiting execution
            if hasattr(self, 'pending_pair_trades') and self.pending_pair_trades:
                logger.info(f"Pending Trades   : {len(self.pending_pair_trades)} awaiting execution")
            
            logger.info("-" * 80)
            
            # Log signal generation status
            logger.info("SIGNAL GENERATION STATUS")
            logger.info("-" * 80)
            total_price_updates = sum(getattr(self, '_price_log_counter', {}).values())
            total_signal_checks = getattr(self, '_signal_check_counter', 0)
            logger.info(f"Total Price Updates : {total_price_updates}")
            logger.info(f"Total Signal Checks : {total_signal_checks}")
            logger.info(f"Subscribed Symbols  : {len(self.subscribed_symbols)}")
            logger.info(f"Live Price Data     : {len(self.spot_prices)} symbols")
            
            # Show data accumulation status
            if hasattr(self, '_data_accumulation_log'):
                logger.info("-" * 40)
                logger.info("DATA ACCUMULATION STATUS")
                logger.info("-" * 40)
                for pair_str, state in self.pair_states.items():
                    min_data_points = getattr(self.strategy, 'get_minimum_data_points', lambda: 50)()
                    p1_count = len(state['price1'])
                    p2_count = len(state['price2'])
                    ready = "✅" if p1_count >= min_data_points and p2_count >= min_data_points else "⏳"
                    logger.info(f"{ready} {pair_str}: {p1_count}/{min_data_points} + {p2_count}/{min_data_points}")
            
            if status['positions']:
                logger.info("-" * 40)
                logger.info("ACTIVE PAIRS P&L")
                logger.info("-" * 40)
                logger.info("PAIR            P&L($)      P&L(%)   VALUE($)")
                logger.info("-" * 40)
                
                for pos in status['positions']:
                    position_value = abs(pos['volume1'] * pos['current_price1']) + abs(pos['volume2'] * pos['current_price2'])
                    pnl_pct = (pos['pnl'] / position_value * 100) if position_value > 0 else 0
                    logger.info(f"{pos['pair']:<15} {pos['pnl']:>8.2f}   {pnl_pct:>6.2f}    {position_value:>8,.0f}")
            
            # Show pending trades if any
            if hasattr(self, 'pending_pair_trades') and self.pending_pair_trades:
                logger.info("-" * 40)
                logger.info("PENDING TRADES")
                logger.info("-" * 40)
                logger.info("PAIR            DIRECTION   ELAPSED   LEG1  LEG2")
                logger.info("-" * 40)
                
                for trade_key, pending_trade in self.pending_pair_trades.items():
                    elapsed = datetime.now() - pending_trade['timestamp']
                    elapsed_str = f"{elapsed.total_seconds():.0f}s"
                    leg1_status = "✓" if pending_trade['order1_filled'] else "○"
                    leg2_status = "✓" if pending_trade['order2_filled'] else "○"
                    logger.info(f"{pending_trade['pair_str']:<15} {pending_trade['direction']:<9} {elapsed_str:>7}   {leg1_status:>4}  {leg2_status:>4}")
            
            logger.info("=" * 80)
            logger.info("")
            
        except Exception as e:
            logger.error(f"Error logging portfolio status: {e}")
    
    def _monitor_positions(self):
        """Monitor existing positions for risk management"""
        try:
            current_time = datetime.now()
            
            for pair_str, position in list(self.active_positions.items()):
                # Check if position should be closed due to time limits or risk management
                if position['entry_time']:
                    position_duration = current_time - position['entry_time']
                    
                    # Example: Close positions after 24 hours (can be configured)
                    if position_duration.total_seconds() > 86400:  # 24 hours
                        logger.info(f"Closing {pair_str} position due to time limit")
                        self._close_pair_position(pair_str)
                        continue
                
                # Check stop loss / take profit levels
                self._check_position_exit_conditions(pair_str, position)
                
        except Exception as e:
            logger.error(f"Error monitoring positions: {e}")
    
    def _check_position_exit_conditions(self, pair_str: str, position: dict):
        """Check if position should be closed based on stop loss or take profit"""
        try:
            s1, s2 = position['symbol1'], position['symbol2']
            direction = position['direction']
            
            # Get current prices
            if s1 not in self.spot_prices or s2 not in self.spot_prices:
                return
            
            current_price1 = self.spot_prices[s1]
            current_price2 = self.spot_prices[s2]
            
            # Calculate current P&L percentage
            if direction == 'LONG':
                pnl = (current_price1 - position['entry_price1']) * position['volume1'] - \
                      (current_price2 - position['entry_price2']) * position['volume2']
            else:
                pnl = (position['entry_price1'] - current_price1) * position['volume1'] - \
                      (position['entry_price2'] - current_price2) * position['volume2']
            
            # Calculate position value for percentage calculation
            position_value = abs(position['volume1'] * position['entry_price1']) + \
                           abs(position['volume2'] * position['entry_price2'])
            
            if position_value > 0:
                pnl_percentage = (pnl / position_value) * 100
                
                # Check stop loss
                if pnl_percentage <= -self.config.stop_loss_perc:
                    logger.info(f"Stop loss triggered for {pair_str}: {pnl_percentage:.2f}%")
                    self._close_pair_position(pair_str)
                    return
                
                # Check take profit
                if pnl_percentage >= self.config.take_profit_perc:
                    logger.info(f"Take profit triggered for {pair_str}: {pnl_percentage:.2f}%")
                    self._close_pair_position(pair_str)
                    return
                
        except Exception as e:
            logger.error(f"Error checking exit conditions for {pair_str}: {e}")

    def stop_trading(self):
        """Stop real-time trading"""
        self.is_trading = False
        
        # Wait for trading thread to stop
        if self.trading_thread and self.trading_thread.is_alive():
            logger.info("Waiting for trading thread to stop...")
            self.trading_thread.join(timeout=10)
        
        # Unsubscribe from data
        for symbol in list(self.subscribed_symbols):
            self._unsubscribe_from_spots(symbol)
        
        # Disconnect client - handle different disconnect methods
        if self.client:
            try:
                if hasattr(self.client, 'disconnect'):
                    self.client.disconnect()
                elif hasattr(self.client, 'transport') and hasattr(self.client.transport, 'loseConnection'):
                    self.client.transport.loseConnection()
                elif hasattr(self.client, 'stopService'):
                    self.client.stopService()
                else:
                    logger.warning("No known disconnect method found for cTrader client")
            except Exception as e:
                logger.warning(f"Error disconnecting cTrader client: {e}")
        
        logger.info("CTrader real-time trading stopped")
    
    def _unsubscribe_from_spots(self, symbol):
        """Unsubscribe from spot price updates"""
        if symbol in self.symbols_map and symbol in self.subscribed_symbols:
            symbol_id = self.symbols_map[symbol]
            
            request = self._create_protobuf_request('UNSUBSCRIBE_SPOTS',
                                                   account_id=self.account_id,
                                                   symbol_ids=[symbol_id])
            
            if request is None:
                logger.error(f"Failed to create unsubscribe request for {symbol}")
                return
            
            try:
                deferred = self.client.send(request)
                deferred.addErrback(lambda f: logger.debug(f"Unsubscribe error: {f}"))
                self.subscribed_symbols.discard(symbol)
                logger.debug(f"Unsubscribed from spot prices for {symbol}")
            except Exception as e:
                logger.error(f"Error unsubscribing from {symbol}: {e}")
    
    def get_portfolio_status(self) -> Dict[str, Any]:
        """Get current portfolio status including strategy information"""
        total_positions = len(self.active_positions)
        
        # Calculate basic portfolio metrics
        portfolio_value = self.config.initial_portfolio_value
        unrealized_pnl = 0.0
        total_exposure = 0.0
        realized_pnl = 0.0  # This would need to be tracked from completed trades
        
        positions = []
        for pair_str, position in self.active_positions.items():
            # Calculate position P&L (simplified)
            current_price1 = self.spot_prices.get(position['symbol1'], position['entry_price1'])
            current_price2 = self.spot_prices.get(position['symbol2'], position['entry_price2'])
            
            # Basic P&L calculation (expand for more accuracy)
            if position['direction'] == 'LONG':
                pnl = (current_price1 - position['entry_price1']) * position['volume1'] - \
                      (current_price2 - position['entry_price2']) * position['volume2']
            else:
                pnl = (position['entry_price1'] - current_price1) * position['volume1'] - \
                      (position['entry_price2'] - current_price2) * position['volume2']
            
            unrealized_pnl += pnl
            total_exposure += abs(position['volume1'] * current_price1) + abs(position['volume2'] * current_price2)
            
            positions.append({
                'pair': pair_str,
                'direction': position['direction'],
                'volume1': position['volume1'],
                'volume2': position['volume2'],
                'entry_price1': position['entry_price1'],
                'entry_price2': position['entry_price2'],
                'current_price1': current_price1,
                'current_price2': current_price2,
                'pnl': pnl,
                'entry_time': position['entry_time']
            })
        
        # Update portfolio peak value for drawdown calculation
        current_portfolio_value = portfolio_value + unrealized_pnl
        if current_portfolio_value > self.portfolio_peak_value:
            self.portfolio_peak_value = current_portfolio_value
        
        # Get strategy information
        strategy_info = self.strategy.get_strategy_info()
        
        return {
            'portfolio_value': current_portfolio_value,
            'unrealized_pnl': unrealized_pnl,
            'realized_pnl': realized_pnl,
            'position_count': total_positions,
            'total_exposure': total_exposure,
            'positions': positions,
            'account_currency': self.account_currency,
            'broker': 'ctrader',
            'strategy': strategy_info['name'],
            'strategy_type': strategy_info['type'],
            'max_positions': self.config.max_open_positions,
            'max_exposure': getattr(self.config, 'max_monetary_exposure', float('inf')),
            'trading_suspended': self.portfolio_trading_suspended,
            'instruments_tracked': len(self.pair_states),
            'symbols_subscribed': len(self.subscribed_symbols)
        }
