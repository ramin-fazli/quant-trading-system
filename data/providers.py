"""
Data providers for different sources
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pandas as pd
import numpy as np
from pathlib import Path

from .data_manager import DataProvider, DataQuery, DataType, DataSource

logger = logging.getLogger(__name__)


class MT5DataProvider(DataProvider):
    """MetaTrader 5 data provider"""
    
    def __init__(self, config: Dict):
        self.config = config
        self._mt5 = None
        self._initialize_mt5()
    
    def _initialize_mt5(self):
        """Initialize MT5 connection"""
        try:
            import MetaTrader5 as mt5
            self._mt5 = mt5
            
            if not mt5.initialize():
                raise ConnectionError("Failed to initialize MT5")
            
            logger.info("MT5 connection established")
        except ImportError:
            logger.error("MetaTrader5 package not found. Install with: pip install MetaTrader5")
            raise
        except Exception as e:
            logger.error(f"MT5 initialization failed: {e}")
            raise
    
    async def fetch_data(self, query: DataQuery) -> pd.DataFrame:
        """Fetch data from MT5"""
        if not self._mt5:
            raise ConnectionError("MT5 not initialized")
        
        all_data = []
        
        for symbol in query.symbols:
            try:
                # Convert timeframe string to MT5 timeframe
                mt5_timeframe = self._convert_timeframe(query.timeframe)
                
                # Fetch rates
                rates = self._mt5.copy_rates_range(
                    symbol,
                    mt5_timeframe,
                    query.start_date,
                    query.end_date
                )
                
                if rates is None or len(rates) == 0:
                    logger.warning(f"No data found for {symbol}")
                    continue
                
                # Convert to DataFrame
                df = pd.DataFrame(rates)
                df['time'] = pd.to_datetime(df['time'], unit='s')
                df.set_index('time', inplace=True)
                df['symbol'] = symbol
                
                # Rename columns to standard format
                df.rename(columns={
                    'tick_volume': 'volume',
                    'real_volume': 'real_volume'
                }, inplace=True)
                
                all_data.append(df)
                
            except Exception as e:
                logger.error(f"Failed to fetch data for {symbol}: {e}")
                continue
        
        if not all_data:
            return pd.DataFrame()
        
        # Combine all symbols
        combined_df = pd.concat(all_data, ignore_index=False)
        combined_df.sort_index(inplace=True)
        
        return combined_df
    
    def _convert_timeframe(self, timeframe: str) -> int:
        """Convert timeframe string to MT5 constant"""
        timeframe_map = {
            '1M': self._mt5.TIMEFRAME_M1,
            '5M': self._mt5.TIMEFRAME_M5,
            '15M': self._mt5.TIMEFRAME_M15,
            '30M': self._mt5.TIMEFRAME_M30,
            '1H': self._mt5.TIMEFRAME_H1,
            '4H': self._mt5.TIMEFRAME_H4,
            '1D': self._mt5.TIMEFRAME_D1,
            '1W': self._mt5.TIMEFRAME_W1,
            '1MN': self._mt5.TIMEFRAME_MN1
        }
        
        if timeframe not in timeframe_map:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        
        return timeframe_map[timeframe]
    
    def validate_connection(self) -> bool:
        """Validate MT5 connection"""
        if not self._mt5:
            return False
        
        try:
            terminal_info = self._mt5.terminal_info()
            return terminal_info is not None
        except:
            return False
    
    def get_available_symbols(self) -> List[str]:
        """Get available symbols from MT5"""
        if not self._mt5:
            return []
        
        try:
            symbols = self._mt5.symbols_get()
            return [s.name for s in symbols if s.visible]
        except:
            return []


class ParquetDataProvider(DataProvider):
    """Parquet file data provider"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.data_dir = Path(config.get('data_dir', 'data'))
        self.data_dir.mkdir(parents=True, exist_ok=True)
    
    async def fetch_data(self, query: DataQuery) -> pd.DataFrame:
        """Fetch data from parquet files"""
        all_data = []
        
        for symbol in query.symbols:
            file_pattern = f"{symbol}_{query.timeframe}_*.parquet"
            files = list(self.data_dir.glob(file_pattern))
            
            if not files:
                logger.warning(f"No parquet files found for {symbol}")
                continue
            
            symbol_data = []
            for file_path in files:
                try:
                    df = pd.read_parquet(file_path)
                    
                    # Filter by date range
                    if 'time' in df.columns:
                        df['time'] = pd.to_datetime(df['time'])
                        df.set_index('time', inplace=True)
                    
                    # Filter data within date range
                    mask = (df.index >= query.start_date) & (df.index <= query.end_date)
                    df = df[mask]
                    
                    if not df.empty:
                        df['symbol'] = symbol
                        symbol_data.append(df)
                        
                except Exception as e:
                    logger.error(f"Failed to read {file_path}: {e}")
                    continue
            
            if symbol_data:
                symbol_df = pd.concat(symbol_data)
                symbol_df.sort_index(inplace=True)
                all_data.append(symbol_df)
        
        if not all_data:
            return pd.DataFrame()
        
        combined_df = pd.concat(all_data)
        combined_df.sort_index(inplace=True)
        
        return combined_df
    
    def validate_connection(self) -> bool:
        """Validate data directory exists"""
        return self.data_dir.exists()
    
    def get_available_symbols(self) -> List[str]:
        """Get available symbols from parquet files"""
        symbols = set()
        for file_path in self.data_dir.glob("*.parquet"):
            # Extract symbol from filename (assuming format: SYMBOL_TIMEFRAME_DATE.parquet)
            parts = file_path.stem.split('_')
            if len(parts) >= 2:
                symbols.add(parts[0])
        
        return sorted(list(symbols))


class CSVDataProvider(DataProvider):
    """CSV file data provider"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.data_dir = Path(config.get('data_dir', 'data'))
        self.date_column = config.get('date_column', 'timestamp')
        self.date_format = config.get('date_format', '%Y-%m-%d %H:%M:%S')
    
    async def fetch_data(self, query: DataQuery) -> pd.DataFrame:
        """Fetch data from CSV files"""
        all_data = []
        
        for symbol in query.symbols:
            csv_file = self.data_dir / f"{symbol}_{query.timeframe}.csv"
            
            if not csv_file.exists():
                logger.warning(f"CSV file not found: {csv_file}")
                continue
            
            try:
                # Read CSV with flexible parsing
                df = pd.read_csv(csv_file)
                
                # Handle different date column names
                possible_date_cols = [self.date_column, 'time', 'timestamp', 'datetime', 'date']
                date_col = None
                
                for col in possible_date_cols:
                    if col in df.columns:
                        date_col = col
                        break
                
                if date_col is None:
                    logger.error(f"No date column found in {csv_file}")
                    continue
                
                # Parse dates
                df[date_col] = pd.to_datetime(df[date_col], format=self.date_format, errors='coerce')
                df.set_index(date_col, inplace=True)
                df.dropna(inplace=True)
                
                # Filter by date range
                mask = (df.index >= query.start_date) & (df.index <= query.end_date)
                df = df[mask]
                
                if not df.empty:
                    df['symbol'] = symbol
                    
                    # Standardize column names
                    column_mapping = {
                        'Open': 'open', 'OPEN': 'open',
                        'High': 'high', 'HIGH': 'high',
                        'Low': 'low', 'LOW': 'low',
                        'Close': 'close', 'CLOSE': 'close',
                        'Volume': 'volume', 'VOLUME': 'volume',
                        'Vol': 'volume', 'VOL': 'volume'
                    }
                    df.rename(columns=column_mapping, inplace=True)
                    
                    all_data.append(df)
                    
            except Exception as e:
                logger.error(f"Failed to read CSV {csv_file}: {e}")
                continue
        
        if not all_data:
            return pd.DataFrame()
        
        combined_df = pd.concat(all_data)
        combined_df.sort_index(inplace=True)
        
        return combined_df
    
    def validate_connection(self) -> bool:
        """Validate data directory exists"""
        return self.data_dir.exists()
    
    def get_available_symbols(self) -> List[str]:
        """Get available symbols from CSV files"""
        symbols = set()
        for file_path in self.data_dir.glob("*.csv"):
            # Extract symbol from filename
            parts = file_path.stem.split('_')
            if len(parts) >= 2:
                symbols.add(parts[0])
        
        return sorted(list(symbols))


class DatabaseDataProvider(DataProvider):
    """Database data provider"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.connection_string = config.get('connection_string', 'sqlite:///trading_data.db')
        self.table_name = config.get('table_name', 'ohlcv_data')
        
        from sqlalchemy import create_engine
        self.engine = create_engine(self.connection_string)
    
    async def fetch_data(self, query: DataQuery) -> pd.DataFrame:
        """Fetch data from database"""
        try:
            # Build SQL query
            symbol_list = "', '".join(query.symbols)
            sql_query = f"""
            SELECT * FROM {self.table_name}
            WHERE symbol IN ('{symbol_list}')
            AND timestamp BETWEEN '{query.start_date}' AND '{query.end_date}'
            AND timeframe = '{query.timeframe}'
            ORDER BY timestamp
            """
            
            # Execute query
            df = pd.read_sql(sql_query, self.engine, parse_dates=['timestamp'])
            df.set_index('timestamp', inplace=True)
            
            return df
            
        except Exception as e:
            logger.error(f"Database query failed: {e}")
            return pd.DataFrame()
    
    def validate_connection(self) -> bool:
        """Validate database connection"""
        try:
            with self.engine.connect() as conn:
                conn.execute("SELECT 1")
            return True
        except:
            return False
    
    def get_available_symbols(self) -> List[str]:
        """Get available symbols from database"""
        try:
            sql_query = f"SELECT DISTINCT symbol FROM {self.table_name}"
            df = pd.read_sql(sql_query, self.engine)
            return df['symbol'].tolist()
        except:
            return []


class AlphaVantageProvider(DataProvider):
    """Alpha Vantage API data provider"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.api_key = config.get('api_key')
        self.base_url = "https://www.alphavantage.co/query"
        
        if not self.api_key:
            raise ValueError("Alpha Vantage API key is required")
    
    async def fetch_data(self, query: DataQuery) -> pd.DataFrame:
        """Fetch data from Alpha Vantage API"""
        import aiohttp
        import json
        
        all_data = []
        
        async with aiohttp.ClientSession() as session:
            for symbol in query.symbols:
                try:
                    # Determine function based on timeframe
                    if query.timeframe in ['1D', 'Daily']:
                        function = 'TIME_SERIES_DAILY'
                        data_key = 'Time Series (Daily)'
                    elif query.timeframe in ['1W', 'Weekly']:
                        function = 'TIME_SERIES_WEEKLY'
                        data_key = 'Weekly Time Series'
                    else:
                        function = 'TIME_SERIES_INTRADAY'
                        data_key = f'Time Series ({query.timeframe})'
                    
                    params = {
                        'function': function,
                        'symbol': symbol,
                        'apikey': self.api_key,
                        'outputsize': 'full'
                    }
                    
                    if function == 'TIME_SERIES_INTRADAY':
                        params['interval'] = query.timeframe
                    
                    async with session.get(self.base_url, params=params) as response:
                        data = await response.json()
                        
                        if data_key not in data:
                            logger.warning(f"No data found for {symbol}")
                            continue
                        
                        # Convert to DataFrame
                        df = pd.DataFrame(data[data_key]).T
                        df.index = pd.to_datetime(df.index)
                        
                        # Standardize column names
                        df.columns = ['open', 'high', 'low', 'close', 'volume']
                        df = df.astype(float)
                        df['symbol'] = symbol
                        
                        # Filter by date range
                        mask = (df.index >= query.start_date) & (df.index <= query.end_date)
                        df = df[mask]
                        
                        if not df.empty:
                            all_data.append(df)
                            
                except Exception as e:
                    logger.error(f"Failed to fetch data for {symbol} from Alpha Vantage: {e}")
                    continue
        
        if not all_data:
            return pd.DataFrame()
        
        combined_df = pd.concat(all_data)
        combined_df.sort_index(inplace=True)
        
        return combined_df
    
    def validate_connection(self) -> bool:
        """Validate API connection"""
        try:
            import requests
            params = {
                'function': 'TIME_SERIES_DAILY',
                'symbol': 'AAPL',
                'apikey': self.api_key,
                'outputsize': 'compact'
            }
            response = requests.get(self.base_url, params=params, timeout=10)
            return response.status_code == 200
        except:
            return False
    
    def get_available_symbols(self) -> List[str]:
        """Get available symbols (Alpha Vantage supports most US stocks)"""
        # This would typically require a separate API call to get symbol list
        # For now, return common symbols
        return ['AAPL', 'GOOGL', 'MSFT', 'TSLA', 'AMZN', 'NVDA', 'META']


class InfluxDBDataProvider(DataProvider):
    """InfluxDB data provider for time series data"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.url = config.get('url', 'http://localhost:8086')
        self.token = config.get('token')
        self.org = config.get('org', 'trading')
        self.bucket = config.get('bucket', 'market_data')
        self.timeout = config.get('timeout', 30000)
        
        # Initialize InfluxDB client
        self._client = None
        self._query_api = None
        self._write_api = None
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize InfluxDB client connection"""
        try:
            from influxdb_client import InfluxDBClient
            from influxdb_client.client.query_api import QueryApi
            from influxdb_client.client.write_api import SYNCHRONOUS
            
            self._client = InfluxDBClient(
                url=self.url,
                token=self.token,
                org=self.org,
                timeout=self.timeout
            )
            
            self._query_api = self._client.query_api()
            self._write_api = self._client.write_api(write_options=SYNCHRONOUS)
            
            logger.info("InfluxDB client initialized successfully")
            
        except ImportError:
            logger.error("InfluxDB client package not found. Install with: pip install influxdb-client")
            raise
        except Exception as e:
            logger.error(f"InfluxDB client initialization failed: {e}")
            raise
    
    async def fetch_data(self, query: DataQuery) -> pd.DataFrame:
        """Fetch data from InfluxDB"""
        if not self._client or not self._query_api:
            raise ConnectionError("InfluxDB client not initialized")
        
        try:
            # Build Flux query based on data type
            if query.data_type == DataType.OHLCV:
                flux_query = self._build_ohlcv_query(query)
            elif query.data_type == DataType.TICK:
                flux_query = self._build_tick_query(query)
            else:
                flux_query = self._build_generic_query(query)
            
            logger.debug(f"Executing InfluxDB query: {flux_query}")
            
            # Execute query
            result = self._query_api.query_data_frame(flux_query)
            
            if result.empty:
                logger.warning(f"No data found in InfluxDB for symbols: {query.symbols}")
                return pd.DataFrame()
            
            # Transform to standard format
            processed_data = self._process_query_result(result, query)
            
            logger.info(f"Retrieved {len(processed_data)} records from InfluxDB")
            return processed_data
            
        except Exception as e:
            logger.error(f"InfluxDB query failed: {e}")
            return pd.DataFrame()
    
    def _build_ohlcv_query(self, query: DataQuery) -> str:
        """Build Flux query for OHLCV data"""
        symbols_filter = '|'.join(query.symbols)
        
        flux_query = f'''
            from(bucket: "{self.bucket}")
            |> range(start: {query.start_date.isoformat()}, stop: {query.end_date.isoformat()})
            |> filter(fn: (r) => r._measurement == "ohlcv")
            |> filter(fn: (r) => r.symbol =~ /{symbols_filter}/)
            |> filter(fn: (r) => r.timeframe == "{query.timeframe}")
            |> pivot(rowKey:["_time", "symbol"], columnKey: ["_field"], valueColumn: "_value")
            |> sort(columns: ["_time"])
        '''
        
        if query.limit:
            flux_query += f'\n|> limit(n: {query.limit})'
        
        return flux_query
    
    def _build_tick_query(self, query: DataQuery) -> str:
        """Build Flux query for tick data"""
        symbols_filter = '|'.join(query.symbols)
        
        flux_query = f'''
            from(bucket: "{self.bucket}")
            |> range(start: {query.start_date.isoformat()}, stop: {query.end_date.isoformat()})
            |> filter(fn: (r) => r._measurement == "ticks")
            |> filter(fn: (r) => r.symbol =~ /{symbols_filter}/)
            |> pivot(rowKey:["_time", "symbol"], columnKey: ["_field"], valueColumn: "_value")
            |> sort(columns: ["_time"])
        '''
        
        if query.limit:
            flux_query += f'\n|> limit(n: {query.limit})'
        
        return flux_query
    
    def _build_generic_query(self, query: DataQuery) -> str:
        """Build generic Flux query"""
        symbols_filter = '|'.join(query.symbols)
        
        flux_query = f'''
            from(bucket: "{self.bucket}")
            |> range(start: {query.start_date.isoformat()}, stop: {query.end_date.isoformat()})
            |> filter(fn: (r) => r.symbol =~ /{symbols_filter}/)
            |> sort(columns: ["_time"])
        '''
        
        if query.limit:
            flux_query += f'\n|> limit(n: {query.limit})'
        
        return flux_query
    
    def _process_query_result(self, result: pd.DataFrame, query: DataQuery) -> pd.DataFrame:
        """Process and standardize query result"""
        if result.empty:
            return result
        
        # Rename time column to standard format
        if '_time' in result.columns:
            result = result.rename(columns={'_time': 'time'})
        
        # Set time as index
        if 'time' in result.columns:
            result['time'] = pd.to_datetime(result['time'])
            result.set_index('time', inplace=True)
        
        # Drop InfluxDB metadata columns
        metadata_cols = ['result', 'table', '_start', '_stop', '_measurement']
        result = result.drop(columns=[col for col in metadata_cols if col in result.columns])
        
        # Ensure numeric columns are properly typed
        numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'bid', 'ask', 'last']
        for col in numeric_cols:
            if col in result.columns:
                result[col] = pd.to_numeric(result[col], errors='coerce')
        
        # Filter columns if specified
        if query.columns:
            available_cols = [col for col in query.columns if col in result.columns]
            if available_cols:
                # Always include symbol column if present
                if 'symbol' in result.columns and 'symbol' not in available_cols:
                    available_cols.append('symbol')
                result = result[available_cols]
        
        return result
    
    def validate_connection(self) -> bool:
        """Validate InfluxDB connection"""
        if not self._client:
            return False
        
        try:
            # Test connection with a simple ping
            health = self._client.health()
            return health.status == "pass"
        except Exception as e:
            logger.warning(f"InfluxDB connection validation failed: {e}")
            return False
    
    def get_available_symbols(self) -> List[str]:
        """Get available symbols from InfluxDB"""
        if not self._query_api:
            return []
        
        try:
            flux_query = f'''
                from(bucket: "{self.bucket}")
                |> range(start: -30d)
                |> filter(fn: (r) => r._measurement == "ohlcv" or r._measurement == "ticks")
                |> keep(columns: ["symbol"])
                |> distinct(column: "symbol")
                |> sort()
            '''
            
            result = self._query_api.query_data_frame(flux_query)
            
            if result.empty or 'symbol' not in result.columns:
                return []
            
            return sorted(result['symbol'].dropna().unique().tolist())
            
        except Exception as e:
            logger.error(f"Failed to get available symbols from InfluxDB: {e}")
            return []
    
    def get_available_timeframes(self) -> List[str]:
        """Get available timeframes from InfluxDB"""
        if not self._query_api:
            return []
        
        try:
            flux_query = f'''
                from(bucket: "{self.bucket}")
                |> range(start: -7d)
                |> filter(fn: (r) => r._measurement == "ohlcv")
                |> keep(columns: ["timeframe"])
                |> distinct(column: "timeframe")
                |> sort()
            '''
            
            result = self._query_api.query_data_frame(flux_query)
            
            if result.empty or 'timeframe' not in result.columns:
                return []
            
            return sorted(result['timeframe'].dropna().unique().tolist())
            
        except Exception as e:
            logger.error(f"Failed to get available timeframes from InfluxDB: {e}")
            return []
    
    def write_data(self, data: pd.DataFrame, measurement: str = "ohlcv") -> bool:
        """Write data to InfluxDB"""
        if not self._write_api:
            logger.error("InfluxDB write API not initialized")
            return False
        
        try:
            from influxdb_client import Point
            from influxdb_client.client.write_api import SYNCHRONOUS
            
            points = []
            
            for index, row in data.iterrows():
                point = Point(measurement)
                
                # Add timestamp
                if isinstance(index, pd.Timestamp):
                    point.time(index)
                
                # Add symbol as tag
                if 'symbol' in row:
                    point.tag("symbol", row['symbol'])
                
                # Add timeframe as tag if present
                if 'timeframe' in row:
                    point.tag("timeframe", row['timeframe'])
                
                # Add numeric fields
                numeric_cols = row.select_dtypes(include=[np.number]).index
                for col in numeric_cols:
                    if col not in ['symbol', 'timeframe'] and pd.notna(row[col]):
                        point.field(col, float(row[col]))
                
                points.append(point)
            
            # Write points to InfluxDB
            self._write_api.write(bucket=self.bucket, record=points)
            
            logger.info(f"Successfully wrote {len(points)} points to InfluxDB")
            return True
            
        except Exception as e:
            logger.error(f"Failed to write data to InfluxDB: {e}")
            return False
    
    def create_retention_policy(self, name: str, duration: str = "30d") -> bool:
        """Create retention policy for automatic data cleanup"""
        try:
            # This would typically be done through InfluxDB admin interface
            # or using the management API
            logger.info(f"Retention policy creation: {name} with duration {duration}")
            return True
        except Exception as e:
            logger.error(f"Failed to create retention policy: {e}")
            return False
    
    def close(self):
        """Close InfluxDB client connection"""
        if self._client:
            try:
                self._client.close()
                logger.info("InfluxDB client connection closed")
            except Exception as e:
                logger.warning(f"Error closing InfluxDB client: {e}")


class TimescaleDBDataProvider(DataProvider):
    """TimescaleDB data provider for time series data"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.connection_string = config.get('connection_string')
        self.table_name = config.get('table_name', 'ohlcv_data')
        self.hypertable_time_column = config.get('time_column', 'timestamp')
        
        if not self.connection_string:
            raise ValueError("TimescaleDB connection string is required")
        
        from sqlalchemy import create_engine
        self.engine = create_engine(self.connection_string)
        
    async def fetch_data(self, query: DataQuery) -> pd.DataFrame:
        """Fetch data from TimescaleDB"""
        try:
            # Build SQL query with time-based optimization
            symbol_list = "', '".join(query.symbols)
            
            sql_query = f"""
            SELECT * FROM {self.table_name}
            WHERE symbol IN ('{symbol_list}')
            AND {self.hypertable_time_column} BETWEEN '{query.start_date}' AND '{query.end_date}'
            AND timeframe = '{query.timeframe}'
            ORDER BY {self.hypertable_time_column}
            """
            
            if query.limit:
                sql_query += f" LIMIT {query.limit}"
            
            # Execute query with time-based partitioning benefits
            df = pd.read_sql(
                sql_query, 
                self.engine, 
                parse_dates=[self.hypertable_time_column]
            )
            
            if not df.empty:
                df.set_index(self.hypertable_time_column, inplace=True)
            
            return df
            
        except Exception as e:
            logger.error(f"TimescaleDB query failed: {e}")
            return pd.DataFrame()
    
    def validate_connection(self) -> bool:
        """Validate TimescaleDB connection"""
        try:
            with self.engine.connect() as conn:
                # Test with TimescaleDB specific query
                result = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
                return result.rowcount > 0
        except:
            return False
    
    def get_available_symbols(self) -> List[str]:
        """Get available symbols from TimescaleDB"""
        try:
            sql_query = f"SELECT DISTINCT symbol FROM {self.table_name} ORDER BY symbol"
            df = pd.read_sql(sql_query, self.engine)
            return df['symbol'].tolist()
        except:
            return []