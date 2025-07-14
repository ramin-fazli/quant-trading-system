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
        
        # Validate credentials
        if not all([self.client_id, self.client_secret, self.access_token]):
            logger.error("Missing cTrader credentials. Check CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET, and CTRADER_ACCESS_TOKEN")
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
            host = EndPoints.PROTOBUF_LIVE_HOST
            port = EndPoints.PROTOBUF_PORT
            
            self.client = Client(host, port, TcpProtocol)
            self.client.setConnectedCallback(self._on_connected)
            self.client.setDisconnectedCallback(self._on_disconnected)
            self.client.setMessageReceivedCallback(self._on_message_received)
            
            # Start the client
            d = self.client.startService()
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
        request = ProtoOAApplicationAuthReq()
        request.clientId = self.client_id
        request.clientSecret = self.client_secret
        
        deferred = self.client.send(request)
        deferred.addErrback(self._on_error)
    
    def _authenticate_account(self):
        """Authenticate the trading account"""
        request = ProtoOAAccountAuthReq()
        request.ctidTraderAccountId = self.account_id
        request.accessToken = self.access_token
        
        deferred = self.client.send(request)
        deferred.addErrback(self._on_error)
    
    def _get_symbols_list(self):
        """Get the list of available symbols"""
        request = ProtoOASymbolsListReq()
        request.ctidTraderAccountId = self.account_id
        
        deferred = self.client.send(request)
        deferred.addErrback(self._on_error)
    
    def _on_message_received(self, client, message):
        """Handle incoming messages from cTrader API"""
        try:
            if message.payloadType == ProtoOAApplicationAuthRes().payloadType:
                logger.info("Application authenticated with cTrader")
                self._authenticate_account()
                
            elif message.payloadType == ProtoOAAccountAuthRes().payloadType:
                logger.info("Account authenticated with cTrader")
                self._get_symbols_list()
                
            elif message.payloadType == ProtoOASymbolsListRes().payloadType:
                self._process_symbols_list(message)
                
            elif message.payloadType == ProtoOAGetTrendbarsRes().payloadType:
                self._process_trendbar_data(message)
                
            elif message.payloadType == ProtoOASpotEvent().payloadType:
                self._process_spot_event(message.payload)
                
            elif message.payloadType == ProtoOAExecutionEvent().payloadType:
                self._process_execution_event(message.payload)
                
            elif message.payloadType == ProtoOASubscribeSpotsRes().payloadType:
                logger.debug("Spot subscription confirmed")
                
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    def _process_symbols_list(self, message):
        """Process the symbols list and initialize trading pairs"""
        for symbol in message.payload.symbol:
            symbol_name = symbol.symbolName
            symbol_id = symbol.symbolId
            
            self.symbols_map[symbol_name] = symbol_id
            self.symbol_id_to_name_map[symbol_id] = symbol_name
            
            # Store symbol details
            self.symbol_details[symbol_name] = {
                'digits': symbol.digits,
                'pip_factor': getattr(symbol, 'pipFactor', 0),
                'min_volume': getattr(symbol, 'minVolume', 1000),
                'max_volume': getattr(symbol, 'maxVolume', 100000000),
                'volume_step': getattr(symbol, 'stepVolume', 1000),
            }
        
        logger.info(f"Retrieved {len(self.symbols_map)} symbols from cTrader")
        
        # Initialize pair states
        self._initialize_pair_states()
        
        # Subscribe to real-time data
        self._subscribe_to_data()
    
    def _initialize_pair_states(self):
        """Initialize pair states for trading"""
        # Extract unique symbols from configured pairs
        all_symbols = set()
        valid_pairs = []
        
        for pair_str in self.config.pairs:
            s1, s2 = pair_str.split('-')
            if s1 in self.symbols_map and s2 in self.symbols_map:
                all_symbols.add(s1)
                all_symbols.add(s2)
                valid_pairs.append(pair_str)
            else:
                logger.warning(f"Pair {pair_str} skipped - symbols not available in cTrader")
        
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
    
    def _subscribe_to_data(self):
        """Subscribe to real-time data for all required symbols"""
        all_symbols = set()
        for pair_str in self.pair_states.keys():
            s1, s2 = pair_str.split('-')
            all_symbols.add(s1)
            all_symbols.add(s2)
        
        # Subscribe to spot prices
        for symbol in all_symbols:
            self._subscribe_to_spot_prices(symbol)
        
        logger.info(f"Subscribed to real-time data for {len(all_symbols)} symbols")
    
    def _subscribe_to_spot_prices(self, symbol):
        """Subscribe to real-time price updates for a symbol"""
        if symbol in self.symbols_map and symbol not in self.subscribed_symbols:
            symbol_id = self.symbols_map[symbol]
            
            request = ProtoOASubscribeSpotsReq()
            request.ctidTraderAccountId = self.account_id
            request.symbolId.append(symbol_id)
            
            try:
                deferred = self.client.send(request)
                deferred.addErrback(self._on_subscription_error)
                self.subscribed_symbols.add(symbol)
                logger.debug(f"Subscribed to spot prices for {symbol}")
                return True
            except Exception as e:
                logger.error(f"Error subscribing to {symbol}: {e}")
                return False
        return False
    
    def _on_subscription_error(self, failure):
        """Handle subscription errors"""
        logger.error(f"Subscription error: {failure}")
    
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
                
                # Process entry signals
                if current_position is None and not has_active_position:
                    if z_score > z_entry_threshold:
                        logger.info(f"SHORT entry signal for {pair_str}, z-score: {z_score:.2f}")
                        self._execute_pair_trade(pair_str, 'SHORT')
                    elif z_score < -z_entry_threshold:
                        logger.info(f"LONG entry signal for {pair_str}, z-score: {z_score:.2f}")
                        self._execute_pair_trade(pair_str, 'LONG')
                
                # Process exit signals
                elif current_position is not None or has_active_position:
                    if (current_position == 'LONG' and z_score > -z_exit_threshold) or \
                       (current_position == 'SHORT' and z_score < z_exit_threshold):
                        logger.info(f"Exit signal for {pair_str} {current_position}, z-score: {z_score:.2f}")
                        self._close_pair_position(pair_str)
                        
        except Exception as e:
            logger.error(f"Error in trading signal check: {e}")
    
    def _execute_pair_trade(self, pair_str: str, direction: str) -> bool:
        """Execute a pairs trade"""
        if not self._check_drawdown_limits(pair_str):
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
            return False
        
        volume1, volume2, _, _ = volumes
        
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
            
            logger.info(f"Executed {direction} trade for {pair_str}")
            return True
        
        return False
    
    def _close_pair_position(self, pair_str: str) -> bool:
        """Close a pair position"""
        if pair_str not in self.active_positions:
            return False
        
        position = self.active_positions[pair_str]
        state = self.pair_states[pair_str]
        
        s1, s2 = position['symbol1'], position['symbol2']
        direction = position['direction']
        
        # Determine closing sides (opposite of opening)
        if direction == 'LONG':
            close_side1, close_side2 = ProtoOATradeSide.SELL, ProtoOATradeSide.BUY
        else:
            close_side1, close_side2 = ProtoOATradeSide.BUY, ProtoOATradeSide.SELL
        
        # Close positions
        self._send_market_order(s1, close_side1, None, is_close=True)
        self._send_market_order(s2, close_side2, None, is_close=True)
        
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
        
        request = ProtoOANewOrderReq()
        request.ctidTraderAccountId = self.account_id
        request.symbolId = symbol_id
        request.orderType = ProtoOAOrderType.MARKET
        request.tradeSide = side
        request.clientOrderId = client_order_id
        
        if is_close:
            # Close all positions for the symbol
            request.closePositionDetails.SetInParent()
        else:
            # Convert volume to broker format (typically in units, not lots)
            broker_volume = int(volume * 100000)  # Adjust based on broker requirements
            request.volume = broker_volume
        
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
        if client_order_id in self.execution_requests:
            order_data = self.execution_requests[client_order_id]
            
            # Process execution details
            if hasattr(event, 'executionType'):
                execution_type = event.executionType
                logger.info(f"Order {client_order_id} execution: {execution_type}")
            
            # Clean up completed orders
            if hasattr(event, 'orderStatus') and event.orderStatus in ['FILLED', 'CANCELLED', 'REJECTED']:
                del self.execution_requests[client_order_id]
    
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
        
        # The reactor will be started by the main thread or callback
        # Just ensure the client is ready to receive messages
        logger.info("cTrader trader ready for real-time trading")
    
    def stop_trading(self):
        """Stop real-time trading"""
        self.is_trading = False
        
        # Close all positions
        for pair_str in list(self.active_positions.keys()):
            self._close_pair_position(pair_str)
        
        # Unsubscribe from data
        for symbol in list(self.subscribed_symbols):
            self._unsubscribe_from_spots(symbol)
        
        # Disconnect client
        if self.client:
            self.client.disconnect()
        
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
        
        return {
            'portfolio_value': portfolio_value + unrealized_pnl,
            'unrealized_pnl': unrealized_pnl,
            'position_count': total_positions,
            'total_exposure': total_exposure,
            'positions': positions,
            'account_currency': self.account_currency,
            'broker': 'ctrader'
        }
