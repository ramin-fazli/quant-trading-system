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
    
    # Verify key classes are available
    _required_classes = [
        'ProtoOAApplicationAuthReq', 'ProtoOAApplicationAuthRes',
        'ProtoOAAccountAuthReq', 'ProtoOAAccountAuthRes', 
        'ProtoOASymbolsListReq', 'ProtoOASymbolsListRes',
        'ProtoOASymbolByIdReq', 'ProtoOASymbolByIdRes',
        'ProtoOANewOrderReq', 'ProtoOAOrderType', 'ProtoOATradeSide',
        'ProtoOASubscribeSpotsReq', 'ProtoOAUnsubscribeSpotsReq',
        'ProtoOASpotEvent', 'ProtoOAExecutionEvent', 'ProtoOAGetTrendbarsRes',
        'ProtoOASubscribeSpotsRes'
    ]
    
    for cls_name in _required_classes:
        if cls_name not in globals():
            logger.warning(f"Required class {cls_name} not found, but continuing anyway")
    
    CTRADER_API_AVAILABLE = True
except ImportError as e:
    CTRADER_API_AVAILABLE = False
    logger.warning(f"cTrader Open API not available: {e}")


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
        request = ProtoOAApplicationAuthReq()
        request.clientId = self.client_id
        request.clientSecret = self.client_secret
        
        deferred = self.client.send(request)
        deferred.addErrback(self._on_error)
    
    def _authenticate_account(self):
        """Authenticate the trading account"""
        logger.info("Authenticating account with cTrader...")
        request = ProtoOAAccountAuthReq()
        request.ctidTraderAccountId = self.account_id
        request.accessToken = self.access_token
        
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
            request = ProtoOASymbolsListReq()
            request.ctidTraderAccountId = self.account_id
            request.includeArchivedSymbols = False
            
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
                
                request = ProtoOASymbolByIdReq()
                request.ctidTraderAccountId = self.account_id
                request.symbolId.extend(chunk)
                
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
            logger.debug(f"Received message type: {message.payloadType}")
            
            if message.payloadType == ProtoOAApplicationAuthRes().payloadType:
                logger.info("Application authenticated with cTrader")
                self._authenticate_account()
                
            elif message.payloadType == ProtoOAAccountAuthRes().payloadType:
                logger.info("Account authenticated with cTrader")
                # Extract account details for verification
                response = Protobuf.extract(message)
                logger.info(f"🔍 ACCOUNT VERIFICATION:")
                logger.info(f"   Account ID: {getattr(response, 'ctidTraderAccountId', 'Unknown')}")
                logger.info(f"   Live Account: {getattr(response, 'liveAccount', 'Unknown')}")
                logger.info(f"   Account Type: {'LIVE' if getattr(response, 'liveAccount', False) else 'DEMO'}")
                
                if not self.symbols_initialized:  # Only request once
                    self._get_symbols_list()
                    
            elif message.payloadType == 2142:  # Account auth response type
                if not hasattr(self, '_account_authenticated'):
                    self._account_authenticated = True
                    logger.info("Account authenticated with cTrader (type 2142)")
                    if not self.symbols_initialized:  # Only request once
                        self._get_symbols_list()
                        
            elif message.payloadType == ProtoOASymbolsListRes().payloadType:
                logger.info("Received symbols list response")
                self._process_symbols_list(message)
            elif message.payloadType == 2143:  # Alternative symbols list response type
                logger.info("Received symbols list response (type 2143)")
                self._process_symbols_list(message)
                
            elif message.payloadType == ProtoOASymbolByIdRes().payloadType:
                logger.info("Received symbol details response")
                self._process_symbol_details(message)
            elif message.payloadType == 2144:  # Alternative symbol details response type
                logger.info("Received symbol details response (type 2144)")
                self._process_symbol_details(message)
                
            elif message.payloadType == ProtoOAGetTrendbarsRes().payloadType:
                self._process_trendbar_data(message)
                
            elif message.payloadType == ProtoOASpotEvent().payloadType:
                # Extract the spot event properly
                event = Protobuf.extract(message)
                self._process_spot_event(event)
                
            elif message.payloadType == ProtoOAExecutionEvent().payloadType:
                # Extract the execution event properly  
                event = Protobuf.extract(message)
                self._process_execution_event(event)
                
            elif message.payloadType == ProtoOASubscribeSpotsRes().payloadType:
                logger.debug("Spot subscription confirmed")
                
            else:
                # Reduce log noise from unhandled message types
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
            
            # Collect symbol IDs for detailed symbol information request
            symbol_ids = []
            
            for symbol in response.symbol:
                logger.info(f"Processing symbol: {symbol.symbolName}")
                symbol_name = symbol.symbolName
                symbol_id = symbol.symbolId
                
                self.symbols_map[symbol_name] = symbol_id
                self.symbol_id_to_name_map[symbol_id] = symbol_name
                symbol_ids.append(symbol_id)
            
            # Request detailed symbol information for all symbols
            logger.info(f"Requesting detailed symbol information for {len(symbol_ids)} symbols...")
            self._request_symbol_details(symbol_ids)
            
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
        logger.info("Processing detailed symbol information...")
        
        try:
            # Extract the message content properly using Protobuf.extract
            response = Protobuf.extract(message)
            
            # Check if we have symbols in the response
            if not hasattr(response, 'symbol') or len(response.symbol) == 0:
                logger.warning("No detailed symbols received from cTrader API")
                return
                
            symbols_processed = 0
            
            for symbol in response.symbol:
                symbol_name = self.symbol_id_to_name_map.get(symbol.symbolId)
                
                if not symbol_name:
                    logger.warning(f"Symbol ID {symbol.symbolId} not found in name mapping")
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
                        symbol_details[local_name] = value
                    else:
                        logger.debug(f"Optional field {api_name} not available for symbol {symbol_name}")
                
                # Store the detailed symbol information
                self.symbol_details[symbol_name] = symbol_details
                symbols_processed += 1
                
                logger.info(f"Processed detailed info for {symbol_name}: "
                          f"digits={symbol_details.get('digits')}, "
                          f"min_volume={symbol_details.get('min_volume')}, "
                          f"max_volume={symbol_details.get('max_volume')}, "
                          f"lot_size={symbol_details.get('lot_size')}")
            
            logger.info(f"Successfully processed detailed information for {symbols_processed} symbols")
            
            # Check if we have processed all expected symbols
            expected_symbols = len(self.symbols_map)
            if symbols_processed < expected_symbols:
                logger.warning(f"Only processed {symbols_processed}/{expected_symbols} symbols - some may be missing detailed info")
            
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
        
        # Subscribe to spot prices for available symbols
        successful_subscriptions = 0
        for symbol in available_symbols:
            if self._subscribe_to_spot_prices(symbol):
                successful_subscriptions += 1
        
        logger.info(f"✅ Real-time data subscriptions: {successful_subscriptions}/{len(available_symbols)} symbols")
        logger.info(f"✅ Trading system ready with {self.strategy.__class__.__name__}")
        if unavailable_symbols:
            logger.info("📝 Note: Some symbols were unavailable and skipped")
    
    def _subscribe_to_spot_prices(self, symbol):
        """Subscribe to real-time price updates for a symbol"""
        if symbol in self.symbols_map and symbol not in self.subscribed_symbols:
            symbol_id = self.symbols_map[symbol]
            
            request = ProtoOASubscribeSpotsReq()
            request.ctidTraderAccountId = self.account_id
            request.symbolId.append(symbol_id)
            
            try:
                deferred = self.client.send(request)
                deferred.addErrback(self._on_subscription_error, symbol)
                self.subscribed_symbols.add(symbol)
                logger.debug(f"Subscribed to spot prices for {symbol}")
                return True
            except Exception as e:
                logger.error(f"Error subscribing to {symbol}: {e}")
                return False
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
    
    def _process_spot_event(self, event):
        """Process real-time price updates with throttling to reduce duplicates"""
        symbol_id = event.symbolId
        symbol_name = self.symbol_id_to_name_map.get(symbol_id)
        
        if not symbol_name:
            return
        
        # Get symbol details for price conversion
        details = self.symbol_details.get(symbol_name)
        if not details:
            return
        
        digits = details['digits']
        
        # Extract bid/ask prices
        bid = event.bid / (10 ** digits) if hasattr(event, 'bid') else None
        ask = event.ask / (10 ** digits) if hasattr(event, 'ask') else None
        timestamp = datetime.fromtimestamp(event.timestamp / 1000)
        
        if bid is None or ask is None:
            return
        
        # Store mid price
        price = (bid + ask) / 2
        self.spot_prices[symbol_name] = price
        
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
            if not hasattr(self, '_signal_check_counter'):
                self._signal_check_counter = 0
            self._signal_check_counter += 1
            
            if self._signal_check_counter % 20 == 0:
                logger.info(f"🧠 SIGNAL CHECKING: Processing {pair_str} (check #{self._signal_check_counter})")
            
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
            
            # Thread-safe check of active positions
            with self._update_lock:
                has_active_position = pair_str in self.active_positions
                current_position_count = len(self.active_positions)
            
            # Check if trading conditions are suitable
            if not getattr(latest_signal, 'suitable', True):
                return
            
            # Check portfolio limits before entry
            if current_position is None and not has_active_position:
                if current_position_count >= self.config.max_open_positions:
                    return
            
            # Process entry signals using strategy output
            if current_position is None and not has_active_position:
                if getattr(latest_signal, 'short_entry', False):
                    logger.info(f"[SHORT ENTRY] Signal for {pair_str} - Positions: {current_position_count}/{self.config.max_open_positions}")
                    self._execute_pair_trade(pair_str, 'SHORT')
                elif getattr(latest_signal, 'long_entry', False):
                    logger.info(f"[LONG ENTRY] Signal for {pair_str} - Positions: {current_position_count}/{self.config.max_open_positions}")
                    self._execute_pair_trade(pair_str, 'LONG')
            
            # Process exit signals using strategy output
            elif current_position is not None or has_active_position:
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
        
        if not self._check_drawdown_limits(pair_str):
            logger.error(f"[ERROR] Trade blocked by drawdown limits for {pair_str}")
            return False
        
        state = self.pair_states[pair_str]
        s1, s2 = state['symbol1'], state['symbol2']
        
        # Get current prices
        if s1 not in self.spot_prices or s2 not in self.spot_prices:
            logger.warning(f"Missing spot prices for {pair_str}")
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
            side1 = ProtoOATradeSide.Value("BUY")   # Buy first symbol
            side2 = ProtoOATradeSide.Value("SELL")  # Sell second symbol
        else:
            side1 = ProtoOATradeSide.Value("SELL")  # Sell first symbol
            side2 = ProtoOATradeSide.Value("BUY")   # Buy second symbol
        
        # Execute trades
        order1 = self._send_market_order(s1, side1, volume1)
        order2 = self._send_market_order(s2, side2, volume2)
        
        if order1 and order2:
            # Store position with thread safety
            with self._update_lock:
                self.active_positions[pair_str] = {
                    'direction': direction,
                    'symbol1': s1,
                    'symbol2': s2,
                    'volume1': volume1,
                    'volume2': volume2,
                    'entry_price1': price1,
                    'entry_price2': price2,
                    'entry_time': datetime.now(),
                    'order_ids': (order1, order2)
                }
                
                state['position'] = direction
                state['entry_time'] = datetime.now()
                state['entry_price1'] = price1
                state['entry_price2'] = price2
            
            logger.info(f"Successfully executed {direction} trade for {pair_str} - Portfolio now: {len(self.active_positions)}/{self.config.max_open_positions}")
            return True
        else:
            logger.error(f"[ERROR] Failed to execute {direction} trade for {pair_str}")
        
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
            close_side1 = ProtoOATradeSide.Value("SELL")  # Sell to close long position
            close_side2 = ProtoOATradeSide.Value("BUY")   # Buy to close short position
        else:
            close_side1 = ProtoOATradeSide.Value("BUY")   # Buy to close short position  
            close_side2 = ProtoOATradeSide.Value("SELL")  # Sell to close long position
        
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
        
        request = ProtoOANewOrderReq()
        request.ctidTraderAccountId = self.account_id
        request.symbolId = symbol_id
        request.orderType = ProtoOAOrderType.Value("MARKET")
        request.tradeSide = side
        request.clientOrderId = client_order_id
        
        if is_close:
            # For closing positions, we need to use ProtoOAClosePositionReq instead
            # For now, we'll create a regular market order in the opposite direction
            # TODO: Implement proper position ID tracking for ProtoOAClosePositionReq
            logger.debug(f"Using market order to close position for {symbol} (fallback method)")
        
        # Validate volume is provided
        if volume is None:
            logger.error(f"Volume is required for all orders")
            return None
        
        # Convert volume to cTrader format - following the sample pattern
        # The sample uses: request.volume = int(volume) * 100
        # This means volume should be in centilots (standard lots * 100)
        symbol_details = self.symbol_details.get(symbol, {})
        min_volume = symbol_details.get('min_volume', 1000)
        
        # Convert to centilots format like the sample
        broker_volume = int(volume) * 100
        # Ensure volume meets minimum requirements
        broker_volume = max(broker_volume, min_volume)
        request.volume = broker_volume
        
        # Convert side to readable string for logging
        side_name = "BUY" if side == ProtoOATradeSide.Value("BUY") else "SELL"
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
            
            return client_order_id
            
        except Exception as e:
            logger.error(f"Error sending order for {symbol}: {e}")
            return None
    
    def _on_order_error(self, failure):
        """Handle order execution errors"""
        logger.error(f"Order execution error: {failure}")
    
    def _process_execution_event(self, event):
        """Process order execution events"""
        client_order_id = getattr(event, 'clientOrderId', None)
        
        # Log all execution events for debugging
        logger.info(f"🎯 EXECUTION EVENT RECEIVED:")
        logger.info(f"   Client Order ID: {client_order_id}")
        logger.info(f"   Event Type: {getattr(event, 'executionType', 'Unknown')}")
        logger.info(f"   Order Status: {getattr(event, 'orderStatus', 'Unknown')}")
        logger.info(f"   Position ID: {getattr(event, 'positionId', 'None')}")
        logger.info(f"   Deal ID: {getattr(event, 'dealId', 'None')}")
        
        if client_order_id in self.execution_requests:
            order_data = self.execution_requests[client_order_id]
            
            # Process execution details
            if hasattr(event, 'executionType'):
                execution_type = event.executionType
                logger.info(f"✅ Order {client_order_id} execution: {execution_type}")
                
                # Log additional details for successful executions
                if hasattr(event, 'executedVolume'):
                    logger.info(f"   Executed Volume: {getattr(event, 'executedVolume', 0)}")
                if hasattr(event, 'executionPrice'):
                    logger.info(f"   Execution Price: {getattr(event, 'executionPrice', 0)}")
            
            # Clean up completed orders
            if hasattr(event, 'orderStatus') and event.orderStatus in ['FILLED', 'CANCELLED', 'REJECTED']:
                logger.info(f"🏁 Order {client_order_id} completed with status: {event.orderStatus}")
                del self.execution_requests[client_order_id]
        else:
            logger.warning(f"⚠️  Received execution event for unknown order: {client_order_id}")
    
    def _calculate_balanced_volumes(self, symbol1: str, symbol2: str, price1: float, price2: float) -> Optional[Tuple[float, float, float, float]]:
        """Calculate volumes for equal monetary exposure between two symbols with comprehensive validation"""
        
        # Get symbol information
        details1 = self.symbol_details.get(symbol1)
        logger.info(f"Symbol details for {symbol1}: {details1}")
        details2 = self.symbol_details.get(symbol2)
        logger.info(f"Symbol details for {symbol2}: {details2}")
        
        if not details1 or not details2:
            logger.error(f"Missing symbol details for {symbol1} or {symbol2}")
            return None
        
        # Enhanced logging for debugging
        logger.info(f"Volume calculation for {symbol1}-{symbol2}:")
        logger.info(f"  {symbol1}: price={price1:.5f}, min_volume={details1.get('min_volume', 'N/A')}")
        logger.info(f"  {symbol2}: price={price2:.5f}, min_volume={details2.get('min_volume', 'N/A')}")
        
        # Calculate target monetary value per leg (half of max position size for each leg)
        target_monetary_value = self.config.max_position_size / 2
        
        # For crypto pairs, we need to scale down the position size significantly
        # to avoid enormous volumes on low-priced assets like SOLUSD, XRPUSD, etc.
        crypto_symbols = {'BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'DOT', 'LINK', 'UNI', 'AVAX', 'MATIC'}
        is_crypto_pair = any(crypto in symbol1 for crypto in crypto_symbols) or any(crypto in symbol2 for crypto in crypto_symbols)
        
        if is_crypto_pair:
            # For crypto pairs, use much smaller position sizes to avoid massive volumes
            # Scale down by 100x for crypto to keep volumes reasonable
            target_monetary_value = target_monetary_value / 100
            logger.info(f"Crypto pair detected ({symbol1}-{symbol2}), scaling position size down to ${target_monetary_value:.2f} per leg")
        
        logger.info(f"  Target monetary value per leg: ${target_monetary_value:.2f}")
        
        # Get contract sizes - cTrader typically uses different contract size model
        # For cTrader: 1 standard lot = 100,000 units for major forex pairs
        # For other instruments, we need to get the actual contract size from symbol details
        contract_size1 = details1.get('contract_size', 100000)  # Default to 100,000 for forex
        contract_size2 = details2.get('contract_size', 100000)
        
        # If contract size not available, estimate based on symbol type
        if contract_size1 == 100000 and any(crypto in symbol1 for crypto in crypto_symbols):
            contract_size1 = 1  # Crypto typically has contract size of 1
        if contract_size2 == 100000 and any(crypto in symbol2 for crypto in crypto_symbols):
            contract_size2 = 1  # Crypto typically has contract size of 1
        
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
        
        # Additional safety check: Cap volumes to reasonable maximum values for crypto
        if is_crypto_pair:
            max_crypto_notional = 500  # No single leg should exceed $500 notional for crypto
            if monetary_value1 > max_crypto_notional or monetary_value2 > max_crypto_notional:
                logger.warning(f"Crypto position size too large for {symbol1}-{symbol2}: ${monetary_value1:.2f}, ${monetary_value2:.2f}")
                return None
        
        logger.info(f"  Successfully calculated balanced volumes with {final_diff_pct:.4f} difference")
        return volume1, volume2, monetary_value1, monetary_value2
    
    def _normalize_ctrader_volume(self, symbol: str, volume_raw: float, symbol_details: Dict) -> Optional[float]:
        """Normalize volume to valid increments and constraints for cTrader"""
        
        # Extract volume constraints from symbol details
        min_vol = symbol_details.get('min_volume', 0.01)  # Default minimum
        max_vol = symbol_details.get('max_volume', 100.0)  # Default maximum
        
        # For cTrader, volume step is typically 0.01 for most instruments
        step = symbol_details.get('volume_step', 0.01)
        
        logger.info(f"Normalizing volume for {symbol}: raw={volume_raw:.6f}, min={min_vol}, max={max_vol}, step={step}")
        
        # Handle edge case where raw volume is 0 or negative
        if volume_raw <= 0:
            logger.warning(f"Invalid raw volume for {symbol}: {volume_raw}")
            return None
        
        # Round to valid step increments
        volume = round(volume_raw / step) * step
        
        # Apply min/max constraints
        volume = max(min_vol, min(max_vol, volume))
        
        # Validate minimum volume requirement
        if volume < min_vol:
            logger.warning(f"Volume {volume:.6f} below minimum {min_vol} for {symbol}")
            return None
        
        # Validate maximum volume requirement  
        if volume > max_vol:
            logger.warning(f"Volume {volume:.6f} exceeds maximum {max_vol} for {symbol}")
            volume = max_vol
        
        # Additional validation: ensure volume is not zero after rounding
        if volume == 0:
            logger.warning(f"Volume became zero after normalization for {symbol} (raw: {volume_raw:.6f})")
            return None
        
        logger.info(f"Normalized volume for {symbol}: {volume:.6f}")
        return volume
    
    def _check_drawdown_limits(self, pair_str: str = None) -> bool:
        """Check if trading should be allowed based on drawdown limits"""
        # Simplified implementation - expand based on requirements
        if self.portfolio_trading_suspended:
            return False
        
        if pair_str and pair_str in self.suspended_pairs:
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
        
        while self.is_trading:
            try:
                # Check if symbols need to be retried (less frequently to avoid spam)
                symbols_check_counter += 1
                if symbols_check_counter % 60 == 0:  # Check every 60 seconds instead of 30
                    self._check_symbols_timeout()
                    symbols_check_counter = 0
                
                # Only proceed with trading if symbols are available or in degraded mode
                if self.symbols_initialized or self._degraded_mode:
                    # Check trading signals for all pairs
                    self._check_trading_signals()
                    
                    # Monitor existing positions
                    self._monitor_positions()
                else:
                    # Log status periodically while waiting for symbols (less frequently)
                    if symbols_check_counter % 30 == 0:
                        logger.debug("Waiting for symbols initialization before starting trading...")
                
                # Log portfolio status every 5 minutes (300 seconds)
                status_counter += 1
                if status_counter % 300 == 0:
                    self._log_portfolio_status()
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
            logger.info(f"Suspended Pairs  : {len(self.suspended_pairs)}")
            logger.info(f"Open P&L        : ${status['unrealized_pnl']:,.2f} ({status['unrealized_pnl']/self.config.initial_portfolio_value*100:+.2f}%)")
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
            
            request = ProtoOAUnsubscribeSpotsReq()
            request.ctidTraderAccountId = self.account_id
            request.symbolId.append(symbol_id)
            
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
