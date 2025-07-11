"""
Real-time data streaming and live data management
"""

import asyncio
import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set
from dataclasses import dataclass
from enum import Enum
import queue

import pandas as pd
import numpy as np
from threading import Event, Thread

from .data_manager import DataQuery, DataType, DataSource

logger = logging.getLogger(__name__)


class StreamState(Enum):
    """Stream states"""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class Tick:
    """Individual tick data"""
    symbol: str
    timestamp: datetime
    bid: float
    ask: float
    last: float
    volume: float
    spread: float = 0.0
    
    def __post_init__(self):
        self.spread = self.ask - self.bid if self.ask > 0 and self.bid > 0 else 0.0


@dataclass
class StreamConfig:
    """Stream configuration"""
    symbols: List[str]
    buffer_size: int = 10000
    update_interval: float = 0.1  # seconds
    auto_reconnect: bool = True
    max_reconnect_attempts: int = 5
    reconnect_delay: float = 5.0
    enable_compression: bool = True
    batch_size: int = 100


class DataBuffer:
    """Thread-safe circular buffer for streaming data"""
    
    def __init__(self, maxsize: int = 10000):
        self.maxsize = maxsize
        self._buffer = deque(maxlen=maxsize)
        self._lock = threading.RLock()
        self._timestamps = deque(maxlen=maxsize)
    
    def append(self, data: Any) -> None:
        """Add data to buffer"""
        with self._lock:
            timestamp = datetime.now()
            self._buffer.append(data)
            self._timestamps.append(timestamp)
    
    def extend(self, data: List[Any]) -> None:
        """Add multiple data points"""
        with self._lock:
            timestamp = datetime.now()
            for item in data:
                self._buffer.append(item)
                self._timestamps.append(timestamp)
    
    def get_latest(self, n: int = 1) -> List[Any]:
        """Get latest n items"""
        with self._lock:
            if n == 1:
                return [self._buffer[-1]] if self._buffer else []
            return list(self._buffer)[-n:] if len(self._buffer) >= n else list(self._buffer)
    
    def get_range(self, start_time: datetime, end_time: datetime) -> List[Any]:
        """Get data within time range"""
        with self._lock:
            result = []
            for i, ts in enumerate(self._timestamps):
                if start_time <= ts <= end_time:
                    result.append(self._buffer[i])
            return result
    
    def to_dataframe(self, columns: Optional[List[str]] = None) -> pd.DataFrame:
        """Convert buffer to DataFrame"""
        with self._lock:
            if not self._buffer:
                return pd.DataFrame()
            
            data = list(self._buffer)
            timestamps = list(self._timestamps)
            
            if isinstance(data[0], Tick):
                # Convert ticks to DataFrame
                rows = []
                for tick in data:
                    rows.append({
                        'symbol': tick.symbol,
                        'timestamp': tick.timestamp,
                        'bid': tick.bid,
                        'ask': tick.ask,
                        'last': tick.last,
                        'volume': tick.volume,
                        'spread': tick.spread
                    })
                df = pd.DataFrame(rows)
                df.set_index('timestamp', inplace=True)
                return df
            else:
                # Generic data
                df = pd.DataFrame(data, index=timestamps, columns=columns)
                return df
    
    def clear(self) -> None:
        """Clear buffer"""
        with self._lock:
            self._buffer.clear()
            self._timestamps.clear()
    
    def size(self) -> int:
        """Get buffer size"""
        with self._lock:
            return len(self._buffer)


class StreamProcessor:
    """Process and aggregate streaming data"""
    
    def __init__(self, config: StreamConfig):
        self.config = config
        self.buffers: Dict[str, DataBuffer] = {}
        self.aggregated_data: Dict[str, Dict[str, pd.DataFrame]] = defaultdict(dict)
        self._lock = threading.RLock()
        
        # Initialize buffers for each symbol
        for symbol in config.symbols:
            self.buffers[symbol] = DataBuffer(config.buffer_size)
    
    def process_tick(self, tick: Tick) -> None:
        """Process a single tick"""
        if tick.symbol in self.buffers:
            self.buffers[tick.symbol].append(tick)
            self._update_aggregations(tick.symbol)
    
    def process_batch(self, ticks: List[Tick]) -> None:
        """Process multiple ticks efficiently"""
        symbol_groups = defaultdict(list)
        
        # Group ticks by symbol
        for tick in ticks:
            if tick.symbol in self.buffers:
                symbol_groups[tick.symbol].append(tick)
        
        # Process each symbol's ticks
        for symbol, symbol_ticks in symbol_groups.items():
            self.buffers[symbol].extend(symbol_ticks)
            self._update_aggregations(symbol)
    
    def _update_aggregations(self, symbol: str) -> None:
        """Update aggregated data for a symbol"""
        buffer = self.buffers[symbol]
        
        if buffer.size() < 2:
            return
        
        # Get recent data
        df = buffer.to_dataframe()
        
        if df.empty:
            return
        
        # Create different timeframe aggregations
        timeframes = ['1T', '5T', '15T', '1H']  # 1min, 5min, 15min, 1hour
        
        for tf in timeframes:
            try:
                # Resample to timeframe
                if 'last' in df.columns:
                    agg_df = df['last'].resample(tf).agg({
                        'open': 'first',
                        'high': 'max',
                        'low': 'min',
                        'close': 'last'
                    }).dropna()
                    
                    if 'volume' in df.columns:
                        agg_df['volume'] = df['volume'].resample(tf).sum()
                    
                    if 'bid' in df.columns and 'ask' in df.columns:
                        agg_df['bid'] = df['bid'].resample(tf).last()
                        agg_df['ask'] = df['ask'].resample(tf).last()
                        agg_df['spread'] = agg_df['ask'] - agg_df['bid']
                    
                    # Store aggregated data
                    with self._lock:
                        self.aggregated_data[symbol][tf] = agg_df.tail(1000)  # Keep last 1000 bars
                        
            except Exception as e:
                logger.warning(f"Failed to aggregate data for {symbol} {tf}: {e}")
    
    def get_latest_bar(self, symbol: str, timeframe: str = '1T') -> Optional[pd.Series]:
        """Get the latest OHLCV bar for a symbol"""
        with self._lock:
            if symbol in self.aggregated_data and timeframe in self.aggregated_data[symbol]:
                df = self.aggregated_data[symbol][timeframe]
                if not df.empty:
                    return df.iloc[-1]
        return None
    
    def get_bars(self, symbol: str, timeframe: str = '1T', count: int = 100) -> pd.DataFrame:
        """Get recent bars for a symbol"""
        with self._lock:
            if symbol in self.aggregated_data and timeframe in self.aggregated_data[symbol]:
                df = self.aggregated_data[symbol][timeframe]
                return df.tail(count).copy()
        return pd.DataFrame()
    
    def get_tick_data(self, symbol: str, count: int = 100) -> pd.DataFrame:
        """Get raw tick data"""
        if symbol in self.buffers:
            return self.buffers[symbol].to_dataframe().tail(count)
        return pd.DataFrame()


class LiveDataStream:
    """Main live data streaming manager"""
    
    def __init__(self, config: StreamConfig):
        self.config = config
        self.state = StreamState.STOPPED
        self.processor = StreamProcessor(config)
        
        # Event handling
        self.subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._stop_event = Event()
        self._worker_thread: Optional[Thread] = None
        
        # Statistics
        self.stats = {
            'ticks_received': 0,
            'ticks_processed': 0,
            'errors': 0,
            'last_update': None,
            'uptime': 0,
            'reconnects': 0
        }
        
        self._start_time = None
        
    def subscribe(self, event_type: str, callback: Callable) -> None:
        """Subscribe to stream events"""
        self.subscribers[event_type].append(callback)
        logger.info(f"Subscribed to {event_type} events")
    
    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """Unsubscribe from stream events"""
        if callback in self.subscribers[event_type]:
            self.subscribers[event_type].remove(callback)
    
    def _emit_event(self, event_type: str, data: Any) -> None:
        """Emit event to subscribers"""
        for callback in self.subscribers[event_type]:
            try:
                callback(data)
            except Exception as e:
                logger.error(f"Error in event callback: {e}")
    
    def start(self) -> None:
        """Start the data stream"""
        if self.state == StreamState.RUNNING:
            logger.warning("Stream is already running")
            return
        
        self.state = StreamState.STARTING
        self._stop_event.clear()
        self._start_time = datetime.now()
        
        # Start worker thread
        self._worker_thread = Thread(target=self._stream_worker, daemon=True)
        self._worker_thread.start()
        
        self.state = StreamState.RUNNING
        logger.info("Live data stream started")
        self._emit_event('stream_started', {'timestamp': datetime.now()})
    
    def stop(self) -> None:
        """Stop the data stream"""
        if self.state == StreamState.STOPPED:
            return
        
        self.state = StreamState.STOPPED
        self._stop_event.set()
        
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5.0)
        
        logger.info("Live data stream stopped")
        self._emit_event('stream_stopped', {'timestamp': datetime.now()})
    
    def pause(self) -> None:
        """Pause the data stream"""
        if self.state == StreamState.RUNNING:
            self.state = StreamState.PAUSED
            logger.info("Live data stream paused")
    
    def resume(self) -> None:
        """Resume the data stream"""
        if self.state == StreamState.PAUSED:
            self.state = StreamState.RUNNING
            logger.info("Live data stream resumed")
    
    def _stream_worker(self) -> None:
        """Main streaming worker thread"""
        reconnect_attempts = 0
        
        while not self._stop_event.is_set():
            try:
                if self.state == StreamState.PAUSED:
                    time.sleep(0.1)
                    continue
                
                # Simulate receiving ticks (replace with actual data source)
                ticks = self._fetch_live_ticks()
                
                if ticks:
                    self.processor.process_batch(ticks)
                    self.stats['ticks_received'] += len(ticks)
                    self.stats['ticks_processed'] += len(ticks)
                    self.stats['last_update'] = datetime.now()
                    
                    # Emit tick events
                    for tick in ticks:
                        self._emit_event('tick', tick)
                    
                    # Reset reconnect attempts on successful data
                    reconnect_attempts = 0
                
                # Update uptime
                if self._start_time:
                    self.stats['uptime'] = (datetime.now() - self._start_time).total_seconds()
                
                time.sleep(self.config.update_interval)
                
            except Exception as e:
                logger.error(f"Stream error: {e}")
                self.stats['errors'] += 1
                self._emit_event('error', {'error': str(e), 'timestamp': datetime.now()})
                
                if self.config.auto_reconnect and reconnect_attempts < self.config.max_reconnect_attempts:
                    reconnect_attempts += 1
                    self.stats['reconnects'] += 1
                    logger.info(f"Attempting reconnect {reconnect_attempts}/{self.config.max_reconnect_attempts}")
                    time.sleep(self.config.reconnect_delay)
                else:
                    self.state = StreamState.ERROR
                    break
    
    def _fetch_live_ticks(self) -> List[Tick]:
        """Fetch live ticks from data source"""
        # This is a simulation - replace with actual data source implementation
        ticks = []
        
        for symbol in self.config.symbols:
            # Simulate tick data
            base_price = 1.1000 if 'EUR' in symbol else 100.0
            price_change = np.random.normal(0, 0.0001)
            
            tick = Tick(
                symbol=symbol,
                timestamp=datetime.now(),
                bid=base_price + price_change - 0.0001,
                ask=base_price + price_change + 0.0001,
                last=base_price + price_change,
                volume=np.random.randint(1, 100)
            )
            ticks.append(tick)
        
        return ticks
    
    def get_latest_data(self, symbol: str, timeframe: str = '1T') -> pd.DataFrame:
        """Get latest aggregated data"""
        return self.processor.get_bars(symbol, timeframe)
    
    def get_tick_data(self, symbol: str, count: int = 100) -> pd.DataFrame:
        """Get raw tick data"""
        return self.processor.get_tick_data(symbol, count)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get stream statistics"""
        return self.stats.copy()
    
    def get_status(self) -> Dict[str, Any]:
        """Get stream status"""
        return {
            'state': self.state.value,
            'symbols': self.config.symbols,
            'uptime': self.stats['uptime'],
            'ticks_processed': self.stats['ticks_processed'],
            'errors': self.stats['errors'],
            'last_update': self.stats['last_update']
        }


class MultiStreamManager:
    """Manage multiple data streams"""
    
    def __init__(self):
        self.streams: Dict[str, LiveDataStream] = {}
        self._lock = threading.RLock()
    
    def create_stream(self, name: str, config: StreamConfig) -> LiveDataStream:
        """Create a new data stream"""
        with self._lock:
            if name in self.streams:
                raise ValueError(f"Stream {name} already exists")
            
            stream = LiveDataStream(config)
            self.streams[name] = stream
            logger.info(f"Created stream: {name}")
            return stream
    
    def get_stream(self, name: str) -> Optional[LiveDataStream]:
        """Get a stream by name"""
        with self._lock:
            return self.streams.get(name)
    
    def start_stream(self, name: str) -> None:
        """Start a specific stream"""
        with self._lock:
            if name in self.streams:
                self.streams[name].start()
    
    def stop_stream(self, name: str) -> None:
        """Stop a specific stream"""
        with self._lock:
            if name in self.streams:
                self.streams[name].stop()
    
    def start_all(self) -> None:
        """Start all streams"""
        with self._lock:
            for stream in self.streams.values():
                stream.start()
    
    def stop_all(self) -> None:
        """Stop all streams"""
        with self._lock:
            for stream in self.streams.values():
                stream.stop()
    
    def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all streams"""
        with self._lock:
            return {name: stream.get_status() for name, stream in self.streams.items()}
    
    def remove_stream(self, name: str) -> None:
        """Remove a stream"""
        with self._lock:
            if name in self.streams:
                self.streams[name].stop()
                del self.streams[name]
                logger.info(f"Removed stream: {name}")


# Example usage and factory functions
def create_forex_stream(symbols: List[str]) -> LiveDataStream:
    """Create a forex data stream"""
    config = StreamConfig(
        symbols=symbols,
        buffer_size=50000,
        update_interval=0.05,  # 50ms for forex
        auto_reconnect=True,
        batch_size=50
    )
    return LiveDataStream(config)


def create_stock_stream(symbols: List[str]) -> LiveDataStream:
    """Create a stock data stream"""
    config = StreamConfig(
        symbols=symbols,
        buffer_size=10000,
        update_interval=0.1,  # 100ms for stocks
        auto_reconnect=True,
        batch_size=20
    )
    return LiveDataStream(config)