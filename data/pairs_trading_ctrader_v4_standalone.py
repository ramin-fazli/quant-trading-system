import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.vector_ar.vecm import coint_johansen
import warnings
import os
from dotenv import load_dotenv
import datetime
import time
import logging
from collections import deque, defaultdict

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

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("pairs_trading.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("PairsTrading")

warnings.filterwarnings("ignore")

# === PARAMETERS ===
pairs = [
    # Tech & Growth
    "AMZN.US-GOOG.US",
    "AAPL.US-MSFT.US",
    "NVDA.US-AMD.US",
    "META.US-GOOG.US",
    "CRM.US-ADBE.US",
    "ORCL.US-IBM.US",
    "INTC.US-QCOM.US",
    "SHOP.US-ETSY.US",

    # Automotive & EVs
    "TSLA.US-F.US",
    "TSLA.US-GM.US",
    "F.US-GM.US",
    "RIVN.US-LCID.US",

    # Banks & Financials
    "JPM.US-BAC.US",
    "GS.US-MS.US",
    "C.US-WFC.US",
    "V.US-MA.US",
    "AXP.US-COF.US",

    # Energy & Commodities
    "XOM.US-CVX.US",
    "COP.US-EOG.US",
    "SLB.US-HAL.US",
    "GDX.US-GDXJ.US",
    "USO.US-UNG.US",
    "XLE.US-XOP.US",
    "XAUUSD-XAGUSD",
    "XAUUSD-XPTUSD", 
    "XAUUSD-XPDUSD", 
    "XAUUSD-XAUEUR",
    "XAGUSD-XAGEUR",   
    "XAUUSD-Copper",

    # Consumer & Retail
    "KO.US-PEP.US",
    "HD.US-LOW.US",
    "MCD.US-YUM.US",
    "DIS.US-CMG.US",

    # Healthcare & Pharma
    "UNH.US-HUM.US",
    "PFE.US-MRK.US",
    "JNJ.US-LLY.US",
    "ABT.US-BDX.US",
    "CVS.US-WBA.US",

    # Industrials & Transports
    "CAT.US-DE.US",
    "BA.US-LMT.US",
    "FDX.US-UPS.US",
    "GE.US-HON.US",
    "MMM.US-EMR.US",

    # Utilities & Staples
    "NEE.US-D.US",
    "PG.US-CL.US",
    "KMB.US-CHD.US",

    # Index ETFs
    "SPY.US-QQQ.US",
    "SPY.US-IWM.US",
    "DIA.US-SPY.US",

    # International ETFs
    "EFA.US-EEM.US",
    "FXI.US-EEM.US",

    # Volatility & Bonds
    "TLT.US-IEF.US",
    "VIXY.US-SVXY.US",

    # Top Crypto Pairs
    "BTCUSD-ETHUSD",
    "BTCUSD-SOLUSD",
    "ETHUSD-SOLUSD",
    "BTCUSD-XRPUSD",
    "ETHUSD-XRPUSD",
]
INTERVAL = "15m"
START = "2025-06-01"
END = None
Z_PERIOD = 100
CORR_PERIOD = 100
ADF_PERIOD = 100
Z_ENTRY = 2.0
Z_EXIT = 0.5
VOL_RATIO_MAX = 100
VOL_RATIO_TF = "1d"
MIN_CORR = -100
MAX_ADF_PVAL = 1
JOHANSEN_CRIT_LEVEL = 90
TAKE_PROFIT_PERC = 10
STOP_LOSS_PERC = 10
TRAILING_STOP_PERC = 1.0
MIN_SPREAD = 0
MIN_VOLATILITY = 1e-6
COOLDOWN_BARS = 0
DYNAMIC_Z = False
COMMISSION_PERC = 0.1

# === CTRADER API SETUP ===
load_dotenv()

CTRADER_CLIENT_ID = os.getenv("CTRADER_CLIENT_ID")
CTRADER_CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET")
CTRADER_ACCESS_TOKEN = os.getenv("CTRADER_ACCESS_TOKEN")
CTRADER_ACCOUNT_ID = os.getenv("CTRADER_ACCOUNT_ID")  # Account ID needed for API calls

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
class CTraderDataRetriever:
    def __init__(self):
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
        elif message.payloadType == ProtoOASpotEvent().payloadType:
            # Handle real-time price updates
            if hasattr(self, 'trading_engine') and self.trading_engine:
                self.trading_engine.process_spot_event(Protobuf.extract(message))
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
                for pair_str in pairs:
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
        start_dt = pd.to_datetime(START)
        end_dt = pd.to_datetime(END) if END else pd.Timestamp.now()
        
        from_timestamp = int(calendar.timegm(start_dt.utctimetuple())) * 1000
        to_timestamp = int(calendar.timegm(end_dt.utctimetuple())) * 1000
        
        # Map interval to cTrader period
        period_map = {
            "1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1", "4h": "H4", "1d": "D1"
        }
        period = period_map.get(INTERVAL, "M15")
        
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

# === DATA DOWNLOAD ===
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
        retriever = CTraderDataRetriever()
        return retriever.get_historical_data(symbols, interval, start, end)
    except Exception as e:
        print(f"Error retrieving data: {e}")
        import traceback
        traceback.print_exc()
        return {symbol: pd.Series(dtype=float) for symbol in symbols}

# === STRATEGY LOGIC ===
def run_pair_strategy(price1, price2):
    # ...existing code for rolling_adf, rolling_johansen_stat...
    def rolling_adf(series, window):
        pvals = np.full(len(series), np.nan)
        for i in range(window, len(series)):
            try:
                pvals[i] = adfuller(series[i-window+1:i+1])[1]
            except:
                pvals[i] = np.nan
        return pvals

    def rolling_johansen_stat(series1, series2, window, crit_level=95):
        stats = np.full(len(series1), np.nan)
        crit_vals = np.full(len(series1), np.nan)
        crit_idx = {90: 0, 95: 1, 99: 2}[crit_level]
        for i in range(window, len(series1)):
            data = np.column_stack([series1[i-window+1:i+1], series2[i-window+1:i+1]])
            try:
                johansen_result = coint_johansen(data, det_order=0, k_ar_diff=1)
                stats[i] = johansen_result.lr1[0]
                crit_vals[i] = johansen_result.cvt[0, crit_idx]
            except Exception:
                stats[i] = np.nan
                crit_vals[i] = np.nan
        return stats, crit_vals

    df = pd.concat([price1, price2], axis=1).dropna()
    df.columns = ['price1', 'price2']
    ratio = df['price1'] / df['price2']
    ratio_ma = ratio.rolling(Z_PERIOD).mean()
    ratio_std = ratio.rolling(Z_PERIOD).std()
    zscore = (ratio - ratio_ma) / ratio_std
    # Calculate volatility as rolling standard deviation of the ratio
    volatility = ratio.rolling(Z_PERIOD).std()

    corr = df['price1'].rolling(CORR_PERIOD).corr(df['price2'])
    
    # Optimized volatility calculation
    price1_rolling_mean = df['price1'].rolling(Z_PERIOD).mean()
    price1_rolling_std = df['price1'].rolling(Z_PERIOD).std()
    price2_rolling_mean = df['price2'].rolling(Z_PERIOD).mean()
    price2_rolling_std = df['price2'].rolling(Z_PERIOD).std()
    
    vol1 = price1_rolling_std / price1_rolling_mean
    vol2 = price2_rolling_std / price2_rolling_mean
    
    vol_ratio = np.maximum(vol1, vol2) / np.minimum(vol1, vol2)
    vol_ratio_ok = vol_ratio <= VOL_RATIO_MAX


    dyn_z_entry = np.where(DYNAMIC_Z, np.maximum(2, 2 * volatility / MIN_VOLATILITY), Z_ENTRY)
    dyn_z_exit = np.where(DYNAMIC_Z, np.maximum(0.5, 0.5 * volatility / MIN_VOLATILITY), Z_EXIT)
    adf_pval = rolling_adf(ratio.values, ADF_PERIOD)
    johansen_stats, johansen_crit = rolling_johansen_stat(
        df['price1'].values, df['price2'].values, ADF_PERIOD, crit_level=JOHANSEN_CRIT_LEVEL
    )
    johansen_pass = True #(johansen_stats > johansen_crit)
    spread_ok = np.abs(ratio - ratio_ma) > MIN_SPREAD
    vol_ok = volatility > MIN_VOLATILITY
    
    # Use all suitability conditions
    suitable = (corr > MIN_CORR) & (adf_pval < MAX_ADF_PVAL) & johansen_pass & spread_ok & vol_ok & vol_ratio_ok

    signals = pd.DataFrame(index=df.index)
    signals['zscore'] = zscore
    signals['suitable'] = suitable
    signals['long_entry'] = (zscore.shift(1) < -dyn_z_entry) & (zscore >= -dyn_z_entry) & suitable
    signals['short_entry'] = (zscore.shift(1) > dyn_z_entry) & (zscore <= dyn_z_entry) & suitable
    signals['exit_long'] = (zscore.shift(1) < -dyn_z_exit) & (zscore >= -dyn_z_exit)
    signals['exit_short'] = (zscore.shift(1) > dyn_z_exit) & (zscore <= dyn_z_exit)

    position = 0
    entry_price = 0
    cooldown = 0
    equity = [100.0]
    max_equity = 100.0
    max_drawdown = 0.0
    trades = []
    for i in range(len(signals)):
        if i == 0:
            equity.append(equity[-1])
            continue
        if cooldown > 0:
            cooldown -= 1
        if position == 0 and cooldown == 0:
            if signals['long_entry'].iloc[i]:
                position = 1
                entry_price = ratio.iloc[i]
                cooldown = COOLDOWN_BARS
                trade = {'type': 'long', 'entry': i, 'entry_price': entry_price}
            elif signals['short_entry'].iloc[i]:
                position = -1
                entry_price = ratio.iloc[i]
                cooldown = COOLDOWN_BARS
                trade = {'type': 'short', 'entry': i, 'entry_price': entry_price}
        if position == 1:
            ret = (ratio.iloc[i] - entry_price) / entry_price
            tp = ret >= TAKE_PROFIT_PERC / 100
            sl = ret <= -STOP_LOSS_PERC / 100
            exit_signal = signals['exit_long'].iloc[i] or tp or sl
            if exit_signal:
                ret -= COMMISSION_PERC / 100
                equity.append(equity[-1] * (1 + ret))
                max_equity = max(max_equity, equity[-1])
                max_drawdown = min(max_drawdown, (equity[-1] - max_equity) / max_equity * 100)
                trade.update({'exit': i, 'exit_price': ratio.iloc[i], 'ret': ret})
                trades.append(trade)
                position = 0
                entry_price = 0
                continue
        elif position == -1:
            ret = (entry_price - ratio.iloc[i]) / entry_price
            tp = ret >= TAKE_PROFIT_PERC / 100
            sl = ret <= -STOP_LOSS_PERC / 100
            exit_signal = signals['exit_short'].iloc[i] or tp or sl
            if exit_signal:
                ret -= COMMISSION_PERC / 100
                equity.append(equity[-1] * (1 + ret))
                max_equity = max(max_equity, equity[-1])
                max_drawdown = min(max_drawdown, (equity[-1] - max_equity) / max_equity * 100)
                trade.update({'exit': i, 'exit_price': ratio.iloc[i], 'ret': ret})
                trades.append(trade)
                position = 0
                entry_price = 0
                continue
        equity.append(equity[-1])
    equity = np.array(equity[1:])
    return {
        'signals': signals,
        'trades': trades,
        'equity': equity,
        'max_drawdown': max_drawdown,
        'df': df,
        'ratio': ratio,
        'ratio_ma': ratio_ma,
        'zscore': zscore,
        'adf_pval': adf_pval,
        'johansen_stats': johansen_stats,
        'johansen_crit': johansen_crit
    }

# === MAIN MULTI-PAIR LOGIC ===
def run_backtest():
    """Runs the backtesting process and generates a report."""
    # 1. Parse all unique symbols
    all_symbols = set()
    pair_tuples = []
    for p in pairs:
        s1, s2 = p.split('-')
        pair_tuples.append((s1, s2))
        all_symbols.add(s1)
        all_symbols.add(s2)
    all_symbols = list(all_symbols)

    # 2. Download all data at once
    series_dict = get_data(all_symbols, INTERVAL, START, END)

    # 3. Ensure all requested symbols exist in series_dict (even if empty)
    for symbol in all_symbols:
        if symbol not in series_dict:
            print(f"Warning: Symbol {symbol} not found in retrieved data, adding empty series")
            series_dict[symbol] = pd.Series(dtype=float)

    # 4. Run strategy for each pair
    pair_results = []
    successful_pairs = 0
    skipped_pairs = 0

    for s1, s2 in pair_tuples:
        try:
            # Safely get the price series - this line was causing the KeyError
            price1 = series_dict.get(s1, pd.Series(dtype=float))
            price2 = series_dict.get(s2, pd.Series(dtype=float))
            
            # Skip pair if either symbol has no data
            if price1.empty or price2.empty:
                print(f"Skipping pair {s1}-{s2} due to missing data.")
                skipped_pairs += 1
                continue
                
            # Check if we have sufficient data points
            if len(price1) < Z_PERIOD or len(price2) < Z_PERIOD:
                print(f"Skipping pair {s1}-{s2} due to insufficient data (need at least {Z_PERIOD} points).")
                skipped_pairs += 1
                continue
            
            price1.name = 'price1'
            price2.name = 'price2'
            
            result = run_pair_strategy(price1, price2)
            result['pair'] = f"{s1}-{s2}"
            pair_results.append(result)
            successful_pairs += 1
            
        except KeyError as e:
            print(f"KeyError for pair {s1}-{s2}: Symbol {e} not found in data")
            skipped_pairs += 1
            continue
        except Exception as e:
            print(f"Error processing pair {s1}-{s2}: {e}")
            skipped_pairs += 1
            continue

    print(f"\nPair Processing Summary:")
    print(f"  Total pairs requested: {len(pair_tuples)}")
    print(f"  Successfully processed: {successful_pairs}")
    print(f"  Skipped due to missing/insufficient data: {skipped_pairs}")

    # 5. Aggregate results
    all_trades = []
    total_equity = np.array([])
    total_max_drawdown = 0

    if pair_results:
        for res in pair_results:
            all_trades.extend(res['trades'])
        
        # Align all equity curves to a common time index for accurate portfolio calculation
        all_indices = pd.Index([])
        for res in pair_results:
            if not res['df'].empty:
                all_indices = all_indices.union(res['df'].index)
        all_indices = all_indices.sort_values()

        if not all_indices.empty:
            equity_matrix = []
            for res in pair_results:
                if not res['df'].empty and len(res['equity']) > 0:
                    # Create a Series with the correct index, then reindex
                    eq_series = pd.Series(res['equity'], index=res['df'].index)
                    # Forward-fill to handle non-trading periods, back-fill for initial NaNs
                    eq_aligned = eq_series.reindex(all_indices, method='ffill').fillna(method='bfill')
                    equity_matrix.append(eq_aligned.values)
            
            if equity_matrix:
                # Sum the aligned equity curves to get the total portfolio equity
                total_equity = np.sum(equity_matrix, axis=0)
        
        # Calculate portfolio drawdown correctly from the aggregate equity curve
        if len(total_equity) > 0:
            cummax = np.maximum.accumulate(total_equity)
            # Avoid division by zero if cummax is 0
            safe_cummax = np.where(cummax == 0, 1, cummax)
            drawdowns = (total_equity - cummax) / safe_cummax * 100
            total_max_drawdown = drawdowns.min() if len(drawdowns) > 0 else 0
        else:
            total_max_drawdown = 0
    else:
        print("No pairs were successfully processed. Cannot generate results.")

    # === Plot Equity Curve and Insights ===
    if pair_results:  # Only generate report if we have results
        generate_excel_report(pair_results, all_trades, total_equity, total_max_drawdown)
    else:
        print("No successful pairs to generate report for.")

# === Plot Equity Curve and Insights ===
def generate_excel_report(pair_results, all_trades, total_equity, total_max_drawdown):
    """Generates an Excel report with backtest results and charts."""
    try:
        import matplotlib.pyplot as plt
        import xlsxwriter
        import tempfile
        import shutil
    except ImportError as e:
        print("Required packages for Excel report are missing:", e)
        print("Please install: pip install matplotlib xlsxwriter")
        return

    # Prepare table_data for results summary
    table_data = [
        ["Pair", "Trades", "Total Return %", "Max Drawdown %", "Sharpe", "Win %", "Avg Return %", "Profit Factor", "Avg Win %", "Avg Loss %", "Avg Hold"],
    ]
    pair_table_rows = []
    for res in pair_results:
        trades = res['trades']
        n_trades = len(trades)
        total_return = (res['equity'][-1] - 100) if len(res['equity']) > 0 else 0
        max_dd = res['max_drawdown']
        returns = [t['ret'] for t in trades if 'ret' in t]
        sharpe = np.mean(returns) / (np.std(returns) + 1e-9) * np.sqrt(252) if returns else 0
        win_pct = np.mean([r > 0 for r in returns]) * 100 if returns else 0
        avg_trade = np.mean(returns) * 100 if returns else 0
        profit_factor = (np.sum([r for r in returns if r > 0]) / abs(np.sum([r for r in returns if r <= 0])) 
                         if np.sum([r for r in returns if r <= 0]) != 0 and returns else 0)
        avg_win = np.mean([r for r in returns if r > 0]) * 100 if any(r > 0 for r in returns) else 0
        avg_loss = np.mean([r for r in returns if r <= 0]) * 100 if any(r <= 0 for r in returns) else 0
        avg_hold = np.mean([(t['exit'] - t['entry']) if 'exit' in t else 0 for t in trades]) if trades else 0
        row = [
            res['pair'], n_trades, f"{total_return:.2f}", f"{max_dd:.2f}", f"{sharpe:.2f}", f"{win_pct:.1f}",
            f"{avg_trade:.2f}", f"{profit_factor:.2f}", f"{avg_win:.2f}", f"{avg_loss:.2f}", f"{avg_hold:.1f}"
        ]
        pair_table_rows.append(row)
    
    # Add total/aggregate row for all pairs (portfolio)
    all_returns = [t['ret'] for t in all_trades if 'ret' in t]
    total_trades = len(all_trades)
    if len(total_equity) > 0:
        # Initial equity is the sum of initial equities (100 * num_pairs)
        initial_portfolio_equity = sum(1 for res in pair_results if not res['df'].empty and len(res['equity']) > 0) * 100
        total_return = (total_equity[-1] - initial_portfolio_equity) / initial_portfolio_equity * 100 if initial_portfolio_equity > 0 else 0
    else:
        total_return = 0
    if len(total_equity) > 1:
        # Use log returns for Sharpe calculation to handle portfolio aggregation correctly
        portfolio_logrets = np.diff(np.log(total_equity + 1e-9))
        total_sharpe = np.mean(portfolio_logrets) / (np.std(portfolio_logrets) + 1e-9) * np.sqrt(252)
    else:
        total_sharpe = 0
    total_win_pct = np.mean([r > 0 for r in all_returns]) * 100 if all_returns else 0
    total_avg_trade = np.mean(all_returns) * 100 if all_returns else 0
    total_profit_factor = (np.sum([r for r in all_returns if r > 0]) / abs(np.sum([r for r in all_returns if r <= 0]))
                          if np.sum([r for r in all_returns if r <= 0]) != 0 and all_returns else 0)
    total_avg_win = np.mean([r for r in all_returns if r > 0]) * 100 if any(r > 0 for r in all_returns) else 0
    total_avg_loss = np.mean([r for r in all_returns if r <= 0]) * 100 if any(r <= 0 for r in all_returns) else 0
    total_avg_hold = np.mean([(t['exit'] - t['entry']) if 'exit' in t else 0 for t in all_trades]) if all_trades else 0
    total_row = [
        "Total", total_trades, f"{total_return:.2f}", f"{total_max_drawdown:.2f}", f"{total_sharpe:.2f}", f"{total_win_pct:.1f}",
        f"{total_avg_trade:.2f}", f"{total_profit_factor:.2f}", f"{total_avg_win:.2f}", f"{total_avg_loss:.2f}", f"{total_avg_hold:.1f}"
    ]
    # Sort by Sharpe (column 4, as string, so convert to float)
    pair_table_rows_sorted = sorted(pair_table_rows, key=lambda x: float(x[4]), reverse=True)
    table_data = [table_data[0]] + pair_table_rows_sorted + [total_row]

    # --- Excel Report Generation ---
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    excel_path = os.path.join(os.getcwd(), f"Pairs_Trading_Report_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
    tempdir = tempfile.mkdtemp()

    # Save all pair plots (in table order)
    pair_plot_imgs = []
    for row in pair_table_rows_sorted:
        pair = row[0]
        res = next(r for r in pair_results if r['pair'] == pair)
        df = res['df']
        ratio = res['ratio']
        ratio_ma = res['ratio_ma']
        zscore = res['zscore']
        equity = res['equity']
        s1, s2 = pair.split('-')
        adf_pval = res.get('adf_pval', np.full(len(df), np.nan))
        johansen_stats = res.get('johansen_stats', np.full(len(df), np.nan))
        johansen_crit = res.get('johansen_crit', np.full(len(df), np.nan))
        fig, axs = plt.subplots(5, 1, figsize=(10, 18), constrained_layout=True)
        # 1. Price chart with dual y-axes
        time_index = df.index
        x_vals = np.arange(len(time_index))
        x_labels = pd.to_datetime(time_index).strftime('%Y-%m-%d %H:%M')

        ax_price1 = axs[0]
        ax_price2 = ax_price1.twinx()
        ax_price1.plot(x_vals, df['price1'], label=s1, color='tab:blue')
        ax_price2.plot(x_vals, df['price2'], label=s2, color='tab:orange')
        ax_price1.set_ylabel(f"{s1} Price", color='tab:blue')
        ax_price2.set_ylabel(f"{s2} Price", color='tab:orange')
        ax_price1.set_title(f"{pair}: {s1} (left) and {s2} (right) Price")
        step = max(1, len(x_vals)//8)
        axs[0].set_xticks(x_vals[::step])
        axs[0].set_xticklabels(x_labels[::step], rotation=30, ha='right')
        # Legends for both axes
        lines1, labels1 = ax_price1.get_legend_handles_labels()
        lines2, labels2 = ax_price2.get_legend_handles_labels()
        ax_price1.legend(lines1 + lines2, labels1 + labels2, loc='upper left')
        # 2. Ratio chart
        axs[1].plot(x_vals, ratio, label="Ratio")
        axs[1].plot(x_vals, ratio_ma, label="Ratio MA", linestyle="--")
        axs[1].set_ylabel("Price Ratio")
        axs[1].set_title(f"{pair}: Price Ratio ({s1}/{s2})")
        axs[1].set_xticks(x_vals[::step])
        axs[1].set_xticklabels(x_labels[::step], rotation=30, ha='right')
        axs[1].legend()
        # 3. Z-score chart
        axs[2].plot(x_vals, zscore, label="Z-score")
        axs[2].axhline(Z_ENTRY, color='g', linestyle='--', label="Entry Threshold")
        axs[2].axhline(-Z_ENTRY, color='g', linestyle='--')
        axs[2].axhline(Z_EXIT, color='r', linestyle='--', label="Exit Threshold")
        axs[2].axhline(-Z_EXIT, color='r', linestyle='--')
        axs[2].set_ylabel("Z-score")
        axs[2].set_title(f"{pair}: Z-score")
        axs[2].set_xticks(x_vals[::step])
        axs[2].set_xticklabels(x_labels[::step], rotation=30, ha='right')
        axs[2].legend()
        # 4. ADF and Johansen stats
        axs[3].plot(x_vals, adf_pval, label="ADF p-value", color='tab:blue')
        axs[3].axhline(MAX_ADF_PVAL, color='tab:blue', linestyle='--', label=f"ADF Threshold ({MAX_ADF_PVAL})")
        axs[3].set_ylabel("ADF p-value")
        ax2 = axs[3].twinx()
        ax2.plot(x_vals, johansen_stats, label="Johansen Stat", color='tab:orange')
        ax2.plot(x_vals, johansen_crit, label=f"Johansen {JOHANSEN_CRIT_LEVEL}% Crit", color='tab:red', linestyle='--')
        ax2.set_ylabel("Johansen Stat")
        axs[3].set_title(f"{pair}: ADF p-value & Johansen Stat")
        axs[3].set_xticks(x_vals[::step])
        axs[3].set_xticklabels(x_labels[::step], rotation=30, ha='right')
        lines1, labels1 = axs[3].get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        axs[3].legend(lines1 + lines2, labels1 + labels2, loc='upper right')
        # 5. Equity curve
        axs[4].plot(x_vals, equity, label="Equity Curve")
        axs[4].set_ylabel("Equity")
        axs[4].set_title(f"{pair}: Equity Curve")
        axs[4].set_xticks(x_vals[::step])
        axs[4].set_xticklabels(x_labels[::step], rotation=30, ha='right')
        axs[4].legend()
        pair_img = os.path.join(tempdir, f"{pair}_plot.png")
        plt.savefig(pair_img)
        plt.close(fig)
        pair_plot_imgs.append((pair, pair_img))

    # Save aggregate equity plot
    agg_equity_img = os.path.join(tempdir, "aggregate_equity.png")
    plt.figure(figsize=(10, 5))
    if len(total_equity) > 0:
        # Use the common time index for the x-axis
        all_indices = pd.Index([])
        for res in pair_results:
            if not res['df'].empty:
                all_indices = all_indices.union(res['df'].index)
        all_indices = all_indices.sort_values()
        
        x_vals = np.arange(len(all_indices))
        x_labels = pd.to_datetime(all_indices).strftime('%Y-%m-%d %H:%M')
        plt.plot(x_vals, total_equity, label="Aggregate Equity (Portfolio)")
        step = max(1, len(x_vals)//8)
        plt.xticks(x_vals[::step], x_labels[::step], rotation=30, ha='right')
        plt.xlabel("Time")
        plt.ylabel("Equity")
        plt.title("Aggregate Equity Curve (All Pairs Portfolio)")
        plt.legend()
    else:
        plt.plot([], [])
        plt.title("Aggregate Equity Curve (All Pairs Portfolio)")
    plt.tight_layout()
    plt.savefig(agg_equity_img)
    plt.close()

    # Write Excel report
    workbook = xlsxwriter.Workbook(excel_path)
    # --- Cover Sheet ---
    cover = workbook.add_worksheet("Summary")
    bold = workbook.add_format({'bold': True, 'font_size': 14})
    normal = workbook.add_format({'font_size': 12})
    small = workbook.add_format({'font_size': 10})
    # Title
    cover.write('A1', "Pairs Trading Backtest Report (cTrader API)", bold)
    cover.write('A2', f"Generated: {now}", normal)
    # Parameters
    cover.write('A4', "Input Parameters:", bold)
    param_row = 5
    for k in [
        "INTERVAL", "START", "END", "Z_PERIOD", "CORR_PERIOD", "ADF_PERIOD", "Z_ENTRY", "Z_EXIT", "VOL_RATIO_MAX",
        "MIN_CORR", "MAX_ADF_PVAL", "JOHANSEN_CRIT_LEVEL", "TAKE_PROFIT_PERC", "STOP_LOSS_PERC", "TRAILING_STOP_PERC",
        "MIN_SPREAD", "MIN_VOLATILITY", "COOLDOWN_BARS", "DYNAMIC_Z", "COMMISSION_PERC"
    ]:
        cover.write(param_row, 0, k, small)
        cover.write(param_row, 1, str(globals().get(k)), small)
        param_row += 1

    # Data Table
    table_start = param_row+3
    cover.write(table_start, 0, "Results Table (sorted by Sharpe):", bold)
    for col, val in enumerate(table_data[0]):
        cover.write(table_start+1, col, val, bold)
    for row_idx, row in enumerate(table_data[1:]):
        for col, val in enumerate(row):
            cover.write(table_start+2+row_idx, col, val, normal)
    # Insert aggregate equity plot
    cover.write(table_start+4+len(table_data), 0, "Aggregate Equity Curve:", bold)
    cover.insert_image(table_start+5+len(table_data), 0, agg_equity_img, {'x_scale': 0.7, 'y_scale': 0.7})

    # --- Pair Sheets ---
    for idx, (pair, img_path) in enumerate(pair_plot_imgs):
        ws = workbook.add_worksheet(f"{pair[:28]}")
        ws.write('A1', f"Pair: {pair}", bold)
        ws.write('A2', f"Rank: {idx+1} (by Sharpe)", normal)

        # Write pair's results table (same columns as main table, but only this pair)
        pair_row = next(row for row in pair_table_rows_sorted if row[0] == pair)
        ws.write('A4', "Results:", bold)
        for col, val in enumerate(table_data[0]):
            ws.write(4, col, val, bold)
            ws.write(5, col, val, normal)

        # Optionally, write trade details for this pair
        res = next(r for r in pair_results if r['pair'] == pair)
        trades = res['trades']
        trade_start_row = 7
        if trades:
            trade_headers = ["Type", "Entry Idx", "Entry Price", "Exit Idx", "Exit Price", "Return"]
            ws.write(trade_start_row, 0, "Trade Details:", bold)
            for col, val in enumerate(trade_headers):
                ws.write(trade_start_row + 1, col, val, bold)
            for row_idx, t in enumerate(trades):
                ws.write(trade_start_row + 2 + row_idx, 0, t.get('type', ''))
                ws.write(trade_start_row + 2 + row_idx, 1, t.get('entry', ''))
                ws.write(trade_start_row + 2 + row_idx, 2, t.get('entry_price', ''))
                ws.write(trade_start_row + 2 + row_idx, 3, t.get('exit', ''))
                ws.write(trade_start_row + 2 + row_idx, 4, t.get('exit_price', ''))
                ws.write(trade_start_row + 2 + row_idx, 5, t.get('ret', ''))
            plot_row = trade_start_row + 3 + len(trades)
        else:
            plot_row = trade_start_row

        # Insert plot below the tables
        ws.insert_image(plot_row, 0, img_path, {'x_scale': 0.8, 'y_scale': 0.8})

    workbook.close()
    shutil.rmtree(tempdir)
    print(f"Excel report saved to: {excel_path}")

# Real-time trading implementation
class PairsTradingEngine:
    def __init__(self, client, account_id, symbols_map, symbol_details, pairs, params):
        self.client = client
        self.account_id = account_id
        self.symbols_map = symbols_map
        self.symbol_details = symbol_details  # Add symbol_details as an instance attribute
        self.symbol_id_to_name_map = {v: k for k, v in symbols_map.items()}
        self.pairs = pairs
        self.params = params
        
        # Trading state
        self.active_positions = {}  # position_id -> position_data
        self.pair_states = {}  # pair_name -> state (data, position, signal)
        self.spot_prices = {}  # symbol -> latest price
        self.price_history = defaultdict(lambda: deque(maxlen=500))  # symbol -> deque of prices
        self.subscribed_symbols = set()
        self.last_candle_time = {}  # symbol -> last candle timestamp
        self.is_initialized = False
        self.execution_requests = {}  # clientOrderId -> order request data
        self.next_order_id = 1
        self.trendbar_period = params.get('INTERVAL', '15m')
        self.trendbar_period_map = {"1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1", "4h": "H4", "1d": "D1"}
        
    def initialize(self):
        """Download initial historical data and set up indicators, subscribe to trendbars."""
        logger.info("Initializing trading engine...")
        # Extract all symbols from pairs
        all_symbols = set()
        for pair_str in self.pairs:
            s1, s2 = pair_str.split('-')
            all_symbols.add(s1)
            all_symbols.add(s2)
        
        # Get initial historical data for all symbols
        retriever = CTraderDataRetriever()
        retriever.client = self.client
        retriever.account_id = self.account_id
        retriever.symbols_map = self.symbols_map
        retriever.symbol_id_to_name_map = self.symbol_id_to_name_map
        initial_data = retriever.get_historical_data(list(all_symbols), self.params['INTERVAL'],
                                                    datetime.datetime.now() - datetime.timedelta(days=10),
                                                    datetime.datetime.now())
        
        # Initialize pair states with historical data
        for pair_str in self.pairs:
            s1, s2 = pair_str.split('-')
            if s1 in initial_data and s2 in initial_data and not initial_data[s1].empty and not initial_data[s2].empty:
                price1 = initial_data[s1]
                price2 = initial_data[s2]
                self.pair_states[pair_str] = {
                    'price1': price1,
                    'price2': price2,
                    'position': None,
                    'last_signal': None,
                    'ratio': price1 / price2,
                    'ratio_ma': (price1 / price2).rolling(self.params['Z_PERIOD']).mean(),
                    'ratio_std': (price1 / price2).rolling(self.params['Z_PERIOD']).std(),
                    'zscore': None,
                    'symbol1': s1,
                    'symbol2': s2,
                    'cooldown': 0,
                    'last_trade_time': None
                }
                
                # Calculate zscore
                ratio = price1 / price2
                ratio_ma = ratio.rolling(self.params['Z_PERIOD']).mean()
                ratio_std = ratio.rolling(self.params['Z_PERIOD']).std()
                zscore = (ratio - ratio_ma) / ratio_std
                self.pair_states[pair_str]['zscore'] = zscore
                logger.info(f"Initialized pair {pair_str} with {len(price1)} data points")
            else:
                logger.warning(f"Could not initialize pair {pair_str} - missing data")
        # Subscribe to trendbars for all symbols
        for symbol in all_symbols:
            if symbol in self.symbols_map:
                self.subscribe_to_trendbars(symbol)
        logger.info(f"Trading engine initialized with {len(self.pair_states)} active pairs")
        self.is_initialized = True

    def subscribe_to_spot_prices(self, symbol):
        """Subscribe to real-time price updates for a symbol"""
        if symbol in self.symbols_map and symbol not in self.subscribed_symbols:
            symbol_id = self.symbols_map[symbol]
            logger.info(f"Subscribing to spot prices for {symbol} (ID: {symbol_id})")
            
            request = ProtoOASubscribeSpotsReq()
            request.ctidTraderAccountId = self.account_id
            request.symbolId.append(symbol_id)
            
            try:
                deferred = self.client.send(request)
                deferred.addErrback(self._on_subscription_error)
                # Don't add to subscribed_symbols here - wait for confirmation
                return True
            except Exception as e:
                logger.error(f"Error subscribing to {symbol}: {e}")
                return False
        return False

    def subscribe_to_trendbars(self, symbol):
        """Subscribe to actual OHLC/candle data for a symbol."""
        if symbol in self.symbols_map:
            symbol_id = self.symbols_map[symbol]
            logger.info(f"Subscribing to trendbars for {symbol} (ID: {symbol_id})")
            request = ProtoOAGetTrendbarsReq()
            request.ctidTraderAccountId = self.account_id
            request.symbolId = symbol_id
            period = self.trendbar_period_map.get(self.trendbar_period, "M15")
            request.period = ProtoOATrendbarPeriod.Value(period)
            # Request only the latest candle (or a small batch for robustness)
            now = int(time.time() * 1000)
            request.fromTimestamp = now - 60 * 60 * 1000  # last hour
            request.toTimestamp = now
            try:
                deferred = self.client.send(request)
                deferred.addErrback(self._on_trendbar_subscription_error)
                return True
            except Exception as e:
                logger.error(f"Error subscribing to trendbars for {symbol}: {e}")
                return False
        return False

    def _on_trendbar_subscription_error(self, failure):
        logger.error(f"Trendbar subscription error: {failure}")

    # def process_spot_event(self, event):
    #     """Process a real-time price update"""
    #     symbol_id = event.symbolId
    #     symbol_name = self.symbol_id_to_name_map.get(symbol_id)
        
    #     if not symbol_name:
    #         logger.warning(f"Received spot event for unknown symbol ID: {symbol_id}")
    #         return
        
    #     # Get symbol details for price conversion
    #     details = self.symbol_details.get(symbol_name)
    #     if not details:
    #         logger.warning(f"Missing symbol details for {symbol_name}, cannot process spot event.")
    #         return
            
    #     digits = details['digits']
        
    #     # Extract the bid/ask prices
    #     bid = event.bid / (10 ** digits) if hasattr(event, 'bid') else None
    #     ask = event.ask / (10 ** digits) if hasattr(event, 'ask') else None
    #     timestamp = datetime.datetime.fromtimestamp(event.timestamp / 1000)
        
    #     if bid is None or ask is None:
    #         logger.debug(f"Spot event for {symbol_name} missing bid or ask price.")
    #         return

    #     # Store mid price
    #     price = (bid + ask) / 2
    #     self.spot_prices[symbol_name] = price
        
    #     # Add to price history
    #     self.price_history[symbol_name].append((timestamp, price))
        
        # Check if we need to update the candle
        # if self._should_update_candle(symbol_name, timestamp):
        #     self._update_candle(symbol_name, timestamp)
        #     self._update_trading_signals()

    def process_trendbar_event(self, event):
        """Process a new trendbar (candle) event for a symbol."""
        symbol_id = event.symbolId
        symbol_name = self.symbol_id_to_name_map.get(symbol_id)
        if not symbol_name:
            logger.warning(f"Received trendbar event for unknown symbol ID: {symbol_id}")
            return
        closes = []
        timestamps = []
        pip_factor = getattr(event, 'pipFactor', 0)
        if hasattr(event, 'trendbar') and len(event.trendbar) > 0:
            for bar in event.trendbar:
                close_price = None
                if hasattr(bar, 'close'):
                    close_price = bar.close
                elif hasattr(bar, 'closePrice'):
                    close_price = bar.closePrice
                elif hasattr(bar, 'c'):
                    close_price = bar.c
                if close_price is not None:
                    adjusted_price = close_price / (10 ** pip_factor) if pip_factor > 0 else close_price
                    closes.append(adjusted_price)
                    timestamp = None
                    if hasattr(bar, 'utcTimestampInMinutes'):
                        timestamp = pd.to_datetime(bar.utcTimestampInMinutes * 60, unit='s')
                    elif hasattr(bar, 'timestamp'):
                        timestamp = pd.to_datetime(bar.timestamp, unit='ms')
                    elif hasattr(bar, 'time'):
                        timestamp = pd.to_datetime(bar.time, unit='s')
                    if timestamp:
                        timestamps.append(timestamp)
        if closes and timestamps and len(closes) == len(timestamps):
            # Only append the latest candle
            latest_price = closes[-1]
            latest_time = timestamps[-1]
            # Update all pairs containing this symbol
            for pair_str, pair_state in self.pair_states.items():
                s1, s2 = pair_state['symbol1'], pair_state['symbol2']
                if symbol_name == s1:
                    pair_state['price1'] = pd.concat([pair_state['price1'], pd.Series([latest_price], index=[latest_time])])
                elif symbol_name == s2:
                    pair_state['price2'] = pd.concat([pair_state['price2'], pd.Series([latest_price], index=[latest_time])])
                # Only update indicators if both series have the latest timestamp
                if (not pair_state['price1'].empty and not pair_state['price2'].empty and
                    pair_state['price1'].index[-1] == pair_state['price2'].index[-1]):
                    ratio = pair_state['price1'].iloc[-1] / pair_state['price2'].iloc[-1]
                    pair_state['ratio'] = pd.concat([pair_state['ratio'], pd.Series([ratio], index=[latest_time])])
                    ratio_series = pair_state['ratio']
                    if len(ratio_series) >= self.params['Z_PERIOD']:
                        ratio_ma = ratio_series.rolling(self.params['Z_PERIOD']).mean().iloc[-1]
                        ratio_std = ratio_series.rolling(self.params['Z_PERIOD']).std().iloc[-1]
                        pair_state['ratio_ma'] = pd.concat([pair_state['ratio_ma'], pd.Series([ratio_ma], index=[latest_time])])
                        pair_state['ratio_std'] = pd.concat([pair_state['ratio_std'], pd.Series([ratio_std], index=[latest_time])])
                        zscore = (ratio - ratio_ma) / ratio_std if ratio_std > 0 else 0
                        pair_state['zscore'] = pd.concat([pair_state['zscore'], pd.Series([zscore], index=[latest_time])])
                        logger.debug(f"Updated {pair_str} Z-score: {zscore:.2f}")
    
    def _update_trading_signals(self):
        """Process updated candles and generate trading signals"""
        if not self.is_initialized:
            return
        
        for pair_str, state in self.pair_states.items():
            # Skip if not enough data
            if state['zscore'] is None or len(state['zscore']) < 2:
                continue
            
            # Get current and previous Z-scores
            zscore = state['zscore'].iloc[-1]
            prev_zscore = state['zscore'].iloc[-2] if len(state['zscore']) >= 2 else 0
            
            # Check if pair is in cooldown
            if state['cooldown'] > 0:
                state['cooldown'] -= 1
                continue
            
            # Check entry/exit conditions
            position = state.get('position')
            
            # Determine Z-score thresholds (static or dynamic)
            if self.params['DYNAMIC_Z']:
                volatility = state['ratio_std'].iloc[-1] if not state['ratio_std'].empty else 0
                z_entry = max(2, 2 * volatility / self.params['MIN_VOLATILITY'])
                z_exit = max(0.5, 0.5 * volatility / self.params['MIN_VOLATILITY'])
            else:
                z_entry = self.params['Z_ENTRY']
                z_exit = self.params['Z_EXIT']
            
            # Check entry signals
            if position is None:
                # Check long entry (negative Z-score crossing upward through threshold)
                if prev_zscore < -z_entry and zscore >= -z_entry:
                    logger.info(f"LONG ENTRY SIGNAL for {pair_str} (Z-score: {zscore:.2f})")
                    state['last_signal'] = ('LONG_ENTRY', zscore)
                    self._execute_pair_trade(pair_str, 'LONG')
                    
                # Check short entry (positive Z-score crossing downward through threshold)    
                elif prev_zscore > z_entry and zscore <= z_entry:
                    logger.info(f"SHORT ENTRY SIGNAL for {pair_str} (Z-score: {zscore:.2f})")
                    state['last_signal'] = ('SHORT_ENTRY', zscore)
                    self._execute_pair_trade(pair_str, 'SHORT')
            
            # Check exit signals
            elif position == 'LONG':
                # Check long exit (negative Z-score crossing upward through exit threshold)
                if prev_zscore < -z_exit and zscore >= -z_exit:
                    logger.info(f"LONG EXIT SIGNAL for {pair_str} (Z-score: {zscore:.2f})")
                    state['last_signal'] = ('LONG_EXIT', zscore)
                    self._close_pair_position(pair_str)
            
            elif position == 'SHORT':
                # Check short exit (positive Z-score crossing downward through exit threshold)
                if prev_zscore > z_exit and zscore <= z_exit:
                    logger.info(f"SHORT EXIT SIGNAL for {pair_str} (Z-score: {zscore:.2f})")
                    state['last_signal'] = ('SHORT_EXIT', zscore)
                    self._close_pair_position(pair_str)
    
    def _execute_pair_trade(self, pair_str, direction):
        """Execute a trade for a pair in the specified direction"""
        state = self.pair_states[pair_str]
        s1, s2 = pair_str.split('-')
        
        # Get latest prices from pair state data (updated by trendbar events)
        price1 = None
        price2 = None
        
        # First try to get from pair state (most reliable for trading decisions)
        if not state['price1'].empty and not state['price2'].empty:
            price1 = state['price1'].iloc[-1]
            price2 = state['price2'].iloc[-1]
            logger.debug(f"Using pair state prices for {pair_str}: {s1}={price1}, {s2}={price2}")
        
        # Fallback to spot prices if available
        elif s1 in self.spot_prices and s2 in self.spot_prices:
            price1 = self.spot_prices[s1]
            price2 = self.spot_prices[s2]
            logger.debug(f"Using spot prices for {pair_str}: {s1}={price1}, {s2}={price2}")
        
        # If still no prices available, cannot trade
        if price1 is None or price2 is None:
            logger.warning(f"Can't trade {pair_str}: missing price data. State1 empty: {state['price1'].empty}, State2 empty: {state['price2'].empty}, Spot prices available: {s1 in self.spot_prices}, {s2 in self.spot_prices}")
            return False
        
        # Validate prices are reasonable (not zero or negative)
        if price1 <= 0 or price2 <= 0:
            logger.warning(f"Invalid prices for {pair_str}: {s1}={price1}, {s2}={price2}")
            return False
        
        # Calculate position sizes
        # For simplicity, use equal notional value for both legs
        base_volume = 1000  # $1,000 notional value, adjust as needed
        
        volume1 = base_volume / price1
        volume2 = base_volume / price2
        
        # Apply minimum volume constraints if needed
        min_volume = 0.01  # Minimum volume, adjust based on broker requirements
        volume1 = max(volume1, min_volume)
        volume2 = max(volume2, min_volume)
        
        logger.info(f"Executing {direction} trade for {pair_str} at prices: {s1}={price1:.5f}, {s2}={price2:.5f}")
        logger.info(f"Calculated volumes: {s1}={volume1:.4f}, {s2}={volume2:.4f}")
        
        # Determine trade directions based on pair strategy
        if direction == 'LONG':
            # Long the ratio: Buy s1, Sell s2
            side1 = ProtoOATradeSide.BUY
            side2 = ProtoOATradeSide.SELL
        else:  # SHORT
            # Short the ratio: Sell s1, Buy s2
            side1 = ProtoOATradeSide.SELL
            side2 = ProtoOATradeSide.BUY
        
        # Execute first leg
        order_id1 = self._send_market_order(s1, side1, volume1)
        if not order_id1:
            logger.error(f"Failed to send first order for {s1}")
            return False
        
        # Execute second leg
        order_id2 = self._send_market_order(s2, side2, volume2)
        if not order_id2:
            logger.error(f"Failed to send second order for {s2}")
            return False
        
        # Store pair position info
        state['position'] = direction
        state['position_orders'] = (order_id1, order_id2)
        state['entry_time'] = datetime.datetime.now()
        state['entry_zscore'] = state['zscore'].iloc[-1] if not state['zscore'].empty else 0
        state['entry_price1'] = price1
        state['entry_price2'] = price2
        
        logger.info(f"Successfully executed {direction} trade for {pair_str} - Orders: {order_id1}, {order_id2}")
        return True
    
    def _close_pair_position(self, pair_str):
        """Close an open position for a pair"""
        state = self.pair_states[pair_str]
        
        if state['position'] is None:
            logger.warning(f"No position to close for {pair_str}")
            return False
        
        # Get the position details
        position = state['position']
        s1, s2 = pair_str.split('-')
        
        # Close positions by executing opposite orders
        if position == 'LONG':
            # Close long position: Sell s1, Buy s2
            self._send_market_order(s1, ProtoOATradeSide.SELL, None, is_close=True)
            self._send_market_order(s2, ProtoOATradeSide.BUY, None, is_close=True)
        else:  # SHORT
            # Close short position: Buy s1, Sell s2
            self._send_market_order(s1, ProtoOATradeSide.BUY, None, is_close=True)
            self._send_market_order(s2, ProtoOATradeSide.SELL, None, is_close=True)
        
        # Reset position state
        state['position'] = None
        state['cooldown'] = self.params['COOLDOWN_BARS']
        state['last_trade_time'] = datetime.datetime.now()
        
        logger.info(f"Closed {position} position for {pair_str}")
        return True
    
    def _send_market_order(self, symbol, side, volume=None, is_close=False):
        """Send a market order to the broker"""
        if symbol not in self.symbols_map:
            logger.error(f"Symbol {symbol} not found in symbols map")
            return None
            
        symbol_id = self.symbols_map[symbol]
        
        # Generate a unique client order ID
        client_order_id = f"PT_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{self.next_order_id}"
        self.next_order_id += 1
        
        request = ProtoOANewOrderReq()
        request.ctidTraderAccountId = self.account_id
        request.symbolId = symbol_id
        request.orderType = ProtoOAOrderType.MARKET
        request.tradeSide = side
        
        # If closing, use close all positions
        if is_close:
            request.closePositionDetails.SetInParent()  # This will close all positions for the symbol
        else:
            request.volume = int(volume * 100)  # Convert to lots (typically *100, but check your broker)
        
        # Send the order
        try:
            request.clientOrderId = client_order_id
            deferred = self.client.send(request)
            deferred.addErrback(self._on_order_error)
            
            # Store the order request for reference
            self.execution_requests[client_order_id] = {
                'symbol': symbol,
                'side': side,
                'volume': volume,
                'time': datetime.datetime.now()
            }
            
            logger.info(f"Sent {'CLOSE ' if is_close else ''}{side} order for {symbol}: {client_order_id}")
            return client_order_id
        except Exception as e:
            logger.error(f"Error sending order for {symbol}: {e}")
            return None
    
    def _on_order_error(self, failure):
        """Handle order execution errors"""
        logger.error(f"Order execution error: {failure}")
    
    def process_execution_event(self, event):
        """Process order execution events from the broker"""
        try:
            if hasattr(event, 'position'):
                position = event.position
                symbol_id = position.symbolId
                symbol_name = self.symbol_id_to_name_map.get(symbol_id)
                position_id = position.positionId
                
                logger.info(f"Position update for {symbol_name} (ID: {position_id}): {position.positionStatus}")
                
                # Update active positions map
                if position.positionStatus == "OPEN":
                    self.active_positions[position_id] = {
                        'symbol': symbol_name,
                        'volume': position.volume / 100,  # Convert from lots
                        'side': "BUY" if position.tradeSide == ProtoOATradeSide.BUY else "SELL",
                        'entry_price': position.price,
                        'open_time': datetime.datetime.fromtimestamp(position.openTimestamp / 1000)
                    }
                elif position.positionStatus == "CLOSED":
                    if position_id in self.active_positions:
                        del self.active_positions[position_id]
            
            if hasattr(event, 'order'):
                order = event.order
                logger.info(f"Order update: {order.orderStatus} - {order.orderType}")
                
        except Exception as e:
            logger.error(f"Error processing execution event: {e}")
    
    def run_trading_loop(self):
        """Main trading loop that processes real-time data"""
        try:
            if not self.is_initialized:
                self.initialize()
            
            # The actual trading logic is executed in the _update_trading_signals method
            # which is called whenever we receive new price data via process_spot_event
            
            logger.info("Trading engine started and ready for real-time data")
            
            # Keep the reactor running to process events
            # Note: The twisted reactor will handle the event loop
            
        except Exception as e:
            logger.error(f"Error in trading loop: {e}")

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
            for pair_str in pairs:
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

# Replace the original method with our updated one
CTraderDataRetriever._on_message_received = _on_message_received

# Main function for real-time trading
def run_realtime_trading():
    """Run the pairs trading strategy in real-time mode"""
    logger.info("Starting pairs trading in real-time mode")
    
    if not CTRADER_API_AVAILABLE:
        logger.error("cTrader API not available. Install with: pip install ctrader-open-api")
        return
    
    # Check credentials
    if not all([CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET, CTRADER_ACCESS_TOKEN]):
        logger.error("Missing API credentials. Please check your .env file.")
        return
    
    # Setup API client
    try:
        # Enable more detailed logging for troubleshooting
        logging.getLogger().setLevel(logging.DEBUG)
        
        logger.info("Creating cTrader API client...")
        client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)
        
        # Create a data retriever for authentication and symbol loading
        retriever = CTraderDataRetriever()
        
        # Add flags to track the process
        retriever.symbols_list_requested = False
        retriever.symbols_list_loaded = False
        retriever.symbol_loading_error = None
        retriever.trading_engine = None
        retriever.ready_for_trading = False
        
        # Create parameters dictionary from global variables
        params = {
            'Z_PERIOD': Z_PERIOD,
            'CORR_PERIOD': CORR_PERIOD,
            'ADF_PERIOD': ADF_PERIOD,
            'Z_ENTRY': Z_ENTRY,
            'Z_EXIT': Z_EXIT,
            'VOL_RATIO_MAX': VOL_RATIO_MAX,
            'VOL_RATIO_TF': VOL_RATIO_TF,
            'MIN_CORR': MIN_CORR,
            'MAX_ADF_PVAL': MAX_ADF_PVAL,
            'JOHANSEN_CRIT_LEVEL': JOHANSEN_CRIT_LEVEL,
            'TAKE_PROFIT_PERC': TAKE_PROFIT_PERC,
            'STOP_LOSS_PERC': STOP_LOSS_PERC,
            'TRAILING_STOP_PERC': TRAILING_STOP_PERC,
            'MIN_SPREAD': MIN_SPREAD,
            'MIN_VOLATILITY': MIN_VOLATILITY,
            'COOLDOWN_BARS': COOLDOWN_BARS,
            'DYNAMIC_Z': DYNAMIC_Z,
            'COMMISSION_PERC': COMMISSION_PERC,
            'INTERVAL': INTERVAL
        }
        
        # Enhanced message handler that processes symbols and initializes trading
        def enhanced_message_received(client, message):
            try:
                # logger.debug(f"Received message type: {message.payloadType}")
                
                if message.payloadType == ProtoOAApplicationAuthRes().payloadType:
                    print("API Application authorized")
                    if retriever.account_id:
                        retriever._authenticate_account()
                    else:
                        retriever._get_account_list()
                        
                elif message.payloadType == OpenApiMessages_pb2.ProtoOAGetAccountListByAccessTokenRes().payloadType:
                    retriever._process_account_list(message)
                    
                elif message.payloadType == ProtoOAAccountAuthRes().payloadType:
                    print(f"Account {retriever.account_id} authorized")
                    # Request symbols list
                    request = ProtoOASymbolsListReq()
                    request.ctidTraderAccountId = retriever.account_id
                    request.includeArchivedSymbols = False
                    logger.debug(f"Requesting symbols for account: {retriever.account_id}")
                    deferred = client.send(request)
                    deferred.addErrback(lambda failure: logger.error(f"Symbol request error: {failure}"))
                    retriever.symbols_list_requested = True
                    
                elif message.payloadType == ProtoOASymbolsListRes().payloadType:
                    logger.debug("Processing symbols list response...")
                    response = Protobuf.extract(message)
                    
                    # Process symbols and extract details
                    for symbol in response.symbol:
                        retriever.symbols_map[symbol.symbolName] = symbol.symbolId
                        retriever.symbol_id_to_name_map[symbol.symbolId] = symbol.symbolName
                        # --- FIX: Extract symbol details (at least digits) ---
                        retriever.symbol_details[symbol.symbolName] = {
                            'digits': getattr(symbol, 'digits', 0),
                            'symbolId': symbol.symbolId,
                            'symbolName': symbol.symbolName,
                            # Add more fields if needed
                        }
                    
                    logger.info(f"Successfully loaded {len(retriever.symbols_map)} symbols")
                    retriever.symbols_list_loaded = True
                    
                    # Now initialize the trading engine
                    if not retriever.ready_for_trading:
                        logger.info("Initializing trading engine...")
                        
                        # Create trading engine
                        trading_engine = PairsTradingEngine(
                            client,
                            retriever.account_id,
                            retriever.symbols_map,
                            retriever.symbol_details,  # <-- Now contains digits
                            pairs,
                            params
                        )
                        retriever.trading_engine = trading_engine
                        
                        # Initialize trading engine without calling get_historical_data which would start another reactor
                        logger.info("Setting up pairs for trading...")
                        
                        # Extract all symbols from pairs
                        all_symbols = set()
                        for pair_str in pairs:
                            s1, s2 = pair_str.split('-')
                            all_symbols.add(s1)
                            all_symbols.add(s2)
                        
                        # Initialize pair states without historical data for now
                        for pair_str in pairs:
                            s1, s2 = pair_str.split('-')
                            if s1 in retriever.symbols_map and s2 in retriever.symbols_map:
                                # Initialize empty pair state that will be populated by real-time data
                                trading_engine.pair_states[pair_str] = {
                                    'price1': pd.Series(dtype=float),
                                    'price2': pd.Series(dtype=float),
                                    'position': None,
                                    'last_signal': None,
                                    'ratio': pd.Series(dtype=float),
                                    'ratio_ma': pd.Series(dtype=float),
                                    'ratio_std': pd.Series(dtype=float),
                                    'zscore': pd.Series(dtype=float),
                                    'symbol1': s1,
                                    'symbol2': s2,
                                    'cooldown': 0,
                                    'last_trade_time': None
                                }
                                logger.info(f"Initialized pair {pair_str} for real-time trading")
                        
                        # Subscribe to real-time prices
                        for symbol in all_symbols:
                            if symbol in retriever.symbols_map:
                                trading_engine.subscribe_to_spot_prices(symbol)
                        
                        trading_engine.is_initialized = True
                        retriever.ready_for_trading = True
                        logger.info(f"Trading engine ready with {len(trading_engine.pair_states)} pairs")
                        
                        # --- Add status log to confirm real-time trading is running ---
                        logger.info("Real-time trading is ACTIVE. Waiting for spot events and trading signals...")
                
                elif message.payloadType == ProtoOASubscribeSpotsRes().payloadType:
                    # Handle subscription confirmation
                    response = Protobuf.extract(message)
                    logger.info("Received subscription confirmation response")
                    
                    # The response doesn't contain symbol details, but we can log the success
                    if retriever.trading_engine:
                        # Count how many symbols we attempted to subscribe to
                        pending_symbols = set()
                        for pair_str in pairs:
                            s1, s2 = pair_str.split('-')
                            if s1 in retriever.symbols_map:
                                pending_symbols.add(s1)
                            if s2 in retriever.symbols_map:
                                pending_symbols.add(s2)
                        
                        logger.info(f"Spot subscription confirmed - monitoring {len(pending_symbols)} symbols for price updates")
                        logger.info(f"Subscribed symbols: {', '.join(sorted(pending_symbols))}")
                        
                        # Mark all pending symbols as subscribed since we got a success response
                        for symbol in pending_symbols:
                            if symbol not in retriever.trading_engine.subscribed_symbols:
                                retriever.trading_engine.subscribed_symbols.add(symbol)
                                logger.debug(f"Confirmed subscription for {symbol}")
                    
                # elif message.payloadType == ProtoOASpotEvent().payloadType:
                #     # Handle real-time price updates
                #     if retriever.trading_engine and retriever.ready_for_trading:
                #         spot_event = Protobuf.extract(message)
                #         symbol_id = spot_event.symbolId
                #         symbol_name = retriever.symbol_id_to_name_map.get(symbol_id, f"Unknown[{symbol_id}]")
                        
                #         # Log first few spot events to confirm data flow
                #         if not hasattr(retriever, '_spot_event_count'):
                #             retriever._spot_event_count = {}
                        
                #         if symbol_name not in retriever._spot_event_count:
                #             retriever._spot_event_count[symbol_name] = 0
                        
                #         retriever._spot_event_count[symbol_name] += 1
                        
                #         # Log first 5 events per symbol, then every 100th event
                #         count = retriever._spot_event_count[symbol_name]
                #         if count <= 5 or count % 100 == 0:
                #             logger.info(f"Spot event #{count} for {symbol_name}: bid={getattr(spot_event, 'bid', 'N/A')}, ask={getattr(spot_event, 'ask', 'N/A')}")
                        
                #         retriever.trading_engine.process_spot_event(spot_event)
                        
                elif message.payloadType == ProtoOAExecutionEvent().payloadType:
                    # Handle order execution events
                    if retriever.trading_engine and retriever.ready_for_trading:
                        retriever.trading_engine.process_execution_event(Protobuf.extract(message))
                        
            except Exception as e:
                logger.error(f"Error handling message: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
        
        # Set client and initialize
        logger.info("Setting up client and initializing retriever...")
        retriever.client = client
        
        # Store API account ID for logging
        api_account_id = CTRADER_ACCOUNT_ID
        logger.info(f"Using account ID from .env: {api_account_id}")
        
        # Try to parse account ID
        if CTRADER_ACCOUNT_ID and CTRADER_ACCOUNT_ID.strip():
            try:
                retriever.account_id = int(CTRADER_ACCOUNT_ID.strip())
                logger.info(f"Using account ID: {retriever.account_id}")
            except ValueError as e:
                logger.error(f"Invalid account ID format: '{CTRADER_ACCOUNT_ID}' - {e}")
                retriever.account_id = None
        else:
            logger.info("No account ID provided, will attempt to get from API")
            retriever.account_id = None
        
        retriever.access_token = CTRADER_ACCESS_TOKEN
        
        # Set callbacks
        logger.info("Setting up callbacks...")
        client.setConnectedCallback(retriever._on_connected)
        client.setDisconnectedCallback(retriever._on_disconnected)
        client.setMessageReceivedCallback(enhanced_message_received)
        
        # Start API connection
        logger.info("Starting API service...")
        client.startService()
        
        # Keep the script running - this is the only reactor.run() call
        logger.info("Entering main trading loop. Press Ctrl+C to exit.")
        logger.info("Expected sequence: Connect -> Auth -> Load Symbols -> Subscribe to Spots -> Receive Spot Events")
        reactor.run()
        
    except KeyboardInterrupt:
        logger.info("Trading stopped by user")
    except Exception as e:
        logger.error(f"Error in real-time trading: {e}")
        import traceback
        traceback.print_exc()

# Add this to allow running in real-time or backtest mode
if __name__ == "__main__":
    # Check if we should run in real-time mode
    if os.getenv('REALTIME_TRADING', 'false').lower() == 'true':
        run_realtime_trading()
    else:
        # Run the original backtest
        run_backtest()