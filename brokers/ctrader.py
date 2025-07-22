"""
CTrader Real-Time Trading Engine (Strategy-Agnostic)
===================================================

High-performance real-time trading engine for cTrader Open API
that works with any strategy implementing the BaseStrategy interface.

This engine promotes separation of concerns by delegating all strategy
decisions to the provided strategy instance while handling broker-specific
functionality like API communication, order execution, and risk management.

Position Closing Implementation:
- Uses ProtoOAClosePositionReq for proper position closing when position IDs are available
- Falls back to market orders when position IDs are not available
- Tracks position IDs from execution events to enable proper closing
- Supports both pair trading and individual position management

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
from utils.risk_manager import RiskManager, RiskLimits, CurrencyConverter
from utils.portfolio_manager import PortfolioManager, PriceProvider
from utils.signal_processor import SignalProcessor, PairState, TradingSignal
from brokers.base_broker import BaseBroker

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
            ProtoOANewOrderReq, ProtoOAClosePositionReq, ProtoOASubscribeSpotsReq, ProtoOAUnsubscribeSpotsReq,
            ProtoOASpotEvent, ProtoOAExecutionEvent, ProtoOAGetTrendbarsRes,
            ProtoOASubscribeSpotsRes, ProtoOAReconcileReq, ProtoOAReconcileRes
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
        'TRENDBAR_RES': 2138,         # PROTO_OA_GET_TRENDBARS_RES
        'RECONCILE_RES': 2125         # PROTO_OA_RECONCILE_RES (for position reconciliation)
    }
    
    CTRADER_API_AVAILABLE = True
except ImportError as e:
    CTRADER_API_AVAILABLE = False
    logger.warning(f"cTrader Open API not available: {e}")
    MESSAGE_TYPES = {}


class CTraderRealTimeTrader(BaseBroker):
    """
    Strategy-agnostic real-time trading engine for cTrader Open API.
    
    This class handles all broker-specific functionality while delegating
    strategy decisions to the provided strategy instance. Supports any
    strategy that implements the BaseStrategy interface.
    """
    
    def __init__(self, data_manager, config=None, strategy: BaseStrategy = None):
        if not CTRADER_API_AVAILABLE:
            raise ImportError("cTrader Open API not available. Install with: pip install ctrader-open-api")
        
        # Set account currency before calling parent constructor
        self.account_currency = "USD"  # Default, will be updated from account info
        
        # Initialize base broker first
        super().__init__(config, data_manager, strategy)
        
        # CTrader-specific initialization
        self.data_manager = data_manager  # Ensure we have the CTrader data manager
        
        # Initialize CTrader-specific currency converter
        self._setup_ctrader_currency_converter()
        
        # cTrader API setup
        self.client = None
        self.account_id = int(os.getenv('CTRADER_ACCOUNT_ID', '0'))
        self.client_id = os.getenv('CTRADER_CLIENT_ID')
        self.client_secret = os.getenv('CTRADER_CLIENT_SECRET')
        self.access_token = os.getenv('CTRADER_ACCESS_TOKEN')
        
        # Real-time data state
        self.spot_prices = {}
        self.price_history = defaultdict(lambda: deque(maxlen=500))
        self.subscribed_symbols = set()
        self.execution_requests = {}
        self.pending_pair_trades = {}  # Track pending pair trades awaiting execution confirmation
        self.pending_close_positions = {}  # Track pending close position requests
        self.next_order_id = 1
        self.symbols_map = {}
        self.symbol_id_to_name_map = {}
        self.symbol_details = {}
        
        # Data throttling to reduce duplicate processing
        self._last_strategy_check = {}  # Track last strategy check time per pair
        self._min_check_interval = 0.5  # Minimum 500ms between strategy checks per pair
        self._price_update_buffer = defaultdict(lambda: deque(maxlen=10))  # Buffer recent updates
        
        # CTrader API Rate Limiting (50 req/sec for non-historical, 5 req/sec for historical)
        self._non_historical_rate_limiter = {
            'max_requests': 50,  # CTrader limit: 50 req/sec for non-historical
            'time_window': 1.0,  # 1 second window
            'requests': deque(maxlen=50),  # Track timestamps of last 50 requests
            'queue': deque(),  # Queue for pending requests
            'processing': False
        }
        self._historical_rate_limiter = {
            'max_requests': 5,   # CTrader limit: 5 req/sec for historical
            'time_window': 1.0,  # 1 second window
            'requests': deque(maxlen=5),   # Track timestamps of last 5 requests
            'queue': deque(),   # Queue for pending historical requests
            'processing': False
        }
        
        # Enhanced subscription management for large-scale deployments
        self._subscription_batch_size = 8    # Conservative batch size to avoid timeouts
        self._subscription_delay = 0.25      # 250ms delay between batches (4 batches/sec max)
        self._subscription_retry_delay = 2.0 # 2 second delay for retries
        self._max_concurrent_subscriptions = 200  # Limit concurrent subscriptions
        self._subscription_health_check_interval = 30  # Check subscription health every 30s
        
        # Trading threads
        self.trading_thread = None
        self.symbols_request_time = None
        self.symbols_initialized = False
        self.trading_started = False  # Track if trading loop has started
        self.authentication_in_progress = False
        self._degraded_mode = False  # Flag for degraded mode operation
        
        # Update risk manager with correct account currency (now that it's initialized)
        self.risk_manager.account_currency = self.account_currency
        
        # Portfolio tracking attributes
        self.portfolio_peak_value = self.config.initial_portfolio_value
        self.portfolio_trading_suspended = False
        
        # Log strategy information
        strategy_info = self.strategy.get_strategy_info()
        logger.info(f"Strategy-Agnostic CTrader Trader initialized")
        logger.info(f"  Strategy: {strategy_info['name']}")
        logger.info(f"  Type: {strategy_info['type']}")
        logger.info(f"  Required symbols: {len(strategy_info['required_symbols'])}")
        logger.info(f"  Tradeable instruments: {len(strategy_info['tradeable_instruments'])}")
        
        # Determine if this is a pairs trading strategy
        from strategies.base_strategy import PairsStrategyInterface
        self.is_pairs_strategy = isinstance(self.strategy, PairsStrategyInterface)
    
    def _setup_ctrader_currency_converter(self):
        """Setup CTrader-specific currency converter"""
        class CTraderCurrencyConverter(CurrencyConverter):
            def __init__(self, account_currency: str, trader_instance):
                super().__init__(account_currency)
                self.trader = trader_instance
            
            def get_fx_rate_from_broker(self, from_currency: str, to_currency: str) -> float:
                """Get FX rate from CTrader spot prices"""
                if from_currency == to_currency:
                    return 1.0
                
                # Try direct symbol
                direct_symbol = f"{from_currency}{to_currency}"
                inverse_symbol = f"{to_currency}{from_currency}"
                
                for symbol in [direct_symbol, inverse_symbol]:
                    spot_price = self.trader._get_spot_price(symbol)
                    spot_data = self.trader._get_spot_price_data(symbol)
                    if spot_data and 'bid' in spot_data and 'ask' in spot_data:
                        bid = spot_data['bid']
                        ask = spot_data['ask']
                        if symbol == direct_symbol:
                            return (bid + ask) / 2
                        else:
                            return 1 / ((bid + ask) / 2)
                
                logger.warning(f"FX rate not found for {from_currency}->{to_currency}, using 1.0")
                return 1.0
            
            def get_symbol_currency(self, symbol: str, symbol_info_cache: Dict) -> str:
                """Get the profit currency for a symbol from CTrader symbol details"""
                if symbol in self.trader.symbol_details:
                    symbol_detail = self.trader.symbol_details[symbol]
                    if hasattr(symbol_detail, 'quoteCurrency'):
                        return symbol_detail.quoteCurrency
                return self.account_currency
        
        self.currency_converter = CTraderCurrencyConverter(self.account_currency, self)
    
    # Implementation of abstract methods from BaseBroker
    
    def get_current_prices(self, symbols: List[str]) -> Dict[str, float]:
        """Get current prices for symbols from spot prices"""
        prices = {}
        for symbol in symbols:
            spot_data = self._get_spot_price_data(symbol)
            if spot_data:
                if 'bid' in spot_data and 'ask' in spot_data:
                    # Use mid price for current price
                    prices[symbol] = (spot_data['bid'] + spot_data['ask']) / 2
                elif 'price' in spot_data:
                    prices[symbol] = spot_data['price']
        return prices
    
    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price for a single symbol"""
        spot_data = self._get_spot_price_data(symbol)
        if spot_data:
            if 'bid' in spot_data and 'ask' in spot_data:
                # Use mid price for current price
                return (spot_data['bid'] + spot_data['ask']) / 2
            elif 'price' in spot_data:
                return spot_data['price']
        return None
    
    def get_bid_ask_prices(self, symbols: List[str]) -> Dict[str, Tuple[float, float]]:
        """Get bid/ask prices for symbols from spot prices"""
        bid_ask_prices = {}
        for symbol in symbols:
            spot_data = self._get_spot_price_data(symbol)
            if spot_data and 'bid' in spot_data and 'ask' in spot_data:
                bid_ask_prices[symbol] = (spot_data['bid'], spot_data['ask'])
        return bid_ask_prices
    
    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Get symbol information from symbol details"""
        if symbol in self.symbol_details:
            symbol_detail = self.symbol_details[symbol]
            return {
                'symbol_id': getattr(symbol_detail, 'symbolId', 0),
                'symbol_name': getattr(symbol_detail, 'symbolName', symbol),
                'digits': getattr(symbol_detail, 'digits', 5),
                'pip_position': getattr(symbol_detail, 'pipPosition', -5),
                'enable_short_selling': getattr(symbol_detail, 'enableShortSelling', True),
                'min_volume': getattr(symbol_detail, 'minVolume', 0.01),
                'max_volume': getattr(symbol_detail, 'maxVolume', 100.0),
                'volume_step': getattr(symbol_detail, 'stepVolume', 0.01),
                'base_currency': getattr(symbol_detail, 'baseCurrency', 'USD'),
                'quote_currency': getattr(symbol_detail, 'quoteCurrency', 'USD'),
                'trade_contract_size': 1.0  # CTrader typically uses 1.0 for contract size
            }
        return {}
    
    def get_account_info(self) -> Dict[str, Any]:
        """Get account information from CTrader (placeholder implementation)"""
        # Note: CTrader account info would need to be retrieved via API calls
        # This is a placeholder implementation
        return {
            'balance': 0.0,
            'equity': 0.0,
            'margin': 0.0,
            'free_margin': 0.0,
            'margin_level': 0.0,
            'profit': 0.0,
            'currency': self.account_currency,
            'leverage': 100,
            'server': 'cTrader',
            'name': 'CTrader Account'
        }
    
    def get_symbol_info_cache(self) -> Dict[str, Dict]:
        """Get symbol info cache for shared modules"""
        cache = {}
        for symbol, details in self.symbol_details.items():
            cache[symbol] = self.get_symbol_info(symbol)
        return cache
    
    def _execute_trade(self, signal: TradingSignal) -> bool:
        """Execute a trading signal with intelligent readiness checking"""
        try:
            # Check if we have sufficient price feeds for this trade
            if not self._is_ready_for_trading(signal.pair_str):
                logger.warning(f"Trading not ready for {signal.pair_str} - insufficient price feeds")
                return False
                
            if signal.signal_type in ['OPEN_LONG', 'OPEN_SHORT']:
                return self._execute_pair_trade(signal.pair_str, signal.signal_type.replace('OPEN_', ''))
            elif signal.signal_type == 'CLOSE':
                return self._close_pair_position(signal.pair_str)
            else:
                logger.warning(f"Unknown signal type: {signal.signal_type}")
                return False
        except Exception as e:
            logger.error(f"Error executing trade signal: {e}")
            return False
    
    def _is_ready_for_trading(self, pair_str: str) -> bool:
        """Check if system is ready for trading this specific pair"""
        
        if '-' not in pair_str:
            logger.warning(f"Invalid pair format: {pair_str}")
            return False
            
        symbol1, symbol2 = pair_str.split('-', 1)
        required_symbols = [symbol1, symbol2]
        
        # Check if we have real-time prices for both symbols
        missing_prices = []
        for symbol in required_symbols:
            if not self._get_spot_price(symbol):
                missing_prices.append(symbol)
        
        if missing_prices:
            logger.warning(f"Missing spot prices for {pair_str}")
            logger.warning(f"  Required: {required_symbols}")
            logger.warning(f"  Available spot prices ({len(self.spot_prices)}): {list(self.spot_prices.keys())[:10]}{'...' if len(self.spot_prices) > 10 else ''}")
            logger.warning(f"  Missing: {missing_prices}")
            logger.warning(f"  Subscribed symbols: {sorted(self.subscribed_symbols)}")
            
            # Auto-retry subscription for missing symbols if not too many
            if len(missing_prices) <= 2:
                logger.info(f"🔄 Auto-retrying subscription for missing symbols: {missing_prices}")
                for symbol in missing_prices:
                    if symbol in self.subscribed_symbols:
                        # Already subscribed but no price - re-subscribe
                        self.subscribed_symbols.discard(symbol)
                    self._subscribe_to_spot_prices_with_retry(symbol)
            
            return False
        
        # Check if prices are recent (not stale)
        current_time = time.time()
        stale_symbols = []
        
        for symbol in required_symbols:
            price_data = self._get_spot_price_data(symbol)
            if price_data:
                last_update = price_data.get('timestamp', 0)
                if current_time - last_update > 30:  # Price older than 30 seconds
                    stale_symbols.append(symbol)
        
        if stale_symbols:
            logger.warning(f"Stale price data for {pair_str}: {stale_symbols}")
            return False
            
        logger.debug(f"✅ Trading ready for {pair_str} - all price feeds available and current")
        return True
    
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
            elif request_type == 'RECONCILE':
                if globals().get('ProtoOAReconcileReq'):
                    request = ProtoOAReconcileReq()
                    request.ctidTraderAccountId = kwargs.get('account_id')
                    return request
            elif request_type == 'CLOSE_POSITION':
                if globals().get('ProtoOAClosePositionReq'):
                    request = ProtoOAClosePositionReq()
                    request.ctidTraderAccountId = kwargs.get('account_id')
                    request.positionId = kwargs.get('position_id')
                    request.volume = kwargs.get('volume')
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
                # logger.info(f"📨 Message stats (last 100): {dict(self._message_type_counts)}")
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
                    
            elif (message.payloadType == MESSAGE_TYPES.get('RECONCILE_RES', 2125) or
                  message.payloadType == 2124):
                # Handle position reconciliation response
                logger.info(f"📊 RECEIVED RECONCILE RESPONSE - payload type: {message.payloadType}")
                try:
                    response = Protobuf.extract(message)
                    logger.info(f"📊 Processing position reconciliation response")
                    self._process_reconcile_response(response)
                except Exception as e:
                    logger.error(f"❌ Error processing reconcile response: {e}")
                    traceback.print_exc()
                
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
                            # logger.info(f"Converted {local_name} from {value} centilots to {converted_value} lots")
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
        
        # Reconcile existing positions with broker
        logger.info("📊 Reconciling existing positions with CTrader...")
        self._reconcile_positions()
        
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
        """Subscribe to real-time data with intelligent rate limiting and priority management"""
        # Get required symbols from strategy
        required_symbols = self.strategy.get_required_symbols()
        
        # Filter to only available symbols
        available_symbols = {symbol for symbol in required_symbols if symbol in self.symbols_map}
        unavailable_symbols = set(required_symbols) - available_symbols
        
        if unavailable_symbols:
            logger.warning(f"Some required symbols not available: {unavailable_symbols}")
        
        logger.info(f"🔔 Starting intelligent spot price subscriptions for {len(available_symbols)} symbols...")
        
        # Implement priority-based subscription with rate limiting
        self._subscribe_with_priority(available_symbols)
        
        # Start subscription monitoring
        self._start_subscription_monitoring()
        
        logger.info(f"✅ Subscription system started with {self.strategy.__class__.__name__}")
        if unavailable_symbols:
            logger.info("� Note: Some symbols were unavailable and skipped")
    
    def _subscribe_with_priority(self, symbols):
        """Enhanced priority-based subscription with strict CTrader API rate limiting"""
        if not symbols:
            return
        
        logger.info(f"🚀 Starting intelligent subscription for {len(symbols)} symbols")
        logger.info(f"   CTrader API limits: {self._non_historical_rate_limiter['max_requests']} req/sec")
        logger.info(f"   Batch size: {self._subscription_batch_size}, Delay: {self._subscription_delay}s")
        
        # Get symbol IDs for all symbols
        available_symbol_ids = {}
        for symbol in symbols:
            symbol_id = self.symbols_map.get(symbol)
            if symbol_id:
                available_symbol_ids[symbol] = symbol_id
            else:
                logger.warning(f"Symbol {symbol} not found in symbols map")
        
        if not available_symbol_ids:
            logger.warning("No valid symbol IDs found for subscription")
            return
        
        # Determine priority groups
        high_priority_symbols = set(self._get_priority_symbols(symbols))
        normal_priority_symbols = symbols - high_priority_symbols
        
        logger.info(f"   High priority symbols: {len(high_priority_symbols)}")
        logger.info(f"   Normal priority symbols: {len(normal_priority_symbols)}")
        
        # Subscribe with priority and rate limiting
        if high_priority_symbols:
            self._batch_subscribe_with_rate_limiting(high_priority_symbols, priority='HIGH')
        
        if normal_priority_symbols:
            # Delay normal priority to let high priority establish first
            def delayed_normal_subscription():
                time.sleep(2.0)  # 2 second delay
                self._batch_subscribe_with_rate_limiting(normal_priority_symbols, priority='NORMAL')
            
            threading.Thread(target=delayed_normal_subscription, daemon=True).start()
    
    def _get_priority_symbols(self, symbols):
        """Identify high-priority symbols based on trading activity and major currencies"""
        priority_symbols = set()
        
        # Priority 1: Symbols from active trading pairs
        if hasattr(self, 'pair_states') and self.pair_states:
            active_pairs = list(self.pair_states.keys())[:10]  # Top 10 pairs
            for pair_str in active_pairs:
                if '-' in pair_str:
                    s1, s2 = pair_str.split('-', 1)
                    if s1 in symbols:
                        priority_symbols.add(s1)
                    if s2 in symbols:
                        priority_symbols.add(s2)
        
        # Priority 2: Major currency pairs and symbols
        major_symbols = {
            'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'USDCAD', 'AUDUSD', 'NZDUSD',
            'EURJPY', 'GBPJPY', 'EURGBP', 'XAUUSD', 'XAGUSD', 'US30', 'SPX500', 'NAS100'
        }
        priority_symbols.update(major_symbols.intersection(symbols))
        
        # Priority 3: Any USD pairs
        for symbol in symbols:
            if 'USD' in symbol and len(priority_symbols) < 30:
                priority_symbols.add(symbol)
        
        # Limit priority symbols to reasonable number
        priority_list = list(priority_symbols)[:25]
        
        logger.info(f"🎯 Priority symbols identified: {sorted(priority_list)}")
        return priority_list
    
    def _batch_subscribe_with_rate_limiting(self, symbols, priority="NORMAL"):
        """Subscribe to symbols in batches with strict CTrader API rate limiting"""
        if not symbols:
            return
        
        # Convert symbols to IDs
        symbol_ids = []
        symbol_names = []
        for symbol in symbols:
            symbol_id = self.symbols_map.get(symbol)
            if symbol_id:
                symbol_ids.append(symbol_id)
                symbol_names.append(symbol)
        
        if not symbol_ids:
            logger.warning(f"No valid symbol IDs found for {priority} priority subscription")
            return
        
        # Calculate batching strategy for rate limits
        # CTrader allows 50 req/sec, use conservative approach
        batch_size = min(self._subscription_batch_size, len(symbol_ids))
        batches = [symbol_ids[i:i + batch_size] for i in range(0, len(symbol_ids), batch_size)]
        
        # Calculate safe delay: ensure we don't exceed 40 req/sec (80% of limit)
        max_safe_rate = self._non_historical_rate_limiter['max_requests'] * 0.8  # 40 req/sec
        min_delay = 1.0 / max_safe_rate  # Minimum delay between requests
        batch_delay = max(self._subscription_delay, min_delay * batch_size)
        
        total_time = len(batches) * batch_delay
        
        logger.info(f"📦 {priority} batching strategy:")
        logger.info(f"   Total symbols: {len(symbol_ids)}")
        logger.info(f"   Batch size: {batch_size}")
        logger.info(f"   Number of batches: {len(batches)}")
        logger.info(f"   Delay between batches: {batch_delay:.2f}s")
        logger.info(f"   Estimated completion time: {total_time:.1f}s")
        
        def process_subscription_batches():
            successful_subscriptions = 0
            
            for batch_num, batch_symbol_ids in enumerate(batches, 1):
                try:
                    # Use rate-limited subscription
                    self._subscribe_to_spot_prices_with_rate_limiting(batch_symbol_ids, priority)
                    
                    # Update tracking
                    for symbol_id in batch_symbol_ids:
                        symbol_name = self.symbol_id_to_name_map.get(symbol_id)
                        if symbol_name:
                            self.subscribed_symbols.add(symbol_name)
                            successful_subscriptions += 1
                    
                    logger.info(f"� {priority} Batch {batch_num}/{len(batches)} queued: {len(batch_symbol_ids)} symbols")
                    
                    # Wait before next batch (except for last batch)
                    if batch_num < len(batches):
                        time.sleep(batch_delay)
                    
                except Exception as e:
                    logger.error(f"Error processing {priority} batch {batch_num}: {e}")
                    continue
            
            logger.info(f"✅ {priority} batch subscription completed: {successful_subscriptions}/{len(symbol_ids)} symbols queued")
        
        # Process in background thread
        thread = threading.Thread(target=process_subscription_batches, daemon=True)
        thread.start()
        
        return thread
        
        logger.info(f"✅ {priority} Batch {batch_num}: {successful}/{len(batch_symbols)} subscriptions sent")
    
    def _subscribe_to_spot_prices_with_retry(self, symbol):
        """Subscribe to spot prices with intelligent retry logic and rate limiting"""
        
        if symbol not in self.symbols_map:
            logger.warning(f"Symbol {symbol} not found in symbols_map")
            return False
        
        if symbol in self.subscribed_symbols:
            logger.debug(f"Already subscribed to {symbol}")
            return True
        
        symbol_id = self.symbols_map[symbol]
        
        def make_subscription_request():
            try:
                request = self._create_protobuf_request('SUBSCRIBE_SPOTS',
                                                       account_id=self.account_id,
                                                       symbol_ids=[symbol_id])
                
                if request is None:
                    logger.error(f"Failed to create subscription request for {symbol}")
                    return
                
                deferred = self.client.send(request)
                deferred.addErrback(self._on_subscription_error_enhanced, symbol)
                deferred.addTimeout(10, reactor)
                
                self.subscribed_symbols.add(symbol)
                logger.debug(f"📡 Subscription request queued for {symbol}")
                
            except Exception as e:
                logger.error(f"Error creating subscription request for {symbol}: {e}")
        
        # Use rate limiting queue
        self._queue_request(make_subscription_request, 'non_historical', 'NORMAL')
        return True
    
    def _on_subscription_error(self, failure):
        """Handle subscription errors with enhanced context"""
        logger.error(f"Subscription error: {failure}")
        
        # Update subscription stats
        if hasattr(self, 'subscription_stats'):
            self.subscription_stats['failed_responses'] += 1
    
    def _on_subscription_error_enhanced(self, failure, symbol):
        """Enhanced subscription error handling with symbol context"""
        logger.error(f"Subscription error for {symbol}: {failure}")
        
        # Remove from subscribed symbols to allow retry
        if symbol in self.subscribed_symbols:
            self.subscribed_symbols.discard(symbol)
        
        # Update stats
        if hasattr(self, 'subscription_stats'):
            self.subscription_stats['failed_responses'] += 1
        
        # Schedule retry for important symbols
        if self._is_high_priority_symbol(symbol):
            logger.info(f"🔄 Scheduling retry for high-priority symbol {symbol}")
            reactor.callLater(self._subscription_retry_delay, 
                            self._subscribe_to_spot_prices_with_retry, symbol)
    
    def _is_high_priority_symbol(self, symbol):
        """Check if symbol is high priority and should be retried"""
        high_priority_patterns = ['USD', 'EUR', 'GBP', 'JPY', 'XAU', 'SPX', 'NAS', 'US30']
        return any(pattern in symbol for pattern in high_priority_patterns)
    
    def _start_subscription_monitoring(self):
        """Start comprehensive subscription monitoring with health checks"""
        
        # Initialize enhanced subscription tracking
        if not hasattr(self, 'subscription_stats'):
            self.subscription_stats = {
                'total_requested': 0,
                'successful_responses': 0,
                'failed_responses': 0,
                'active_feeds': 0,
                'last_check': time.time(),
                'price_update_counts': defaultdict(int),
                'stale_feeds': set(),
                'retry_queue': deque(),
                'health_score': 100.0
            }
        
        logger.info("🔍 Starting enhanced subscription monitoring system")
        logger.info(f"   Health check interval: {self._subscription_health_check_interval}s")
        logger.info(f"   Max concurrent subscriptions: {self._max_concurrent_subscriptions}")
        
        # Schedule first health check
        reactor.callLater(10.0, self._monitor_subscriptions)  # Start after 10 seconds
        logger.info("🔍 Subscription monitoring started")
    
    def _monitor_subscriptions(self):
        """Enhanced subscription monitoring with health scoring and intelligent recovery"""
        try:
            current_time = time.time()
            active_feeds = len(self.spot_prices)
            subscribed_count = len(self.subscribed_symbols)
            required_symbols = self.strategy.get_required_symbols()
            
            # Update basic stats
            self.subscription_stats.update({
                'active_feeds': active_feeds,
                'last_check': current_time,
                'subscription_efficiency': (active_feeds / max(subscribed_count, 1)) * 100
            })
            
            # Check for missing price feeds
            missing_feeds = []
            stale_feeds = []
            healthy_feeds = 0
            
            for symbol in required_symbols:
                if symbol not in self.spot_prices:
                    missing_feeds.append(symbol)
                else:
                    # Check if feed is stale
                    price_data = self._get_spot_price_data(symbol)
                    if price_data and 'timestamp' in price_data:
                        feed_age = current_time - price_data['timestamp']
                        if feed_age > 60:  # Consider stale if no update for 60 seconds
                            stale_feeds.append(symbol)
                        else:
                            healthy_feeds += 1
                    else:
                        stale_feeds.append(symbol)
            
            # Calculate health score
            total_required = len(required_symbols)
            if total_required > 0:
                health_score = (healthy_feeds / total_required) * 100
                self.subscription_stats['health_score'] = health_score
            else:
                health_score = 100.0
            
            # Log comprehensive status
            if hasattr(self, '_monitor_log_counter'):
                self._monitor_log_counter += 1
            else:
                self._monitor_log_counter = 1
            
            # Calculate coverage percentage for logging
            coverage_percent = (active_feeds / len(required_symbols)) * 100 if required_symbols else 0
            
            # Log brief status every 60 seconds (every 2 cycles since we run every 30s)
            if self._monitor_log_counter % 2 == 0:
                logger.info(f"📊 Subscription Status: {active_feeds} active price feeds")
                logger.info(f"📊 Total subscribed symbols: {subscribed_count}")
                logger.info(f"� Price feed coverage: {coverage_percent:.1f}% ({active_feeds}/{len(required_symbols)})")
                
                if stale_feeds:
                    logger.warning(f"⚠️ {len(stale_feeds)} stale feeds detected - some may need re-subscription")
            
            # Log detailed status every 10 cycles (5 minutes with 30s interval)
            if self._monitor_log_counter % 10 == 0:
                logger.info("=" * 60)
                logger.info("📊 SUBSCRIPTION HEALTH REPORT")
                logger.info("-" * 60)
                logger.info(f"Required symbols      : {total_required}")
                logger.info(f"Subscribed symbols    : {subscribed_count}")
                logger.info(f"Active price feeds    : {active_feeds}")
                logger.info(f"Healthy feeds         : {healthy_feeds}")
                logger.info(f"Missing feeds         : {len(missing_feeds)}")
                logger.info(f"Stale feeds           : {len(stale_feeds)}")
                logger.info(f"Health score          : {health_score:.1f}%")
                logger.info(f"Subscription efficiency: {self.subscription_stats['subscription_efficiency']:.1f}%")
                
                # Show rate limiter status
                non_hist_queue_size = len(self._non_historical_rate_limiter['queue'])
                hist_queue_size = len(self._historical_rate_limiter['queue'])
                if non_hist_queue_size > 0 or hist_queue_size > 0:
                    logger.info(f"Rate limiter queues   : Non-hist={non_hist_queue_size}, Hist={hist_queue_size}")
                
                logger.info("=" * 60)
            
            # Handle missing feeds - retry with rate limiting
            if missing_feeds and health_score < 90:  # Only if health is below 90%
                retry_count = min(len(missing_feeds), 10)  # Limit retries per cycle
                symbols_to_retry = missing_feeds[:retry_count]
                
                logger.warning(f"🔄 Health score {health_score:.1f}% - retrying {retry_count} missing feeds")
                
                for symbol in symbols_to_retry:
                    if symbol in self.subscribed_symbols:
                        # Already subscribed but no data - re-subscribe
                        self.subscribed_symbols.discard(symbol)
                    
                    # Use rate-limited retry
                    self._subscribe_to_spot_prices_with_retry(symbol)
            
            # Handle stale feeds
            if stale_feeds and len(stale_feeds) > 5:  # Only if many stale feeds
                logger.warning(f"⚠️ {len(stale_feeds)} stale feeds detected - some may need re-subscription")
                
                # Re-subscribe to worst stale feeds
                stale_retry_count = min(len(stale_feeds), 5)
                for symbol in stale_feeds[:stale_retry_count]:
                    # logger.info(f"🔄 Re-subscribing to stale feed: {symbol}")
                    self.subscribed_symbols.discard(symbol)
                    self._subscribe_to_spot_prices_with_retry(symbol)
            
            # Emergency recovery if health is critically low
            if health_score < 50 and active_feeds < 10:
                logger.error("🚨 CRITICAL: Subscription health below 50% - initiating emergency recovery")
                logger.error(f"   Only {active_feeds} active feeds out of {total_required} required")
                
                # Clear subscription tracking and restart core subscriptions
                priority_symbols = self._get_priority_symbols(set(required_symbols))[:10]
                logger.error(f"🚨 Emergency re-subscription for {len(priority_symbols)} critical symbols")
                
                for symbol in priority_symbols:
                    self.subscribed_symbols.discard(symbol)
                    self._subscribe_to_spot_prices_with_retry(symbol)
            
            # Schedule next monitoring cycle
            reactor.callLater(self._subscription_health_check_interval, self._monitor_subscriptions)
            
        except Exception as e:
            logger.error(f"Error in subscription monitoring: {e}")
            # Still schedule next cycle even if this one failed
            reactor.callLater(self._subscription_health_check_interval, self._monitor_subscriptions)
        
    
    def _retry_missing_subscriptions(self, missing_symbols):
        """Retry subscriptions for symbols that should have feeds but don't"""
        
        logger.info(f"🔄 Retrying subscriptions for {len(missing_symbols)} symbols")
        
        # Remove from subscribed set to allow retry
        for symbol in missing_symbols:
            self.subscribed_symbols.discard(symbol)
        
        # Retry with conservative batching
        self._batch_subscribe(missing_symbols, batch_size=3, delay=0.5, priority="RETRY")
    
    def _subscribe_to_spot_prices(self, symbol):
        """Subscribe to real-time price updates for a symbol (legacy method)"""
        return self._subscribe_to_spot_prices_with_retry(symbol)
    
    def _on_subscription_error_enhanced(self, failure, symbol=None):
        """Enhanced subscription error handling with intelligent categorization"""
        
        error_type = str(type(failure.value).__name__)
        error_msg = str(failure.value)
        
        # Categorize error types
        if 'TimeoutError' in error_type:
            # Timeout is normal with CTrader API under heavy load
            logger.debug(f"Subscription timeout for {symbol} (normal CTrader behavior)")
            
            # Don't treat timeouts as failures - they often succeed anyway
            if hasattr(self, 'subscription_stats'):
                self.subscription_stats.setdefault('timeout_count', 0)
                self.subscription_stats['timeout_count'] += 1
                
        elif 'ConnectionLost' in error_type or 'ConnectionRefused' in error_type:
            # Connection issues - more serious
            logger.warning(f"Connection issue for {symbol}: {error_type}")
            
        elif 'RateLimitExceeded' in error_msg or 'TooManyRequests' in error_msg:
            # Rate limiting - back off
            logger.warning(f"Rate limit hit for {symbol} - backing off")
            
        else:
            # Other errors - log for investigation
            logger.warning(f"Subscription error for {symbol}: {error_type} - {error_msg}")
        
        # Update error stats
        if hasattr(self, 'subscription_stats'):
            self.subscription_stats['failed_responses'] = self.subscription_stats.get('failed_responses', 0) + 1
    
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
    
    def _get_spot_price(self, symbol: str) -> Optional[float]:
        """Get the current spot price for a symbol"""
        if symbol not in self.spot_prices:
            return None
            
        price_data = self.spot_prices[symbol]
        
        # Handle both old format (float) and new format (dict)
        if isinstance(price_data, dict):
            return price_data.get('price')
        else:
            # Legacy format - just return the float
            return price_data
    
    def _get_spot_price_data(self, symbol: str) -> Optional[dict]:
        """Get the full spot price data for a symbol"""
        if symbol not in self.spot_prices:
            return None
            
        price_data = self.spot_prices[symbol]
        
        # Handle both old format (float) and new format (dict)
        if isinstance(price_data, dict):
            return price_data
        else:
            # Legacy format - convert to dict
            return {
                'price': price_data,
                'bid': price_data,
                'ask': price_data,
                'timestamp': time.time(),
                'symbol_id': None,
                'update_count': 1
            }
    
    def _can_make_request(self, request_type='non_historical'):
        """Check if we can make a request without exceeding rate limits"""
        limiter = self._non_historical_rate_limiter if request_type == 'non_historical' else self._historical_rate_limiter
        current_time = time.time()
        
        # Remove old requests outside the time window
        while limiter['requests'] and current_time - limiter['requests'][0] > limiter['time_window']:
            limiter['requests'].popleft()
        
        # Check if we can make another request
        return len(limiter['requests']) < limiter['max_requests']
    
    def _record_request(self, request_type='non_historical'):
        """Record that a request was made"""
        limiter = self._non_historical_rate_limiter if request_type == 'non_historical' else self._historical_rate_limiter
        limiter['requests'].append(time.time())
    
    def _queue_request(self, request_func, request_type='non_historical', priority='NORMAL'):
        """Queue a request to be processed respecting rate limits"""
        limiter = self._non_historical_rate_limiter if request_type == 'non_historical' else self._historical_rate_limiter
        
        # Add to queue with priority
        request_item = {
            'func': request_func,
            'priority': priority,
            'timestamp': time.time(),
            'request_type': request_type
        }
        
        # Insert based on priority (HIGH first, then NORMAL, then LOW)
        if priority == 'HIGH':
            limiter['queue'].appendleft(request_item)
        else:
            limiter['queue'].append(request_item)
        
        # Start processing if not already running
        if not limiter['processing']:
            self._start_request_processor(request_type)
    
    def _start_request_processor(self, request_type):
        """Start processing queued requests respecting rate limits"""
        def process_requests():
            limiter = self._non_historical_rate_limiter if request_type == 'non_historical' else self._historical_rate_limiter
            limiter['processing'] = True
            
            try:
                while limiter['queue']:
                    if self._can_make_request(request_type):
                        # Process next request
                        request_item = limiter['queue'].popleft()
                        try:
                            request_item['func']()
                            self._record_request(request_type)
                            logger.debug(f"Processed {request_type} request (priority: {request_item['priority']})")
                        except Exception as e:
                            logger.error(f"Error processing {request_type} request: {e}")
                    else:
                        # Wait before checking again
                        wait_time = 0.1 if request_type == 'non_historical' else 0.5
                        time.sleep(wait_time)
                        continue
                    
                    # Small delay between requests to be conservative
                    time.sleep(0.02 if request_type == 'non_historical' else 0.2)
                        
            except Exception as e:
                logger.error(f"Error in request processor for {request_type}: {e}")
            finally:
                limiter['processing'] = False
        
        # Run processor in background thread
        threading.Thread(target=process_requests, daemon=True).start()
    
    def _subscribe_to_spot_prices_with_rate_limiting(self, symbol_ids, priority='NORMAL'):
        """Subscribe to spot prices with intelligent rate limiting"""
        def make_subscription_request():
            try:
                if not symbol_ids:
                    return
                
                request = self._create_protobuf_request('SUBSCRIBE_SPOTS',
                                                       account_id=self.account_id,
                                                       symbol_ids=symbol_ids)
                
                if request is None:
                    logger.error(f"Failed to create subscription request for symbols: {symbol_ids}")
                    return
                
                deferred = self.client.send(request)
                deferred.addErrback(self._on_subscription_error)
                
                # Log subscription attempt
                symbol_names = [self.symbol_id_to_name_map.get(sid, f'ID:{sid}') for sid in symbol_ids]
                logger.info(f"📡 Subscribed to {len(symbol_ids)} symbols with {priority} priority: {symbol_names}")
                
            except Exception as e:
                logger.error(f"Error creating subscription request: {e}")
        
        # Queue the subscription request
        self._queue_request(make_subscription_request, 'non_historical', priority)
    
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
                existing_price_data = self.spot_prices[symbol_name]
                if isinstance(existing_price_data, dict):
                    existing_price = existing_price_data.get('price', 0)
                else:
                    existing_price = existing_price_data
                    
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
        
        # Store price with timestamp for freshness checking
        current_time = time.time()
        price = (bid + ask) / 2
        
        # Enhanced price storage with metadata
        self.spot_prices[symbol_name] = {
            'price': price,
            'bid': bid,
            'ask': ask,
            'timestamp': current_time,
            'symbol_id': symbol_id,
            'update_count': getattr(self, '_price_update_count', {}).get(symbol_name, 0) + 1
        }
        
        # Update counter
        if not hasattr(self, '_price_update_count'):
            self._price_update_count = {}
        self._price_update_count[symbol_name] = self._price_update_count.get(symbol_name, 0) + 1
        
        # Log first few price updates to confirm data flow
        if not hasattr(self, '_spot_debug_count'):
            self._spot_debug_count = 0
        self._spot_debug_count += 1
        
        # if self._spot_debug_count <= 50:  # Log first 50 spot events for debugging
        #     logger.info(f"🔥 SPOT EVENT #{self._spot_debug_count}: {symbol_name} = ${price:.5f} (bid={bid}, ask={ask})")
        #     if self._spot_debug_count == 50:
        #         logger.info("🔥 Spot event debugging complete - future price updates will be throttled")
        
        # Log price updates periodically to prove data flow
        if not hasattr(self, '_price_log_counter'):
            self._price_log_counter = {}
        if symbol_name not in self._price_log_counter:
            self._price_log_counter[symbol_name] = 0
        
        self._price_log_counter[symbol_name] += 1
        
        # Log every 100th price update to show data flow without spam
        # if self._price_log_counter[symbol_name] % 100 == 0:
        #     logger.info(f"📈 PRICE DATA FLOW: {symbol_name} = ${price:.5f} (updates: {self._price_log_counter[symbol_name]})")
        
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
                    # logger.info(f"   Data points: {len(series1)} x {len(series2)}")
                    logger.info(f"   Latest prices: {state['symbol1']}=${series1.iloc[-1]:.5f}, {state['symbol2']}=${series2.iloc[-1]:.5f}")
                
                indicators = self.strategy.calculate_indicators(market_data)
                if not indicators:
                    if self._strategy_calc_log[pair_str] % 10 == 0:
                        logger.warning(f"❌ STRATEGY CALCULATION: No indicators returned for {pair_str}")
                    return
                    
                # Log indicator values periodically with enhanced debugging
                if self._strategy_calc_log[pair_str] % 10 == 0:
                    logger.info(f"✅ STRATEGY CALCULATION: Indicators calculated for {pair_str}")
                    # logger.info(f"   Available indicators: {list(indicators.keys())}")
                    
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
                
                # RACE CONDITION FIX: Include pending trades in total position count
                # This prevents exceeding MAX_OPEN_POSITIONS when multiple signals occur simultaneously
                pending_count = len(getattr(self, 'pending_pair_trades', {}))
                total_position_count = current_position_count + pending_count
            
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
            # RACE CONDITION FIX: Use total count including pending trades
            if total_position_count >= self.config.max_open_positions:
                if not hasattr(self, '_limit_log_throttle'):
                    self._limit_log_throttle = {}
                if pair_str not in self._limit_log_throttle:
                    self._limit_log_throttle[pair_str] = 0
                self._limit_log_throttle[pair_str] += 1
                
                # Log occasionally to avoid spam
                if self._limit_log_throttle[pair_str] % 10 == 1:
                    signal_type = "LONG" if getattr(latest_signal, 'long_entry', False) else "SHORT"
                    logger.info(f"[PORTFOLIO LIMIT] Skipping {signal_type} for {pair_str} - at limit: {current_position_count} active + {pending_count} pending = {total_position_count}/{self.config.max_open_positions}")
                return
            
            # Process entry signals using strategy output
            # CRITICAL FIX: This section should only be reached if no active/pending positions exist
            if getattr(latest_signal, 'short_entry', False):
                logger.info(f"[SHORT ENTRY] Signal for {pair_str} - Positions: {current_position_count} active + {pending_count} pending = {total_position_count}/{self.config.max_open_positions}")
                self._execute_pair_trade(pair_str, 'SHORT')
            elif getattr(latest_signal, 'long_entry', False):
                logger.info(f"[LONG ENTRY] Signal for {pair_str} - Positions: {current_position_count} active + {pending_count} pending = {total_position_count}/{self.config.max_open_positions}")
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
            # Double-check position limits including pending trades to prevent race conditions
            current_active = len(self.active_positions)
            current_pending = len(getattr(self, 'pending_pair_trades', {}))
            total_positions = current_active + current_pending
            
            if total_positions >= self.config.max_open_positions:
                logger.warning(f"[PORTFOLIO LIMIT] Cannot open {direction} position for {pair_str} - at limit: {current_active} active + {current_pending} pending = {total_positions}/{self.config.max_open_positions}")
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
        
        price1 = self._get_spot_price(s1)
        price2 = self._get_spot_price(s2)
        
        if price1 is None or price2 is None:
            logger.error(f"Failed to get prices for {pair_str}: {s1}={price1}, {s2}={price2}")
            return False
        
        # Get symbol details for volume calculation
        details1 = self.symbol_details.get(s1)
        details2 = self.symbol_details.get(s2)
        
        if not details1 or not details2:
            logger.error(f"Missing symbol details for volume calculation: {s1}={bool(details1)}, {s2}={bool(details2)}")
            return False
        
        # Calculate volumes using enhanced shared VolumeBalancer
        volumes = self.risk_manager.volume_balancer.calculate_balanced_volumes(
            s1, s2, price1, price2,
            details1, details2,
            self.config.max_position_size
        )
        
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
        """Close a pair position using ProtoOAClosePositionReq when position IDs are available"""
        if pair_str not in self.active_positions:
            return False
        
        position = self.active_positions[pair_str]
        state = self.pair_states[pair_str]
        
        s1, s2 = position['symbol1'], position['symbol2']
        direction = position['direction']
        volume1, volume2 = position['volume1'], position['volume2']
        position_id1 = position.get('position_id1')
        position_id2 = position.get('position_id2')
        
        logger.info(f"🔄 Closing {direction} position for {pair_str}")
        logger.info(f"   Position IDs: {s1}={position_id1}, {s2}={position_id2}")
        
        success_count = 0
        
        # Try to close using ProtoOAClosePositionReq if position IDs are available
        if position_id1:
            if self._close_position_by_id(s1, position_id1, volume1):
                success_count += 1
            else:
                logger.warning(f"Failed to close position {position_id1} for {s1}, falling back to market order")
                # Fallback to market order
                close_side1 = self._get_trade_side_value("SELL") if direction == 'LONG' else self._get_trade_side_value("BUY")
                if self._send_market_order(s1, close_side1, volume1, is_close=True):
                    success_count += 1
        else:
            logger.info(f"No position ID available for {s1}, using market order to close")
            close_side1 = self._get_trade_side_value("SELL") if direction == 'LONG' else self._get_trade_side_value("BUY")
            if self._send_market_order(s1, close_side1, volume1, is_close=True):
                success_count += 1
        
        if position_id2:
            if self._close_position_by_id(s2, position_id2, volume2):
                success_count += 1
            else:
                logger.warning(f"Failed to close position {position_id2} for {s2}, falling back to market order")
                # Fallback to market order
                close_side2 = self._get_trade_side_value("BUY") if direction == 'LONG' else self._get_trade_side_value("SELL")
                if self._send_market_order(s2, close_side2, volume2, is_close=True):
                    success_count += 1
        else:
            logger.info(f"No position ID available for {s2}, using market order to close")
            close_side2 = self._get_trade_side_value("BUY") if direction == 'LONG' else self._get_trade_side_value("SELL")
            if self._send_market_order(s2, close_side2, volume2, is_close=True):
                success_count += 1
        
        # Clean up position state with thread safety if at least one close order was sent
        if success_count > 0:
            with self._update_lock:
                if pair_str in self.active_positions:
                    del self.active_positions[pair_str]
                state['position'] = None
                state['cooldown'] = self.config.cooldown_bars
            
            logger.info(f"Initiated close for {direction} position on {pair_str} ({success_count}/2 orders sent)")
            logger.info(f"Portfolio now: {len(self.active_positions)}/{self.config.max_open_positions}")
            return True
        else:
            logger.error(f"Failed to send any close orders for {pair_str}")
            return False
    
    def _close_position_by_id(self, symbol: str, position_id: int, volume: float) -> bool:
        """Close a position using ProtoOAClosePositionReq"""
        logger.info(f"🎯 Closing position by ID: {symbol} (Position ID: {position_id}, Volume: {volume:.5f} lots)")
        
        if symbol not in self.symbols_map:
            logger.error(f"Symbol {symbol} not found")
            return False
        
        # Convert volume to cTrader centilots format
        if symbol == 'XRPUSD':
            broker_volume = int(round(volume * 10000))
        else:
            broker_volume = int(round(volume * 100))
        
        # Ensure minimum volume
        broker_volume = max(broker_volume, 1)
        
        request = self._create_protobuf_request('CLOSE_POSITION',
                                              account_id=self.account_id,
                                              position_id=position_id,
                                              volume=broker_volume)
        
        if request is None:
            logger.error(f"Failed to create close position request for {symbol}")
            return False
        
        try:
            deferred = self.client.send(request)
            deferred.addErrback(self._on_close_position_error, symbol, position_id)
            
            # Track the close position request differently since cTrader generates its own order IDs
            if not hasattr(self, 'pending_close_positions'):
                self.pending_close_positions = {}
            
            self.pending_close_positions[position_id] = {
                'symbol': symbol,
                'position_id': position_id,
                'volume': volume,
                'timestamp': datetime.now(),
                'is_close': True
            }
            
            logger.info(f"📨 CLOSE POSITION REQUEST SENT - Symbol: {symbol}, Position ID: {position_id}, Volume: {volume:.5f} lots ({broker_volume} centilots)")
            logger.info(f"📨 Tracking close position request for Position ID: {position_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending close position request for {symbol}: {e}")
            return False
    
    def _on_close_position_error(self, failure, symbol: str, position_id: int):
        """Handle close position request errors"""
        logger.error(f"Close position error for {symbol} (Position ID: {position_id}): {failure}")
        # You might want to implement retry logic or fallback to market orders here
    
    def _handle_close_position_execution(self, position_id: int, event):
        """Handle execution events for close position requests"""
        execution_type = getattr(event, 'executionType', None)
        order_status = getattr(event, 'order', None)
        if order_status:
            order_status = getattr(order_status, 'orderStatus', None)
        
        close_request = self.pending_close_positions.get(position_id)
        if not close_request:
            logger.warning(f"⚠️ Close position execution event for unknown position ID: {position_id}")
            return
        
        symbol = close_request['symbol']
        volume = close_request['volume']
        
        logger.info(f"🎯 CLOSE POSITION EXECUTION EVENT:")
        logger.info(f"   Position ID: {position_id}")
        logger.info(f"   Symbol: {symbol}")
        logger.info(f"   Volume: {volume:.5f} lots")
        logger.info(f"   Execution Type: {execution_type}")
        logger.info(f"   Order Status: {order_status} ({self._get_order_status_name(order_status) if order_status else 'None'})")
        
        # Handle different execution types
        if execution_type == 'ORDER_FILLED' or execution_type == 3:  # 3 = ORDER_FILLED
            logger.info(f"✅ Position {position_id} for {symbol} CLOSED successfully")
            del self.pending_close_positions[position_id]
        elif execution_type == 'ORDER_ACCEPTED' or execution_type == 2:  # 2 = ORDER_ACCEPTED
            logger.info(f"📝 Close position request for {symbol} (Position ID: {position_id}) ACCEPTED")
        elif execution_type in ['ORDER_REJECTED', 'ORDER_EXPIRED', 'ORDER_CANCELLED'] or execution_type in [4, 5, 6]:
            logger.error(f"❌ Close position request for {symbol} (Position ID: {position_id}) FAILED: {execution_type}")
            del self.pending_close_positions[position_id]
        
        # Also check order status as secondary indicator
        if order_status == 2:  # ORDER_STATUS_FILLED
            logger.info(f"✅ Position {position_id} for {symbol} status: FILLED")
            if position_id in self.pending_close_positions:
                del self.pending_close_positions[position_id]
        elif order_status in [3, 4, 5]:  # REJECTED, EXPIRED, CANCELLED
            logger.error(f"❌ Position {position_id} for {symbol} status: {self._get_order_status_name(order_status)}")
            if position_id in self.pending_close_positions:
                del self.pending_close_positions[position_id]
    
    def get_position_ids_status(self) -> dict:
        """Get status of position ID tracking for debugging"""
        status = {
            'total_positions': len(self.active_positions),
            'positions_with_ids': 0,
            'positions_without_ids': 0,
            'pending_close_positions': len(getattr(self, 'pending_close_positions', {})),
            'position_details': {}
        }
        
        for pair_str, position in self.active_positions.items():
            pos_id1 = position.get('position_id1')
            pos_id2 = position.get('position_id2')
            
            has_id1 = pos_id1 is not None
            has_id2 = pos_id2 is not None
            
            if has_id1 and has_id2:
                status['positions_with_ids'] += 1
            else:
                status['positions_without_ids'] += 1
            
            status['position_details'][pair_str] = {
                'symbol1': position['symbol1'],
                'symbol2': position['symbol2'],
                'position_id1': pos_id1,
                'position_id2': pos_id2,
                'has_both_ids': has_id1 and has_id2
            }
        
        # Add information about pending close positions
        if hasattr(self, 'pending_close_positions'):
            status['pending_close_details'] = {}
            for position_id, close_request in self.pending_close_positions.items():
                status['pending_close_details'][position_id] = {
                    'symbol': close_request['symbol'],
                    'volume': close_request['volume'],
                    'timestamp': close_request['timestamp'].isoformat()
                }
        
        return status
    
    def validate_close_position_implementation(self) -> bool:
        """Validate that ProtoOAClosePositionReq implementation is working"""
        logger.info("🔍 Validating ProtoOAClosePositionReq implementation...")
        
        # Check if protobuf class is available
        if not globals().get('ProtoOAClosePositionReq'):
            logger.error("❌ ProtoOAClosePositionReq not available in protobuf imports")
            return False
        
        # Test creating a close position request
        try:
            test_request = self._create_protobuf_request('CLOSE_POSITION',
                                                        account_id=12345,
                                                        position_id=67890,
                                                        volume=100)
            if test_request is None:
                logger.error("❌ Failed to create test ProtoOAClosePositionReq")
                return False
            
            logger.info("✅ ProtoOAClosePositionReq creation test passed")
        except Exception as e:
            logger.error(f"❌ Error creating test ProtoOAClosePositionReq: {e}")
            return False
        
        # Check position ID tracking in active positions
        position_status = self.get_position_ids_status()
        logger.info(f"📊 Position ID tracking status:")
        logger.info(f"   Total positions: {position_status['total_positions']}")
        logger.info(f"   Positions with IDs: {position_status['positions_with_ids']}")
        logger.info(f"   Positions without IDs: {position_status['positions_without_ids']}")
        logger.info(f"   Pending close positions: {position_status['pending_close_positions']}")
        
        # Test close position tracking initialization
        if not hasattr(self, 'pending_close_positions'):
            logger.error("❌ pending_close_positions not initialized")
            return False
        else:
            logger.info("✅ Close position tracking initialized")
        
        logger.info("✅ ProtoOAClosePositionReq implementation validation complete")
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
            # Note: This is a fallback method for closing positions when position IDs are not available
            # The preferred method is to use _close_position_by_id() with ProtoOAClosePositionReq
            logger.debug(f"Using market order to close position for {symbol} (fallback method - no position ID)")
        
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
            
            # Check if this is a close position execution event by looking for position information
            if position:
                position_id = getattr(position, 'positionId', None)
                if position_id and hasattr(self, 'pending_close_positions') and position_id in self.pending_close_positions:
                    logger.info(f"🎯 Identified close position execution event for Position ID: {position_id}")
                    self._handle_close_position_execution(position_id, event)
                    return
            
            # Log raw event structure for debugging if it's not a close position event
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
            
            # Check if this is a close position execution event by looking for position information
            # This handles cases where cTrader generates its own order IDs for close position requests
            if position:
                position_id = getattr(position, 'positionId', None)
                if position_id and hasattr(self, 'pending_close_positions') and position_id in self.pending_close_positions:
                    logger.info(f"🎯 Identified close position execution event for Position ID: {position_id}")
                    self._handle_close_position_execution(position_id, event)
                    return
            
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
                # Extract position ID from execution event if available
                position = getattr(event, 'position', None)
                if position:
                    position_id = getattr(position, 'positionId', None)
                    if position_id:
                        pending_trade['position_id1'] = position_id
                        logger.info(f"📋 Captured position ID for {pending_trade['symbol1']}: {position_id}")
                logger.info(f"✅ First leg of pair trade filled: {pending_trade['symbol1']} (Order: {order_id})")
                logger.info(f"   Pair: {pending_trade['pair_str']}, Direction: {pending_trade['direction']}")
            elif pending_trade['order2_id'] == order_id:
                pending_trade['order2_filled'] = True
                # Extract position ID from execution event if available
                position = getattr(event, 'position', None)
                if position:
                    position_id = getattr(position, 'positionId', None)
                    if position_id:
                        pending_trade['position_id2'] = position_id
                        logger.info(f"📋 Captured position ID for {pending_trade['symbol2']}: {position_id}")
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
                'order_ids': (pending_trade['order1_id'], pending_trade['order2_id']),
                # Position IDs will be populated when we receive execution events with position data
                'position_id1': pending_trade.get('position_id1', None),
                'position_id2': pending_trade.get('position_id2', None)
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
        
        # Log position ID tracking status
        pos_id1 = pending_trade.get('position_id1')
        pos_id2 = pending_trade.get('position_id2')
        if pos_id1 and pos_id2:
            logger.info(f"   📋 Position IDs captured: {pending_trade['symbol1']}={pos_id1}, {pending_trade['symbol2']}={pos_id2}")
            logger.info(f"   ✅ Can use ProtoOAClosePositionReq for closing")
        elif pos_id1 or pos_id2:
            logger.warning(f"   ⚠️ Partial position IDs: {pending_trade['symbol1']}={pos_id1}, {pending_trade['symbol2']}={pos_id2}")
            logger.warning(f"   ⚠️ Will use mixed close methods (ProtoOAClosePositionReq + market orders)")
        else:
            logger.warning(f"   ⚠️ No position IDs captured - will use market orders for closing")
        
        logger.info(f"   Portfolio now: {len(self.active_positions)}/{self.config.max_open_positions} positions")
    
    def _check_drawdown_limits(self, pair_str: str = None) -> bool:
        """Check if trading should be allowed based on drawdown limits"""
        # Check global portfolio suspension
        if self.portfolio_trading_suspended:
            return False
        
        # Check pair-specific suspension
        if pair_str and hasattr(self, 'suspended_pairs') and pair_str in self.suspended_pairs:
            logger.debug(f"Trading blocked for suspended pair: {pair_str}")
            return False
        
        # Use shared risk manager for drawdown checks
        current_portfolio_value = self.get_portfolio_value()
        current_positions = len(self.active_positions)
        
        # Calculate current exposure
        current_exposure = 0.0
        for position in self.active_positions.values():
            # Calculate exposure based on position values
            exposure1 = position.get('volume1', 0) * position.get('entry_price1', 0)
            exposure2 = position.get('volume2', 0) * position.get('entry_price2', 0)
            current_exposure += abs(exposure1) + abs(exposure2)
        
        return self.risk_manager.check_trading_allowed(
            current_portfolio_value, current_positions, current_exposure, pair_str=pair_str
        )
    
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
            # logger.info("SIGNAL GENERATION STATUS")
            # logger.info("-" * 80)
            # total_price_updates = sum(getattr(self, '_price_log_counter', {}).values())
            # total_signal_checks = getattr(self, '_signal_check_counter', 0)
            # logger.info(f"Total Price Updates : {total_price_updates}")
            # logger.info(f"Total Signal Checks : {total_signal_checks}")
            # logger.info(f"Subscribed Symbols  : {len(self.subscribed_symbols)}")
            # logger.info(f"Live Price Data     : {len(self.spot_prices)} symbols")
            
            # Show data accumulation status
            # if hasattr(self, '_data_accumulation_log'):
            #     logger.info("-" * 40)
            #     logger.info("DATA ACCUMULATION STATUS")
            #     logger.info("-" * 40)
            #     for pair_str, state in self.pair_states.items():
            #         min_data_points = getattr(self.strategy, 'get_minimum_data_points', lambda: 50)()
            #         p1_count = len(state['price1'])
            #         p2_count = len(state['price2'])
                    # ready = "✅" if p1_count >= min_data_points and p2_count >= min_data_points else "⏳"
                    # logger.info(f"{ready} {pair_str}: {p1_count}/{min_data_points} + {p2_count}/{min_data_points}")
            
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
            
            current_price1 = self._get_spot_price(s1)
            current_price2 = self._get_spot_price(s2)
            
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
            # Calculate position P&L (simplified) - use helper methods for price access
            current_price1 = self._get_spot_price(position['symbol1'])
            current_price2 = self._get_spot_price(position['symbol2'])
            
            # Fallback to entry prices if current prices not available
            if current_price1 is None:
                current_price1 = position['entry_price1']
            if current_price2 is None:
                current_price2 = position['entry_price2']
            
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
    
    def get_portfolio_value(self) -> float:
        """Get current portfolio value including unrealized P&L"""
        portfolio_value = self.config.initial_portfolio_value
        unrealized_pnl = 0.0
        
        for pair_str, position in self.active_positions.items():
            # Calculate position P&L (simplified) - use helper methods for price access
            current_price1 = self._get_spot_price(position['symbol1'])
            current_price2 = self._get_spot_price(position['symbol2'])
            
            # Fallback to entry prices if current prices not available
            if current_price1 is None:
                current_price1 = position['entry_price1']
            if current_price2 is None:
                current_price2 = position['entry_price2']
            
            # Basic P&L calculation
            if position['direction'] == 'LONG':
                pnl = (current_price1 - position['entry_price1']) * position['volume1'] - \
                      (current_price2 - position['entry_price2']) * position['volume2']
            else:
                pnl = (position['entry_price1'] - current_price1) * position['volume1'] - \
                      (position['entry_price2'] - current_price2) * position['volume2']
            
            unrealized_pnl += pnl
        
        return portfolio_value + unrealized_pnl
    
    def _reconcile_positions(self):
        """Request position reconciliation from CTrader broker"""
        logger.info("="*60)
        logger.info("INITIATING POSITION RECONCILIATION WITH CTRADER")
        logger.info("="*60)
        
        try:
            # Send reconcile request to get current positions from broker
            logger.info(f"📊 Sending reconcile request for account: {self.account_id}")
            
            request = self._create_protobuf_request('RECONCILE', account_id=self.account_id)
            
            if request is None:
                logger.error("Failed to create reconcile request")
                return
            
            # Send the request
            deferred = self.client.send(request)
            deferred.addErrback(self._on_error)
            
            logger.info("📊 Reconcile request sent - waiting for response...")
            
            # Store flag to indicate reconciliation is in progress
            self._reconciliation_in_progress = True
            
        except Exception as e:
            logger.error(f"❌ Failed to send position reconcile request: {e}")
            logger.error(f"Will continue with local position tracking only")
            traceback.print_exc()
    
    def _process_reconcile_response(self, response):
        """Process reconciliation response containing actual broker positions"""
        logger.info("="*60)
        logger.info("PROCESSING POSITION RECONCILIATION RESPONSE")
        logger.info("="*60)
        
        try:
            self._reconciliation_in_progress = False
            
            # Extract positions from reconcile response
            broker_positions = []
            orders = []
            
            # Check for positions in response
            if hasattr(response, 'position') and response.position:
                broker_positions = response.position
                logger.info(f"📊 Found {len(broker_positions)} positions from broker")
            else:
                logger.info("📊 No positions found in broker")
            
            # Check for orders in response  
            if hasattr(response, 'order') and response.order:
                orders = response.order
                logger.info(f"📊 Found {len(orders)} pending orders from broker")
            else:
                logger.info("📊 No pending orders found in broker")
            
            # Process and convert broker positions to our format
            restored_positions = {}
            for pos in broker_positions:
                try:
                    position_info = self._convert_broker_position_to_local(pos)
                    if position_info:
                        pair_key = position_info['pair']
                        restored_positions[pair_key] = position_info
                        logger.info(f"📊 Restored position: {pair_key} | {position_info['direction']} | Entry: {position_info.get('entry_price', 'N/A')}")
                except Exception as e:
                    logger.warning(f"Failed to convert broker position: {e}")
            
            # Update our local position tracking
            if restored_positions:
                logger.info("="*60)
                logger.info("UPDATING LOCAL POSITION TRACKING")
                logger.info("="*60)
                
                # Clear existing positions (they should be empty anyway during startup)
                old_count = len(self.active_positions)
                self.active_positions.clear()
                
                # Add restored positions
                for pair_key, position_info in restored_positions.items():
                    self.active_positions[pair_key] = position_info
                
                logger.info(f"📊 Position reconciliation complete:")
                logger.info(f"   Cleared {old_count} local positions")
                logger.info(f"   Restored {len(restored_positions)} broker positions")
                logger.info(f"   New total: {len(self.active_positions)} active positions")
                
                # Update state manager with restored positions
                if hasattr(self, 'state_manager') and self.state_manager:
                    try:
                        logger.info("💾 Updating state manager with reconciled positions...")
                        # Convert to state manager format
                        state_positions = {}
                        for pair_key, pos in self.active_positions.items():
                            state_positions[pair_key] = {
                                'symbol1': pos.get('symbol1', ''),
                                'symbol2': pos.get('symbol2', ''),
                                'direction': pos.get('direction', ''),
                                'entry_price': pos.get('entry_price', 0),
                                'quantity': pos.get('volume1', 0),  # Use first leg volume
                                'entry_time': pos.get('entry_time', datetime.now().isoformat()),
                                'pair': pair_key,
                                'broker_position_id': pos.get('position_id', None)
                            }
                        
                        # Save reconciled positions to state manager
                        self.state_manager.save_trading_state(
                            active_positions=state_positions,
                            pair_states={},
                            portfolio_data={'reconciliation_time': datetime.now().isoformat()}
                        )
                        logger.info("✅ State manager updated with reconciled positions")
                    except Exception as e:
                        logger.warning(f"Failed to update state manager: {e}")
                
                logger.info("="*60)
                logger.info("POSITION RECONCILIATION COMPLETED SUCCESSFULLY")
                logger.info("="*60)
                logger.info(f"✅ Trading system will continue with {len(self.active_positions)} existing positions")
                
            else:
                logger.info("="*60)
                logger.info("NO EXISTING POSITIONS TO RECONCILE")
                logger.info("="*60)
                logger.info("✅ Trading system will start with fresh state")
                
        except Exception as e:
            logger.error(f"❌ Error processing reconcile response: {e}")
            logger.error("Trading system will continue with local tracking only")
            traceback.print_exc()
    
    def _convert_broker_position_to_local(self, broker_position):
        """Convert a broker position object to our local position format"""
        try:
            # Extract basic position information
            position_id = getattr(broker_position, 'positionId', None)
            symbol_id = getattr(broker_position, 'symbolId', None)
            trade_side = getattr(broker_position, 'tradeSide', None)
            volume = getattr(broker_position, 'volume', 0)
            entry_price = getattr(broker_position, 'entryPrice', 0)
            open_timestamp = getattr(broker_position, 'openTimestamp', None)
            
            if not symbol_id or not position_id:
                logger.warning(f"Position missing required fields: symbolId={symbol_id}, positionId={position_id}")
                return None
            
            # Convert symbol ID to symbol name
            symbol_name = self.symbol_id_to_name_map.get(symbol_id)
            if not symbol_name:
                logger.warning(f"Unknown symbol ID: {symbol_id}")
                return None
            
            # For now, we can only handle single-symbol positions
            # Pairs positions would require additional logic to match pairs
            logger.info(f"📊 Found broker position: {symbol_name} | {trade_side} | Volume: {volume} | Price: {entry_price}")
            
            # Convert to our format (simplified for single symbols)
            direction = "LONG" if trade_side == 1 else "SHORT"  # 1=BUY, 2=SELL in cTrader
            
            # Try to identify if this could be part of a pairs trade
            # This is a simplified approach - in practice, you'd need better logic
            # to match individual positions back to pairs
            
            position_info = {
                'position_id': position_id,
                'position_id1': position_id,  # For backward compatibility with pairs logic
                'position_id2': None,  # Unknown for single positions
                'symbol1': symbol_name,
                'symbol2': None,  # Unknown for single positions
                'direction': direction,
                'volume1': volume / 100,  # Convert centilots to lots
                'volume2': 0,
                'entry_price': entry_price,
                'entry_price1': entry_price,
                'entry_price2': 0,
                'entry_time': datetime.fromtimestamp(open_timestamp / 1000) if open_timestamp else datetime.now(),
                'pair': symbol_name,  # Use symbol name as pair key for now
                'status': 'open',
                'broker_reconciled': True
            }
            
            return position_info
            
        except Exception as e:
            logger.error(f"Error converting broker position: {e}")
            return None
