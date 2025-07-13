import os
import numpy as np
import pandas as pd
import warnings
import time
import logging
from collections import deque, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Union
import vectorbt as vbt
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing
from config import TradingConfig
from data.mt5 import MT5DataManager
from strategies.pairs_trading import OptimizedPairsStrategy

def get_mt5_config():
    """Get TradingConfig from config module, ensuring .env is loaded first."""
    from config import get_config, force_config_update
    force_config_update()
    return get_config()

# Only load config if run as script, not on import
if __name__ == "__main__":
    CONFIG = get_mt5_config()

# Setup optimized logging with proper log file path
CONFIG = get_mt5_config()
log_file_path = os.path.join(CONFIG.logs_dir, "VectorBTBacktester.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("VectorBTBacktester")
warnings.filterwarnings("ignore")


# === VECTORBT BACKTESTING ENGINE ===
class VectorBTBacktester:
    """High-performance vectorbt-based backtesting engine"""
    
    def __init__(self, config: TradingConfig, data_manager: MT5DataManager):
        self.config = config
        self.data_manager = data_manager
        self.strategy = OptimizedPairsStrategy(config, data_manager)
        
        # Performance optimization - use config values
        self.use_multiprocessing = self.config.use_multiprocessing
        self.max_workers = min(self.config.max_workers, multiprocessing.cpu_count())
        
        # Results storage
        self.pair_results = []
        self.portfolio_results = {}
        
    def run_backtest(self) -> Dict:
        """Run comprehensive backtesting with portfolio optimization"""
        logger.info("Starting VectorBT backtesting engine...")
        start_time = time.time()
        
        # Run individual pair backtests
        pair_results = self._run_pair_backtests()
        
        # Run portfolio-level analysis
        portfolio_results = self._run_portfolio_backtest(pair_results)
        
        # Calculate ranking and composite scores
        ranked_results = self._calculate_pair_rankings(pair_results)
        
        # Generate comprehensive report
        backtest_results = {
            'pair_results': ranked_results,
            'portfolio_metrics': portfolio_results,
            'portfolio_equity': portfolio_results.get('equity_curve', []),
            'portfolio_dates': portfolio_results.get('dates', []),
            'config': self.config
        }

        elapsed_time = time.time() - start_time
        logger.info(f"Backtest completed in {elapsed_time:.2f} seconds")
        
        return backtest_results
    
    def _run_pair_backtests(self) -> List[Dict]:
        """Run backtests for all pairs with CTrader-aware processing"""
        logger.info(f"Running backtests for {len(self.config.pairs)} pairs...")
        
        # Check if we're using CTrader data manager - if so, use sequential processing
        data_manager_type = type(self.data_manager).__name__
        is_ctrader = 'CTrader' in data_manager_type
        
        if is_ctrader:
            logger.info("Using CTrader data manager - running pairs sequentially to avoid API timeouts")
            return self._run_pairs_sequential()
        elif self.use_multiprocessing:
            return self._run_pairs_parallel()
        else:
            return self._run_pairs_sequential()
    
    def _run_pairs_parallel(self) -> List[Dict]:
        """Run pair backtests in parallel for maximum performance"""
        results = []
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all pair backtests
            future_to_pair = {
                executor.submit(self._backtest_single_pair, pair_str): pair_str 
                for pair_str in self.config.pairs
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_pair):
                pair_str = future_to_pair[future]
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                        logger.info(f"Completed backtest for {pair_str}")
                except Exception as e:
                    logger.error(f"Error backtesting {pair_str}: {e}")
        
        logger.info(f"Completed {len(results)} pair backtests")
        return results
    
    def _run_pairs_sequential(self) -> List[Dict]:
        """Run pair backtests sequentially (for debugging or CTrader)"""
        results = []
        data_manager_type = type(self.data_manager).__name__
        is_ctrader = 'CTrader' in data_manager_type
        
        for i, pair_str in enumerate(self.config.pairs):
            try:
                logger.info(f"Backtesting {pair_str} ({i+1}/{len(self.config.pairs)})")
                
                # Add delay between requests for CTrader to avoid rate limiting
                if is_ctrader and i > 0:
                    time.sleep(2)  # 2 second delay between CTrader requests
                
                result = self._backtest_single_pair(pair_str)
                if result:
                    results.append(result)
                    logger.info(f"Completed backtest for {pair_str}")
                else:
                    logger.warning(f"No data for {pair_str}")
                    
            except Exception as e:
                logger.error(f"Error backtesting {pair_str}: {e}")
                # Continue with next pair instead of failing completely
                continue
        
        logger.info(f"Completed {len(results)} pair backtests out of {len(self.config.pairs)} attempted")
        return results
    
    def _backtest_single_pair(self, pair_str: str) -> Optional[Dict]:
        """Backtest a single pair using vectorbt with improved CTrader data handling"""
        try:
            s1, s2 = pair_str.split('-')
            data_manager_type = type(self.data_manager).__name__
            is_ctrader = 'CTrader' in data_manager_type
            
            # For CTrader, add extra logging
            if is_ctrader:
                logger.debug(f"Using CTrader data manager for {pair_str}")
            
            # Get historical data with error handling
            try:
                data1 = self.data_manager.get_historical_data(s1, self.config.interval, 
                                                             self.config.start_date, self.config.end_date)
            except Exception as e:
                logger.error(f"Error getting data for {s1}: {e}")
                return None
            
            try:
                data2 = self.data_manager.get_historical_data(s2, self.config.interval,
                                                             self.config.start_date, self.config.end_date)
            except Exception as e:
                logger.error(f"Error getting data for {s2}: {e}")
                return None
            
            # Safety check: ensure we have pandas Series, not dicts or other types
            if not isinstance(data1, pd.Series):
                if isinstance(data1, dict):
                    # If it's a dict, try to get the symbol data
                    data1 = data1.get(s1, pd.Series(dtype=float))
                else:
                    logger.warning(f"Unexpected data type for {s1}: {type(data1)}")
                    data1 = pd.Series(dtype=float)
                    
            if not isinstance(data2, pd.Series):
                if isinstance(data2, dict):
                    # If it's a dict, try to get the symbol data
                    data2 = data2.get(s2, pd.Series(dtype=float))
                else:
                    logger.warning(f"Unexpected data type for {s2}: {type(data2)}")
                    data2 = pd.Series(dtype=float)
            
            # Check for empty data
            if data1.empty or data2.empty:
                if is_ctrader:
                    logger.warning(f"No data retrieved from CTrader for {pair_str} (s1: {len(data1)}, s2: {len(data2)})")
                else:
                    logger.warning(f"No data for {pair_str}")
                return None
            
            # Log data info for CTrader debugging
            if is_ctrader:
                logger.debug(f"CTrader data for {pair_str}: {s1}={len(data1)} bars, {s2}={len(data2)} bars")
            
            # Align data
            aligned_data = pd.concat([data1, data2], axis=1).fillna(method='ffill').dropna()
            if len(aligned_data) < self.config.z_period:
                logger.warning(f"Insufficient data for {pair_str} after alignment: {len(aligned_data)} bars")
                return None
            
            aligned_data.columns = ['price1', 'price2']
            
            # Calculate indicators
            indicators = self.strategy.calculate_indicators_vectorized(
                aligned_data['price1'], aligned_data['price2']
            )
            
            if not indicators:
                logger.warning(f"No indicators calculated for {pair_str}")
                return None
            
            # Generate signals
            signals = self.strategy.generate_signals_vectorized(indicators)
            if signals.empty:
                logger.warning(f"No signals generated for {pair_str}")
                return None
            
            # Run vectorbt simulation
            trades, equity_curve = self._simulate_pair_trades(pair_str, aligned_data, signals)
            
            # Calculate metrics
            metrics = self._calculate_pair_metrics(pair_str, trades, equity_curve, aligned_data)
            
            return {
                'pair': pair_str,
                'trades': trades,
                'equity_curve': equity_curve,
                'metrics': metrics,
                'data': aligned_data
            }
            
        except Exception as e:
            logger.error(f"Error in single pair backtest for {pair_str}: {e}")
            return None
    
    def _simulate_pair_trades(self, pair_str: str, data: pd.DataFrame, signals: pd.DataFrame) -> Tuple[List[Dict], pd.Series]:
        """Simulate trades using proper pairs trading logic with both legs tracked separately"""
        
        # Initialize trade tracking
        trades = []
        position = 0  # 0=neutral, 1=long, -1=short
        entry_price1 = None
        entry_price2 = None
        entry_time = None
        equity_curve = pd.Series(1.0, index=data.index)
        cooldown_remaining = 0  # Track cooldown period
        
        # Calculate costs for both legs
        s1, s2 = pair_str.split('-')
        costs_per_leg = self.config.max_commission_perc / 2  # Split costs between legs
        
        # Process signals
        for i, (timestamp, signal) in enumerate(signals.iterrows()):
            if i == 0:
                continue
                
            current_price1 = data.loc[timestamp, 'price1']
            current_price2 = data.loc[timestamp, 'price2']
            
            # Decrement cooldown
            if cooldown_remaining > 0:
                cooldown_remaining -= 1
            
            # Entry logic - only if no cooldown
            if position == 0 and cooldown_remaining == 0:
                if signal['long_entry'] and signal['suitable']:
                    # LONG pair trade: Long s1, Short s2
                    position = 1
                    entry_price1 = current_price1
                    entry_price2 = current_price2
                    entry_time = timestamp
                    
                elif signal['short_entry'] and signal['suitable']:
                    # SHORT pair trade: Short s1, Long s2
                    position = -1
                    entry_price1 = current_price1
                    entry_price2 = current_price2
                    entry_time = timestamp
            
            # Exit logic
            elif position != 0:
                should_exit = False
                exit_reason = ""
                
                if position == 1 and signal['long_exit']:
                    should_exit = True
                    exit_reason = "Signal Exit"
                elif position == -1 and signal['short_exit']:
                    should_exit = True
                    exit_reason = "Signal Exit"
                
                # Risk management exits - calculate combined P&L
                if entry_price1 is not None and entry_price2 is not None:
                    # Calculate P&L for both legs
                    if position == 1:
                        # Long s1, Short s2
                        pnl_leg1 = (current_price1 - entry_price1) / entry_price1 * 100  # Long leg P&L
                        pnl_leg2 = (entry_price2 - current_price2) / entry_price2 * 100  # Short leg P&L
                    else:
                        # Short s1, Long s2
                        pnl_leg1 = (entry_price1 - current_price1) / entry_price1 * 100  # Short leg P&L
                        pnl_leg2 = (current_price2 - entry_price2) / entry_price2 * 100  # Long leg P&L
                    
                    # Combined P&L (average of both legs for equal monetary exposure)
                    combined_pnl_pct = (pnl_leg1 + pnl_leg2) / 2
                    
                    if combined_pnl_pct <= -self.config.stop_loss_perc:
                        should_exit = True
                        exit_reason = "Stop Loss"
                    elif combined_pnl_pct >= self.config.take_profit_perc:
                        should_exit = True
                        exit_reason = "Take Profit"
                
                if should_exit:
                    # Calculate final trade metrics
                    exit_price1 = current_price1
                    exit_price2 = current_price2
                    bars_held = len(data.loc[entry_time:timestamp]) - 1
                    
                    # Calculate P&L for both legs
                    if position == 1:
                        # Long s1, Short s2
                        pnl_leg1 = (exit_price1 - entry_price1) / entry_price1 * 100
                        pnl_leg2 = (entry_price2 - exit_price2) / entry_price2 * 100
                    else:
                        # Short s1, Long s2
                        pnl_leg1 = (entry_price1 - exit_price1) / entry_price1 * 100
                        pnl_leg2 = (exit_price2 - entry_price2) / entry_price2 * 100
                    
                    # Combined gross P&L (average for equal monetary exposure)
                    gross_pnl_pct = (pnl_leg1 + pnl_leg2) / 2
                    
                    # Apply costs (costs for both legs)
                    total_trade_costs = costs_per_leg * 2  # Costs for both legs
                    net_pnl_pct = gross_pnl_pct - total_trade_costs
                    
                    # Update equity curve
                    equity_multiplier = 1 + (net_pnl_pct / 100)
                    equity_curve.loc[timestamp:] *= equity_multiplier
                    
                    # Apply cooldown if losing trade
                    if net_pnl_pct < 0:
                        cooldown_remaining = self.config.cooldown_bars
                    
                    # Record trade with detailed leg information
                    trades.append({
                        'entry_time': entry_time,
                        'exit_time': timestamp,
                        'direction': 'LONG' if position == 1 else 'SHORT',
                        'entry_price1': entry_price1,
                        'entry_price2': entry_price2,
                        'exit_price1': exit_price1,
                        'exit_price2': exit_price2,
                        'pnl_leg1': pnl_leg1,
                        'pnl_leg2': pnl_leg2,
                        'pnl_pct': gross_pnl_pct,
                        'net_pnl_pct': net_pnl_pct,
                        'costs_pct': total_trade_costs,
                        'exit_reason': exit_reason,
                        'bars_held': bars_held,
                        'symbol1': s1,
                        'symbol2': s2
                    })
                    
                    # Reset position
                    position = 0
                    entry_price1 = None
                    entry_price2 = None
                    entry_time = None
        
        return trades, equity_curve
    
    def _calculate_trading_costs(self, s1: str, s2: str, data: pd.DataFrame) -> pd.Series:
        """Calculate trading costs for each timestamp"""
        
        if not self.data_manager.symbol_info_cache:
            # Return minimal costs if no symbol info
            return pd.Series(0.1, index=data.index)  # 0.1% default cost
        
        # Get symbol info
        info1 = self.data_manager.symbol_info_cache.get(s1, {})
        info2 = self.data_manager.symbol_info_cache.get(s2, {})
        
        # Calculate spread costs
        spread1_pct = info1.get('spread', 2) * info1.get('point', 0.0001) / data['price1'] * 100
        spread2_pct = info2.get('spread', 2) * info2.get('point', 0.0001) / data['price2'] * 100
        
        # Calculate commission costs
        def is_stock_or_etf(symbol: str) -> bool:
            return '.US' in symbol
        
        commission1_pct = pd.Series(0.0, index=data.index)
        commission2_pct = pd.Series(0.0, index=data.index)
        
        if is_stock_or_etf(s1):
            commission1_pct = self.config.commission_fixed / data['price1'] * 100
        
        if is_stock_or_etf(s2):
            commission2_pct = self.config.commission_fixed / data['price2'] * 100
        
        # Total costs (round trip for both legs)
        total_costs = (2 * commission1_pct + 2 * commission2_pct + 
                      spread1_pct + spread2_pct)
        
        return total_costs.fillna(0.1)  # Default to 0.1% if calculation fails
    
    def _calculate_pair_metrics(self, pair_str: str, trades: List[Dict], 
                               equity_curve: pd.Series, data: pd.DataFrame) -> Dict:
        """Calculate comprehensive metrics for a pair"""
        
        if not trades:
            return {
                'pair': pair_str,
                'total_trades': 0,
                'total_return': 0.0,
                'sharpe_ratio': 0.0,
                'max_drawdown': 0.0,
                'win_rate': 0.0,
                'profit_factor': 0.0,
                'avg_trade_pnl': 0.0,
                'max_trade_pnl': 0.0,
                'min_trade_pnl': 0.0,
                'avg_bars_held': 0.0,
                'composite_score': 0.0,
                'rank': 0
            }
        
        # Basic metrics
        total_trades = len(trades)
        total_return = (equity_curve.iloc[-1] - 1) * 100
        
        # Trade-based metrics
        pnl_list = [trade['net_pnl_pct'] for trade in trades]
        winning_trades = [pnl for pnl in pnl_list if pnl > 0]
        losing_trades = [pnl for pnl in pnl_list if pnl < 0]
        
        win_rate = len(winning_trades) / total_trades if total_trades > 0 else 0
        avg_trade_pnl = np.mean(pnl_list) if pnl_list else 0
        max_trade_pnl = max(pnl_list) if pnl_list else 0
        min_trade_pnl = min(pnl_list) if pnl_list else 0
        
        # Profit factor
        gross_profit = sum(winning_trades) if winning_trades else 0
        gross_loss = abs(sum(losing_trades)) if losing_trades else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        # Sharpe ratio (annualized)
        returns = equity_curve.pct_change().dropna()
        if len(returns) > 0 and returns.std() > 0:
            periods_per_year = self._get_periods_per_year()
            sharpe_ratio = (returns.mean() * periods_per_year) / (returns.std() * np.sqrt(periods_per_year))
        else:
            sharpe_ratio = 0
        
        # Maximum drawdown
        running_max = equity_curve.expanding().max()
        drawdown = (equity_curve - running_max) / running_max
        max_drawdown = drawdown.min()
        
        # Average bars held
        avg_bars_held = np.mean([trade['bars_held'] for trade in trades]) if trades else 0
        
        # Composite score (higher is better)
        composite_score = self._calculate_composite_score(
            total_return, sharpe_ratio, max_drawdown, win_rate, 
            profit_factor, total_trades, avg_trade_pnl
        )
        
        return {
            'pair': pair_str,
            'total_trades': total_trades,
            'total_return': total_return,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_trade_pnl': avg_trade_pnl,
            'max_trade_pnl': max_trade_pnl,
            'min_trade_pnl': min_trade_pnl,
            'avg_bars_held': avg_bars_held,
            'composite_score': composite_score,
            'rank': 0  # Will be set during ranking
        }
    
    def _get_periods_per_year(self) -> int:
        """Get number of periods per year for annualization"""
        interval_map = {
            'M1': 525600,   # 1 minute
            'M5': 105120,   # 5 minutes
            'M15': 35040,   # 15 minutes
            'M30': 17520,   # 30 minutes
            'H1': 8760,     # 1 hour
            'H4': 2190,     # 4 hours
            'D1': 365,      # 1 day
            'W1': 52,       # 1 week
            'MN1': 12       # 1 month
        }
        return interval_map.get(self.config.interval, 35040)
    
    def _calculate_composite_score(self, total_return: float, sharpe_ratio: float, 
                                  max_drawdown: float, win_rate: float, 
                                  profit_factor: float, total_trades: int, 
                                  avg_trade_pnl: float) -> float:
        """Calculate composite score for ranking pairs"""
        
        # Normalize metrics (0-1 scale)
        return_score = min(max(total_return / 100, -1), 1)  # Cap at +/-100%
        sharpe_score = min(max(sharpe_ratio / 3, -1), 1)    # Cap at +/-3
        drawdown_score = min(max(max_drawdown / -50, -1), 1)  # Cap at -50%
        winrate_score = win_rate
        
        # Profit factor score (logarithmic)
        pf_score = min(np.log(max(profit_factor, 0.1)) / np.log(5), 1)
        
        # Trade frequency score
        freq_score = min(total_trades / 100, 1)  # Cap at 100 trades
        
        # Average trade score
        avg_trade_score = min(max(avg_trade_pnl / 5, -1), 1)  # Cap at +/-5%
        
        # Weighted composite score
        weights = {
            'return': 0.25,
            'sharpe': 0.25,
            'drawdown': 0.15,
            'winrate': 0.15,
            'profit_factor': 0.10,
            'frequency': 0,
            'avg_trade': 0.10
        }
        
        composite_score = (
            weights['return'] * return_score +
            weights['sharpe'] * sharpe_score +
            weights['drawdown'] * drawdown_score +
            weights['winrate'] * winrate_score +
            weights['profit_factor'] * pf_score +
            weights['frequency'] * freq_score +
            weights['avg_trade'] * avg_trade_score
        )
        
        return composite_score * 100  # Scale to 0-100
    
    def _calculate_pair_rankings(self, pair_results: List[Dict]) -> List[Dict]:
        """Calculate rankings based on composite scores"""
        
        # Sort by composite score (descending)
        sorted_results = sorted(
            pair_results, 
            key=lambda x: x['metrics']['composite_score'], 
            reverse=True
        )
        
        # Add rankings
        for i, result in enumerate(sorted_results):
            result['metrics']['rank'] = i + 1
        
        # logger.info(f"Pair rankings calculated. Top 5 pairs:")
        # for i, result in enumerate(sorted_results[:5]):
        #     metrics = result['metrics']
        #     logger.info(f"  {i+1}. {metrics['pair']}: Score={metrics['composite_score']:.2f}, "
        #                f"Return={metrics['total_return']:.2f}%, Sharpe={metrics['sharpe_ratio']:.2f}")
        
        return sorted_results
    
    def _run_portfolio_backtest(self, pair_results: List[Dict]) -> Dict:
        """Run portfolio-level backtest with position sizing and risk management"""
        
        logger.info("Running portfolio-level backtest...")
        
        # Filter pairs with trades
        valid_pairs = [result for result in pair_results if result['trades']]
        
        if not valid_pairs:
            logger.warning("No valid pairs for portfolio backtest")
            return {}
        
        # Align all equity curves
        portfolio_equity, portfolio_dates = self._build_portfolio_equity_curve(valid_pairs)
        
        # Calculate portfolio metrics
        portfolio_metrics = self._calculate_portfolio_metrics(
            portfolio_equity, portfolio_dates, valid_pairs
        )
        
        return {
            'equity_curve': portfolio_equity,
            'dates': portfolio_dates,
            **portfolio_metrics
        }
    
    def _build_portfolio_equity_curve(self, pair_results: List[Dict]) -> Tuple[List[float], List[pd.Timestamp]]:
        """Build portfolio equity curve with position sizing"""
        
        # Get common date range
        all_dates = set()
        for result in pair_results:
            all_dates.update(result['equity_curve'].index)
        
        common_dates = sorted(all_dates)
        
        # Initialize portfolio equity
        portfolio_equity = []
        portfolio_value = self.config.initial_portfolio_value
        
        # Track active positions
        active_positions = {}
        position_counter = 0
        
        for date in common_dates:
            daily_pnl = 0.0
            
            # Check for new entries and exits
            for result in pair_results:
                pair_str = result['pair']
                trades = result['trades']
                
                for trade in trades:
                    # Check for entries
                    if trade['entry_time'] == date:
                        if len(active_positions) < self.config.max_open_positions:
                            position_id = f"{pair_str}_{position_counter}"
                            active_positions[position_id] = {
                                'pair': pair_str,
                                'entry_date': date,
                                'position_size': self.config.max_position_size,
                                'trade': trade
                            }
                            position_counter += 1
                    
                    # Check for exits
                    elif trade['exit_time'] == date and any(
                        pos['pair'] == pair_str and pos['entry_date'] == trade['entry_time']
                        for pos in active_positions.values()
                    ):
                        # Find matching position
                        for pos_id, pos in list(active_positions.items()):
                            if (pos['pair'] == pair_str and 
                                pos['entry_date'] == trade['entry_time']):
                                
                                # Calculate position P&L
                                position_pnl = pos['position_size'] * (trade['net_pnl_pct'] / 100)
                                daily_pnl += position_pnl
                                
                                # Remove position
                                del active_positions[pos_id]
                                break
            
            # Update portfolio value
            portfolio_value += daily_pnl
            portfolio_equity.append(portfolio_value)
        
        return portfolio_equity, common_dates
    
    def _calculate_portfolio_metrics(self, equity_curve: List[float], 
                                   dates: List[pd.Timestamp], 
                                   pair_results: List[Dict]) -> Dict:
        """Calculate portfolio-level metrics"""
        
        if not equity_curve:
            return {}
        
        # Convert to series for calculations
        equity_series = pd.Series(equity_curve, index=dates)
        
        # Basic metrics
        initial_value = self.config.initial_portfolio_value
        final_value = equity_series.iloc[-1]
        portfolio_return = (final_value - initial_value) / initial_value * 100
        
        # Sharpe ratio
        returns = equity_series.pct_change().dropna()
        if len(returns) > 0 and returns.std() > 0:
            periods_per_year = self._get_periods_per_year()
            portfolio_sharpe = (returns.mean() * periods_per_year) / (returns.std() * np.sqrt(periods_per_year))
        else:
            portfolio_sharpe = 0
        
        # Maximum drawdown
        running_max = equity_series.expanding().max()
        drawdown = (equity_series - running_max) / running_max * 100
        portfolio_max_drawdown = drawdown.min()
        
        # Trade statistics
        total_trades = sum(len(result['trades']) for result in pair_results)
        total_pairs = len(pair_results)
        
        # Position statistics
        max_concurrent_positions = self._calculate_max_concurrent_positions(pair_results)
        avg_concurrent_positions = self._calculate_avg_concurrent_positions(pair_results)
        
        return {
            'portfolio_return': portfolio_return,
            'portfolio_sharpe': portfolio_sharpe,
            'portfolio_max_drawdown': portfolio_max_drawdown,
            'total_trades': total_trades,
            'total_pairs': total_pairs,
            'max_concurrent_positions': max_concurrent_positions,
            'avg_concurrent_positions': avg_concurrent_positions,
            'final_value': final_value,
            'initial_value': initial_value
        }
    
    def _calculate_max_concurrent_positions(self, pair_results: List[Dict]) -> int:
        """Calculate maximum number of concurrent positions"""
        
        # Get all trade periods
        trade_periods = []
        for result in pair_results:
            for trade in result['trades']:
                trade_periods.append({
                    'start': trade['entry_time'],
                    'end': trade['exit_time'],
                    'pair': result['pair']
                })
        
        if not trade_periods:
            return 0
        
        # Find maximum overlap
        max_concurrent = 0
        all_dates = set()
        
        for period in trade_periods:
            all_dates.add(period['start'])
            all_dates.add(period['end'])
        
        for date in sorted(all_dates):
            concurrent = 0
            for period in trade_periods:
                if period['start'] <= date < period['end']:
                    concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
        
        return max_concurrent
    
    def _calculate_avg_concurrent_positions(self, pair_results: List[Dict]) -> float:
        """Calculate average number of concurrent positions"""
        
        # Get all trade periods
        trade_periods = []
        for result in pair_results:
            for trade in result['trades']:
                trade_periods.append({
                    'start': trade['entry_time'],
                    'end': trade['exit_time'],
                    'pair': result['pair']
                })
        
        if not trade_periods:
            return 0.0
        
        # Calculate average over time
        all_dates = set()
        for period in trade_periods:
            all_dates.add(period['start'])
            all_dates.add(period['end'])
        
        concurrent_counts = []
        for date in sorted(all_dates):
            concurrent = 0
            for period in trade_periods:
                if period['start'] <= date < period['end']:
                    concurrent += 1
            concurrent_counts.append(concurrent)
        
        return np.mean(concurrent_counts) if concurrent_counts else 0.0
