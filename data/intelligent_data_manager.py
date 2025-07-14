"""
Comprehensive Data Management Module
===================================

This module handles intelligent data fetching, storage, and retrieval for the trading system.
It provides smart caching with InfluxDB to avoid redundant data fetching and optimize performance.

Key Features:
- Check existing data in InfluxDB before fetching
- Fetch only missing data gaps
- Support for multiple data providers
- Efficient bulk data operations
- Data validation and cleanup

Author: Trading System v3.0
Date: July 2025
"""

import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Union
import logging
from collections import defaultdict

try:
    from influxdb_client import InfluxDBClient, Point, WritePrecision
    from influxdb_client.client.write_api import SYNCHRONOUS
    INFLUXDB_AVAILABLE = True
except ImportError:
    INFLUXDB_AVAILABLE = False

logger = logging.getLogger(__name__)


class DataGap:
    """Represents a gap in data that needs to be fetched"""
    
    def __init__(self, symbol: str, start_date: datetime, end_date: datetime, interval: str):
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.interval = interval
    
    def __repr__(self):
        return f"DataGap({self.symbol}, {self.start_date} -> {self.end_date}, {self.interval})"


class IntelligentDataManager:
    """
    Intelligent data management with InfluxDB caching and gap detection
    """
    
    def __init__(self, config, influxdb_manager):
        self.config = config
        self.influxdb_manager = influxdb_manager
        
        # Data providers
        self.data_providers = {}
        
        # Cache settings
        self.data_retention_days = 365  # Keep data for 1 year
        self.batch_size = 1000  # Points per batch for InfluxDB writes
        
        logger.info("Intelligent Data Manager initialized")
    
    def register_data_provider(self, provider_name: str, provider_instance):
        """Register a data provider for fetching data"""
        self.data_providers[provider_name] = provider_instance
        logger.info(f"Registered data provider: {provider_name}")
    
    def get_historical_data_intelligent(self, symbols: Union[str, List[str]], interval: str, 
                                      start_date: Union[str, datetime], end_date: Optional[Union[str, datetime]] = None,
                                      data_provider: str = 'ctrader', force_refresh: bool = False) -> Dict[str, pd.Series]:
        """
        Intelligently fetch historical data with caching and gap detection
        
        Args:
            symbols: Single symbol or list of symbols
            interval: Time interval (e.g., 'M15', 'H1', 'D1')
            start_date: Start date for data
            end_date: End date for data (default: now)
            data_provider: Which provider to use for fetching new data
            force_refresh: Force re-fetch all data ignoring cache
            
        Returns:
            Dict mapping symbol -> pandas Series with price data
        """
        # Normalize inputs
        if isinstance(symbols, str):
            symbols = [symbols]
        
        if isinstance(start_date, str):
            start_date = pd.to_datetime(start_date)
        if end_date is None:
            end_date = datetime.now()
        elif isinstance(end_date, str):
            end_date = pd.to_datetime(end_date)
        
        logger.info(f"Fetching data for {len(symbols)} symbols from {start_date} to {end_date}")
        logger.info(f"Provider: {data_provider}, Interval: {interval}, Force refresh: {force_refresh}")
        
        result = {}
        
        # Handle cTrader specially - bulk fetch all symbols in one reactor session
        if data_provider.lower() == 'ctrader':
            logger.info(f"cTrader provider detected - using bulk fetch strategy")
            
            if force_refresh:
                logger.info(f"Force refresh enabled - fetching all data for {len(symbols)} symbols")
                # Force fetch all data in bulk
                bulk_data = self._fetch_bulk_data_ctrader(symbols, interval, start_date, end_date, data_provider)
                result.update(bulk_data)
            else:
                # Smart fetch with cTrader optimization
                symbols_to_fetch = []
                
                # First pass: check what symbols need fetching
                for symbol in symbols:
                    existing_data = self._get_existing_data_from_influx(symbol, interval, start_date, end_date)
                    
                    if existing_data.empty:
                        logger.info(f"No existing data for {symbol} - adding to bulk fetch")
                        symbols_to_fetch.append(symbol)
                        result[symbol] = pd.Series(dtype=float, name=symbol)  # Placeholder
                    else:
                        # Check for gaps
                        gaps = self._detect_data_gaps(symbol, interval, start_date, end_date, existing_data)
                        if gaps:
                            logger.info(f"Found {len(gaps)} gaps for {symbol} - adding to bulk fetch")
                            symbols_to_fetch.append(symbol)
                            result[symbol] = existing_data  # Use existing as base
                        else:
                            logger.info(f"Complete data available for {symbol} - using cached")
                            result[symbol] = existing_data
                
                # Bulk fetch symbols that need data
                if symbols_to_fetch:
                    logger.info(f"Bulk fetching {len(symbols_to_fetch)} symbols from cTrader")
                    bulk_data = self._fetch_bulk_data_ctrader(symbols_to_fetch, interval, start_date, end_date, data_provider)
                    
                    # Merge bulk data with existing data where necessary
                    for symbol in symbols_to_fetch:
                        new_data = bulk_data.get(symbol, pd.Series(dtype=float, name=symbol))
                        existing_data = result.get(symbol, pd.Series(dtype=float, name=symbol))
                        
                        if not new_data.empty:
                            if not existing_data.empty:
                                # Normalize timezones before merging
                                existing_data = self._normalize_timezone(existing_data)
                                new_data = self._normalize_timezone(new_data)
                                
                                # Merge new data with existing
                                combined_data = pd.concat([existing_data, new_data]).sort_index().drop_duplicates()
                                result[symbol] = combined_data[(combined_data.index >= start_date) & (combined_data.index <= end_date)]
                            else:
                                result[symbol] = self._normalize_timezone(new_data)
                        # If new_data is empty, keep existing_data (which might also be empty)
                        else:
                            if not existing_data.empty:
                                result[symbol] = self._normalize_timezone(existing_data)
        
        else:
            # Handle MT5 and other providers with individual fetching
            logger.info(f"Using individual fetch strategy for {data_provider}")
            
            for symbol in symbols:
                try:
                    if force_refresh:
                        logger.info(f"Force refresh enabled - fetching all data for {symbol}")
                        data = self._fetch_and_store_data(symbol, interval, start_date, end_date, data_provider)
                    else:
                        # Smart fetch with gap detection
                        data = self._smart_fetch_data(symbol, interval, start_date, end_date, data_provider)
                    
                    # Normalize timezone
                    data = self._normalize_timezone(data)
                    result[symbol] = data
                    logger.info(f"Retrieved {len(data)} data points for {symbol}")
                    
                except Exception as e:
                    logger.error(f"Error fetching data for {symbol}: {e}")
                    result[symbol] = pd.Series(dtype=float, name=symbol)
        
        # Log final results
        successful_symbols = [s for s, d in result.items() if not d.empty]
        failed_symbols = [s for s, d in result.items() if d.empty]
        
        logger.info(f"Data fetching complete for {data_provider}:")
        logger.info(f"  Successfully retrieved: {len(successful_symbols)}/{len(symbols)} symbols")
        if failed_symbols:
            logger.warning(f"  Failed to retrieve: {', '.join(failed_symbols)}")
        
        return result
    
    def _smart_fetch_data(self, symbol: str, interval: str, start_date: datetime, 
                         end_date: datetime, data_provider: str) -> pd.Series:
        """
        Smart data fetching with gap detection and partial updates
        """
        # Step 1: Check what data exists in InfluxDB
        existing_data = self._get_existing_data_from_influx(symbol, interval, start_date, end_date)
        
        if existing_data.empty:
            logger.info(f"No existing data found for {symbol} - will fetch in bulk if cTrader")
            if data_provider.lower() == 'ctrader':
                # For cTrader, we'll handle this in the bulk method
                return pd.Series(dtype=float, name=symbol)
            else:
                return self._fetch_and_store_data(symbol, interval, start_date, end_date, data_provider)
        
        # Step 2: Detect gaps in existing data
        gaps = self._detect_data_gaps(symbol, interval, start_date, end_date, existing_data)
        
        if not gaps:
            logger.info(f"Complete data already available for {symbol} - using cached data")
            return existing_data
        
        logger.info(f"Found {len(gaps)} data gaps for {symbol}")
        
        # Step 3: Handle gaps based on provider
        if data_provider.lower() == 'ctrader':
            # For cTrader, we need to fetch all missing data in one go to avoid reactor issues
            logger.info(f"cTrader provider detected - will handle gaps in bulk fetch")
            # Return existing data for now, bulk fetch will handle the gaps
            return existing_data
        else:
            # For MT5, fetch gaps individually
            logger.info(f"Fetching missing data gaps individually for {symbol}")
            for gap in gaps:
                logger.info(f"Fetching gap: {gap}")
                gap_data = self._fetch_and_store_data(gap.symbol, gap.interval, gap.start_date, gap.end_date, data_provider)
                
                if not gap_data.empty:
                    # Normalize timezones before merging
                    existing_data = self._normalize_timezone(existing_data)
                    gap_data = self._normalize_timezone(gap_data)
                    
                    # Merge with existing data
                    existing_data = pd.concat([existing_data, gap_data]).sort_index().drop_duplicates()
        
        # Step 4: Return final combined dataset
        final_data = existing_data[(existing_data.index >= start_date) & (existing_data.index <= end_date)]
        logger.info(f"Final dataset for {symbol}: {len(final_data)} points")
        
        return final_data
    
    def _get_existing_data_from_influx(self, symbol: str, interval: str, 
                                     start_date: datetime, end_date: datetime) -> pd.Series:
        """Get existing data from InfluxDB"""
        try:
            if not self.influxdb_manager.query_api:
                return pd.Series(dtype=float, name=symbol)
            
            # Convert dates to proper format
            start_str = start_date.strftime('%Y-%m-%dT%H:%M:%SZ')
            end_str = end_date.strftime('%Y-%m-%dT%H:%M:%SZ')
            
            # Query for historical data from any source (ctrader_bulk, mt5, etc.)
            query = f'''
                from(bucket: "{self.influxdb_manager.bucket}")
                |> range(start: {start_str}, stop: {end_str})
                |> filter(fn: (r) => r["_measurement"] == "historical_data")
                |> filter(fn: (r) => r["symbol"] == "{symbol}")
                |> filter(fn: (r) => r["interval"] == "{interval}")
                |> filter(fn: (r) => r["_field"] == "close" or r["_field"] == "price")
                |> sort(columns: ["_time"])
            '''
            
            result = self.influxdb_manager.query_api.query(org=self.influxdb_manager.org, query=query)
            
            timestamps = []
            prices = []
            
            for table in result:
                for record in table.records:
                    timestamps.append(record.get_time())
                    prices.append(record.get_value())
            
            if timestamps and prices:
                series = pd.Series(prices, index=pd.DatetimeIndex(timestamps), name=symbol)
                # Remove duplicates and sort
                series = series.sort_index().drop_duplicates()
                # Normalize timezone
                series = self._normalize_timezone(series)
                logger.debug(f"Retrieved {len(series)} existing data points for {symbol} from InfluxDB")
                return series
            else:
                return pd.Series(dtype=float, name=symbol)
                
        except Exception as e:
            logger.warning(f"Error retrieving existing data for {symbol}: {e}")
            return pd.Series(dtype=float, name=symbol)
    
    def _detect_data_gaps(self, symbol: str, interval: str, start_date: datetime, 
                         end_date: datetime, existing_data: pd.Series) -> List[DataGap]:
        """Detect gaps in existing data that need to be filled"""
        gaps = []
        
        if existing_data.empty:
            gaps.append(DataGap(symbol, start_date, end_date, interval))
            return gaps
        
        # Get expected frequency based on interval
        freq = self._interval_to_frequency(interval)
        if not freq:
            logger.warning(f"Unknown interval {interval}, cannot detect gaps")
            return gaps
        
        # Create expected index
        expected_index = pd.date_range(start=start_date, end=end_date, freq=freq)
        existing_index = existing_data.index
        
        # Find missing periods
        missing_periods = expected_index.difference(existing_index)
        
        if len(missing_periods) == 0:
            return gaps  # No gaps
        
        # Group consecutive missing periods into gaps
        if len(missing_periods) > 0:
            # Group consecutive timestamps
            groups = []
            current_group = [missing_periods[0]]
            
            for i in range(1, len(missing_periods)):
                current_ts = missing_periods[i]
                prev_ts = missing_periods[i-1]
                
                # Check if this timestamp is consecutive to the previous one
                expected_next = prev_ts + pd.Timedelta(freq)
                if current_ts <= expected_next + pd.Timedelta(freq):  # Allow some tolerance
                    current_group.append(current_ts)
                else:
                    # Start a new group
                    groups.append(current_group)
                    current_group = [current_ts]
            
            # Add the last group
            groups.append(current_group)
            
            # Convert groups to gaps
            for group in groups:
                gap_start = min(group)
                gap_end = max(group)
                
                # Extend gap slightly to ensure we get all needed data
                gap_start = gap_start - pd.Timedelta(freq)
                gap_end = gap_end + pd.Timedelta(freq)
                
                # Ensure gap is within requested range
                gap_start = max(gap_start, start_date)
                gap_end = min(gap_end, end_date)
                
                if gap_start < gap_end:
                    gaps.append(DataGap(symbol, gap_start, gap_end, interval))
        
        logger.debug(f"Detected {len(gaps)} gaps for {symbol}")
        return gaps
    
    def _normalize_timezone(self, data: pd.Series) -> pd.Series:
        """Normalize timezone to UTC or remove timezone info to avoid comparison issues"""
        if data.empty:
            return data
        
        try:
            if data.index.tz is not None:
                # Convert to UTC and then remove timezone info
                data_utc = data.copy()
                data_utc.index = data_utc.index.tz_convert('UTC').tz_localize(None)
                return data_utc
            else:
                # Already timezone-naive
                return data
        except Exception as e:
            logger.warning(f"Error normalizing timezone for data: {e}")
            # If there's an error, try to remove timezone info entirely
            try:
                data_copy = data.copy()
                if hasattr(data_copy.index, 'tz_localize'):
                    data_copy.index = data_copy.index.tz_localize(None)
                return data_copy
            except:
                return data
    
    def _interval_to_frequency(self, interval: str) -> Optional[str]:
        """Convert trading interval to pandas frequency"""
        interval_map = {
            'M1': '1T',    # 1 minute
            'M5': '5T',    # 5 minutes
            'M15': '15T',  # 15 minutes
            'M30': '30T',  # 30 minutes
            'H1': '1H',    # 1 hour
            'H4': '4H',    # 4 hours
            'D1': '1D',    # 1 day
        }
        return interval_map.get(interval)
    
    def _fetch_and_store_data(self, symbol: str, interval: str, start_date: datetime, 
                            end_date: datetime, data_provider: str) -> pd.Series:
        """Fetch data from provider and store in InfluxDB"""
        try:
            # Get the data provider
            if data_provider not in self.data_providers:
                raise ValueError(f"Data provider '{data_provider}' not registered")
            
            provider = self.data_providers[data_provider]
            
            # Fetch data from provider
            logger.info(f"Fetching {symbol} data from {data_provider} ({start_date} to {end_date})")
            
            # Call the provider's get_historical_data method
            if hasattr(provider, 'get_historical_data'):
                raw_data = provider.get_historical_data(symbol, interval, start_date, end_date)
            else:
                logger.error(f"Provider {data_provider} does not support get_historical_data")
                return pd.Series(dtype=float, name=symbol)
            
            # Handle the response (could be Series or dict)
            if isinstance(raw_data, dict):
                data = raw_data.get(symbol, pd.Series(dtype=float, name=symbol))
            else:
                data = raw_data
            
            if data.empty:
                logger.warning(f"No data returned for {symbol} from {data_provider}")
                return pd.Series(dtype=float, name=symbol)
            
            # Normalize timezone
            data = self._normalize_timezone(data)
            
            # Store in InfluxDB
            self._store_data_in_influx(symbol, interval, data, data_provider)
            
            logger.info(f"Successfully fetched and stored {len(data)} points for {symbol}")
            return data
            
        except Exception as e:
            logger.error(f"Error fetching data for {symbol} from {data_provider}: {e}")
            return pd.Series(dtype=float, name=symbol)
    
    def _fetch_bulk_data_ctrader(self, symbols: List[str], interval: str, start_date: datetime, 
                               end_date: datetime, data_provider: str) -> Dict[str, pd.Series]:
        """Fetch data for multiple symbols from cTrader in one reactor session"""
        try:
            # Get the data provider
            if data_provider not in self.data_providers:
                raise ValueError(f"Data provider '{data_provider}' not registered")
            
            provider = self.data_providers[data_provider]
            
            # Fetch all data at once for cTrader
            logger.info(f"Fetching bulk data for {len(symbols)} symbols from {data_provider} ({start_date} to {end_date})")
            
            # Call the provider's get_historical_data method with all symbols
            if hasattr(provider, 'get_historical_data'):
                bulk_data = provider.get_historical_data(symbols, interval, start_date, end_date)
            else:
                logger.error(f"Provider {data_provider} does not support get_historical_data")
                return {symbol: pd.Series(dtype=float, name=symbol) for symbol in symbols}
            
            # Handle the response
            if not isinstance(bulk_data, dict):
                logger.error(f"Expected dict response from {data_provider}, got {type(bulk_data)}")
                return {symbol: pd.Series(dtype=float, name=symbol) for symbol in symbols}
            
            # Store each symbol's data in InfluxDB
            result = {}
            for symbol in symbols:
                data = bulk_data.get(symbol, pd.Series(dtype=float, name=symbol))
                if not data.empty:
                    # Normalize timezone
                    data = self._normalize_timezone(data)
                    self._store_data_in_influx(symbol, interval, data, data_provider)
                    logger.info(f"Successfully stored {len(data)} points for {symbol}")
                else:
                    logger.warning(f"No data returned for {symbol} from {data_provider}")
                result[symbol] = data
            
            logger.info(f"Successfully fetched and stored bulk data for {len([s for s, d in result.items() if not d.empty])}/{len(symbols)} symbols")
            return result
            
        except Exception as e:
            logger.error(f"Error fetching bulk data from {data_provider}: {e}")
            return {symbol: pd.Series(dtype=float, name=symbol) for symbol in symbols}
    
    def _store_data_in_influx(self, symbol: str, interval: str, data: pd.Series, source: str):
        """Store data in InfluxDB efficiently"""
        try:
            if not self.influxdb_manager.write_api or data.empty:
                return
            
            logger.debug(f"Storing {len(data)} data points for {symbol} in InfluxDB")
            
            # Prepare points in batches
            points = []
            
            for timestamp, price in data.items():
                if pd.isna(price):
                    continue
                
                point = (
                    Point("historical_data")
                    .tag("symbol", symbol)
                    .tag("interval", interval)
                    .tag("source", source)
                    .field("close", float(price))
                    .field("price", float(price))  # Alias for compatibility
                    .time(timestamp, WritePrecision.NS)
                )
                points.append(point)
                
                # Write in batches
                if len(points) >= self.batch_size:
                    self.influxdb_manager.write_api.write(
                        bucket=self.influxdb_manager.bucket, 
                        org=self.influxdb_manager.org, 
                        record=points
                    )
                    points = []
            
            # Write remaining points
            if points:
                self.influxdb_manager.write_api.write(
                    bucket=self.influxdb_manager.bucket, 
                    org=self.influxdb_manager.org, 
                    record=points
                )
            
            logger.debug(f"Successfully stored {len(data)} points for {symbol}")
            
        except Exception as e:
            logger.error(f"Error storing data for {symbol} in InfluxDB: {e}")
    
    def cleanup_old_data(self, days_to_keep: int = None):
        """Clean up old data from InfluxDB to manage storage"""
        try:
            if not self.influxdb_manager.write_api:
                return
            
            days = days_to_keep or self.data_retention_days
            cutoff_date = datetime.now() - timedelta(days=days)
            
            logger.info(f"Cleaning up data older than {cutoff_date}")
            
            # Delete old data
            delete_api = self.influxdb_manager.client.delete_api()
            
            start = "1970-01-01T00:00:00Z"
            stop = cutoff_date.strftime('%Y-%m-%dT%H:%M:%SZ')
            
            delete_api.delete(
                start, stop,
                '_measurement="historical_data"',
                bucket=self.influxdb_manager.bucket,
                org=self.influxdb_manager.org
            )
            
            logger.info("Old data cleanup completed")
            
        except Exception as e:
            logger.error(f"Error cleaning up old data: {e}")
    
    def get_data_coverage_report(self, symbols: List[str], interval: str, 
                               start_date: datetime, end_date: datetime) -> Dict[str, Dict]:
        """Generate a report on data coverage for symbols"""
        report = {}
        
        for symbol in symbols:
            existing_data = self._get_existing_data_from_influx(symbol, interval, start_date, end_date)
            gaps = self._detect_data_gaps(symbol, interval, start_date, end_date, existing_data)
            
            # Calculate coverage percentage
            expected_freq = self._interval_to_frequency(interval)
            if expected_freq:
                expected_points = len(pd.date_range(start=start_date, end=end_date, freq=expected_freq))
                actual_points = len(existing_data)
                coverage_pct = (actual_points / expected_points * 100) if expected_points > 0 else 0
            else:
                coverage_pct = 0
            
            report[symbol] = {
                'existing_points': len(existing_data),
                'gaps_count': len(gaps),
                'coverage_percentage': coverage_pct,
                'first_data_point': existing_data.index.min() if not existing_data.empty else None,
                'last_data_point': existing_data.index.max() if not existing_data.empty else None,
                'gaps': [
                    {
                        'start': gap.start_date,
                        'end': gap.end_date,
                        'duration': gap.end_date - gap.start_date
                    }
                    for gap in gaps
                ]
            }
        
        return report
    
    def prefetch_backtest_data(self, symbols: List[str], interval: str, 
                             start_date: Union[str, datetime], end_date: Optional[Union[str, datetime]] = None,
                             data_provider: str = 'ctrader', force_refresh: bool = False) -> Dict[str, pd.Series]:
        """
        Pre-fetch and cache all data needed for backtesting
        
        This is the main method to be called before running backtests to ensure
        all required data is available and cached locally.
        
        For cTrader: Uses bulk fetch in one reactor session
        For MT5: Uses individual fetch with gap detection
        """
        logger.info("="*60)
        logger.info("STARTING BACKTEST DATA PRE-FETCH")
        logger.info("="*60)
        
        # Normalize dates
        if isinstance(start_date, str):
            start_date = pd.to_datetime(start_date)
        if end_date is None:
            end_date = datetime.now()
        elif isinstance(end_date, str):
            end_date = pd.to_datetime(end_date)
        
        logger.info(f"Symbols: {symbols}")
        logger.info(f"Interval: {interval}")
        logger.info(f"Date Range: {start_date} to {end_date}")
        logger.info(f"Data Provider: {data_provider}")
        logger.info(f"Force Refresh: {force_refresh}")
        
        if data_provider.lower() == 'ctrader':
            logger.info("="*50)
            logger.info("CTRADER BULK FETCH STRATEGY")
            logger.info("="*50)
            logger.info("Using optimized bulk fetch to get all data in one reactor session")
            logger.info("This avoids cTrader connection issues and is much faster")
        
        # First, generate coverage report (unless force refresh)
        if not force_refresh:
            logger.info("Analyzing existing data coverage...")
            coverage_report = self.get_data_coverage_report(symbols, interval, start_date, end_date)
            
            total_coverage = sum(r['coverage_percentage'] for r in coverage_report.values()) / len(coverage_report) if coverage_report else 0
            logger.info(f"Average data coverage: {total_coverage:.1f}%")
            
            # Log coverage for each symbol
            for symbol, report in coverage_report.items():
                logger.info(f"{symbol}: {report['coverage_percentage']:.1f}% coverage, {report['gaps_count']} gaps")
        
        # Fetch data intelligently with provider-specific optimization
        logger.info("Starting intelligent data fetch...")
        data = self.get_historical_data_intelligent(
            symbols=symbols,
            interval=interval, 
            start_date=start_date,
            end_date=end_date,
            data_provider=data_provider,
            force_refresh=force_refresh
        )
        
        # Validate results
        successful_symbols = [s for s, d in data.items() if not d.empty]
        failed_symbols = [s for s, d in data.items() if d.empty]
        
        logger.info("="*60)
        logger.info("BACKTEST DATA PRE-FETCH COMPLETE")
        logger.info("="*60)
        logger.info(f"Successfully fetched: {len(successful_symbols)}/{len(symbols)} symbols")
        
        if successful_symbols:
            logger.info(f"Success: {', '.join(successful_symbols)}")
        
        if failed_symbols:
            logger.warning(f"Failed: {', '.join(failed_symbols)}")
        
        # Log data statistics
        for symbol, series in data.items():
            if not series.empty:
                logger.info(f"{symbol}: {len(series)} points ({series.index.min()} to {series.index.max()})")
        
        if data_provider.lower() == 'ctrader':
            logger.info("="*50)
            logger.info("CTRADER BULK FETCH COMPLETED")
            logger.info("="*50)
            logger.info("All cTrader data was fetched in one optimized reactor session")
        
        return data


class BacktestDataManager:
    """
    Specialized data manager for backtesting operations
    """
    
    def __init__(self, config, influxdb_manager):
        self.config = config
        self.intelligent_data_manager = IntelligentDataManager(config, influxdb_manager)
        
    def register_data_providers(self, providers: Dict[str, object]):
        """Register multiple data providers"""
        for name, provider in providers.items():
            self.intelligent_data_manager.register_data_provider(name, provider)
    
    def prepare_backtest_data(self, data_provider: str = 'ctrader', force_refresh: bool = False) -> Dict[str, pd.Series]:
        """
        Prepare all data needed for backtesting
        
        This method handles the complete data preparation pipeline:
        1. Check existing data in InfluxDB
        2. Identify gaps and missing data
        3. Fetch only required missing data (using bulk fetch for cTrader)
        4. Store everything in InfluxDB
        5. Return ready-to-use dataset
        """
        # Extract unique symbols from pairs
        all_symbols = set()
        for pair in self.config.pairs:
            symbols = pair.split('-')
            all_symbols.update(symbols)
        
        all_symbols = list(all_symbols)
        
        logger.info(f"Preparing backtest data for {len(all_symbols)} symbols from {len(self.config.pairs)} pairs")
        logger.info(f"Data provider: {data_provider}")
        
        if data_provider.lower() == 'ctrader':
            logger.info("cTrader provider detected - using optimized bulk fetch strategy")
            logger.info("All data will be fetched in one reactor session to avoid connection issues")
        
        # Use intelligent data manager to fetch data with provider-specific optimization
        return self.intelligent_data_manager.get_historical_data_intelligent(
            symbols=all_symbols,
            interval=self.config.interval,
            start_date=self.config.start_date,
            end_date=self.config.end_date,
            data_provider=data_provider,
            force_refresh=force_refresh
        )
    
    def get_pair_data(self, pair: str, data_cache: Dict[str, pd.Series]) -> Tuple[pd.Series, pd.Series]:
        """Extract pair data from the data cache"""
        symbol1, symbol2 = pair.split('-')
        
        data1 = data_cache.get(symbol1, pd.Series(dtype=float))
        data2 = data_cache.get(symbol2, pd.Series(dtype=float))
        
        if data1.empty or data2.empty:
            logger.warning(f"Missing data for pair {pair}: {symbol1}={'empty' if data1.empty else 'ok'}, {symbol2}={'empty' if data2.empty else 'ok'})")
            return pd.Series(dtype=float), pd.Series(dtype=float)
        
        # Normalize timezones before alignment
        data1 = self.intelligent_data_manager._normalize_timezone(data1)
        data2 = self.intelligent_data_manager._normalize_timezone(data2)
        
        # Align the series by index
        aligned_data = pd.concat([data1, data2], axis=1, keys=[symbol1, symbol2]).dropna()
        
        if aligned_data.empty:
            logger.warning(f"No aligned data available for pair {pair}")
            return pd.Series(dtype=float), pd.Series(dtype=float)
        
        return aligned_data[symbol1], aligned_data[symbol2]
    
    def validate_data_quality(self, data_cache: Dict[str, pd.Series]) -> Dict[str, Dict]:
        """Validate the quality of fetched data"""
        quality_report = {}
        
        for symbol, data in data_cache.items():
            if data.empty:
                quality_report[symbol] = {
                    'status': 'EMPTY',
                    'points': 0,
                    'issues': ['No data available']
                }
                continue
            
            issues = []
            
            # Check for NaN values
            nan_count = data.isna().sum()
            if nan_count > 0:
                issues.append(f'{nan_count} NaN values')
            
            # Check for zero or negative prices
            invalid_prices = (data <= 0).sum()
            if invalid_prices > 0:
                issues.append(f'{invalid_prices} invalid prices (<=0)')
            
            # Check for data gaps (basic check)
            if len(data) < 100:  # Minimum data points
                issues.append('Insufficient data points')
            
            # Check date range coverage
            date_range = data.index.max() - data.index.min()
            expected_range = pd.to_datetime(self.config.end_date or datetime.now()) - pd.to_datetime(self.config.start_date)
            coverage = (date_range.total_seconds() / expected_range.total_seconds()) * 100
            
            if coverage < 80:  # Less than 80% coverage
                issues.append(f'Low date coverage: {coverage:.1f}%')
            
            status = 'GOOD' if not issues else 'WARNING' if len(issues) <= 2 else 'POOR'
            
            quality_report[symbol] = {
                'status': status,
                'points': len(data),
                'date_range': f"{data.index.min()} to {data.index.max()}",
                'coverage_pct': coverage,
                'issues': issues
            }
        
        return quality_report
