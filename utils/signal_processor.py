"""
Trading Signal Processing Module
================================

This module provides shared signal processing functionality that can be used
across different broker implementations (MT5, CTrader, etc.).

Features:
- Signal processing and throttling
- Trading signal validation
- Cooldown management
- Signal history tracking

Author: Trading System v3.0
Date: July 2025
"""

import logging
from typing import Dict, List, Tuple, Optional, Any, Callable
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import deque, defaultdict
import threading

logger = logging.getLogger(__name__)


@dataclass
class TradingSignal:
    """Represents a trading signal"""
    pair_str: str
    signal_type: str  # 'OPEN_LONG', 'OPEN_SHORT', 'CLOSE'
    confidence: float
    timestamp: datetime
    metadata: Dict[str, Any]


@dataclass
class PairState:
    """State information for a trading pair"""
    symbol1: str
    symbol2: str
    position: Optional[str]  # 'LONG', 'SHORT', or None
    last_signal: Optional[str]
    cooldown: int
    last_update: datetime
    last_candle_time: Optional[datetime]
    price_history1: deque
    price_history2: deque


class SignalStrategy(ABC):
    """Abstract interface for signal generation strategies"""
    
    @abstractmethod
    def generate_signal(self, pair_state: PairState) -> Optional[TradingSignal]:
        """Generate a trading signal for the given pair state"""
        pass
    
    @abstractmethod
    def get_minimum_data_points(self) -> int:
        """Get minimum data points required for signal generation"""
        pass


class SignalThrottler:
    """
    Manages signal throttling to prevent excessive processing
    """
    
    def __init__(self, min_check_interval_seconds: float = 0.5):
        self.min_check_interval = min_check_interval_seconds
        self._last_check_times = {}
        self._lock = threading.Lock()
    
    def should_process_signal(self, pair_str: str) -> bool:
        """
        Check if enough time has passed since last signal processing for this pair
        
        Args:
            pair_str: Pair identifier
            
        Returns:
            True if signal should be processed, False if throttled
        """
        with self._lock:
            current_time = datetime.now()
            
            if pair_str not in self._last_check_times:
                self._last_check_times[pair_str] = current_time
                return True
            
            time_since_last = (current_time - self._last_check_times[pair_str]).total_seconds()
            
            if time_since_last >= self.min_check_interval:
                self._last_check_times[pair_str] = current_time
                return True
            
            return False
    
    def reset_throttle(self, pair_str: str):
        """Reset throttling for a specific pair"""
        with self._lock:
            self._last_check_times.pop(pair_str, None)
    
    def get_throttle_status(self) -> Dict[str, float]:
        """Get current throttle status for all pairs"""
        with self._lock:
            current_time = datetime.now()
            status = {}
            
            for pair_str, last_time in self._last_check_times.items():
                time_since_last = (current_time - last_time).total_seconds()
                status[pair_str] = time_since_last
            
            return status


class CooldownManager:
    """
    Manages trading cooldowns for pairs
    """
    
    def __init__(self, default_cooldown_bars: int = 5):
        self.default_cooldown_bars = default_cooldown_bars
    
    def set_cooldown(self, pair_state: PairState, bars: Optional[int] = None):
        """Set cooldown for a pair"""
        cooldown_bars = bars if bars is not None else self.default_cooldown_bars
        pair_state.cooldown = cooldown_bars
        logger.info(f"Set cooldown for {pair_state.symbol1}-{pair_state.symbol2}: {cooldown_bars} bars")
    
    def decrement_cooldown(self, pair_state: PairState):
        """Decrement cooldown when new candle arrives"""
        if pair_state.cooldown > 0:
            pair_state.cooldown -= 1
            logger.debug(f"Cooldown decremented for {pair_state.symbol1}-{pair_state.symbol2}: "
                        f"{pair_state.cooldown} bars remaining")
    
    def is_cooldown_active(self, pair_state: PairState) -> bool:
        """Check if pair is in cooldown period"""
        return pair_state.cooldown > 0
    
    def get_cooldown_status(self, pair_states: Dict[str, PairState]) -> Dict[str, int]:
        """Get cooldown status for all pairs"""
        return {pair_str: state.cooldown for pair_str, state in pair_states.items()}


class SignalValidator:
    """
    Validates trading signals before execution
    """
    
    def __init__(self):
        self.validation_rules = []
    
    def add_validation_rule(self, rule: Callable[[TradingSignal, PairState], Tuple[bool, str]]):
        """Add a validation rule"""
        self.validation_rules.append(rule)
    
    def validate_signal(self, signal: TradingSignal, pair_state: PairState) -> Tuple[bool, str]:
        """
        Validate a trading signal against all rules
        
        Args:
            signal: Trading signal to validate
            pair_state: Current state of the pair
            
        Returns:
            Tuple of (is_valid, reason)
        """
        for rule in self.validation_rules:
            try:
                is_valid, reason = rule(signal, pair_state)
                if not is_valid:
                    return False, reason
            except Exception as e:
                logger.error(f"Error in validation rule: {e}")
                return False, f"Validation error: {e}"
        
        return True, "Signal valid"


class SignalHistory:
    """
    Tracks signal history for analysis and debugging
    """
    
    def __init__(self, max_history: int = 1000):
        self.max_history = max_history
        self._history = deque(maxlen=max_history)
        self._lock = threading.Lock()
    
    def add_signal(self, signal: TradingSignal, executed: bool, reason: str = ""):
        """Add a signal to history"""
        with self._lock:
            history_entry = {
                'signal': signal,
                'executed': executed,
                'reason': reason,
                'recorded_at': datetime.now()
            }
            self._history.append(history_entry)
    
    def get_recent_signals(self, count: int = 10) -> List[Dict[str, Any]]:
        """Get recent signals"""
        with self._lock:
            return list(self._history)[-count:]
    
    def get_signals_for_pair(self, pair_str: str, count: int = 10) -> List[Dict[str, Any]]:
        """Get recent signals for a specific pair"""
        with self._lock:
            pair_signals = [entry for entry in self._history 
                          if entry['signal'].pair_str == pair_str]
            return pair_signals[-count:]
    
    def get_signal_statistics(self) -> Dict[str, Any]:
        """Get signal statistics"""
        with self._lock:
            if not self._history:
                return {'total_signals': 0, 'executed_signals': 0, 'execution_rate': 0}
            
            total_signals = len(self._history)
            executed_signals = sum(1 for entry in self._history if entry['executed'])
            execution_rate = executed_signals / total_signals if total_signals > 0 else 0
            
            # Group by signal type
            signal_types = defaultdict(int)
            for entry in self._history:
                signal_types[entry['signal'].signal_type] += 1
            
            return {
                'total_signals': total_signals,
                'executed_signals': executed_signals,
                'execution_rate': execution_rate,
                'signal_types': dict(signal_types)
            }


class SignalProcessor:
    """
    Main signal processing coordinator
    """
    
    def __init__(self, strategy: SignalStrategy, min_check_interval: float = 0.5,
                 default_cooldown_bars: int = 5):
        self.strategy = strategy
        self.throttler = SignalThrottler(min_check_interval)
        self.cooldown_manager = CooldownManager(default_cooldown_bars)
        self.validator = SignalValidator()
        self.history = SignalHistory()
        
        # Add default validation rules
        self._setup_default_validation_rules()
    
    def _setup_default_validation_rules(self):
        """Setup default validation rules"""
        
        def cooldown_rule(signal: TradingSignal, pair_state: PairState) -> Tuple[bool, str]:
            if self.cooldown_manager.is_cooldown_active(pair_state):
                return False, f"Pair in cooldown: {pair_state.cooldown} bars remaining"
            return True, "Cooldown check passed"
        
        def data_sufficiency_rule(signal: TradingSignal, pair_state: PairState) -> Tuple[bool, str]:
            min_points = self.strategy.get_minimum_data_points()
            if (len(pair_state.price_history1) < min_points or 
                len(pair_state.price_history2) < min_points):
                return False, f"Insufficient data: need {min_points} points"
            return True, "Data sufficiency check passed"
        
        def position_consistency_rule(signal: TradingSignal, pair_state: PairState) -> Tuple[bool, str]:
            if signal.signal_type == 'CLOSE' and pair_state.position is None:
                return False, "Cannot close: no open position"
            if signal.signal_type in ['OPEN_LONG', 'OPEN_SHORT'] and pair_state.position is not None:
                return False, f"Cannot open: already have {pair_state.position} position"
            return True, "Position consistency check passed"
        
        self.validator.add_validation_rule(cooldown_rule)
        self.validator.add_validation_rule(data_sufficiency_rule)
        self.validator.add_validation_rule(position_consistency_rule)
    
    def process_signals(self, pair_states: Dict[str, PairState],
                       risk_check_callback: Optional[Callable[[str], Tuple[bool, str]]] = None,
                       execution_callback: Optional[Callable[[TradingSignal], bool]] = None) -> Dict[str, Any]:
        """
        Process trading signals for all pairs
        
        Args:
            pair_states: Dict of pair_str -> PairState
            risk_check_callback: Optional callback for risk management checks
            execution_callback: Optional callback for signal execution
            
        Returns:
            Processing summary
        """
        processed_count = 0
        executed_count = 0
        skipped_count = 0
        error_count = 0
        
        processing_summary = {
            'timestamp': datetime.now().isoformat(),
            'processed_pairs': [],
            'skipped_pairs': [],
            'errors': []
        }
        
        for pair_str, pair_state in pair_states.items():
            try:
                # Check throttling
                if not self.throttler.should_process_signal(pair_str):
                    skipped_count += 1
                    processing_summary['skipped_pairs'].append({
                        'pair': pair_str,
                        'reason': 'throttled'
                    })
                    continue
                
                # Generate signal
                signal = self.strategy.generate_signal(pair_state)
                if signal is None:
                    processed_count += 1
                    processing_summary['processed_pairs'].append({
                        'pair': pair_str,
                        'signal': None,
                        'executed': False,
                        'reason': 'no_signal'
                    })
                    continue
                
                # Validate signal
                is_valid, validation_reason = self.validator.validate_signal(signal, pair_state)
                if not is_valid:
                    processed_count += 1
                    self.history.add_signal(signal, False, validation_reason)
                    processing_summary['processed_pairs'].append({
                        'pair': pair_str,
                        'signal': signal.signal_type,
                        'executed': False,
                        'reason': validation_reason
                    })
                    continue
                
                # Risk management check
                if risk_check_callback:
                    risk_ok, risk_reason = risk_check_callback(pair_str)
                    if not risk_ok:
                        processed_count += 1
                        self.history.add_signal(signal, False, risk_reason)
                        processing_summary['processed_pairs'].append({
                            'pair': pair_str,
                            'signal': signal.signal_type,
                            'executed': False,
                            'reason': risk_reason
                        })
                        continue
                
                # Execute signal
                executed = False
                execution_reason = "not_executed"
                
                if execution_callback:
                    try:
                        executed = execution_callback(signal)
                        execution_reason = "executed" if executed else "execution_failed"
                        if executed:
                            executed_count += 1
                            # Set cooldown after successful execution
                            if signal.signal_type in ['OPEN_LONG', 'OPEN_SHORT', 'CLOSE']:
                                self.cooldown_manager.set_cooldown(pair_state)
                    except Exception as e:
                        logger.error(f"Error executing signal for {pair_str}: {e}")
                        execution_reason = f"execution_error: {e}"
                
                processed_count += 1
                self.history.add_signal(signal, executed, execution_reason)
                processing_summary['processed_pairs'].append({
                    'pair': pair_str,
                    'signal': signal.signal_type,
                    'executed': executed,
                    'reason': execution_reason
                })
                
            except Exception as e:
                error_count += 1
                logger.error(f"Error processing signals for {pair_str}: {e}")
                processing_summary['errors'].append({
                    'pair': pair_str,
                    'error': str(e)
                })
        
        # Update summary statistics
        processing_summary.update({
            'total_pairs': len(pair_states),
            'processed_count': processed_count,
            'executed_count': executed_count,
            'skipped_count': skipped_count,
            'error_count': error_count,
            'throttle_status': self.throttler.get_throttle_status(),
            'cooldown_status': self.cooldown_manager.get_cooldown_status(pair_states),
            'signal_statistics': self.history.get_signal_statistics()
        })
        
        return processing_summary
    
    def update_pair_on_new_candle(self, pair_str: str, pair_state: PairState):
        """Update pair state when new candle arrives"""
        self.cooldown_manager.decrement_cooldown(pair_state)
        logger.debug(f"New candle for {pair_str} - cooldown: {pair_state.cooldown}")
    
    def get_processing_status(self) -> Dict[str, Any]:
        """Get current processing status"""
        return {
            'timestamp': datetime.now().isoformat(),
            'signal_statistics': self.history.get_signal_statistics(),
            'recent_signals': self.history.get_recent_signals(20)
        }
