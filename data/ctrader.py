import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.vector_ar.vecm import coint_johansen
import warnings
import os
import datetime
import time
import logging
from collections import deque, defaultdict
from config import TradingConfig, get_config, force_config_update



# cTrader Open API imports
try:
    from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
    from ctrader_open_api.messages import (
        OpenApiMessages_pb2,
        OpenApiModelMessages_pb2
    )
    from twisted.internet import reactor, defer
    import calendar
    
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
    
    # Additional imports for real-time trading
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
except ImportError as e:
    print(f"cTrader Open API not available: {e}")
    print("Install with: pip install ctrader-open-api")
    CTRADER_API_AVAILABLE = False

CONFIG = get_config()
# Setup logging
log_file_path = os.path.join(CONFIG.logs_dir, "ctrader_api.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)



# === CTRADER API SETUP ===
def load_ctrader_env(env_path=None):
    """Load .env file for cTrader API credentials. Only loads once per process."""
    from dotenv import load_dotenv
    if env_path is None:
        env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    load_dotenv(env_path, override=False)

def get_ctrader_credentials():
    """Get cTrader API credentials from environment variables."""
    creds = {
        'CTRADER_CLIENT_ID': os.getenv('CTRADER_CLIENT_ID'),
        'CTRADER_CLIENT_SECRET': os.getenv('CTRADER_CLIENT_SECRET'),
        'CTRADER_ACCESS_TOKEN': os.getenv('CTRADER_ACCESS_TOKEN'),
        'CTRADER_ACCOUNT_ID': os.getenv('CTRADER_ACCOUNT_ID'),
    }
    return creds

# Load .env only if not already loaded (for import safety)
if not os.getenv('CTRADER_CLIENT_ID'):
    load_ctrader_env()

creds = get_ctrader_credentials()
CTRADER_CLIENT_ID = creds['CTRADER_CLIENT_ID']
CTRADER_CLIENT_SECRET = creds['CTRADER_CLIENT_SECRET']
CTRADER_ACCESS_TOKEN = creds['CTRADER_ACCESS_TOKEN']
CTRADER_ACCOUNT_ID = creds['CTRADER_ACCOUNT_ID']

# Optional: print .env contents for debugging only if run as script
if __name__ == "__main__":
    if os.path.exists('.env'):
        with open('.env', 'r') as f:
            print("Contents of .env file:")
            for line_num, line in enumerate(f.readlines(), 1):
                if 'CTRADER_ACCOUNT_ID' in line:
                    print(f"  Line {line_num}: {line.strip()}")
    print("="*40)

# Global variable to store retrieved data
retrieved_data = {}
api_client = None
current_account_id = None

# === CTRADER AUTHENTICATION AND DATA RETRIEVAL ===
class CTraderDataManager:
    def __init__(self, config: TradingConfig):
        self.config = config
        self.client = None
        self.account_id = None
        self.access_token = None
        self.symbols_map = {}  # Map symbol names to IDs
        self.symbol_id_to_name_map = {} # Reverse map for performance
        self.symbol_details = {}  # <-- Add this line to always define symbol_details
        self.retrieved_data = {}
        self.pending_requests = 0
        self.deferred_result = None
        self.available_accounts = []
        self.timeout_count = 0
        self.max_retries = 3
        self.request_delay = 1.0  # Delay between requests
        
    def setup_client(self):
        """Setup cTrader client connection"""
        if not CTRADER_API_AVAILABLE:
            return None
            
        # Use demo environment for testing
        self.client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)
        self.access_token = CTRADER_ACCESS_TOKEN
        
        # Try to parse account ID, if not available we'll get it from API
        if CTRADER_ACCOUNT_ID and CTRADER_ACCOUNT_ID.strip():
            try:
                self.account_id = int(CTRADER_ACCOUNT_ID.strip())
                print(f"Using account ID: {self.account_id}")
            except ValueError as e:
                print(f"Invalid account ID format: '{CTRADER_ACCOUNT_ID}' - {e}")
                self.account_id = None
        else:
            print("No account ID provided, will attempt to get from API")
            self.account_id = None
        
        # Set up callbacks
        self.client.setConnectedCallback(self._on_connected)
        self.client.setDisconnectedCallback(self._on_disconnected)
        self.client.setMessageReceivedCallback(self._on_message_received)
        
        return self.client
    
    def _on_connected(self, client):
        print("Connected to cTrader API")
        # Authenticate application
        request = ProtoOAApplicationAuthReq()
        request.clientId = CTRADER_CLIENT_ID
        request.clientSecret = CTRADER_CLIENT_SECRET
        deferred = client.send(request)
        deferred.addErrback(self._on_error)
        # Add timeout to prevent hanging
        deferred.addTimeout(30, reactor)
    
    def _on_disconnected(self, client, reason):
        print(f"Disconnected from cTrader API: {reason}")

    def _on_error(self, failure):
        print(f"API Error: {failure}")
        self.timeout_count += 1
        
        # Check if it's a timeout error
        if 'TimeoutError' in str(failure) or 'timeout' in str(failure).lower():
            print(f"Request timeout occurred (attempt {self.timeout_count}/{self.max_retries})")
            
            if self.timeout_count < self.max_retries:
                print("Retrying in 5 seconds...")
                reactor.callLater(5, self._retry_connection)
                return
            else:
                print("Max retries reached. Giving up.")
        
        # Complete with empty data on error
        if self.deferred_result and not self.deferred_result.called:
            self.deferred_result.callback({})
        reactor.stop()
    
    def _retry_connection(self):
        """Retry the connection after a timeout"""
        print("Retrying connection...")
        try:
            # Reset state
            self.pending_requests = 0
            self.symbols_map = {}
            self.retrieved_data = {}
            
            # Try to reconnect
            if self.client:
                self.client.disconnect()
            
            # Setup new client
            client = self.setup_client()
            if client:
                client.startService()
            else:
                print("Failed to setup client for retry")
                if self.deferred_result and not self.deferred_result.called:
                    self.deferred_result.callback({})
                reactor.stop()
                
        except Exception as e:
            print(f"Error during retry: {e}")
            if self.deferred_result and not self.deferred_result.called:
                self.deferred_result.callback({})
            reactor.stop()
    
    def _authenticate_account(self):
        """Authenticate trading account"""
        request = ProtoOAAccountAuthReq()
        request.ctidTraderAccountId = self.account_id
        request.accessToken = self.access_token
        deferred = self.client.send(request)
        deferred.addErrback(self._on_error)
        deferred.addTimeout(30, reactor)
    
    def _get_account_list(self):
        """Get list of available accounts"""
        print("Getting account list...")
        request = OpenApiMessages_pb2.ProtoOAGetAccountListByAccessTokenReq()
        request.accessToken = self.access_token
        deferred = self.client.send(request)
        deferred.addErrback(self._on_error)
        deferred.addTimeout(30, reactor)
    
    def _process_account_list(self, message):
        """Process account list response"""
        response = Protobuf.extract(message)
        print("Available accounts:")
        for account in response.ctidTraderAccount:
            print(f"  Account ID: {account.ctidTraderAccountId}")
            print(f"  Broker: {account.brokerName}")
            print(f"  Live: {'Yes' if account.live else 'No'}")
            print(f"  Deposit Currency: {account.depositCurrency}")
            self.available_accounts.append(account.ctidTraderAccountId)
        
        if self.available_accounts:
            # Use the first available account
            self.account_id = self.available_accounts[0]
            print(f"Using first available account: {self.account_id}")
            self._authenticate_account()
        else:
            print("No accounts available")
            if self.deferred_result and not self.deferred_result.called:
                self.deferred_result.callback({})
            reactor.stop()
    
    def _get_symbols_list(self):
        """Get list of available symbols"""
        request = ProtoOASymbolsListReq()
        request.ctidTraderAccountId = self.account_id
        request.includeArchivedSymbols = False
        deferred = self.client.send(request)
        deferred.addErrback(self._on_error)
        deferred.addTimeout(30, reactor)
    
    def _process_symbols_list(self, message):
        """Process symbols list response"""
        response = Protobuf.extract(message)
        for symbol in response.symbol:
            # Map symbol name to ID and vice-versa
            self.symbols_map[symbol.symbolName] = symbol.symbolId
            self.symbol_id_to_name_map[symbol.symbolId] = symbol.symbolName
        
        print(f"Loaded {len(self.symbols_map)} symbols")
        
        # Check if our target symbols are available
        missing_symbols = []
        for symbol in self.target_symbols:
            if symbol not in self.symbols_map:
                missing_symbols.append(symbol)
        
        if missing_symbols:
            print(f"Warning: These symbols were not found: {missing_symbols}")
            
            # Try to find similar symbols
            for missing in missing_symbols:
                similar = [s for s in list(self.symbols_map.keys())[:20] if missing.lower() in s.lower() or s.lower() in missing.lower()]
                if similar:
                    print(f"  Similar to '{missing}': {similar[:5]}")
        
        # Start requesting data for each symbol
        self._request_historical_data()
    
    def _request_historical_data(self):
        """Request historical data for all symbols"""
        if not hasattr(self, 'target_symbols'):
            return
            
        # Calculate time range
        start_dt = pd.to_datetime(self.config.START)
        end_dt = pd.to_datetime(self.config.END) if self.config.END else pd.Timestamp.now()
        
        from_timestamp = int(calendar.timegm(start_dt.utctimetuple())) * 1000
        to_timestamp = int(calendar.timegm(end_dt.utctimetuple())) * 1000
        
        # Map interval to cTrader period
        period_map = {
            "1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1", "4h": "H4", "1d": "D1"
        }
        period = period_map.get(self.config.INTERVAL, "M15")
        
        # Filter symbols to only those available
        available_symbols = [s for s in self.target_symbols if s in self.symbols_map]
        print(f"Requesting data for {len(available_symbols)} available symbols out of {len(self.target_symbols)} requested")
        
        if not available_symbols:
            print("No symbols available for data request")
            self._complete_data_retrieval()
            return
        
        self.pending_requests = len(available_symbols)
        
        # Request data for symbols one by one with delays to avoid rate limiting
        for i, symbol in enumerate(available_symbols):
            reactor.callLater(i * self.request_delay, self._request_symbol_data, 
                            symbol, from_timestamp, to_timestamp, period)
    
    def _request_symbol_data(self, symbol, from_timestamp, to_timestamp, period):
        """Request data for a single symbol"""
        if symbol in self.symbols_map:
            symbol_id = self.symbols_map[symbol]
            print(f"Requesting data for {symbol} (ID: {symbol_id})")
            
            request = ProtoOAGetTrendbarsReq()
            request.ctidTraderAccountId = self.account_id
            request.symbolId = symbol_id
            request.period = ProtoOATrendbarPeriod.Value(period)
            request.fromTimestamp = from_timestamp
            request.toTimestamp = to_timestamp
            
            deferred = self.client.send(request)
            deferred.addErrback(self._on_data_error)
            deferred.addTimeout(60, reactor)  # Longer timeout for data requests
        else:
            print(f"Symbol {symbol} not found in available symbols")
            self.retrieved_data[symbol] = pd.Series(dtype=float)
            self.pending_requests -= 1
            if self.pending_requests <= 0:
                self._complete_data_retrieval()
    
    def _on_data_error(self, failure):
        """Handle errors for individual data requests"""
        print(f"Data request error: {failure}")
        self.pending_requests -= 1
        
        # Continue with remaining requests even if one fails
        if self.pending_requests <= 0:
            self._complete_data_retrieval()
    
    def _process_trendbar_data(self, message):
        """Process trendbar data response"""
        response = Protobuf.extract(message)
        
        # Find symbol name from ID using the reverse map
        symbol_name = self.symbol_id_to_name_map.get(response.symbolId)
        
        if not symbol_name:
            print(f"Unknown symbol ID: {response.symbolId}")
            self.pending_requests -= 1
            return
        
        # Extract price data
        closes = []
        timestamps = []
        
        print(f"Debug: Processing trendbar data for {symbol_name}")
        print(f"Debug: Response has {len(response.trendbar) if hasattr(response, 'trendbar') else 0} bars")
        
        if hasattr(response, 'trendbar') and len(response.trendbar) > 0:
            # Debug: Print first bar structure
            first_bar = response.trendbar[0]
            
            for bar in response.trendbar:
                try:
                    # Try different possible attribute names for close price
                    close_price = None
                    if hasattr(bar, 'close'):
                        close_price = bar.close
                    elif hasattr(bar, 'closePrice'):
                        close_price = bar.closePrice
                    elif hasattr(bar, 'c'):
                        close_price = bar.c
                    elif hasattr(bar, 'low'):  # If close doesn't exist, try using low as fallback
                        close_price = bar.low
                    
                    if close_price is not None:
                        # Adjust for pip factor if available
                        pip_factor = getattr(response, 'pipFactor', 0)
                        adjusted_price = close_price / (10 ** pip_factor) if pip_factor > 0 else close_price
                        closes.append(adjusted_price)
                        
                        # Try different timestamp attribute names
                        timestamp = None
                        if hasattr(bar, 'utcTimestampInMinutes'):
                            timestamp = pd.to_datetime(bar.utcTimestampInMinutes * 60, unit='s')
                        elif hasattr(bar, 'timestamp'):
                            timestamp = pd.to_datetime(bar.timestamp, unit='ms')
                        elif hasattr(bar, 'time'):
                            timestamp = pd.to_datetime(bar.time, unit='s')
                        
                        if timestamp:
                            timestamps.append(timestamp)
                        else:
                            print(f"Debug: No timestamp found for bar")
                            break
                    else:
                        print(f"Debug: No close price found for bar. Available attributes: {[attr for attr in dir(bar) if not attr.startswith('_')]}")
                        break
                        
                except Exception as e:
                    print(f"Debug: Error processing bar: {e}")
                    break
        else:
            print(f"Debug: No trendbar data in response for {symbol_name}")
            if hasattr(response, 'trendbar'):
                print(f"Debug: trendbar length: {len(response.trendbar)}")
            else:
                print(f"Debug: Response attributes: {[attr for attr in dir(response) if not attr.startswith('_')]}")
        
        if closes and timestamps and len(closes) == len(timestamps):
            series = pd.Series(closes, index=timestamps)
            self.retrieved_data[symbol_name] = series
            print(f"Retrieved {len(series)} bars for {symbol_name}")
        else:
            print(f"No valid data for {symbol_name} - closes: {len(closes)}, timestamps: {len(timestamps)}")
            self.retrieved_data[symbol_name] = pd.Series(dtype=float)
        
        self.pending_requests -= 1
        if self.pending_requests <= 0:
            self._complete_data_retrieval()
    def _complete_data_retrieval(self):
        """Complete data retrieval process"""
        print("Data retrieval completed")
        if self.deferred_result and not self.deferred_result.called:
            self.deferred_result.callback(self.retrieved_data)
        reactor.stop()
    
    def get_historical_data(self, symbols, interval, start, end=None):
        """Get historical data for symbols"""
        self.target_symbols = symbols
        self.deferred_result = defer.Deferred()
        
        if not CTRADER_API_AVAILABLE:
            print("cTrader API not available, returning empty data")
            return {symbol: pd.Series(dtype=float) for symbol in symbols}
        
        # Setup client first to initialize account_id
        client = self.setup_client()
        if not client:
            print("Failed to setup client")
            return {symbol: pd.Series(dtype=float) for symbol in symbols}
        
        # Check if we have account ID after setup
        if not self.account_id:
            print("No account ID available after setup - will try to get from API")
        
        try:
            # Start the reactor to handle API communication
            client.startService()
            
            # Add a global timeout for the entire operation
            timeout_deferred = reactor.callLater(300, self._global_timeout)  # 5 minute timeout
            
            def cleanup_timeout(result):
                if timeout_deferred.active():
                    timeout_deferred.cancel()
                return result
            
            self.deferred_result.addBoth(cleanup_timeout)
            reactor.run()
            
        except Exception as e:
            print(f"Exception during data retrieval: {e}")
            return {symbol: pd.Series(dtype=float) for symbol in symbols}
        
        return self.retrieved_data
    
    def _global_timeout(self):
        """Handle global timeout for the entire operation"""
        print("Global timeout reached. Stopping data retrieval.")
        if self.deferred_result and not self.deferred_result.called:
            self.deferred_result.callback(self.retrieved_data)
        reactor.stop()

    @staticmethod
    def get_data(symbols, interval, start, end=None):
        """Download data from cTrader Open API"""
        print(f"Downloading data for symbols: {symbols}")

        if not CTRADER_API_AVAILABLE:
            print("cTrader Open API not available. Please install: pip install ctrader-open-api")
            return {symbol: pd.Series(dtype=float) for symbol in symbols}

        # Check credentials with detailed feedback
        missing_creds = []
        if not CTRADER_CLIENT_ID:
            missing_creds.append("CTRADER_CLIENT_ID")
        if not CTRADER_CLIENT_SECRET:
            missing_creds.append("CTRADER_CLIENT_SECRET")
        if not CTRADER_ACCESS_TOKEN:
            missing_creds.append("CTRADER_ACCESS_TOKEN")
        # Don't require ACCOUNT_ID here since we can get it from API

        if missing_creds:
            print("Missing cTrader API credentials in .env file:")
            for cred in missing_creds:
                print(f"  - {cred}")
            print("\nPlease add these to your .env file:")
            print("CTRADER_CLIENT_ID=your_client_id")
            print("CTRADER_CLIENT_SECRET=your_client_secret")
            print("CTRADER_ACCESS_TOKEN=your_access_token")
            print("CTRADER_ACCOUNT_ID=your_account_id")
            return {symbol: pd.Series(dtype=float) for symbol in symbols}

        try:
            config = get_config()
            retriever = CTraderDataManager(config)
            return retriever.get_historical_data(symbols, interval, start, end)
        except Exception as e:
            print(f"Error retrieving data: {e}")
            import traceback
            traceback.print_exc()
            return {symbol: pd.Series(dtype=float) for symbol in symbols}

    # Update CTraderDataRetriever._on_message_received to handle real-time trading messages
    def _on_message_received(self, client, message):
        if message.payloadType == ProtoOAApplicationAuthRes().payloadType:
            print("API Application authorized")
            if self.account_id:
                self._authenticate_account()
            else:
                # Get list of available accounts first
                self._get_account_list()
        elif message.payloadType == OpenApiMessages_pb2.ProtoOAGetAccountListByAccessTokenRes().payloadType:
            self._process_account_list(message)
        elif message.payloadType == ProtoOAAccountAuthRes().payloadType:
            print(f"Account {self.account_id} authorized")
            self._get_symbols_list()
        elif message.payloadType == ProtoOASymbolsListRes().payloadType:
            self._process_symbols_list(message)
        elif message.payloadType == ProtoOAGetTrendbarsRes().payloadType:
            self._process_trendbar_data(message)
        # elif message.payloadType == ProtoOASpotEvent().payloadType:
        #     # Handle real-time price updates
        #     if hasattr(self, 'trading_engine') and self.trading_engine:
        #         self.trading_engine.process_spot_event(Protobuf.extract(message))
        elif message.payloadType == ProtoOAExecutionEvent().payloadType:
            # Handle order execution events
            if hasattr(self, 'trading_engine') and self.trading_engine:
                self.trading_engine.process_execution_event(Protobuf.extract(message))
        elif message.payloadType == ProtoOASubscribeSpotsRes().payloadType:
            # Handle subscription confirmation
            response = Protobuf.extract(message)
            logger.info("Received subscription confirmation response")
            
            # The response doesn't contain symbol details, but we can log the success
            if self.trading_engine:
                # Count how many symbols we attempted to subscribe to
                pending_symbols = set()
                for pair_str in self.config.pairs:
                    s1, s2 = pair_str.split('-')
                    if s1 in self.symbols_map:
                        pending_symbols.add(s1)
                    if s2 in self.symbols_map:
                        pending_symbols.add(s2)
                
                logger.info(f"Spot subscription confirmed - monitoring {len(pending_symbols)} symbols for price updates")
                logger.info(f"Subscribed symbols: {', '.join(sorted(pending_symbols))}")
                
                # Mark all pending symbols as subscribed since we got a success response
                for symbol in pending_symbols:
                    if symbol not in self.trading_engine.subscribed_symbols:
                        self.trading_engine.subscribed_symbols.add(symbol)
                        logger.debug(f"Confirmed subscription for {symbol}")