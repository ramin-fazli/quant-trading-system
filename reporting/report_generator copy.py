import os
import datetime
import logging
import numpy as np
import pandas as pd
import xlsxwriter
from typing import Dict, Any
from dataclasses import dataclass
from config import TradingConfig, get_config, force_config_update

force_config_update()
# Setup logging
logger = logging.getLogger(__name__)

def generate_enhanced_report(backtest_results: Dict[str, Any], config: TradingConfig = None) -> str:
    """Generate comprehensive Excel report with advanced analytics
    
    Args:
        backtest_results: Dictionary containing backtest results
        config: Trading configuration object (optional, uses global config if not provided)
        
    Returns:
        str: Path to the generated report file
    """
    
    # Use provided config or get global config
    if config is None:
        config = get_config()
    
    # Create reports directory if it doesn't exist
    reports_dir = config.reports_dir
    os.makedirs(reports_dir, exist_ok=True)
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(reports_dir, f"PairsTrading_MT5_Report_{timestamp}.xlsx")
    
    try:
        # Create workbook with options to handle timezone issues and NaN/Infinity values
        workbook = xlsxwriter.Workbook(filename, {
            'remove_timezone': True,
            'nan_inf_to_errors': True  # Convert NaN/Inf to error cells instead of raising errors
        })
        
        # Define formats
        title_fmt = workbook.add_format({'bold': True, 'font_size': 16})
        header_fmt = workbook.add_format({'bold': True, 'bg_color': '#D3D3D3'})
        number_fmt = workbook.add_format({'num_format': '0.00'})
        percent_fmt = workbook.add_format({'num_format': '0.00%'})
        money_fmt = workbook.add_format({'num_format': '$#,##0.00'})
        date_fmt = workbook.add_format({'num_format': 'yyyy-mm-dd hh:mm'})
        red_fmt = workbook.add_format({'font_color': 'red'})
        green_fmt = workbook.add_format({'font_color': 'green'})
        bold_green_fmt = workbook.add_format({'font_color': 'green', 'bold': True})
        bold_red_fmt = workbook.add_format({'font_color': 'red', 'bold': True})
        
        # Helper function to safely write numeric values to Excel
        def safe_write_numeric(worksheet, row_idx, col_idx, value, fmt=None):
            """Write numeric value to worksheet, handling NaN, Inf, and other special cases"""
            if value is None:
                worksheet.write_string(row_idx, col_idx, "N/A", fmt)
            elif isinstance(value, (float, int)):
                if np.isnan(value) or np.isinf(value):
                    worksheet.write_string(row_idx, col_idx, "N/A", fmt)
                else:
                    worksheet.write(row_idx, col_idx, value, fmt)
            else:
                worksheet.write(row_idx, col_idx, value, fmt)
        
        # Generate summary sheet
        _generate_summary_sheet(workbook, backtest_results, config, title_fmt, header_fmt, 
                               number_fmt, percent_fmt, green_fmt, red_fmt, safe_write_numeric)
        
        # Generate pair results sheet
        _generate_pairs_sheet(workbook, backtest_results, header_fmt, number_fmt, 
                         percent_fmt, green_fmt, red_fmt, safe_write_numeric)
        
        # Generate performance charts sheet
        _generate_charts_sheet(workbook, backtest_results, header_fmt, date_fmt)
        
        # Generate individual pair sheets
        _generate_individual_pair_sheets(workbook, backtest_results, title_fmt, header_fmt,
                                       number_fmt, percent_fmt, date_fmt, green_fmt, red_fmt,
                                       bold_green_fmt, bold_red_fmt, safe_write_numeric)
        
        workbook.close()
        logger.info(f"Enhanced report saved to: {filename}")
        return filename
        
    except Exception as e:
        logger.error(f"Error generating report: {e}")
        # Print more detailed error for debugging
        import traceback
        logger.error(traceback.format_exc())
        raise


def _generate_summary_sheet(workbook, backtest_results, config, title_fmt, header_fmt, 
                           number_fmt, percent_fmt, green_fmt, red_fmt, safe_write_numeric):
    """Generate the portfolio summary sheet"""
    summary = workbook.add_worksheet('Portfolio Summary')
    row = 0
    
    summary.write(row, 0, 'Pairs Trading MT5 - Portfolio Report', title_fmt)
    row += 1
    summary.write(row, 0, f'Backtest Period: {config.start_date} to {config.end_date or datetime.datetime.now().strftime("%Y-%m-%d")}')
    row += 2
    
    # Backtest parameters
    summary.write(row, 0, 'Backtest Parameters', header_fmt)
    row += 1
    
    parameters = [
        ('Interval', config.interval),
        ('Start Date', config.start_date),
        ('End Date', config.end_date or 'Current'),
        
        # Strategy Parameters
        ('Z-Score Entry', config.z_entry),
        ('Z-Score Exit', config.z_exit),
        ('Z-Score Period', config.z_period),
        ('Dynamic Z-Score', 'Yes' if config.dynamic_z else 'No'),
        ('Min Distance from Mean (%)', config.min_distance),
        ('Min Volatility', config.min_volatility),
        
        # Statistical Tests
        ('ADF Test Enabled', 'Yes' if config.enable_adf else 'No'),
        ('ADF Max P-Value', config.max_adf_pval),
        ('ADF Period', config.adf_period),
        ('Johansen Test Enabled', 'Yes' if config.enable_johansen else 'No'),
        ('Johansen Critical Level', config.johansen_crit_level),
        ('Correlation Test Enabled', 'Yes' if config.enable_correlation else 'No'),
        ('Min Correlation', config.min_corr),
        ('Correlation Period', config.corr_period),
        ('Volatility Ratio Test Enabled', 'Yes' if config.enable_vol_ratio else 'No'),
        ('Max Volatility Ratio', config.vol_ratio_max),
        
        # Risk Management
        ('Take Profit (%)', config.take_profit_perc),
        ('Stop Loss (%)', config.stop_loss_perc),
        ('Trailing Stop (%)', config.trailing_stop_perc),
        ('Cooldown Bars', config.cooldown_bars),
        ('Max Position Size', config.max_position_size),
        ('Max Open Positions', config.max_open_positions),
        ('Max Monetary Exposure', config.max_monetary_exposure),
        ('Monetary Value Tolerance', config.monetary_value_tolerance),
        ('Max Commission (%)', config.max_commission_perc),
        ('Fixed Commission', config.commission_fixed),
        ('Slippage Points', config.slippage_points),
        ('Max Pair Drawdown (%)', config.max_pair_drawdown_perc),
        ('Max Portfolio Drawdown (%)', config.max_portfolio_drawdown_perc),
        ('Initial Portfolio Value', config.initial_portfolio_value),
    ]
    
    for param, value in parameters:
        summary.write(row, 0, param)
        if isinstance(value, float) and '%' in param:
            summary.write(row, 1, value / 100, percent_fmt)
        else:
            summary.write(row, 1, value)
        row += 1
    
    row += 1
    
    # Portfolio metrics
    portfolio_metrics = backtest_results.get('portfolio_metrics', {})
    
    summary.write(row, 0, 'Portfolio Performance', header_fmt)
    row += 1
    
    metrics = [
        ('Total Pairs', portfolio_metrics.get('total_pairs', 0)),
        ('Total Trades', portfolio_metrics.get('total_trades', 0)),
        ('Portfolio Return (%)', portfolio_metrics.get('portfolio_return', 0)),
        ('Sharpe Ratio', portfolio_metrics.get('portfolio_sharpe', 0)),
        ('Max Drawdown (%)', portfolio_metrics.get('portfolio_max_drawdown', 0)),
        ('Max Concurrent Positions', portfolio_metrics.get('max_concurrent_positions', 0)),
        ('Avg Concurrent Positions', portfolio_metrics.get('avg_concurrent_positions', 0)),
    ]
    
    for metric, value in metrics:
        summary.write(row, 0, metric)
        if isinstance(value, float) and ('%' in metric or 'Return' in metric or 'Drawdown' in metric):
            fmt = percent_fmt
            if value > 0 and ('Return' in metric or 'Profit' in metric):
                fmt = workbook.add_format({'num_format': '0.00%', 'font_color': 'green'})
            elif value < 0 and ('Return' in metric or 'Profit' in metric):
                fmt = workbook.add_format({'num_format': '0.00%', 'font_color': 'red'})
            
            if np.isnan(value) or np.isinf(value):
                summary.write_string(row, 1, "N/A", fmt)
            else:
                summary.write(row, 1, value / 100, fmt)
        else:
            summary.write(row, 1, value)
        row += 1
    
    # Add top/bottom performing pairs
    _add_top_bottom_pairs(summary, backtest_results, row, header_fmt, green_fmt, red_fmt, 
                         number_fmt, percent_fmt, safe_write_numeric)


def _generate_pairs_sheet(workbook, backtest_results, header_fmt, number_fmt, 
                         percent_fmt, green_fmt, red_fmt, safe_write_numeric):
    """Generate the all pairs results sheet"""
    if not backtest_results.get('pair_results'):
        return
    
    pairs_sheet = workbook.add_worksheet('All Pairs')
    
    headers = [
        'Rank', 'Pair', 'Trades', 'Win Rate', 'Return (%)', 'Sharpe Ratio', 
        'Max Drawdown (%)', 'Profit Factor', 'Avg Trade (%)', 'Avg Bars', 
        'Score'
    ]
    
    for col, header in enumerate(headers):
        pairs_sheet.write(0, col, header, header_fmt)
    
    pair_results = sorted(
        backtest_results.get('pair_results', []),
        key=lambda x: x['metrics'].get('composite_score', 0),
        reverse=True
    )
    
    for row_idx, result in enumerate(pair_results, 1):
        metrics = result['metrics']
        pairs_sheet.write(row_idx, 0, metrics.get('rank', row_idx))
        pairs_sheet.write(row_idx, 1, metrics.get('pair', ''))
        pairs_sheet.write(row_idx, 2, metrics.get('total_trades', 0))
        
        # Handle metrics safely
        win_rate = metrics.get('win_rate', 0)
        safe_write_numeric(pairs_sheet, row_idx, 3, win_rate, percent_fmt)
        
        ret = metrics.get('total_return', 0)
        fmt = green_fmt if ret > 0 else red_fmt
        safe_write_numeric(pairs_sheet, row_idx, 4, ret, fmt)
        
        safe_write_numeric(pairs_sheet, row_idx, 5, metrics.get('sharpe_ratio', 0), number_fmt)
        safe_write_numeric(pairs_sheet, row_idx, 6, metrics.get('max_drawdown', 0), percent_fmt)
        safe_write_numeric(pairs_sheet, row_idx, 7, metrics.get('profit_factor', 0), number_fmt)
        safe_write_numeric(pairs_sheet, row_idx, 8, metrics.get('avg_trade_pnl', 0) / 100, percent_fmt)
        safe_write_numeric(pairs_sheet, row_idx, 9, metrics.get('avg_bars_held', 0), number_fmt)
        safe_write_numeric(pairs_sheet, row_idx, 10, metrics.get('composite_score', 0), number_fmt)


def _generate_charts_sheet(workbook, backtest_results, header_fmt, date_fmt):
    """Generate performance charts sheet"""
    try:
        chart_sheet = workbook.add_worksheet('Performance Charts')
        
        if 'portfolio_equity' in backtest_results:
            # Prepare data for chart
            chart_data = pd.DataFrame({
                'Date': [d.replace(tzinfo=None) if hasattr(d, 'tzinfo') and d.tzinfo else d 
                        for d in backtest_results.get('portfolio_dates', [])],
                'Equity': backtest_results.get('portfolio_equity', [])
            })
            
            chart_data_sorted = chart_data.sort_values('Date')
            
            # Write data to sheet
            chart_sheet.write(0, 0, 'Date', header_fmt)
            chart_sheet.write(0, 1, 'Equity', header_fmt)
            
            for i, (date, equity) in enumerate(zip(chart_data_sorted['Date'], chart_data_sorted['Equity'])):
                chart_sheet.write(i+1, 0, date, date_fmt)
                chart_sheet.write(i+1, 1, equity)
            
            # Create chart
            equity_chart = workbook.add_chart({'type': 'line'})
            equity_chart.add_series({
                'name': 'Portfolio Equity',
                'categories': ['Performance Charts', 1, 0, len(chart_data), 0],
                'values': ['Performance Charts', 1, 1, len(chart_data), 1],
                'line': {'color': 'blue', 'width': 1.5},
            })
            
            equity_chart.set_title({'name': 'Portfolio Equity Curve'})
            equity_chart.set_size({'width': 720, 'height': 400})
            chart_sheet.insert_chart('D2', equity_chart)
    
    except Exception as e:
        logger.error(f"Error generating performance charts: {e}")


def _generate_individual_pair_sheets(workbook, backtest_results, title_fmt, header_fmt,
                                   number_fmt, percent_fmt, date_fmt, green_fmt, red_fmt,
                                   bold_green_fmt, bold_red_fmt, safe_write_numeric):
    """Generate individual pair analysis sheets"""
    pair_results = sorted(
        backtest_results.get('pair_results', []),
        key=lambda x: x['metrics'].get('composite_score', 0),
        reverse=True
    )
    
    for result in pair_results[:20]:  # Limit to top 20 pairs
        pair_name = result['pair'].replace('-', '_')
        if len(pair_name) > 31:  # Excel limit
            pair_name = pair_name[:31]
        
        sheet = workbook.add_worksheet(pair_name)
        
        # Pair summary
        sheet.write(0, 0, f"Analysis: {result['pair']} (Rank {result['metrics'].get('rank', '-')})", title_fmt)
        sheet.write(1, 0, "Performance Metrics", header_fmt)
        
        # Detailed metrics
        metrics_data = [
            ('Total Return (%)', result['metrics'].get('total_return', 0), True),
            ('Win Rate', result['metrics'].get('win_rate', 0), False),
            ('Sharpe Ratio', result['metrics'].get('sharpe_ratio', 0), False),
            ('Max Drawdown (%)', result['metrics'].get('max_drawdown', 0), True),
            ('Profit Factor', result['metrics'].get('profit_factor', 0), False),
            ('Total Trades', result['metrics'].get('total_trades', 0), False),
            ('Avg Bars Held', result['metrics'].get('avg_bars_held', 0), False),
        ]
        
        for i, (metric, value, is_percentage) in enumerate(metrics_data):
            sheet.write(i+2, 0, metric)
            
            if is_percentage:
                fmt = percent_fmt
                if 'Return' in metric or 'PnL' in metric:
                    fmt = bold_green_fmt if value > 0 else bold_red_fmt
            else:
                fmt = number_fmt
            
            safe_write_numeric(sheet, i+2, 1, value, fmt)
        
        # Add equity curve for this pair
        _add_pair_equity_curve(sheet, result, workbook, header_fmt, date_fmt, number_fmt)
        
        # Add trade details
        _add_trade_details(sheet, result, header_fmt, date_fmt, number_fmt, 
                          percent_fmt, green_fmt, red_fmt, safe_write_numeric)


def _add_pair_equity_curve(sheet, result, workbook, header_fmt, date_fmt, number_fmt):
    """Add equity curve chart for individual pair"""
    try:
        trades = result.get('trades', [])
        if not trades:
            return
        
        # Sort trades by entry time
        trades = sorted(trades, key=lambda x: x.get('entry_time', ''))
        
        # Calculate cumulative equity curve
        equity_data = []
        cumulative_pnl = 0
        
        for trade in trades:
            entry_time = trade.get('entry_time')
            exit_time = trade.get('exit_time')
            net_pnl_pct = trade.get('net_pnl_pct', 0)
            
            # Handle datetime conversion
            if hasattr(entry_time, 'tzinfo') and entry_time.tzinfo is not None:
                entry_time = entry_time.replace(tzinfo=None)
            if hasattr(exit_time, 'tzinfo') and exit_time.tzinfo is not None:
                exit_time = exit_time.replace(tzinfo=None)
            
            # Add entry point (no change in equity)
            equity_data.append({
                'date': entry_time,
                'equity': cumulative_pnl
            })
            
            # Add exit point (with PnL change)
            cumulative_pnl += net_pnl_pct
            equity_data.append({
                'date': exit_time,
                'equity': cumulative_pnl
            })
        
        if len(equity_data) < 2:
            return
        
        # Find suitable location for equity curve data (after metrics, before trade details)
        equity_start_row = 12
        equity_start_col = 0
        
        # Write equity curve header
        sheet.write(equity_start_row, equity_start_col, 'Equity Curve Data', header_fmt)
        sheet.write(equity_start_row + 1, equity_start_col, 'Date', header_fmt)
        sheet.write(equity_start_row + 1, equity_start_col + 1, 'Cumulative Return (%)', header_fmt)
        
        # Write equity data
        for i, point in enumerate(equity_data):
            row = equity_start_row + 2 + i
            sheet.write(row, equity_start_col, point['date'], date_fmt)
            sheet.write(row, equity_start_col + 1, point['equity'], number_fmt)
        
        # Create equity curve chart
        equity_chart = workbook.add_chart({'type': 'line'})
        
        # Define data range for chart
        data_end_row = equity_start_row + 1 + len(equity_data)
        
        equity_chart.add_series({
            'name': f'{result["pair"]} Equity Curve',
            'categories': [sheet.name, equity_start_row + 2, equity_start_col, data_end_row, equity_start_col],
            'values': [sheet.name, equity_start_row + 2, equity_start_col + 1, data_end_row, equity_start_col + 1],
            'line': {'color': 'blue', 'width': 2},
            'marker': {'type': 'circle', 'size': 3}
        })
        
        equity_chart.set_title({'name': f'{result["pair"]} - Cumulative Return (%)'})
        equity_chart.set_x_axis({'name': 'Date', 'date_axis': True})
        equity_chart.set_y_axis({'name': 'Cumulative Return (%)'})
        equity_chart.set_size({'width': 600, 'height': 300})
        
        # Insert chart next to the metrics
        sheet.insert_chart('D2', equity_chart)
        
    except Exception as e:
        logger.error(f"Error generating equity curve for pair {result.get('pair', 'Unknown')}: {e}")


def _add_top_bottom_pairs(summary, backtest_results, row, header_fmt, green_fmt, red_fmt, 
                         number_fmt, percent_fmt, safe_write_numeric):
    """Add top and bottom performing pairs to summary"""
    summary.write(row, 0, 'Top 10 Performing Pairs', header_fmt)
    summary.write(row, 6, 'Bottom 10 Performing Pairs', header_fmt)
    row += 1
    
    top_headers = ['Rank', 'Pair', 'Return (%)', 'Sharpe', 'Win Rate']
    for col, header in enumerate(top_headers):
        summary.write(row, col, header, header_fmt)
        summary.write(row, col+6, header, header_fmt)
    
    row += 1
    
    pair_results = sorted(
        backtest_results.get('pair_results', []),
        key=lambda x: x['metrics'].get('composite_score', 0),
        reverse=True
    )
    
    # Write top 10 pairs
    for i, result in enumerate(pair_results[:10]):
        metrics = result['metrics']
        summary.write(row + i, 0, metrics.get('rank', i+1))
        summary.write(row + i, 1, metrics.get('pair', ''))
        
        ret = metrics.get('total_return', 0)
        fmt = green_fmt if ret > 0 else red_fmt
        safe_write_numeric(summary, row + i, 2, ret, fmt)
        safe_write_numeric(summary, row + i, 3, metrics.get('sharpe_ratio', 0), number_fmt)
        safe_write_numeric(summary, row + i, 4, metrics.get('win_rate', 0), percent_fmt)
    
    # Write bottom 10 pairs
    bottom_pairs = pair_results[-10:] if len(pair_results) > 10 else []
    bottom_pairs.reverse()
    
    for i, result in enumerate(bottom_pairs):
        metrics = result['metrics']
        summary.write(row + i, 0 + 6, metrics.get('rank', len(pair_results)-i))
        summary.write(row + i, 1 + 6, metrics.get('pair', ''))
        
        ret = metrics.get('total_return', 0)
        fmt = green_fmt if ret > 0 else red_fmt
        safe_write_numeric(summary, row + i, 2 + 6, ret, fmt)
        safe_write_numeric(summary, row + i, 3 + 6, metrics.get('sharpe_ratio', 0), number_fmt)
        safe_write_numeric(summary, row + i, 4 + 6, metrics.get('win_rate', 0), percent_fmt)


def _add_trade_details(sheet, result, header_fmt, date_fmt, number_fmt, 
                      percent_fmt, green_fmt, red_fmt, safe_write_numeric):
    """Add trade details to individual pair sheet"""
    trades = result.get('trades', [])
    if not trades:
        return
    
    # Start trade details further down to accommodate equity curve
    trade_details_start_row = 25
    
    sheet.write(trade_details_start_row, 0, 'Trade History', header_fmt)
    
    trade_headers = [
        'Entry Time', 'Exit Time', 'Direction', 'Entry Price', 
        'Exit Price', 'PnL (%)', 'Net PnL (%)', 'Costs (%)', 'Exit Reason', 'Bars Held'
    ]
    
    for col, header in enumerate(trade_headers):
        sheet.write(trade_details_start_row + 1, col, header, header_fmt)
    
    # Sort trades by entry time
    trades = sorted(trades, key=lambda x: x.get('entry_time', ''))
    
    for row_idx, trade in enumerate(trades[:100], trade_details_start_row + 2):  # Limit to first 100 trades
        # Handle datetime conversion
        entry_time = trade.get('entry_time')
        exit_time = trade.get('exit_time')
        
        if hasattr(entry_time, 'tzinfo') and entry_time.tzinfo is not None:
            entry_time = entry_time.replace(tzinfo=None)
        
        if hasattr(exit_time, 'tzinfo') and exit_time.tzinfo is not None:
            exit_time = exit_time.replace(tzinfo=None)
        
        sheet.write(row_idx, 0, entry_time, date_fmt)
        sheet.write(row_idx, 1, exit_time, date_fmt)
        sheet.write(row_idx, 2, trade.get('direction', ''))
        safe_write_numeric(sheet, row_idx, 3, trade.get('entry_price', 0), number_fmt)
        safe_write_numeric(sheet, row_idx, 4, trade.get('exit_price', 0), number_fmt)
        
        pnl = trade.get('pnl_pct', 0)
        fmt = green_fmt if pnl > 0 else red_fmt
        safe_write_numeric(sheet, row_idx, 5, pnl, fmt)
        
        net_pnl = trade.get('net_pnl_pct', 0)
        fmt = green_fmt if net_pnl > 0 else red_fmt
        safe_write_numeric(sheet, row_idx, 6, net_pnl, fmt)
        
        costs = trade.get('costs_pct', 0)
        safe_write_numeric(sheet, row_idx, 7, costs / 100, percent_fmt)
        
        sheet.write(row_idx, 8, trade.get('exit_reason', ''))
        safe_write_numeric(sheet, row_idx, 9, trade.get('bars_held', 0), None)
    
    # Add conditional formatting for PnL columns
        if len(trades) > 0:
            sheet.conditional_format(trade_details_start_row + 2, 5, trade_details_start_row + 1 + len(trades), 6, {
                'type': '3_color_scale',
                'min_color': "#FF6347",
                'mid_color': "#FFFFFF",
                'max_color': "#90EE90"
            })
        exit_time = trade.get('exit_time')
        
        if hasattr(entry_time, 'tzinfo') and entry_time.tzinfo is not None:
            entry_time = entry_time.replace(tzinfo=None)
        
        if hasattr(exit_time, 'tzinfo') and exit_time.tzinfo is not None:
            exit_time = exit_time.replace(tzinfo=None)
        
        sheet.write(row_idx, 3, entry_time, date_fmt)
        sheet.write(row_idx, 4, exit_time, date_fmt)
        sheet.write(row_idx, 5, trade.get('direction', ''))
        safe_write_numeric(sheet, row_idx, 6, trade.get('entry_price', 0), number_fmt)
        safe_write_numeric(sheet, row_idx, 7, trade.get('exit_price', 0), number_fmt)
        
        pnl = trade.get('pnl_pct', 0)
        fmt = green_fmt if pnl > 0 else red_fmt
        safe_write_numeric(sheet, row_idx, 8, pnl, fmt)
        
        net_pnl = trade.get('net_pnl_pct', 0)
        fmt = green_fmt if net_pnl > 0 else red_fmt
        safe_write_numeric(sheet, row_idx, 9, net_pnl, fmt)
        
        costs = trade.get('costs_pct', 0)
        safe_write_numeric(sheet, row_idx, 10, costs / 100, percent_fmt)
        
        sheet.write(row_idx, 11, trade.get('exit_reason', ''))
        safe_write_numeric(sheet, row_idx, 12, trade.get('bars_held', 0), None)
        
        # Add conditional formatting for PnL columns
        if len(trades) > 0:
            sheet.conditional_format(4, 8, 4+len(trades)-1, 9, {
                'type': '3_color_scale',
                'min_color': "#FF6347",
                'mid_color': "#FFFFFF",
                'max_color': "#90EE90",
            })
