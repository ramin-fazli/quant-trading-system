"""
Strategy-Agnostic CTrader Real-Time Trading Engine
=================================================

High-performance real-time trading engine for cTrader Open API that works
with any strategy implementing the BaseStrategy interface. This design
promotes separation of concerns and allows easy strategy swapping.

Key Features:
- Strategy-agnostic design using BaseStrategy interface
- Optimized for high-frequency trading and low latency
- Advanced risk management and portfolio controls
- Real-time market data handling with buffering
- Robust error handling and reconnection logic
- Support for both single-symbol and multi-symbol strategies

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
from strategies.base_strategy import BaseStrategy, PairsStrategyInterface, SingleSymbolStrategyInterface

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


class StrategyAgnosticCTraderTrader:
    """
    Strategy-agnostic real-time trading engine for cTrader Open API.
    
    This class implements the broker-specific functionality while delegating
    all strategy decisions to the provided strategy instance. This design
    ensures clean separation of concerns and easy strategy swapping.
    """
    
    def __init__(self, config: TradingConfig, data_manager, strategy: BaseStrategy):
        if not CTRADER_API_AVAILABLE:
            raise ImportError("cTrader Open API not available. Install with: pip install ctrader-open-api")
        
        if not isinstance(strategy, BaseStrategy):
            raise TypeError("Strategy must implement BaseStrategy interface")
        
        self.config = config
        self.data_manager = data_manager
        self.strategy = strategy
        
        # Strategy type detection for optimized handling
        self.is_pairs_strategy = isinstance(strategy, PairsStrategyInterface)
        self.is_single_symbol_strategy = isinstance(strategy, SingleSymbolStrategyInterface)
        
        # cTrader API setup
        self.client = None
        self.account_id = int(os.getenv('CTRADER_ACCOUNT_ID', '0'))
        self.client_id = os.getenv('CTRADER_CLIENT_ID')
        self.client_secret = os.getenv('CTRADER_CLIENT_SECRET')
        self.access_token = os.getenv('CTRADER_ACCESS_TOKEN')
        
        # Trading state management
        self.active_positions = {}
        self.instrument_states = {}  # Generic term instead of pair_states
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
        self.trading_started = False
        self.authentication_in_progress = False
        self._degraded_mode = False
        
        # Risk management (broker-level)
        self.portfolio_peak_value = config.initial_portfolio_value
        self.instrument_peak_values = {}
        self.suspended_instruments = set()
        self.portfolio_trading_suspended = False
        
        # Account info
        self.account_currency = "USD"  # Default, updated from account info
        
        # Get required symbols and instruments from strategy
        self.required_symbols = strategy.get_required_symbols()
        self.tradeable_instruments = strategy.get_tradeable_instruments()
        
        logger.info(f"Strategy-Agnostic CTrader Trader initialized with {strategy.__class__.__name__}")
        logger.info(f"Strategy type: {strategy.strategy_type if hasattr(strategy, 'strategy_type') else 'unknown'}")
        logger.info(f"Required symbols: {len(self.required_symbols)}")
        logger.info(f"Tradeable instruments: {len(self.tradeable_instruments)}")
    
    def initialize(self) -> bool:
        """Initialize cTrader real-time trading system"""
        logger.info("Initializing Strategy-Agnostic CTrader trading system...")
        
        # Log environment configuration
        logger.info("🔍 CTRADER CONFIGURATION:")
        logger.info(f"   Account ID: {self.account_id}")
        logger.info(f"   Client ID: {self.client_id[:8] + '...' if self.client_id else 'NOT SET'}")
        logger.info(f"   Client Secret: {'SET' if self.client_secret else 'NOT SET'}")
        logger.info(f"   Access Token: {'SET' if self.access_token else 'NOT SET'}")
        logger.info(f"   Host Type: {os.getenv('CTRADER_HOST_TYPE', 'Live')}")
        
        # Validate credentials
        if not all([self.client_id, self.client_secret, self.access_token]):
            logger.error("Missing cTrader credentials")
            return False
        
        if self.account_id == 0:
            logger.error("Missing or invalid CTRADER_ACCOUNT_ID")
            return False
        
        # Initialize client
        if not self._setup_client():
            logger.error("Failed to setup cTrader client")
            return False
        
        self.is_trading = True
        logger.info("Strategy-Agnostic CTrader trading initialized")
        return True
    
    def _setup_client(self) -> bool:
        """Setup cTrader API client with proper callbacks"""
        try:
            # Determine host based on environment
            host_type = os.getenv('CTRADER_HOST_TYPE', 'Live').lower()
            if host_type == 'demo':
                host = EndPoints.PROTOBUF_DEMO_HOST
                logger.info(f"🔧 Using DEMO server: {host}:{EndPoints.PROTOBUF_PORT}")
            else:
                host = EndPoints.PROTOBUF_LIVE_HOST
                logger.info(f"🔧 Using LIVE server: {host}:{EndPoints.PROTOBUF_PORT}")
            
            port = EndPoints.PROTOBUF_PORT
            
            self.client = Client(host, port, TcpProtocol)
            self.client.setConnectedCallback(self._on_connected)
            self.client.setDisconnectedCallback(self._on_disconnected)
            self.client.setMessageReceivedCallback(self._on_message_received)
            
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
        logger.info("Authenticating application...")
        request = ProtoOAApplicationAuthReq()
        request.clientId = self.client_id
        request.clientSecret = self.client_secret
        
        deferred = self.client.send(request)
        deferred.addErrback(self._on_error)
    
    def _authenticate_account(self):
        """Authenticate the trading account"""
        logger.info("Authenticating account...")
        request = ProtoOAAccountAuthReq()
        request.ctidTraderAccountId = self.account_id
        request.accessToken = self.access_token
        
        deferred = self.client.send(request)
        deferred.addErrback(self._on_error)
    
    def _get_symbols_list(self):
        """Get available symbols from cTrader"""
        if self.symbols_initialized:
            return
            
        logger.info("Requesting symbols list...")
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
    
    def _on_message_received(self, client, message):
        """Handle incoming messages from cTrader API"""
        try:
            if message.payloadType == ProtoOAApplicationAuthRes().payloadType:
                logger.info("Application authenticated")
                self._authenticate_account()
                
            elif message.payloadType == ProtoOAAccountAuthRes().payloadType:
                logger.info("Account authenticated")
                response = Protobuf.extract(message)
                logger.info(f"Account Type: {'LIVE' if getattr(response, 'liveAccount', False) else 'DEMO'}")
                
                if not self.symbols_initialized:
                    self._get_symbols_list()
                    
            elif message.payloadType == 2142:  # Alternative account auth response
                if not hasattr(self, '_account_authenticated'):
                    self._account_authenticated = True
                    logger.info("Account authenticated (type 2142)")
                    if not self.symbols_initialized:
                        self._get_symbols_list()
                        
            elif message.payloadType in [ProtoOASymbolsListRes().payloadType, 2143]:
                logger.info("Received symbols list response")
                self._process_symbols_list(message)
                
            elif message.payloadType == ProtoOAGetTrendbarsRes().payloadType:
                self._process_trendbar_data(message)
                
            elif message.payloadType == ProtoOASpotEvent().payloadType:
                event = Protobuf.extract(message)
                self._process_spot_event(event)
                
            elif message.payloadType == ProtoOAExecutionEvent().payloadType:
                event = Protobuf.extract(message)
                self._process_execution_event(event)
                
            elif message.payloadType == ProtoOASubscribeSpotsRes().payloadType:
                logger.debug("Spot subscription confirmed")
                
            else:
                logger.debug(f"Unhandled message type: {message.payloadType}")
                
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            logger.error(traceback.format_exc())
    
    def _process_symbols_list(self, message):
        """Process symbols list and initialize instrument states"""
        logger.info("Processing symbols list...")
        
        try:
            response = Protobuf.extract(message)
            
            if not hasattr(response, 'symbol') or len(response.symbol) == 0:
                logger.error("No symbols received from cTrader API")
                return
            
            logger.info(f"Received {len(response.symbol)} symbols")
            
            # Process symbols
            for symbol in response.symbol:
                symbol_name = symbol.symbolName
                symbol_id = symbol.symbolId
                
                self.symbols_map[symbol_name] = symbol_id
                self.symbol_id_to_name_map[symbol_id] = symbol_name
                
                # Store symbol details
                self.symbol_details[symbol_name] = {
                    'id': symbol_id,
                    'name': symbol_name,
                    'digits': getattr(symbol, 'digits', 5),
                    'pip_position': getattr(symbol, 'pipPosition', -5),
                    'enabled': getattr(symbol, 'enabled', True),
                    'base_currency': getattr(symbol, 'baseCurrency', ''),
                    'quote_currency': getattr(symbol, 'quoteCurrency', ''),
                    'category': getattr(symbol, 'categoryName', ''),
                    'description': getattr(symbol, 'description', ''),
                    'min_volume': getattr(symbol, 'minVolume', 1000),
                    'max_volume': getattr(symbol, 'maxVolume', 100000000),
                    'volume_step': getattr(symbol, 'stepVolume', 1000)
                }
            
            logger.info(f"Successfully processed {len(self.symbols_map)} symbols")
            self.symbols_initialized = True
            
            # Initialize instrument states based on strategy
            self._initialize_instrument_states()
            
            # Subscribe to real-time data
            self._subscribe_to_data()
            
            # Start trading loop
            if not self.trading_started:
                self._start_trading_loop()
            
        except Exception as e:
            logger.error(f"Error processing symbols list: {e}")
            logger.error(traceback.format_exc())
    
    def _initialize_instrument_states(self):
        """Initialize instrument states based on strategy type"""
        logger.info("Initializing instrument states...")
        
        # Filter valid instruments based on available symbols
        valid_instruments = []
        
        if self.is_pairs_strategy:
            # For pairs strategies, validate that both symbols exist
            for instrument in self.tradeable_instruments:
                if isinstance(instrument, (tuple, list)) and len(instrument) == 2:
                    symbol1, symbol2 = instrument
                    if symbol1 in self.symbols_map and symbol2 in self.symbols_map:
                        valid_instruments.append(instrument)
                        instrument_key = f"{symbol1}-{symbol2}"
                        self._initialize_pairs_state(instrument_key, symbol1, symbol2)
                    else:
                        logger.warning(f"Pair {symbol1}-{symbol2} not available: missing symbols")
        
        elif self.is_single_symbol_strategy:
            # For single symbol strategies, validate symbols exist
            for instrument in self.tradeable_instruments:
                if isinstance(instrument, str) and instrument in self.symbols_map:
                    valid_instruments.append(instrument)
                    self._initialize_symbol_state(instrument)
                else:
                    logger.warning(f"Symbol {instrument} not available")
        
        else:
            # Generic handling for custom strategies
            for instrument in self.tradeable_instruments:
                if isinstance(instrument, str):
                    if instrument in self.symbols_map:
                        valid_instruments.append(instrument)
                        self._initialize_symbol_state(instrument)
                elif isinstance(instrument, (tuple, list)):
                    # Check if all symbols in the instrument are available
                    if all(sym in self.symbols_map for sym in instrument):
                        valid_instruments.append(instrument)
                        instrument_key = "-".join(instrument)
                        self._initialize_generic_state(instrument_key, instrument)
        
        logger.info(f"Initialized {len(self.instrument_states)} instrument states from {len(valid_instruments)} valid instruments")
        
        if len(valid_instruments) == 0:
            logger.warning("No valid instruments found! Check strategy configuration and symbol availability.")
    
    def _initialize_pairs_state(self, pair_key: str, symbol1: str, symbol2: str):
        """Initialize state for a trading pair"""
        min_data_points = self.strategy.get_minimum_data_points()
        
        self.instrument_states[pair_key] = {
            'type': 'pair',
            'symbols': [symbol1, symbol2],
            'symbol1': symbol1,
            'symbol2': symbol2,
            'price1': deque(maxlen=min_data_points * 2),
            'price2': deque(maxlen=min_data_points * 2),
            'position': None,
            'last_signal_time': None,
            'cooldown': 0,
            'last_update': None
        }
    
    def _initialize_symbol_state(self, symbol: str):
        """Initialize state for a single symbol"""
        min_data_points = self.strategy.get_minimum_data_points()
        
        self.instrument_states[symbol] = {
            'type': 'symbol',
            'symbols': [symbol],
            'symbol': symbol,
            'price': deque(maxlen=min_data_points * 2),
            'position': None,
            'last_signal_time': None,
            'cooldown': 0,
            'last_update': None
        }
    
    def _initialize_generic_state(self, instrument_key: str, symbols: List[str]):
        """Initialize state for generic multi-symbol instrument"""
        min_data_points = self.strategy.get_minimum_data_points()
        
        state = {
            'type': 'generic',
            'symbols': symbols,
            'position': None,
            'last_signal_time': None,
            'cooldown': 0,
            'last_update': None
        }
        
        # Create price deques for each symbol
        for i, symbol in enumerate(symbols):
            state[f'price_{i}'] = deque(maxlen=min_data_points * 2)
            state[f'symbol_{i}'] = symbol
        
        self.instrument_states[instrument_key] = state
    
    def _subscribe_to_data(self):
        """Subscribe to real-time data for required symbols"""
        logger.info("Subscribing to real-time data...")
        
        for symbol in self.required_symbols:
            if symbol in self.symbols_map:
                self._subscribe_to_spot_prices(symbol)
            else:
                logger.warning(f"Cannot subscribe to {symbol}: not in symbols map")
    
    def _subscribe_to_spot_prices(self, symbol: str):
        """Subscribe to spot prices for a symbol"""
        if symbol in self.subscribed_symbols:
            return
        
        try:
            symbol_id = self.symbols_map[symbol]
            request = ProtoOASubscribeSpotsReq()
            request.ctidTraderAccountId = self.account_id
            request.symbolId.append(symbol_id)
            
            deferred = self.client.send(request)
            deferred.addErrback(lambda failure: self._on_subscription_error(failure, symbol))
            
            self.subscribed_symbols.add(symbol)
            logger.debug(f"Subscribed to spot prices for {symbol}")
            
        except Exception as e:
            logger.error(f"Error subscribing to {symbol}: {e}")
    
    def _on_subscription_error(self, failure, symbol=None):
        """Handle subscription errors"""
        logger.error(f"Subscription error for {symbol}: {failure}")
    
    def _process_spot_event(self, event):
        """Process real-time spot price events"""
        try:
            symbol_id = event.symbolId
            if symbol_id in self.symbol_id_to_name_map:
                symbol_name = self.symbol_id_to_name_map[symbol_id]
                
                # Extract bid and ask prices
                bid = getattr(event, 'bid', 0) / (10 ** self.symbol_details[symbol_name]['digits'])
                ask = getattr(event, 'ask', 0) / (10 ** self.symbol_details[symbol_name]['digits'])
                
                # Use mid-price for strategy calculations
                price = (bid + ask) / 2
                timestamp = datetime.now()
                
                # Update spot prices
                self.spot_prices[symbol_name] = price
                
                # Update price history
                self.price_history[symbol_name].append((timestamp, price))
                
                # Update instrument states
                self._update_instrument_states(symbol_name, timestamp, price)
        
        except Exception as e:
            logger.error(f"Error processing spot event: {e}")
    
    def _update_instrument_states(self, symbol_name: str, timestamp: datetime, price: float):
        """Update instrument states with new price data"""
        for instrument_key, state in self.instrument_states.items():
            if symbol_name in state['symbols']:
                if state['type'] == 'pair':
                    if state['symbol1'] == symbol_name:
                        state['price1'].append((timestamp, price))
                    elif state['symbol2'] == symbol_name:
                        state['price2'].append((timestamp, price))
                elif state['type'] == 'symbol':
                    if state['symbol'] == symbol_name:
                        state['price'].append((timestamp, price))
                elif state['type'] == 'generic':
                    # Find the index of this symbol in the generic state
                    try:
                        symbol_idx = state['symbols'].index(symbol_name)
                        price_key = f'price_{symbol_idx}'
                        if price_key in state:
                            state[price_key].append((timestamp, price))
                    except ValueError:
                        continue
                
                state['last_update'] = timestamp
    
    def _start_trading_loop(self):
        """Start the main trading loop"""
        if self.trading_started:
            return
        
        self.trading_started = True
        logger.info("Starting trading loop...")
        
        self.trading_thread = threading.Thread(target=self._trading_loop, daemon=True)
        self.trading_thread.start()
    
    def _trading_loop(self):
        """Main trading loop that processes signals and manages positions"""
        logger.info("Trading loop started")
        
        while self.is_trading:
            try:
                if self.symbols_initialized:
                    # Check for trading signals
                    self._process_trading_signals()
                    
                    # Monitor existing positions
                    self._monitor_positions()
                
                # Brief sleep to prevent excessive CPU usage
                time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Error in trading loop: {e}")
                logger.error(traceback.format_exc())
                time.sleep(1.0)  # Longer sleep on error
    
    def _process_trading_signals(self):
        """Process trading signals for all instruments using the strategy"""
        for instrument_key, state in self.instrument_states.items():
            try:
                # Skip if in cooldown
                if state['cooldown'] > 0:
                    state['cooldown'] -= 1
                    continue
                
                # Check if we have sufficient data
                if not self._has_sufficient_data(state):
                    continue
                
                # Prepare market data for strategy
                market_data = self._prepare_market_data(state)
                
                # Validate market data
                if not self.strategy.validate_market_data(market_data):
                    continue
                
                # Calculate indicators using strategy
                indicators = self.strategy.calculate_indicators(market_data)
                if not indicators:
                    continue
                
                # Generate signals using strategy
                signal_kwargs = self._prepare_signal_kwargs(state)
                signals = self.strategy.generate_signals(indicators, **signal_kwargs)
                if signals.empty:
                    continue
                
                # Get latest signal
                latest_signal = signals.iloc[-1]
                
                # Process the signal
                self._process_signal(instrument_key, state, latest_signal, indicators)
                
            except Exception as e:
                logger.error(f"Error processing signals for {instrument_key}: {e}")
    
    def _has_sufficient_data(self, state: Dict) -> bool:
        """Check if instrument state has sufficient data for strategy calculation"""
        min_points = self.strategy.get_minimum_data_points()
        
        if state['type'] == 'pair':
            return len(state['price1']) >= min_points and len(state['price2']) >= min_points
        elif state['type'] == 'symbol':
            return len(state['price']) >= min_points
        elif state['type'] == 'generic':
            # Check all price series in generic state
            for key in state:
                if key.startswith('price_') and len(state[key]) < min_points:
                    return False
            return True
        
        return False
    
    def _prepare_market_data(self, state: Dict) -> Dict[str, Any]:
        """Prepare market data dictionary for strategy calculation"""
        if state['type'] == 'pair':
            # Extract prices for pairs strategy
            prices1 = [p[1] for p in list(state['price1'])]
            prices2 = [p[1] for p in list(state['price2'])]
            
            return {
                'price1': pd.Series(prices1),
                'price2': pd.Series(prices2)
            }
        
        elif state['type'] == 'symbol':
            # Extract prices for single symbol strategy
            prices = [p[1] for p in list(state['price'])]
            
            return {
                'price': pd.Series(prices)
            }
        
        elif state['type'] == 'generic':
            # Extract prices for generic multi-symbol strategy
            market_data = {}
            for key in state:
                if key.startswith('price_'):
                    idx = key.split('_')[1]
                    prices = [p[1] for p in list(state[key])]
                    market_data[f'price_{idx}'] = pd.Series(prices)
                    # Also provide symbol name reference
                    symbol_key = f'symbol_{idx}'
                    if symbol_key in state:
                        market_data[f'symbol_{idx}'] = state[symbol_key]
            
            return market_data
        
        return {}
    
    def _prepare_signal_kwargs(self, state: Dict) -> Dict[str, Any]:
        """Prepare additional kwargs for signal generation"""
        kwargs = {}
        
        if state['type'] == 'pair':
            kwargs['symbol1'] = state['symbol1']
            kwargs['symbol2'] = state['symbol2']
        elif state['type'] == 'symbol':
            kwargs['symbol'] = state['symbol']
        elif state['type'] == 'generic':
            # Add all symbols as kwargs
            for i, symbol in enumerate(state['symbols']):
                kwargs[f'symbol_{i}'] = symbol
        
        return kwargs
    
    def _process_signal(self, instrument_key: str, state: Dict, signal: pd.Series, indicators: Dict):
        """Process a trading signal and execute trades if necessary"""
        current_position = state['position']
        has_active_position = instrument_key in self.active_positions
        
        # Check portfolio limits before entry
        if current_position is None and not has_active_position:
            if len(self.active_positions) >= self.config.max_open_positions:
                return
            
            # Check if signal is suitable for trading
            if not getattr(signal, 'suitable', True):
                return
        
        # Process entry signals
        if current_position is None and not has_active_position:
            if getattr(signal, 'long_entry', False):
                logger.info(f"[LONG ENTRY] Signal for {instrument_key}")
                self._execute_trade(instrument_key, 'LONG', state, indicators)
            elif getattr(signal, 'short_entry', False):
                logger.info(f"[SHORT ENTRY] Signal for {instrument_key}")
                self._execute_trade(instrument_key, 'SHORT', state, indicators)
        
        # Process exit signals
        elif current_position is not None or has_active_position:
            should_exit = False
            
            if current_position == 'LONG' and getattr(signal, 'long_exit', False):
                should_exit = True
            elif current_position == 'SHORT' and getattr(signal, 'short_exit', False):
                should_exit = True
            
            if should_exit:
                logger.info(f"[{current_position} EXIT] Signal for {instrument_key}")
                self._close_position(instrument_key)
    
    def _execute_trade(self, instrument_key: str, direction: str, state: Dict, indicators: Dict) -> bool:
        """Execute a trade based on the signal and instrument type"""
        # Implement risk management checks
        if not self._check_risk_limits(instrument_key):
            logger.warning(f"Trade blocked by risk limits for {instrument_key}")
            return False
        
        # Delegate to specific execution method based on instrument type
        if state['type'] == 'pair':
            return self._execute_pair_trade(instrument_key, direction, state)
        elif state['type'] == 'symbol':
            return self._execute_symbol_trade(instrument_key, direction, state)
        elif state['type'] == 'generic':
            return self._execute_generic_trade(instrument_key, direction, state)
        
        return False
    
    def _execute_pair_trade(self, pair_key: str, direction: str, state: Dict) -> bool:
        """Execute a pairs trade"""
        s1, s2 = state['symbol1'], state['symbol2']
        
        # Get current prices
        if s1 not in self.spot_prices or s2 not in self.spot_prices:
            logger.warning(f"Missing spot prices for {pair_key}")
            return False
        
        price1 = self.spot_prices[s1]
        price2 = self.spot_prices[s2]
        
        # Calculate balanced volumes
        volumes = self._calculate_balanced_volumes(s1, s2, price1, price2)
        if volumes is None:
            logger.error(f"Failed to calculate volumes for {pair_key}")
            return False
        
        volume1, volume2, value1, value2 = volumes
        
        # Determine order types based on direction
        if direction == 'LONG':
            order1_type, order2_type = 'buy', 'sell'
        else:  # SHORT
            order1_type, order2_type = 'sell', 'buy'
        
        # Execute both legs
        order1 = self._send_order(s1, order1_type, volume1)
        order2 = self._send_order(s2, order2_type, volume2)
        
        if order1 and order2:
            # Record the position
            self.active_positions[pair_key] = {
                'direction': direction,
                'symbol1': s1, 'symbol2': s2,
                'volume1': volume1, 'volume2': volume2,
                'order1_id': order1, 'order2_id': order2,
                'entry_time': datetime.now(),
                'entry_price1': price1, 'entry_price2': price2
            }
            state['position'] = direction
            state['cooldown'] = self.config.cooldown_periods
            
            logger.info(f"Pair trade executed: {pair_key} {direction}")
            return True
        
        return False
    
    def _execute_symbol_trade(self, symbol: str, direction: str, state: Dict) -> bool:
        """Execute a single symbol trade"""
        if symbol not in self.spot_prices:
            logger.warning(f"Missing spot price for {symbol}")
            return False
        
        price = self.spot_prices[symbol]
        
        # Calculate volume based on risk management
        volume = self._calculate_symbol_volume(symbol, price)
        if volume is None:
            return False
        
        # Execute order
        order_type = 'buy' if direction == 'LONG' else 'sell'
        order_id = self._send_order(symbol, order_type, volume)
        
        if order_id:
            # Record the position
            self.active_positions[symbol] = {
                'direction': direction,
                'symbol': symbol,
                'volume': volume,
                'order_id': order_id,
                'entry_time': datetime.now(),
                'entry_price': price
            }
            state['position'] = direction
            state['cooldown'] = self.config.cooldown_periods
            
            logger.info(f"Symbol trade executed: {symbol} {direction}")
            return True
        
        return False
    
    def _execute_generic_trade(self, instrument_key: str, direction: str, state: Dict) -> bool:
        """Execute a generic multi-symbol trade"""
        # This is a placeholder for custom multi-symbol strategies
        # Implementation would depend on the specific strategy requirements
        logger.warning(f"Generic trade execution not implemented for {instrument_key}")
        return False
    
    def _calculate_balanced_volumes(self, symbol1: str, symbol2: str, price1: float, price2: float) -> Optional[Tuple[float, float, float, float]]:
        """Calculate balanced volumes for pairs trading"""
        if symbol1 not in self.symbol_details or symbol2 not in self.symbol_details:
            return None
        
        info1 = self.symbol_details[symbol1]
        info2 = self.symbol_details[symbol2]
        
        # Target monetary value per leg
        target_value = self.config.max_position_size / 2
        
        # Calculate raw volumes
        volume1_raw = target_value / price1
        volume2_raw = target_value / price2
        
        # Normalize volumes according to symbol requirements
        volume1 = self._normalize_volume(symbol1, volume1_raw, info1)
        volume2 = self._normalize_volume(symbol2, volume2_raw, info2)
        
        if volume1 is None or volume2 is None:
            return None
        
        # Calculate actual monetary values
        value1 = volume1 * price1
        value2 = volume2 * price2
        
        return volume1, volume2, value1, value2
    
    def _normalize_volume(self, symbol: str, volume_raw: float, symbol_info: Dict) -> Optional[float]:
        """Normalize volume according to symbol specifications"""
        min_volume = symbol_info.get('min_volume', 1000)
        max_volume = symbol_info.get('max_volume', 100000000)
        volume_step = symbol_info.get('volume_step', 1000)
        
        # Round to nearest step
        volume_normalized = round(volume_raw / volume_step) * volume_step
        
        # Apply limits
        volume_normalized = max(min_volume, min(volume_normalized, max_volume))
        
        return volume_normalized if volume_normalized >= min_volume else None
    
    def _calculate_symbol_volume(self, symbol: str, price: float) -> Optional[float]:
        """Calculate volume for single symbol trade"""
        if symbol not in self.symbol_details:
            return None
        
        info = self.symbol_details[symbol]
        target_value = self.config.max_position_size
        
        volume_raw = target_value / price
        return self._normalize_volume(symbol, volume_raw, info)
    
    def _send_order(self, symbol: str, order_type: str, volume: float) -> Optional[int]:
        """Send an order to cTrader"""
        # This is a placeholder - actual implementation would use cTrader API
        # to send orders and return order ID
        logger.info(f"Sending order: {symbol} {order_type} {volume}")
        
        # Simulate order ID for now
        order_id = self.next_order_id
        self.next_order_id += 1
        
        return order_id
    
    def _close_position(self, instrument_key: str) -> bool:
        """Close a position"""
        if instrument_key not in self.active_positions:
            return False
        
        position = self.active_positions[instrument_key]
        
        # Implementation depends on position type
        if 'symbol1' in position:  # Pairs position
            return self._close_pair_position(instrument_key)
        else:  # Single symbol position
            return self._close_symbol_position(instrument_key)
    
    def _close_pair_position(self, pair_key: str) -> bool:
        """Close a pairs position"""
        # Placeholder implementation
        if pair_key in self.active_positions:
            del self.active_positions[pair_key]
            self.instrument_states[pair_key]['position'] = None
            logger.info(f"Closed pair position: {pair_key}")
            return True
        return False
    
    def _close_symbol_position(self, symbol: str) -> bool:
        """Close a single symbol position"""
        # Placeholder implementation
        if symbol in self.active_positions:
            del self.active_positions[symbol]
            self.instrument_states[symbol]['position'] = None
            logger.info(f"Closed symbol position: {symbol}")
            return True
        return False
    
    def _check_risk_limits(self, instrument_key: str) -> bool:
        """Check various risk management limits"""
        # Portfolio-level checks
        if self.portfolio_trading_suspended:
            return False
        
        if len(self.active_positions) >= self.config.max_open_positions:
            return False
        
        # Instrument-level checks
        if instrument_key in self.suspended_instruments:
            return False
        
        # Additional risk checks can be added here
        return True
    
    def _monitor_positions(self):
        """Monitor existing positions"""
        # Placeholder for position monitoring logic
        pass
    
    def _process_execution_event(self, event):
        """Process trade execution events"""
        # Placeholder for execution event processing
        pass
    
    def _process_trendbar_data(self, message):
        """Process historical trendbar data"""
        # Placeholder for trendbar data processing
        pass
    
    def start_trading(self):
        """Start the trading system"""
        logger.info("Starting trading system...")
        # Trading is started automatically when symbols are initialized
    
    def stop_trading(self):
        """Stop the trading system"""
        logger.info("Stopping trading system...")
        self.is_trading = False
        
        if self.trading_thread and self.trading_thread.is_alive():
            self.trading_thread.join(timeout=5.0)
        
        if self.client:
            try:
                self.client.disconnect()
            except:
                pass
    
    def get_portfolio_status(self) -> Dict[str, Any]:
        """Get current portfolio status"""
        return {
            'broker': 'ctrader',
            'strategy': self.strategy.__class__.__name__,
            'active_positions': len(self.active_positions),
            'max_positions': self.config.max_open_positions,
            'portfolio_value': self.config.initial_portfolio_value,  # Placeholder
            'trading_suspended': self.portfolio_trading_suspended,
            'instruments_count': len(self.instrument_states),
            'symbols_subscribed': len(self.subscribed_symbols)
        }


# Backwards compatibility alias
CTraderRealTimeTrader = StrategyAgnosticCTraderTrader
