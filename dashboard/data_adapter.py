"""
Data Adapter - Handles data processing and formatting for dashboard

Converts data from various sources (backtesting results, live trading data,
market data) into formats suitable for dashboard visualization.
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union, Callable
import json

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class DataAdapter:
    """
    Adapter for processing and formatting data for dashboard consumption
    
    Handles conversion between different data formats and provides
    real-time data streaming capabilities.
    """
    
    def __init__(self, config):
        self.config = config
        self.live_data_source = None
        self.live_symbols = []
        
        # Data caches
        self.price_cache = {}
        self.indicator_cache = {}
        self.portfolio_cache = {}
        
        # Threading
        self.data_lock = threading.Lock()
        
        logger.info("DataAdapter initialized")
    
    def process_backtest_data(self, backtest_results: Dict, config: Any = None) -> Dict:
        """
        Process backtesting results for dashboard display
        
        Args:
            backtest_results: Raw backtest results
            config: Trading configuration
            
        Returns:
            Processed data ready for dashboard display
        """
        try:
            logger.info("Processing backtest data")
            
            processed_data = {
                'timestamp': datetime.now().isoformat(),
                'summary': {},
                'pairs': [],
                'portfolio_metrics': {},
                'equity_curve': [],
                'drawdown_curve': [],
                'trade_analysis': {},
                'risk_metrics': {}
            }
            
            # Extract portfolio metrics
            portfolio_metrics = backtest_results.get('portfolio_metrics', {})
            processed_data['portfolio_metrics'] = {
                'total_return': self._safe_float(portfolio_metrics.get('portfolio_return', 0)),
                'sharpe_ratio': self._safe_float(portfolio_metrics.get('portfolio_sharpe', 0)),
                'sortino_ratio': self._safe_float(portfolio_metrics.get('sortino_ratio', 0)),
                'max_drawdown': self._safe_float(portfolio_metrics.get('portfolio_max_drawdown', 0)),
                'calmar_ratio': self._safe_float(portfolio_metrics.get('calmar_ratio', 0)),
                'total_trades': int(portfolio_metrics.get('total_trades', 0)),
                'win_rate': self._safe_float(portfolio_metrics.get('portfolio_win_rate', 0)),
                'profit_factor': self._safe_float(portfolio_metrics.get('profit_factor', 0)),
                'volatility': self._safe_float(portfolio_metrics.get('volatility', 0)),
                'var_95': self._safe_float(portfolio_metrics.get('var_95', 0)),
                'max_concurrent_positions': int(portfolio_metrics.get('max_concurrent_positions', 0)),
                'avg_concurrent_positions': self._safe_float(portfolio_metrics.get('avg_concurrent_positions', 0))
            }
            
            # Process pair results
            pair_results = backtest_results.get('pair_results', [])
            for pair_result in pair_results:
                pair_data = self._process_pair_result(pair_result)
                if pair_data:
                    processed_data['pairs'].append(pair_data)
            
            # Sort pairs by performance
            processed_data['pairs'].sort(
                key=lambda x: x['metrics']['total_return'], 
                reverse=True
            )
            
            # Generate summary statistics
            processed_data['summary'] = self._generate_summary_stats(processed_data)
            
            # Process equity curve
            if 'portfolio_equity' in backtest_results:
                processed_data['equity_curve'] = self._process_equity_curve(
                    backtest_results['portfolio_equity']
                )
            
            # Process drawdown curve
            if 'portfolio_drawdown' in backtest_results:
                processed_data['drawdown_curve'] = self._process_drawdown_curve(
                    backtest_results['portfolio_drawdown']
                )
            
            # Trade analysis
            processed_data['trade_analysis'] = self._analyze_trades(pair_results)
            
            # Risk metrics
            processed_data['risk_metrics'] = self._calculate_risk_metrics(
                processed_data['portfolio_metrics'],
                processed_data['pairs']
            )
            
            logger.info(f"Processed backtest data for {len(processed_data['pairs'])} pairs")
            return processed_data
            
        except Exception as e:
            logger.error(f"Failed to process backtest data: {e}")
            raise
    
    def _process_pair_result(self, pair_result: Dict) -> Optional[Dict]:
        """Process individual pair result"""
        try:
            metrics = pair_result.get('metrics', {})
            
            pair_data = {
                'pair': metrics.get('pair', 'Unknown'),
                'metrics': {
                    'total_return': self._safe_float(metrics.get('total_return', 0)),
                    'sharpe_ratio': self._safe_float(metrics.get('sharpe_ratio', 0)),
                    'sortino_ratio': self._safe_float(metrics.get('sortino_ratio', 0)),
                    'max_drawdown': self._safe_float(metrics.get('max_drawdown', 0)),
                    'total_trades': int(metrics.get('total_trades', 0)),
                    'win_rate': self._safe_float(metrics.get('win_rate', 0)),
                    'profit_factor': self._safe_float(metrics.get('profit_factor', 0)),
                    'avg_trade_return': self._safe_float(metrics.get('avg_trade_return', 0)),
                    'best_trade': self._safe_float(metrics.get('best_trade', 0)),
                    'worst_trade': self._safe_float(metrics.get('worst_trade', 0)),
                    'avg_trade_duration': self._safe_float(metrics.get('avg_trade_duration', 0)),
                    'composite_score': self._safe_float(metrics.get('composite_score', 0))
                },
                'trades': [],
                'equity_curve': [],
                'signals': []
            }
            
            # Process trades
            trades = pair_result.get('trades', [])
            for trade in trades:
                trade_data = {
                    'entry_time': trade.get('entry_time', ''),
                    'exit_time': trade.get('exit_time', ''),
                    'direction': trade.get('direction', ''),
                    'entry_price_1': self._safe_float(trade.get('entry_price_1', 0)),
                    'entry_price_2': self._safe_float(trade.get('entry_price_2', 0)),
                    'exit_price_1': self._safe_float(trade.get('exit_price_1', 0)),
                    'exit_price_2': self._safe_float(trade.get('exit_price_2', 0)),
                    'return_pct': self._safe_float(trade.get('return_pct', 0)),
                    'pnl': self._safe_float(trade.get('pnl', 0)),
                    'duration': trade.get('duration', 0),
                    'exit_reason': trade.get('exit_reason', '')
                }
                pair_data['trades'].append(trade_data)
            
            # Process equity curve if available
            if 'equity' in pair_result:
                pair_data['equity_curve'] = self._process_pair_equity_curve(
                    pair_result['equity']
                )
            
            return pair_data
            
        except Exception as e:
            logger.error(f"Failed to process pair result: {e}")
            return None
    
    def _process_equity_curve(self, equity_data) -> List[Dict]:
        """Process portfolio equity curve data"""
        try:
            if isinstance(equity_data, pd.Series):
                return [
                    {
                        'timestamp': idx.isoformat() if hasattr(idx, 'isoformat') else str(idx),
                        'value': float(val) if not pd.isna(val) else 0.0
                    }
                    for idx, val in equity_data.items()
                ]
            elif isinstance(equity_data, list):
                return [
                    {
                        'timestamp': datetime.now().isoformat(),
                        'value': float(val) if not pd.isna(val) else 0.0
                    }
                    for val in equity_data
                ]
            else:
                return []
        except Exception as e:
            logger.error(f"Failed to process equity curve: {e}")
            return []
    
    def _process_drawdown_curve(self, drawdown_data) -> List[Dict]:
        """Process drawdown curve data"""
        try:
            if isinstance(drawdown_data, pd.Series):
                return [
                    {
                        'timestamp': idx.isoformat() if hasattr(idx, 'isoformat') else str(idx),
                        'value': float(val) if not pd.isna(val) else 0.0
                    }
                    for idx, val in drawdown_data.items()
                ]
            else:
                return []
        except Exception as e:
            logger.error(f"Failed to process drawdown curve: {e}")
            return []
    
    def _process_pair_equity_curve(self, equity_data) -> List[Dict]:
        """Process individual pair equity curve"""
        try:
            if isinstance(equity_data, (list, np.ndarray)):
                return [
                    {
                        'timestamp': datetime.now().isoformat(),
                        'value': float(val) if not pd.isna(val) else 0.0
                    }
                    for val in equity_data
                ]
            elif isinstance(equity_data, pd.Series):
                return [
                    {
                        'timestamp': idx.isoformat() if hasattr(idx, 'isoformat') else str(idx),
                        'value': float(val) if not pd.isna(val) else 0.0
                    }
                    for idx, val in equity_data.items()
                ]
            else:
                return []
        except Exception as e:
            logger.error(f"Failed to process pair equity curve: {e}")
            return []
    
    def _generate_summary_stats(self, processed_data: Dict) -> Dict:
        """Generate summary statistics"""
        try:
            pairs = processed_data['pairs']
            portfolio_metrics = processed_data['portfolio_metrics']
            
            if not pairs:
                return {}
            
            returns = [pair['metrics']['total_return'] for pair in pairs]
            profitable_pairs = len([r for r in returns if r > 0])
            
            summary = {
                'total_pairs': len(pairs),
                'profitable_pairs': profitable_pairs,
                'losing_pairs': len(pairs) - profitable_pairs,
                'win_rate_pairs': (profitable_pairs / len(pairs)) * 100 if pairs else 0,
                'best_pair': max(pairs, key=lambda x: x['metrics']['total_return'])['pair'] if pairs else '',
                'worst_pair': min(pairs, key=lambda x: x['metrics']['total_return'])['pair'] if pairs else '',
                'avg_return_per_pair': np.mean(returns) if returns else 0,
                'median_return_per_pair': np.median(returns) if returns else 0,
                'std_return_per_pair': np.std(returns) if returns else 0,
                'portfolio_return': portfolio_metrics.get('total_return', 0),
                'portfolio_sharpe': portfolio_metrics.get('sharpe_ratio', 0),
                'portfolio_max_dd': portfolio_metrics.get('max_drawdown', 0)
            }
            
            return summary
            
        except Exception as e:
            logger.error(f"Failed to generate summary stats: {e}")
            return {}
    
    def _analyze_trades(self, pair_results: List[Dict]) -> Dict:
        """Analyze all trades across pairs"""
        try:
            all_trades = []
            for pair_result in pair_results:
                trades = pair_result.get('trades', [])
                for trade in trades:
                    trade_copy = trade.copy()
                    trade_copy['pair'] = pair_result.get('metrics', {}).get('pair', '')
                    all_trades.append(trade_copy)
            
            if not all_trades:
                return {}
            
            returns = [trade.get('return_pct', 0) for trade in all_trades]
            durations = [trade.get('duration', 0) for trade in all_trades]
            
            analysis = {
                'total_trades': len(all_trades),
                'winning_trades': len([r for r in returns if r > 0]),
                'losing_trades': len([r for r in returns if r <= 0]),
                'avg_return': np.mean(returns) if returns else 0,
                'avg_winning_return': np.mean([r for r in returns if r > 0]) if any(r > 0 for r in returns) else 0,
                'avg_losing_return': np.mean([r for r in returns if r <= 0]) if any(r <= 0 for r in returns) else 0,
                'best_trade': max(returns) if returns else 0,
                'worst_trade': min(returns) if returns else 0,
                'avg_duration': np.mean(durations) if durations else 0,
                'trade_frequency': len(all_trades) / len(pair_results) if pair_results else 0
            }
            
            return analysis
            
        except Exception as e:
            logger.error(f"Failed to analyze trades: {e}")
            return {}
    
    def _calculate_risk_metrics(self, portfolio_metrics: Dict, pairs: List[Dict]) -> Dict:
        """Calculate additional risk metrics"""
        try:
            returns = [pair['metrics']['total_return'] for pair in pairs]
            
            risk_metrics = {
                'portfolio_volatility': portfolio_metrics.get('volatility', 0),
                'value_at_risk_95': portfolio_metrics.get('var_95', 0),
                'expected_shortfall': 0,  # TODO: Calculate from trade data
                'maximum_drawdown': portfolio_metrics.get('max_drawdown', 0),
                'drawdown_duration': 0,  # TODO: Calculate from equity curve
                'skewness': 0,  # TODO: Calculate from returns
                'kurtosis': 0,  # TODO: Calculate from returns
                'tail_ratio': 0,  # TODO: Calculate from returns
                'concentration_risk': self._calculate_concentration_risk(pairs)
            }
            
            return risk_metrics
            
        except Exception as e:
            logger.error(f"Failed to calculate risk metrics: {e}")
            return {}
    
    def _calculate_concentration_risk(self, pairs: List[Dict]) -> float:
        """Calculate concentration risk (Herfindahl-Hirschman Index)"""
        try:
            if not pairs:
                return 0
            
            total_return = sum(pair['metrics']['total_return'] for pair in pairs)
            if total_return == 0:
                return 0
            
            weights = [pair['metrics']['total_return'] / total_return for pair in pairs]
            hhi = sum(w**2 for w in weights)
            
            return hhi
            
        except Exception as e:
            logger.error(f"Failed to calculate concentration risk: {e}")
            return 0
    
    def connect_live_source(self, data_source: Any, symbols: List[str] = None):
        """Connect to live data source"""
        try:
            with self.data_lock:
                self.live_data_source = data_source
                self.live_symbols = symbols or []
            
            logger.info(f"Connected to live data source with {len(self.live_symbols)} symbols")
            
        except Exception as e:
            logger.error(f"Failed to connect live data source: {e}")
            raise
    
    def get_live_data_update(self) -> Optional[Dict]:
        """Get latest live data update"""
        try:
            if not self.live_data_source:
                return None
            
            update_data = {
                'timestamp': datetime.now().isoformat(),
                'prices': {},
                'portfolio': {},
                'market_data': {}
            }
            
            # Get current prices for symbols
            with self.data_lock:
                for symbol in self.live_symbols:
                    try:
                        # Get latest price (implementation depends on data source)
                        if hasattr(self.live_data_source, 'get_current_price'):
                            price = self.live_data_source.get_current_price(symbol)
                            if price:
                                update_data['prices'][symbol] = {
                                    'bid': float(price.get('bid', 0)),
                                    'ask': float(price.get('ask', 0)),
                                    'last': float(price.get('last', 0)),
                                    'timestamp': datetime.now().isoformat()
                                }
                    except Exception as e:
                        logger.warning(f"Failed to get price for {symbol}: {e}")
            
            # Get portfolio data if available
            if hasattr(self.live_data_source, 'get_portfolio_status'):
                try:
                    portfolio = self.live_data_source.get_portfolio_status()
                    if portfolio:
                        update_data['portfolio'] = self.process_portfolio_data(portfolio)
                except Exception as e:
                    logger.warning(f"Failed to get portfolio data: {e}")
            
            return update_data if update_data['prices'] or update_data['portfolio'] else None
            
        except Exception as e:
            logger.error(f"Failed to get live data update: {e}")
            return None
    
    def process_portfolio_data(self, portfolio_data: Dict) -> Dict:
        """Process portfolio data for dashboard display"""
        try:
            processed = {
                'timestamp': datetime.now().isoformat(),
                'equity': self._safe_float(portfolio_data.get('equity', 0)),
                'balance': self._safe_float(portfolio_data.get('balance', 0)),
                'margin': self._safe_float(portfolio_data.get('margin', 0)),
                'free_margin': self._safe_float(portfolio_data.get('free_margin', 0)),
                'margin_level': self._safe_float(portfolio_data.get('margin_level', 0)),
                'positions': [],
                'orders': [],
                'pnl': self._safe_float(portfolio_data.get('pnl', 0)),
                'daily_pnl': self._safe_float(portfolio_data.get('daily_pnl', 0))
            }
            
            # Process positions
            positions = portfolio_data.get('positions', [])
            for position in positions:
                pos_data = {
                    'symbol': position.get('symbol', ''),
                    'type': position.get('type', ''),
                    'volume': self._safe_float(position.get('volume', 0)),
                    'price': self._safe_float(position.get('price', 0)),
                    'current_price': self._safe_float(position.get('current_price', 0)),
                    'pnl': self._safe_float(position.get('pnl', 0)),
                    'swap': self._safe_float(position.get('swap', 0)),
                    'time': position.get('time', '')
                }
                processed['positions'].append(pos_data)
            
            return processed
            
        except Exception as e:
            logger.error(f"Failed to process portfolio data: {e}")
            return {}
    
    def process_custom_data(self, data: Any, chart_config: Dict = None) -> Dict:
        """Process custom data for dashboard display"""
        try:
            processed = {
                'timestamp': datetime.now().isoformat(),
                'data': data,
                'type': type(data).__name__
            }
            
            # Convert pandas objects to dict
            if isinstance(data, pd.DataFrame):
                processed['data'] = data.to_dict('records')
                processed['columns'] = list(data.columns)
            elif isinstance(data, pd.Series):
                processed['data'] = data.to_dict()
            elif isinstance(data, np.ndarray):
                processed['data'] = data.tolist()
            
            if chart_config:
                processed['chart_config'] = chart_config
            
            return processed
            
        except Exception as e:
            logger.error(f"Failed to process custom data: {e}")
            return {}
    
    def cleanup(self):
        """Cleanup resources"""
        try:
            with self.data_lock:
                self.live_data_source = None
                self.live_symbols.clear()
                
            # Clear caches
            self.price_cache.clear()
            self.indicator_cache.clear()
            self.portfolio_cache.clear()
            
            logger.info("DataAdapter cleaned up")
            
        except Exception as e:
            logger.error(f"Failed to cleanup DataAdapter: {e}")
    
    @staticmethod
    def _safe_float(value, default=0.0):
        """Safely convert value to float"""
        try:
            if pd.isna(value):
                return default
            return float(value)
        except (ValueError, TypeError):
            return default
