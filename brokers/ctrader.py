"""
CTrader Real-Time Trading Engine
===============================

High-performance real-time trading engine for cTrader Open API
Optimized for pairs trading strategies with advanced risk management.

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
from strategies.pairs_trading import OptimizedPairsStrategy

# cTrader Open API imports
try:
    from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
    from ctrader_open_api.messages import (
        OpenApiMessages_pb2,
        OpenApiModelMessages_pb2
    )
    from twisted.internet import reactor, defer
    
    # Import specific message types
    ProtoOAApplicationAuthReq = OpenApiMessages_pb2.ProtoOAApplicationAuthReq
    ProtoOAApplicationAuthRes = OpenApiMessages_pb2.ProtoOAApplicationAuthRes
    ProtoOAAccountAuthReq = OpenApiMessages_pb2.ProtoOAAccountAuthReq
    ProtoOAAccountAuthRes = OpenApiMessages_pb2.ProtoOAAccountAuthRes
    ProtoOASymbolsListReq = OpenApiMessages_pb2.ProtoOASymbolsListReq
    ProtoOASymbolsListRes = OpenApiMessages_pb2.ProtoOASymbolsListRes
    ProtoOAGetTrendbarsReq = OpenApiMessages_pb2.ProtoOAGetTrendbarsReq
    ProtoOAGetTrendbarsRes = OpenApiMessages_pb2.ProtoOAGetTrendbarsRes
    ProtoOATrendbarPeriod = OpenApiModelMessages_pb2.ProtoOATrendbarPeriod
    
    # Real-time trading imports
    ProtoOASubscribeSpotsReq = OpenApiMessages_pb2.ProtoOASubscribeSpotsReq
    ProtoOASubscribeSpotsRes = OpenApiMessages_pb2.ProtoOASubscribeSpotsRes
    ProtoOASpotEvent = OpenApiMessages_pb2.ProtoOASpotEvent
    ProtoOAUnsubscribeSpotsReq = OpenApiMessages_pb2.ProtoOAUnsubscribeSpotsReq
    ProtoOANewOrderReq = OpenApiMessages_pb2.ProtoOANewOrderReq
    ProtoOAExecutionEvent = OpenApiMessages_pb2.ProtoOAExecutionEvent
    ProtoOAClosePositionReq = OpenApiMessages_pb2.ProtoOAClosePositionReq
    ProtoOAAmendPositionSLTPReq = OpenApiMessages_pb2.ProtoOAAmendPositionSLTPReq
    ProtoOAPosition = OpenApiModelMessages_pb2.ProtoOAPosition
    ProtoOAOrderType = OpenApiModelMessages_pb2.ProtoOAOrderType
    ProtoOATradeSide = OpenApiModelMessages_pb2.ProtoOATradeSide
    
    CTRADER_API_AVAILABLE = True
except ImportError:
    CTRADER_API_AVAILABLE = False

logger = logging.getLogger(__name__)


class CTraderRealTimeTrader:
    """
    High-performance real-time trading engine for cTrader Open API
    Optimized for pairs trading with advanced risk management
    """
    
    def __init__(self, config: TradingConfig, data_manager):
        if not CTRADER_API_AVAILABLE:
            raise ImportError("cTrader Open API not available. Install with: pip install ctrader-open-api")
        
        self.config = config
        self.data_manager = data_manager
        self.strategy = OptimizedPairsStrategy(config, data_manager)
        
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
        
        logger.info("CTrader Real-Time Trader initialized")
    
    def initialize(self) -> bool:
        """Initialize cTrader real-time trading system"""
        logger.info("Initializing cTrader real-time trading system...")
        
        # Log environment configuration for debugging
        logger.info("🔍 CTRADER CONFIGURATION DEBUG:")
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
                logger.info("⚠️  Demo mode: Trades will be simulated, not real!")
            else:
                host = EndPoints.PROTOBUF_LIVE_HOST
                logger.info(f"🔧 Using LIVE server: {host}:{EndPoints.PROTOBUF_PORT}")
                logger.info("💰 Live mode: Real trading with real money!")
            
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
            
            for symbol in response.symbol:
                symbol_name = symbol.symbolName
                symbol_id = symbol.symbolId
                
                self.symbols_map[symbol_name] = symbol_id
                self.symbol_id_to_name_map[symbol_id] = symbol_name
                
                # Store symbol details with safe attribute access
                self.symbol_details[symbol_name] = {
                    'digits': getattr(symbol, 'digits', 5),
                    'pip_factor': getattr(symbol, 'pipFactor', 10),
                    'min_volume': getattr(symbol, 'minVolume', 1000),
                    'max_volume': getattr(symbol, 'maxVolume', 100000000),
                    'volume_step': getattr(symbol, 'stepVolume', 1000),
                }
            
            logger.info(f"Successfully retrieved {len(self.symbols_map)} symbols from cTrader")
            self.symbols_initialized = True
            
            # Initialize pair states
            self._initialize_pair_states()
            
            # Subscribe to real-time data
            self._subscribe_to_data()
            
            # Start the trading loop now that we have real symbols
            if not self.trading_started:
                logger.info("🚀 Starting real trading loop with live cTrader data")
                threading.Thread(target=self._trading_loop, daemon=True).start()
                self.trading_started = True
            
        except Exception as e:
            logger.error(f"Error processing symbols list: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def _initialize_pair_states(self):
        """Initialize pair states for trading"""
        # Extract unique symbols from configured pairs
        all_symbols = set()
        valid_pairs = []
        
        for pair_str in self.config.pairs:
            try:
                s1, s2 = pair_str.split('-')
                if s1 in self.symbols_map and s2 in self.symbols_map:
                    all_symbols.add(s1)
                    all_symbols.add(s2)
                    valid_pairs.append(pair_str)
                else:
                    logger.debug(f"Pair {pair_str} skipped - symbols not available in cTrader")
            except ValueError:
                logger.warning(f"Invalid pair format: {pair_str}")
                continue
        
        logger.info(f"Initializing {len(valid_pairs)} valid pairs from {len(self.config.pairs)} configured pairs")
        
        # Initialize pair states
        for pair_str in valid_pairs:
            s1, s2 = pair_str.split('-')
            self.pair_states[pair_str] = {
                'symbol1': s1,
                'symbol2': s2,
                'price1': deque(maxlen=1000),
                'price2': deque(maxlen=1000),
                'position': None,
                'last_signal': None,
                'cooldown': 0,
                'last_trade_time': None,
                'entry_time': None,
                'entry_zscore': 0,
                'entry_price1': 0,
                'entry_price2': 0
            }
        
        logger.info(f"Initialized {len(self.pair_states)} pair states")
        
        # If no valid pairs, log a warning
        if len(valid_pairs) == 0:
            logger.warning("No valid pairs found! Check symbol availability in cTrader")
    
    def _subscribe_to_data(self):
        """Subscribe to real-time data for all required symbols"""
        all_symbols = set()
        for pair_str in self.pair_states.keys():
            s1, s2 = pair_str.split('-')
            all_symbols.add(s1)
            all_symbols.add(s2)
        
        # Subscribe to spot prices
        successful_subscriptions = 0
        for symbol in all_symbols:
            if self._subscribe_to_spot_prices(symbol):
                successful_subscriptions += 1
        
        logger.info(f"✅ Real-time data subscriptions: {successful_subscriptions} symbols")
        logger.info(f"✅ Trading loop started")
        logger.info("📝 Note: Subscription timeout messages above are normal cTrader API behavior")
    
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
        """Process real-time price updates"""
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
        
        # Add to price history
        self.price_history[symbol_name].append((timestamp, price))
        
        # Update pair states
        self._update_pair_prices(symbol_name, price, timestamp)
        
        # Check for trading signals
        self._check_trading_signals()
    
    def _update_pair_prices(self, symbol_name: str, price: float, timestamp: datetime):
        """Update pair state prices and indicators"""
        with self._update_lock:
            for pair_str, state in self.pair_states.items():
                if state['symbol1'] == symbol_name:
                    state['price1'].append((timestamp, price))
                elif state['symbol2'] == symbol_name:
                    state['price2'].append((timestamp, price))
    
    def _check_trading_signals(self):
        """Check for trading signals across all pairs"""
        try:
            for pair_str, state in self.pair_states.items():
                # Skip if in cooldown
                if state['cooldown'] > 0:
                    state['cooldown'] -= 1
                    continue
                
                # Check if we have enough price data
                if len(state['price1']) < self.config.z_period or len(state['price2']) < self.config.z_period:
                    continue
                
                # Extract recent prices
                prices1 = [p[1] for p in list(state['price1'])[-self.config.z_period:]]
                prices2 = [p[1] for p in list(state['price2'])[-self.config.z_period:]]
                
                if len(prices1) != len(prices2) or len(prices1) < 10:
                    continue
                
                # Convert to pandas series for strategy calculations
                price_series1 = pd.Series(prices1)
                price_series2 = pd.Series(prices2)
                
                # Calculate basic ratio and z-score for simple signal generation
                ratio = price_series1 / price_series2
                ratio_mean = ratio.rolling(window=min(20, len(ratio))).mean().iloc[-1]
                ratio_std = ratio.rolling(window=min(20, len(ratio))).std().iloc[-1]
                
                if ratio_std == 0:
                    continue
                
                current_ratio = ratio.iloc[-1]
                z_score = (current_ratio - ratio_mean) / ratio_std
                
                current_position = state['position']
                has_active_position = pair_str in self.active_positions
                
                # Simple signal logic
                z_entry_threshold = self.config.z_entry
                z_exit_threshold = self.config.z_exit
                
                # Check portfolio limits before entry
                if current_position is None and not has_active_position:
                    if len(self.active_positions) >= self.config.max_open_positions:
                        continue
                
                # Process entry signals
                if current_position is None and not has_active_position:
                    if z_score > z_entry_threshold:
                        logger.info(f"[SHORT ENTRY] Signal trigger for {pair_str} - OK - Positions: {len(self.active_positions)}/{self.config.max_open_positions}")
                        self._execute_pair_trade(pair_str, 'SHORT')
                    elif z_score < -z_entry_threshold:
                        logger.info(f"[LONG ENTRY] Signal trigger for {pair_str} - OK - Positions: {len(self.active_positions)}/{self.config.max_open_positions}")
                        self._execute_pair_trade(pair_str, 'LONG')
                
                # Process exit signals
                elif current_position is not None or has_active_position:
                    if (current_position == 'LONG' and z_score > -z_exit_threshold) or \
                       (current_position == 'SHORT' and z_score < z_exit_threshold):
                        logger.info(f"[{current_position} EXIT] Signal trigger for {pair_str}, z-score: {z_score:.2f}")
                        self._close_pair_position(pair_str)
                        
        except Exception as e:
            logger.error(f"Error in trading signal check: {e}")
    
    def _execute_pair_trade(self, pair_str: str, direction: str) -> bool:
        """Execute a pairs trade"""
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
        
        # Check position size limits
        total_monetary = monetary1 + monetary2
        if total_monetary > self.config.max_position_size:
            logger.error(f"Position size for {pair_str} exceeds max_position_size")
            return False
        
        # Determine trade directions
        if direction == 'LONG':
            side1, side2 = ProtoOATradeSide.BUY, ProtoOATradeSide.SELL
        else:
            side1, side2 = ProtoOATradeSide.SELL, ProtoOATradeSide.BUY
        
        # Execute trades
        order1 = self._send_market_order(s1, side1, volume1)
        order2 = self._send_market_order(s2, side2, volume2)
        
        if order1 and order2:
            # Store position
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
            
            logger.info(f"Successfully executed {direction} trade for {pair_str}")
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
            close_side1, close_side2 = ProtoOATradeSide.SELL, ProtoOATradeSide.BUY
        else:
            close_side1, close_side2 = ProtoOATradeSide.BUY, ProtoOATradeSide.SELL
        
        # Close positions with the same volumes as opening
        self._send_market_order(s1, close_side1, volume1, is_close=True)
        self._send_market_order(s2, close_side2, volume2, is_close=True)
        
        # Clean up position state
        del self.active_positions[pair_str]
        state['position'] = None
        state['cooldown'] = self.config.cooldown_bars
        
        logger.info(f"Closed {direction} position for {pair_str}")
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
        request.orderType = ProtoOAOrderType.MARKET
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
        
        # Convert volume to cTrader format (volume in units)
        # For cTrader, volume is typically in the base unit (e.g., 100,000 = 1 lot for forex)
        symbol_details = self.symbol_details.get(symbol, {})
        min_volume = symbol_details.get('min_volume', 1000)
        
        # Ensure volume meets minimum requirements
        broker_volume = max(int(volume * 100000), min_volume)
        request.volume = broker_volume
        
        # Convert side enum to readable string
        side_name = "BUY" if side == ProtoOATradeSide.BUY else "SELL"
        action_type = "Closing" if is_close else "Opening"
        logger.info(f"{action_type} {side_name} order for {symbol}: {volume:.5f} lots ({broker_volume} units)")
        
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
        """Calculate balanced volumes for equal monetary exposure"""
        details1 = self.symbol_details.get(symbol1)
        details2 = self.symbol_details.get(symbol2)
        
        if not details1 or not details2:
            return None
        
        # Calculate target monetary value per leg
        target_value = self.config.max_position_size / 2
        
        # Calculate raw volumes
        volume1_raw = target_value / price1
        volume2_raw = target_value / price2
        
        # Apply volume constraints
        volume1 = max(volume1_raw, details1['min_volume'] / 100000)
        volume2 = max(volume2_raw, details2['min_volume'] / 100000)
        
        # Calculate actual monetary values
        monetary1 = volume1 * price1
        monetary2 = volume2 * price2
        
        return volume1, volume2, monetary1, monetary2
    
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
            
            if status['positions']:
                logger.info("ACTIVE PAIRS P&L")
                logger.info("-" * 80)
                logger.info("PAIR            P&L($)      P&L(%)   VALUE($)")
                logger.info("-" * 80)
                
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
        """Get current portfolio status"""
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
        
        return {
            'portfolio_value': current_portfolio_value,
            'unrealized_pnl': unrealized_pnl,
            'realized_pnl': realized_pnl,
            'position_count': total_positions,
            'total_exposure': total_exposure,
            'positions': positions,
            'account_currency': self.account_currency,
            'broker': 'ctrader',
            'max_positions': self.config.max_open_positions,
            'max_exposure': self.config.max_monetary_exposure
        }
