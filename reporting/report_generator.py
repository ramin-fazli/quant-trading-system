import os
import datetime
import logging
import traceback
import numpy as np
import pandas as pd
import xlsxwriter
import tempfile
import shutil
from typing import Dict, Any
from dataclasses import dataclass
from config import TradingConfig, get_config, force_config_update

force_config_update()
# Setup logging
logger = logging.getLogger(__name__)

# Import matplotlib for advanced plotting
try:
    import matplotlib
    matplotlib.use('Agg')  # Use non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    logger.warning("Matplotlib not available. Advanced plotting will be disabled.")

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
    filename = os.path.join(reports_dir, f"BACKTEST_Report_{timestamp}.xlsx")
    
    # Create temporary directory for plots if matplotlib is available
    tempdir = None
    if MATPLOTLIB_AVAILABLE:
        tempdir = tempfile.mkdtemp()
        logger.debug(f"Created temporary directory for plots: {tempdir}")
    
    try:
        # Create workbook with enhanced options
        workbook = xlsxwriter.Workbook(filename, {
            'remove_timezone': True,
            'nan_inf_to_errors': True,
            'strings_to_numbers': True,
            'default_date_format': 'yyyy-mm-dd hh:mm'
        })
        
        # Define professional color scheme
        colors = {
            'primary': '#1f4e79',      # Dark blue
            'secondary': '#4472c4',    # Medium blue
            'accent': '#70ad47',       # Green
            'warning': '#ffc000',      # Amber
            'danger': '#c55a5a',       # Red
            'light_gray': '#f2f2f2',   # Light gray
            'medium_gray': '#d9d9d9',  # Medium gray
            'dark_gray': '#595959'     # Dark gray
        }
        
        # Define enhanced formats
        formats = _create_enhanced_formats(workbook, colors)
        
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
        
        # Generate Comprehensive Dashboard (replaces Executive Summary, Portfolio Summary, and Risk Analysis)
        _generate_comprehensive_dashboard(workbook, backtest_results, config, formats, safe_write_numeric)
        
        # Generate enhanced pair results sheet
        _generate_pairs_sheet(workbook, backtest_results, formats, safe_write_numeric)
        
        # Generate enhanced performance dashboard
        _generate_performance_dashboard(workbook, backtest_results, formats, safe_write_numeric)
        
        # Generate individual pair sheets - pass tempdir to avoid early cleanup
        _generate_individual_pair_sheets(workbook, backtest_results, formats, safe_write_numeric, tempdir)
        
        # Close workbook BEFORE cleaning up temp directory
        workbook.close()
        logger.debug(f"Enhanced report saved to: {filename}")
        
        return filename
        
    except Exception as e:
        logger.error(f"Error generating report: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise
    finally:
        # Clean up temporary directory only after workbook is closed
        if tempdir and os.path.exists(tempdir):
            try:
                shutil.rmtree(tempdir)
                logger.debug(f"Cleaned up temporary directory: {tempdir}")
            except Exception as e:
                logger.error(f"Error cleaning up temporary directory: {e}")


def _create_enhanced_formats(workbook, colors):
    """Create comprehensive formatting styles"""
    return {
        # Headers and titles
        'main_title': workbook.add_format({
            'font_size': 20, 'bold': True, 'font_color': colors['primary'],
            'align': 'center', 'valign': 'vcenter'
        }),
        'section_title': workbook.add_format({
            'font_size': 14, 'bold': True, 'font_color': colors['primary'],
            'bg_color': colors['light_gray'], 'border': 1
        }),
        'subsection_title': workbook.add_format({
            'font_size': 12, 'bold': True, 'font_color': colors['secondary'],
            'bg_color': colors['light_gray'], 'border': 1
        }),
        'table_header': workbook.add_format({
            'bold': True, 'bg_color': colors['primary'], 'font_color': 'white',
            'border': 1, 'align': 'center', 'valign': 'vcenter'
        }),
        'table_subheader': workbook.add_format({
            'bold': True, 'bg_color': colors['secondary'], 'font_color': 'white',
            'border': 1, 'align': 'center', 'valign': 'vcenter'
        }),
        
        # Data formats
        'number': workbook.add_format({'num_format': '#,##0.00', 'border': 1}),
        'integer': workbook.add_format({'num_format': '#,##0', 'border': 1}),
        'percentage': workbook.add_format({'num_format': '0.00%', 'border': 1}),
        'currency': workbook.add_format({'num_format': '$#,##0.00', 'border': 1}),
        'date': workbook.add_format({'num_format': 'yyyy-mm-dd hh:mm', 'border': 1}),
        'text': workbook.add_format({'border': 1}),
        
        # Conditional colors
        'positive': workbook.add_format({
            'font_color': colors['accent'], 'bold': True, 'border': 1
        }),
        'negative': workbook.add_format({
            'font_color': colors['danger'], 'bold': True, 'border': 1
        }),
        'neutral': workbook.add_format({'border': 1}),
        
        # KPI formats
        'kpi_value': workbook.add_format({
            'font_size': 16, 'bold': True, 'align': 'center',
            'bg_color': colors['light_gray'], 'border': 2
        }),
        'kpi_label': workbook.add_format({
            'font_size': 10, 'align': 'center', 'text_wrap': True,
            'bg_color': colors['medium_gray'], 'border': 1
        }),
        
        # Alert formats
        'alert_high': workbook.add_format({
            'bg_color': colors['danger'], 'font_color': 'white', 'bold': True, 'border': 1
        }),
        'alert_medium': workbook.add_format({
            'bg_color': colors['warning'], 'font_color': 'black', 'bold': True, 'border': 1
        }),
        'alert_low': workbook.add_format({
            'bg_color': colors['accent'], 'font_color': 'white', 'bold': True, 'border': 1
        }),
        
        # Row alternating
        'row_even': workbook.add_format({'bg_color': '#f9f9f9', 'border': 1}),
        'row_odd': workbook.add_format({'bg_color': 'white', 'border': 1}),
    }


def _generate_comprehensive_dashboard(workbook, backtest_results, config, formats, safe_write_numeric):
    """Generate comprehensive dashboard combining executive summary, portfolio summary, and risk analysis"""
    sheet = workbook.add_worksheet('Strategy Dashboard')
    
    # Set column widths for optimal layout
    sheet.set_column('A:A', 25)
    sheet.set_column('B:E', 16)
    sheet.set_column('F:J', 14)
    sheet.set_column('K:P', 12)
    
    # Main title and header
    sheet.merge_range('A1:P2', 'PAIRS TRADING STRATEGY - COMPREHENSIVE DASHBOARD', formats['main_title'])
    sheet.set_row(0, 30)
    sheet.set_row(1, 20)
    
    # Date range and timestamp
    end_date = config.end_date or datetime.datetime.now().strftime("%Y-%m-%d")
    sheet.merge_range('A3:P3', f'Analysis Period: {config.start_date} to {end_date} | Generated: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}', formats['subsection_title'])
    
    portfolio_metrics = backtest_results.get('portfolio_metrics', {})
    
    # === SECTION 1: KEY PERFORMANCE INDICATORS ===
    row = 5
    sheet.merge_range(f'A{row}:P{row}', 'KEY PERFORMANCE INDICATORS', formats['section_title'])
    sheet.set_row(row-1, 25)
    row += 1
    
    # Create KPI grid layout (2 rows x 5 columns)
    kpis = [
        ('Portfolio Return', portfolio_metrics.get('portfolio_return', 0), '%', 'positive'),
        ('Sharpe Ratio', portfolio_metrics.get('portfolio_sharpe', 0), '', 'neutral'),
        ('Max Drawdown', portfolio_metrics.get('portfolio_max_drawdown', 0), '%', 'negative'),
        ('Total Trades', portfolio_metrics.get('total_trades', 0), '', 'neutral'),
        ('Win Rate', portfolio_metrics.get('portfolio_win_rate', 0), '%', 'positive'),
        ('Sortino Ratio', portfolio_metrics.get('sortino_ratio', 0), '', 'neutral'),
        ('Calmar Ratio', portfolio_metrics.get('calmar_ratio', 0), '', 'neutral'),
        ('Volatility', portfolio_metrics.get('volatility', 0), '%', 'negative'),
        ('Profitable Pairs', len([p for p in backtest_results.get('pair_results', []) if p['metrics'].get('total_return', 0) > 0]), '', 'positive'),
        ('Max Positions', portfolio_metrics.get('max_concurrent_positions', 0), '', 'neutral')
    ]
    
    # Top row KPIs
    for i in range(5):
        col = i * 2 + 1  # Reduced from 3 to 2 columns distance
        if i < len(kpis):
            label, value, unit, type_hint = kpis[i]
            
            # KPI label
            sheet.write(row, col, label, formats['kpi_label'])
            
            # KPI value with color coding
            if type_hint == 'positive' and value > 0:
                kpi_fmt = formats['alert_low']
            elif type_hint == 'negative' and value < 0:
                kpi_fmt = formats['alert_high']
            elif type_hint == 'positive' and value < 0:
                kpi_fmt = formats['alert_high']
            else:
                kpi_fmt = formats['kpi_value']
            
            display_value = f"{value:.2f}{unit}" if unit else f"{value:.2f}"
            sheet.write(row + 1, col, display_value, kpi_fmt)
    
    # Bottom row KPIs
    row += 3
    for i in range(5, 10):
        col = (i - 5) * 2 + 1  # Reduced from 3 to 2 columns distance
        if i < len(kpis):
            label, value, unit, type_hint = kpis[i]
            
            # KPI label
            sheet.write(row, col, label, formats['kpi_label'])
            
            # KPI value with color coding
            if type_hint == 'positive' and value > 0:
                kpi_fmt = formats['alert_low']
            elif type_hint == 'negative' and value < 0:
                kpi_fmt = formats['alert_high']
            elif type_hint == 'positive' and value < 0:
                kpi_fmt = formats['alert_high']
            else:
                kpi_fmt = formats['kpi_value']
            
            display_value = f"{value:.2f}{unit}" if unit else f"{value:.2f}"
            sheet.write(row + 1, col, display_value, kpi_fmt)
    
    # === SECTION 2: PORTFOLIO COMPOSITION & STRATEGY PARAMETERS ===
    row = 12
    sheet.merge_range(f'A{row}:H{row}', 'PORTFOLIO COMPOSITION', formats['section_title'])
    sheet.merge_range(f'I{row}:P{row}', 'STRATEGY PARAMETERS', formats['section_title'])
    row += 1
    
    # Portfolio composition (left side)
    composition_data = [
        ('Total Pairs Analyzed', len(backtest_results.get('pair_results', []))),
        ('Profitable Pairs', len([p for p in backtest_results.get('pair_results', []) if p['metrics'].get('total_return', 0) > 0])),
        ('Losing Pairs', len([p for p in backtest_results.get('pair_results', []) if p['metrics'].get('total_return', 0) < 0])),
        ('Average Trades per Pair', portfolio_metrics.get('avg_trades_per_pair', 0)),
        ('Average Position Duration', portfolio_metrics.get('avg_position_duration', 0)),
        ('Total Trading Days', portfolio_metrics.get('total_trading_days', 0)),
        ('Max Concurrent Positions', portfolio_metrics.get('max_concurrent_positions', 0)),
        ('Avg Concurrent Positions', portfolio_metrics.get('avg_concurrent_positions', 0)),
    ]
    
    # Headers for composition
    sheet.write(row, 0, 'Metric', formats['table_header'])
    sheet.write(row, 1, 'Value', formats['table_header'])
    
    # Strategy parameters (right side)
    strategy_params = [
        ('Interval', config.interval),
        ('Z-Score Entry', config.z_entry),
        ('Z-Score Exit', config.z_exit),
        ('Z-Score Period', config.z_period),
        ('Dynamic Z-Score', 'Yes' if config.dynamic_z else 'No'),
        ('Take Profit (%)', config.take_profit_perc),
        ('Stop Loss (%)', config.stop_loss_perc),
        ('Max Position Size', config.max_position_size),
        ('Initial Portfolio Value', config.initial_portfolio_value),
    ]
    
    # Headers for strategy parameters
    sheet.write(row, 8, 'Parameter', formats['table_header'])
    sheet.write(row, 9, 'Value', formats['table_header'])
    row += 1
    
    # Fill composition and strategy data
    max_rows = max(len(composition_data), len(strategy_params))
    for i in range(max_rows):
        # Composition data
        if i < len(composition_data):
            label, value = composition_data[i]
            sheet.write(row + i, 0, label, formats['text'])
            if isinstance(value, float):
                safe_write_numeric(sheet, row + i, 1, value, formats['number'])
            else:
                sheet.write(row + i, 1, value, formats['integer'])
        
        # Strategy parameters
        if i < len(strategy_params):
            param, value = strategy_params[i]
            sheet.write(row + i, 8, param, formats['text'])
            if isinstance(value, float) and '%' in param:
                safe_write_numeric(sheet, row + i, 9, value / 100, formats['percentage'])
            elif isinstance(value, (int, float)):
                safe_write_numeric(sheet, row + i, 9, value, formats['number'])
            else:
                sheet.write(row + i, 9, value, formats['text'])
    
    # Calculate the end row of the composition/strategy section
    composition_end_row = row + max_rows
    
    # === SECTION 3: RISK ANALYSIS ===
    # Position RISK ANALYSIS 2 rows below the end of PORTFOLIO COMPOSITION and STRATEGY PARAMETERS
    risk_row = composition_end_row + 2
    sheet.merge_range(f'A{risk_row}:P{risk_row}', 'RISK ANALYSIS & BENCHMARK COMPARISON', formats['section_title'])
    risk_row += 1
    
    # Risk metrics with benchmarks
    risk_headers = ['Risk Metric', 'Current Value', 'Benchmark', 'Status', 'Risk Level']
    for i, header in enumerate(risk_headers):
        sheet.write(risk_row, i, header, formats['table_header'])
    risk_row += 1
    
    risk_metrics = [
        ('Sharpe Ratio', portfolio_metrics.get('portfolio_sharpe', 0), 1.0, 'Higher is better'),
        ('Sortino Ratio', portfolio_metrics.get('sortino_ratio', 0), 1.5, 'Higher is better'),
        ('Maximum Drawdown (%)', portfolio_metrics.get('portfolio_max_drawdown', 0), 0.15, 'Lower is better'),
        ('Value at Risk 95% (%)', portfolio_metrics.get('var_95', 0), 0.05, 'Lower is better'),
        ('Calmar Ratio', portfolio_metrics.get('calmar_ratio', 0), 0.5, 'Higher is better'),
        ('Volatility (%)', portfolio_metrics.get('volatility', 0), 0.20, 'Lower is better'),
        ('Beta (if available)', portfolio_metrics.get('beta', 0), 1.0, 'Lower is better'),
        ('Information Ratio', portfolio_metrics.get('information_ratio', 0), 0.5, 'Higher is better'),
    ]
    
    for metric_name, value, benchmark, direction in risk_metrics:
        sheet.write(risk_row, 0, metric_name, formats['text'])
        
        # Current value
        if '%' in metric_name:
            safe_write_numeric(sheet, risk_row, 1, value, formats['percentage'])
            safe_write_numeric(sheet, risk_row, 2, benchmark, formats['percentage'])
        else:
            safe_write_numeric(sheet, risk_row, 1, value, formats['number'])
            safe_write_numeric(sheet, risk_row, 2, benchmark, formats['number'])
        
        # Status and risk level
        if 'Higher is better' in direction:
            if value >= benchmark * 1.2:
                status = 'EXCELLENT'
                risk_level = 'LOW'
                status_fmt = formats['alert_low']
            elif value >= benchmark:
                status = 'GOOD'
                risk_level = 'LOW'
                status_fmt = formats['alert_low']
            elif value >= benchmark * 0.8:
                status = 'ACCEPTABLE'
                risk_level = 'MEDIUM'
                status_fmt = formats['alert_medium']
            else:
                status = 'POOR'
                risk_level = 'HIGH'
                status_fmt = formats['alert_high']
        else:
            if value <= benchmark * 0.8:
                status = 'EXCELLENT'
                risk_level = 'LOW'
                status_fmt = formats['alert_low']
            elif value <= benchmark:
                status = 'GOOD'
                risk_level = 'LOW'
                status_fmt = formats['alert_low']
            elif value <= benchmark * 1.2:
                status = 'ACCEPTABLE'
                risk_level = 'MEDIUM'
                status_fmt = formats['alert_medium']
            else:
                status = 'POOR'
                risk_level = 'HIGH'
                status_fmt = formats['alert_high']
        
        sheet.write(risk_row, 3, status, status_fmt)
        sheet.write(risk_row, 4, risk_level, status_fmt)
        risk_row += 1
    
    # === SECTION 4: TOP & BOTTOM PERFORMERS ===
    # Position after risk analysis section
    performers_row = risk_row + 2
    sheet.merge_range(f'A{performers_row}:H{performers_row}', 'TOP 10 PERFORMING PAIRS', formats['section_title'])
    sheet.merge_range(f'I{performers_row}:P{performers_row}', 'BOTTOM 10 PERFORMING PAIRS', formats['section_title'])
    performers_row += 1
    
    # Headers for both tables
    perf_headers = ['Rank', 'Pair', 'Return %', 'Sharpe', 'Trades', 'Win Rate']
    for i, header in enumerate(perf_headers):
        sheet.write(performers_row, i, header, formats['table_header'])
        sheet.write(performers_row, i + 8, header, formats['table_header'])
    performers_row += 1
    
    # Sort pairs by total return
    pair_results = sorted(
        backtest_results.get('pair_results', []),
        key=lambda x: x['metrics'].get('total_return', 0),
        reverse=True
    )
    
    # Top 10 performers
    for i, result in enumerate(pair_results[:10]):
        metrics = result['metrics']
        sheet.write(performers_row + i, 0, i + 1, formats['integer'])
        sheet.write(performers_row + i, 1, metrics.get('pair', ''), formats['text'])
        
        ret = metrics.get('total_return', 0)
        ret_fmt = formats['positive'] if ret > 0 else formats['negative']
        safe_write_numeric(sheet, performers_row + i, 2, ret, ret_fmt)
        safe_write_numeric(sheet, performers_row + i, 3, metrics.get('sharpe_ratio', 0), formats['number'])
        safe_write_numeric(sheet, performers_row + i, 4, metrics.get('total_trades', 0), formats['integer'])
        safe_write_numeric(sheet, performers_row + i, 5, metrics.get('win_rate', 0), formats['percentage'])
    
    # Bottom 10 performers
    bottom_pairs = pair_results[-10:] if len(pair_results) > 10 else []
    bottom_pairs.reverse()
    
    for i, result in enumerate(bottom_pairs):
        metrics = result['metrics']
        sheet.write(performers_row + i, 8, len(pair_results) - len(bottom_pairs) + i + 1, formats['integer'])
        sheet.write(performers_row + i, 9, metrics.get('pair', ''), formats['text'])
        
        ret = metrics.get('total_return', 0)
        ret_fmt = formats['positive'] if ret > 0 else formats['negative']
        safe_write_numeric(sheet, performers_row + i, 10, ret, ret_fmt)
        safe_write_numeric(sheet, performers_row + i, 11, metrics.get('sharpe_ratio', 0), formats['number'])
        safe_write_numeric(sheet, performers_row + i, 12, metrics.get('total_trades', 0), formats['integer'])
        safe_write_numeric(sheet, performers_row + i, 13, metrics.get('win_rate', 0), formats['percentage'])
    
    # === SECTION 5: STATISTICAL TESTS & VALIDATION ===
    # Position after performers section
    stats_row = performers_row + 12  # Assuming max 10 performers + 2 spacing rows
    sheet.merge_range(f'A{stats_row}:P{stats_row}', 'STATISTICAL TESTS & VALIDATION', formats['section_title'])
    stats_row += 1
    
    # Statistical tests configuration
    stat_tests = [
        ('ADF Test', 'Enabled' if config.enable_adf else 'Disabled', config.max_adf_pval if config.enable_adf else 'N/A'),
        ('Johansen Test', 'Enabled' if config.enable_johansen else 'Disabled', config.johansen_crit_level if config.enable_johansen else 'N/A'),
        ('Correlation Test', 'Enabled' if config.enable_correlation else 'Disabled', config.min_corr if config.enable_correlation else 'N/A'),
        ('Volatility Ratio Test', 'Enabled' if config.enable_vol_ratio else 'Disabled', config.vol_ratio_max if config.enable_vol_ratio else 'N/A'),
    ]
    
    # Headers
    sheet.write(stats_row, 0, 'Test Type', formats['table_header'])
    sheet.write(stats_row, 1, 'Status', formats['table_header'])
    sheet.write(stats_row, 2, 'Threshold/Parameter', formats['table_header'])
    sheet.write(stats_row, 4, 'Risk Management', formats['table_header'])
    sheet.write(stats_row, 5, 'Value', formats['table_header'])
    stats_row += 1
    
    # Statistical tests
    for i, (test_name, status, threshold) in enumerate(stat_tests):
        sheet.write(stats_row + i, 0, test_name, formats['text'])
        status_fmt = formats['alert_low'] if 'Enabled' in status else formats['neutral']
        sheet.write(stats_row + i, 1, status, status_fmt)
        sheet.write(stats_row + i, 2, str(threshold), formats['text'])
    
    # Risk management parameters
    risk_mgmt = [
        ('Cooldown Bars', config.cooldown_bars),
        ('Max Monetary Exposure', config.max_monetary_exposure),
        ('Commission Fixed', config.commission_fixed),
        ('Slippage Points', config.slippage_points),
        ('Take Profit (%)', config.take_profit_perc),
        ('Stop Loss (%)', config.stop_loss_perc),
        ('Trailing Stop (%)', config.trailing_stop_perc),
        ('Max Position Size', config.max_position_size),
        ('Max Open Positions', config.max_open_positions),
        ('Monetary Value Tolerance', config.monetary_value_tolerance),
        ('Max Commission (%)', config.max_commission_perc),
        ('Max Pair Drawdown (%)', config.max_pair_drawdown_perc),
        ('Max Portfolio Drawdown (%)', config.max_portfolio_drawdown_perc),
        ('Initial Portfolio Value', config.initial_portfolio_value),
    ]
    
    for i, (param, value) in enumerate(risk_mgmt):
        sheet.write(stats_row + i, 4, param, formats['text'])
        safe_write_numeric(sheet, stats_row + i, 5, value, formats['number'])
    
    # Add conditional formatting for the entire dashboard
    _add_dashboard_conditional_formatting(sheet, formats)


def _add_dashboard_conditional_formatting(sheet, formats):
    """Add conditional formatting to the dashboard for better visual appeal"""
    # Add data bars to KPI values (rows 6-7 and 9-10)
    sheet.conditional_format('B6:N7', {
        'type': 'data_bar',
        'bar_color': '#4472C4',
        'bar_solid': True,
        'bar_border_color': '#1f4e79',
        'bar_direction': 'left_to_right'
    })
    
    sheet.conditional_format('B9:N10', {
        'type': 'data_bar',
        'bar_color': '#70AD47',
        'bar_solid': True,
        'bar_border_color': '#548235',
        'bar_direction': 'left_to_right'
    })
    
    # Add color scales to performance tables (rows 34-43)
    sheet.conditional_format('C34:C43', {
        'type': '3_color_scale',
        'min_color': '#FF6B6B',
        'mid_color': '#FFFFFF',
        'max_color': '#4ECDC4'
    })
    
    sheet.conditional_format('K34:K43', {
        'type': '3_color_scale',
        'min_color': '#FF6B6B',
        'mid_color': '#FFFFFF',
        'max_color': '#4ECDC4'
    })
    
    # Add icon sets for risk levels
    sheet.conditional_format('E24:E31', {
        'type': 'icon_set',
        'icon_style': '3_traffic_lights',
        'icons': [
            {'criteria': '>=', 'type': 'percent', 'value': 67},
            {'criteria': '>=', 'type': 'percent', 'value': 33},
            {'criteria': '>=', 'type': 'percent', 'value': 0}
        ]
    })


def _generate_pairs_sheet(workbook, backtest_results, formats, safe_write_numeric):
    """Generate enhanced all pairs results sheet"""
    if not backtest_results.get('pair_results'):
        return
    
    pairs_sheet = workbook.add_worksheet('All Pairs Analysis')
    pairs_sheet.set_column('A:A', 8)   # Rank
    pairs_sheet.set_column('B:B', 16)  # Pair
    pairs_sheet.set_column('C:L', 12)  # Metrics
    pairs_sheet.set_column('M:M', 15)  # Score
    
    # Title
    pairs_sheet.merge_range('A1:M1', 'COMPREHENSIVE PAIRS ANALYSIS', formats['main_title'])
    
    # Headers with enhanced styling
    headers = [
        'Rank', 'Pair', 'Trades', 'Win Rate', 'Total Return (%)', 'Annualized Return (%)',
        'Sharpe Ratio', 'Max DD (%)', 'Profit Factor', 'Avg Trade (%)', 
        'Avg Duration', 'Composite Score'
    ]
    
    for col, header in enumerate(headers):
        pairs_sheet.write(2, col, header, formats['table_header'])
    
    # Sort pairs by composite score
    pair_results = sorted(
        backtest_results.get('pair_results', []),
        key=lambda x: x['metrics'].get('composite_score', 0),
        reverse=True
    )
    
    # Add data with alternating row colors and conditional formatting
    for row_idx, result in enumerate(pair_results, 3):
        metrics = result['metrics']
        
        # Determine row format
        row_fmt = formats['row_even'] if row_idx % 2 == 0 else formats['row_odd']
        
        # Basic data
        pairs_sheet.write(row_idx, 0, metrics.get('rank', row_idx - 2), formats['integer'])
        pairs_sheet.write(row_idx, 1, metrics.get('pair', ''), formats['text'])
        pairs_sheet.write(row_idx, 2, metrics.get('total_trades', 0), formats['integer'])
        
        # Win rate with color coding
        win_rate = metrics.get('win_rate', 0)
        win_fmt = formats['positive'] if win_rate > 0.5 else formats['negative']
        safe_write_numeric(pairs_sheet, row_idx, 3, win_rate, formats['percentage'])
        
        # Returns with color coding
        total_ret = metrics.get('total_return', 0)
        ret_fmt = formats['positive'] if total_ret > 0 else formats['negative']
        safe_write_numeric(pairs_sheet, row_idx, 4, total_ret/100, formats['percentage'])
        
        ann_ret = metrics.get('annualized_return', 0)
        ann_fmt = formats['positive'] if ann_ret > 0 else formats['negative']
        safe_write_numeric(pairs_sheet, row_idx, 5, ann_ret, ann_fmt)
        
        # Risk metrics
        safe_write_numeric(pairs_sheet, row_idx, 6, metrics.get('sharpe_ratio', 0), formats['number'])
        safe_write_numeric(pairs_sheet, row_idx, 7, metrics.get('max_drawdown', 0), formats['percentage'])
        safe_write_numeric(pairs_sheet, row_idx, 8, metrics.get('profit_factor', 0), formats['number'])
        safe_write_numeric(pairs_sheet, row_idx, 9, metrics.get('avg_trade_pnl', 0)/100, formats['percentage'])
        safe_write_numeric(pairs_sheet, row_idx, 10, metrics.get('avg_bars_held', 0), formats['number'])
        safe_write_numeric(pairs_sheet, row_idx, 11, metrics.get('composite_score', 0), formats['number'])
    
    # Add conditional formatting for key columns
    last_row = len(pair_results) + 2
    
    # Win rate conditional formatting
    pairs_sheet.conditional_format(3, 3, last_row, 3, {
        'type': '3_color_scale',
        'min_color': '#FF6B6B',
        'mid_color': '#FFE066',
        'max_color': '#4ECDC4'
    })
    
    # Total return conditional formatting
    pairs_sheet.conditional_format(3, 4, last_row, 4, {
        'type': '3_color_scale',
        'min_color': '#FF6B6B',
        'mid_color': '#FFFFFF',
        'max_color': '#4ECDC4'
    })
    
    # Add filters
    pairs_sheet.autofilter(2, 0, last_row, len(headers) - 1)


def _generate_performance_dashboard(workbook, backtest_results, formats, safe_write_numeric):
    """Generate enhanced performance dashboard with charts"""
    dashboard = workbook.add_worksheet('Performance Dashboard')
    dashboard.set_column('A:P', 12)
    
    # Title
    dashboard.merge_range('A1:P1', 'PERFORMANCE DASHBOARD & ANALYTICS', formats['main_title'])
    
    # Portfolio equity curve data
    if 'portfolio_equity' in backtest_results:
        _create_equity_curve_chart(dashboard, backtest_results, workbook, formats)
    
    # Drawdown analysis
    _create_drawdown_chart(dashboard, backtest_results, workbook, formats)
    
    # Performance distribution
    _create_performance_distribution(dashboard, backtest_results, formats, safe_write_numeric)
    
    # Monthly returns heatmap
    _create_monthly_returns_heatmap(dashboard, backtest_results, formats, safe_write_numeric)


def _generate_individual_pair_sheets(workbook, backtest_results, formats, safe_write_numeric, tempdir=None):
    """Generate enhanced individual pair sheets with comprehensive analysis and plots"""
    pair_results = backtest_results.get('pair_results', [])
    config = get_config()
    
    if not pair_results:
        logger.warning("No pair results found to generate individual sheets")
        return
    
    logger.debug(f"Generating individual sheets for {len(pair_results)} pairs")
    
    # Use provided tempdir or log that plots will not be generated
    if not MATPLOTLIB_AVAILABLE:
        logger.warning("Matplotlib not available, plots will not be generated")
    elif not tempdir:
        logger.warning("No temporary directory provided, plots will not be generated")
    
    for i, result in enumerate(pair_results):
        pair_name = result.get('pair', f'Unknown_{i}')
        safe_pair_name = pair_name.replace('/', '_').replace('-', '_')[:28]
        
        logger.debug(f"Processing pair {i+1}/{len(pair_results)}: {pair_name}")
        
        # Create worksheet for this pair
        try:
            sheet = workbook.add_worksheet(safe_pair_name)
        except Exception as e:
            logger.error(f"Error creating worksheet for {pair_name}: {e}")
            continue
        
        # Set column widths for better layout
        sheet.set_column('A:E', 15)
        sheet.set_column('F:Z', 12)
        
        # Add pair header
        sheet.merge_range('A1:Z1', f'PAIR ANALYSIS: {pair_name}', formats['main_title'])
        sheet.set_row(0, 25)
        
        # Start with performance summary table at the top
        summary_start_row = 3
        
        # Add performance summary table
        try:
            _add_pair_performance_summary(sheet, result, formats, safe_write_numeric, summary_start_row)
            logger.debug(f"Added performance summary for {pair_name}")
        except Exception as e:
            logger.error(f"Error adding performance summary for {pair_name}: {e}")
        
        # Add equity curve data and basic Excel chart
        equity_start_row = summary_start_row + 16
        try:
            _add_enhanced_equity_curve_data(sheet, result, workbook, formats, safe_write_numeric, equity_start_row)
            logger.debug(f"Added equity curve for {pair_name}")
        except Exception as e:
            logger.error(f"Error adding equity curve for {pair_name}: {e}")
        
        # Add detailed trade history and get the end position
        trade_start_row = equity_start_row + 25
        try:
            trade_end_row = _add_enhanced_trade_details(sheet, result, formats, safe_write_numeric, trade_start_row)
            logger.debug(f"Added trade details for {pair_name}")
        except Exception as e:
            logger.error(f"Error adding trade details for {pair_name}: {e}")
            trade_end_row = trade_start_row + 50  # Fallback position
        
        # Now add matplotlib plots below the trade analysis summary
        # Position plots after the summary table with proper spacing
        plot_row = trade_end_row + 5  # Add spacing after the summary table
        plot_inserted = False
        
        if MATPLOTLIB_AVAILABLE and tempdir:
            logger.debug(f"Attempting to create comprehensive plots for {pair_name}")
            plot_path = _create_comprehensive_pair_plots(result, config, tempdir, safe_pair_name)
            
            if plot_path and os.path.exists(plot_path):
                try:
                    # Insert the comprehensive plot image below the trade analysis summary
                    # Position at column F (index 5) to align with trade history table
                    sheet.insert_image(f'F{plot_row}', plot_path, {
                        'x_scale': 0.8, 
                        'y_scale': 0.8,
                        'x_offset': 10,
                        'y_offset': 10
                    })
                    plot_inserted = True
                    logger.debug(f"Successfully inserted plot for {pair_name}")
                except Exception as e:
                    logger.error(f"Error inserting plot image for {pair_name}: {e}")
            else:
                logger.warning(f"Plot was not created for {pair_name}")
        
        if not plot_inserted:
            # Add a note that plots are not available at column F to align with trade history
            sheet.write(plot_row, 5, 'Note: Advanced plots not available (matplotlib required)', 
                       formats.get('text', None))
        
    logger.debug("Completed generating individual pair sheets")


def _create_comprehensive_pair_plots(result, config, tempdir, safe_pair_name):
    """Create comprehensive matplotlib plots for a pair similar to cTrader implementation"""
    if not MATPLOTLIB_AVAILABLE:
        logger.warning("Matplotlib not available, skipping plot creation")
        return None
    
    try:
        # Extract data from result
        pair_name = result.get('pair', 'Unknown')
        
        logger.debug(f"Creating comprehensive plots for {pair_name}")
        logger.debug(f"Available result keys: {list(result.keys())}")
        
        # Get the main dataframe from result['data'] (aligned price data)
        df = result.get('data', pd.DataFrame())
        if df.empty:
            logger.warning(f"No aligned data available for {pair_name}")
            return None
        
        # Ensure we have the expected columns
        if 'price1' not in df.columns or 'price2' not in df.columns:
            logger.warning(f"Expected columns 'price1' and 'price2' not found in data for {pair_name}. Available columns: {list(df.columns)}")
            return None
        
        logger.debug(f"Dataframe loaded with shape: {df.shape}")
        
        # Extract symbols from pair name
        if '-' in pair_name:
            symbol1, symbol2 = pair_name.split('-', 1)
        elif '/' in pair_name:
            symbol1, symbol2 = pair_name.split('/', 1)
        else:
            logger.warning(f"Cannot parse pair name: {pair_name}")
            return None
        
        # Get price series from the aligned dataframe
        price1 = df['price1']
        price2 = df['price2']
        
        if price1.empty or price2.empty:
            logger.warning(f"Empty price series for {pair_name}: price1={len(price1)}, price2={len(price2)}")
            return None
        
        # Get indicators data (contains ratio, zscore, etc.)
        indicators = result.get('indicators', {})
        
        # Extract indicators or calculate if not available
        ratio = indicators.get('ratio', pd.Series())
        if ratio.empty and not price1.empty and not price2.empty:
            # Calculate ratio
            ratio = price1 / price2
            logger.debug(f"Calculated price ratio for {pair_name}")
        
        ratio_ma = indicators.get('ratio_ma', pd.Series())
        if ratio_ma.empty and not ratio.empty:
            # Calculate moving average of ratio
            window = getattr(config, 'z_period', 20)
            ratio_ma = ratio.rolling(window=window, min_periods=1).mean()
            logger.debug(f"Calculated ratio moving average for {pair_name}")
        
        zscore = indicators.get('zscore', pd.Series())
        if zscore.empty and not ratio.empty and not ratio_ma.empty:
            # Calculate z-score
            window = getattr(config, 'z_period', 20)
            ratio_std = ratio.rolling(window=window, min_periods=1).std()
            zscore = (ratio - ratio_ma) / ratio_std
            logger.debug(f"Calculated z-score for {pair_name}")
        
        # Get equity curve from result
        equity_curve = result.get('equity_curve', pd.Series())
        if isinstance(equity_curve, pd.Series):
            equity = equity_curve.tolist()
        else:
            equity = equity_curve if equity_curve else []
        
        if not equity:
            # Try to construct from trades if equity curve not available
            trades = result.get('trades', [])
            if trades:
                equity = [100]  # Start with 100
                for trade in trades:
                    net_pnl = trade.get('net_pnl_pct', 0) / 100
                    equity.append(equity[-1] * (1 + net_pnl))
                logger.debug(f"Constructed equity curve from {len(trades)} trades")
        
        # Statistical data (from indicators)
        adf_pvals = indicators.get('adf_pvals', np.full(len(df), np.nan))
        johansen_stats = indicators.get('johansen_stats', np.full(len(df), np.nan))
        johansen_crits = indicators.get('johansen_crits', np.full(len(df), np.nan))
        
        logger.debug(f"Data extracted - df shape: {df.shape}, price1 len: {len(price1)}, price2 len: {len(price2)}")
        logger.debug(f"Ratio len: {len(ratio)}, Z-score len: {len(zscore)}, Equity len: {len(equity)}")
        
        # Create the comprehensive plot
        fig, axs = plt.subplots(5, 1, figsize=(12, 20), constrained_layout=True)
        
        # Prepare time axis
        time_index = df.index
        x_vals = np.arange(len(time_index))
        x_labels = pd.to_datetime(time_index).strftime('%Y-%m-%d %H:%M')
        step = max(1, len(x_vals) // 8)
        
        # 1. Price chart with dual y-axes
        ax_price1 = axs[0]
        ax_price2 = ax_price1.twinx()
        
        if not price1.empty:
            ax_price1.plot(x_vals, price1, label=symbol1, color='tab:blue', linewidth=1.5)
        if not price2.empty:
            ax_price2.plot(x_vals, price2, label=symbol2, color='tab:orange', linewidth=1.5)
        
        ax_price1.set_ylabel(f"{symbol1} Price", color='tab:blue', fontweight='bold')
        ax_price2.set_ylabel(f"{symbol2} Price", color='tab:orange', fontweight='bold')
        ax_price1.set_title(f"{pair_name}: {symbol1} (left) and {symbol2} (right) Price", 
                           fontsize=14, fontweight='bold')
        ax_price1.set_xticks(x_vals[::step])
        ax_price1.set_xticklabels(x_labels[::step], rotation=45, ha='right')
        ax_price1.grid(True, alpha=0.3)
        
        # Combined legend
        lines1, labels1 = ax_price1.get_legend_handles_labels()
        lines2, labels2 = ax_price2.get_legend_handles_labels()
        ax_price1.legend(lines1 + lines2, labels1 + labels2, loc='upper left')
        
        # 2. Ratio chart
        if not ratio.empty:
            axs[1].plot(x_vals, ratio, label="Price Ratio", color='navy', linewidth=1.5)
        if not ratio_ma.empty:
            axs[1].plot(x_vals, ratio_ma, label="Ratio Moving Average", 
                       linestyle="--", color='red', linewidth=1.5)
        
        axs[1].set_ylabel("Price Ratio", fontweight='bold')
        axs[1].set_title(f"{pair_name}: Price Ratio ({symbol1}/{symbol2})", 
                        fontsize=14, fontweight='bold')
        axs[1].set_xticks(x_vals[::step])
        axs[1].set_xticklabels(x_labels[::step], rotation=45, ha='right')
        axs[1].grid(True, alpha=0.3)
        axs[1].legend()
        
        # 3. Z-score chart with entry/exit thresholds
        if not zscore.empty:
            axs[2].plot(x_vals, zscore, label="Z-score", color='purple', linewidth=1.5)
        
        # Add threshold lines
        z_entry = getattr(config, 'z_entry', 2.0)
        z_exit = getattr(config, 'z_exit', 0.5)
        
        axs[2].axhline(z_entry, color='green', linestyle='--', 
                      label=f"Entry Threshold (+{z_entry})", linewidth=1.5)
        axs[2].axhline(-z_entry, color='green', linestyle='--', 
                      label=f"Entry Threshold (-{z_entry})", linewidth=1.5)
        axs[2].axhline(z_exit, color='red', linestyle='--', 
                      label=f"Exit Threshold (+{z_exit})", linewidth=1.5)
        axs[2].axhline(-z_exit, color='red', linestyle='--', 
                      label=f"Exit Threshold (-{z_exit})", linewidth=1.5)
        axs[2].axhline(0, color='black', linestyle='-', alpha=0.5, linewidth=1)
        
        axs[2].set_ylabel("Z-score", fontweight='bold')
        axs[2].set_title(f"{pair_name}: Z-score with Entry/Exit Thresholds", 
                        fontsize=14, fontweight='bold')
        axs[2].set_xticks(x_vals[::step])
        axs[2].set_xticklabels(x_labels[::step], rotation=45, ha='right')
        axs[2].grid(True, alpha=0.3)
        axs[2].legend()
        
        # 4. ADF and Johansen statistics (if enabled in config)
        adf_enabled = getattr(config, 'enable_adf', False)
        johansen_enabled = getattr(config, 'enable_johansen', False)
        
        # Only check for data if tests are enabled in config
        has_adf = adf_enabled and not np.all(np.isnan(adf_pvals))
        has_johansen = johansen_enabled and not np.all(np.isnan(johansen_stats))
        
        if has_adf or has_johansen:
            if has_adf:
                max_adf = getattr(config, 'max_adf_pval', 0.05)
                axs[3].plot(x_vals, adf_pvals, label="ADF p-value", color='tab:blue', linewidth=1.5)
                axs[3].axhline(max_adf, color='tab:blue', linestyle='--', 
                              label=f"ADF Threshold ({max_adf})", linewidth=1.5)
                axs[3].set_ylabel("ADF p-value", color='tab:blue', fontweight='bold')
            
            if has_johansen:
                ax2 = axs[3].twinx()
                ax2.plot(x_vals, johansen_stats, label="Johansen Stat", 
                        color='tab:orange', linewidth=1.5)
                ax2.plot(x_vals, johansen_crits, label="Johansen Critical Value", 
                        color='tab:red', linestyle='--', linewidth=1.5)
                ax2.set_ylabel("Johansen Statistic", color='tab:orange', fontweight='bold')
                
                # Combined legend
                if has_adf:
                    lines1, labels1 = axs[3].get_legend_handles_labels()
                    lines2, labels2 = ax2.get_legend_handles_labels()
                    axs[3].legend(lines1 + lines2, labels1 + labels2, loc='upper right')
                else:
                    ax2.legend(loc='upper right')
            
            axs[3].set_title(f"{pair_name}: Cointegration Statistics", 
                            fontsize=14, fontweight='bold')
            axs[3].set_xticks(x_vals[::step])
            axs[3].set_xticklabels(x_labels[::step], rotation=45, ha='right')
            axs[3].grid(True, alpha=0.3)
            
            if has_adf and not has_johansen:
                axs[3].legend()
        else:
            # Hide the statistics plot if no data
            axs[3].text(0.5, 0.5, 'Cointegration Statistics Not Available', 
                       ha='center', va='center', transform=axs[3].transAxes,
                       fontsize=14, style='italic')
            axs[3].set_title(f"{pair_name}: Cointegration Statistics", 
                            fontsize=14, fontweight='bold')
        
        # 5. Equity curve
        if equity and len(equity) > 0:
            equity_x = np.arange(len(equity))
            axs[4].plot(equity_x, equity, label="Equity Curve", 
                       color='darkgreen', linewidth=2)
            axs[4].set_ylabel("Equity", fontweight='bold')
            axs[4].set_title(f"{pair_name}: Equity Curve", 
                            fontsize=14, fontweight='bold')
            
            # Format x-axis for equity curve
            if len(time_index) == len(equity):
                axs[4].set_xticks(equity_x[::step])
                axs[4].set_xticklabels(x_labels[::step], rotation=45, ha='right')
            else:
                axs[4].set_xlabel("Trade Number")
            
            axs[4].grid(True, alpha=0.3)
            axs[4].legend()
        else:
            axs[4].text(0.5, 0.5, 'Equity Curve Not Available', 
                       ha='center', va='center', transform=axs[4].transAxes,
                       fontsize=14, style='italic')
            axs[4].set_title(f"{pair_name}: Equity Curve", 
                            fontsize=14, fontweight='bold')
        
        # Save the plot
        plot_path = os.path.join(tempdir, f"{safe_pair_name}_comprehensive_plot.png")
        plt.savefig(plot_path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        
        logger.debug(f"Plot saved successfully to: {plot_path}")
        
        # Verify file was created
        if os.path.exists(plot_path):
            file_size = os.path.getsize(plot_path)
            logger.debug(f"Plot file size: {file_size} bytes")
            return plot_path
        else:
            logger.error(f"Plot file was not created at: {plot_path}")
            return None
        
    except Exception as e:
        logger.error(f"Error creating comprehensive plots for {pair_name}: {e}")
        logger.error(traceback.format_exc())
        if 'fig' in locals():
            plt.close(fig)
        return None


def _add_pair_performance_summary(sheet, result, formats, safe_write_numeric, start_row):
    """Add comprehensive performance summary for the pair with trading analysis on the right"""
    sheet.write(start_row, 0, 'PERFORMANCE SUMMARY', formats['section_title'])
    sheet.merge_range(f'A{start_row+1}:F{start_row+1}', 'PERFORMANCE SUMMARY', formats['section_title'])
    
    # Add PAIRS TRADING ANALYSIS SUMMARY title on the right side
    sheet.write(start_row, 7, 'PAIRS TRADING ANALYSIS SUMMARY', formats['section_title'])
    sheet.merge_range(f'H{start_row+1}:K{start_row+1}', 'PAIRS TRADING ANALYSIS SUMMARY', formats['section_title'])
    
    # Get metrics and trades
    metrics = result.get('metrics', {})
    pair_name = result.get('pair', 'Unknown')
    trades = result.get('trades', [])
    
    # Performance data
    summary_data = [
        ('Pair', pair_name),
        ('Total Return (%)', metrics.get('total_return', 0)),
        ('Annualized Return (%)', metrics.get('annualized_return', 0)),
        ('Sharpe Ratio', metrics.get('sharpe_ratio', 0)),
        ('Sortino Ratio', metrics.get('sortino_ratio', 0)),
        ('Max Drawdown (%)', metrics.get('max_drawdown', 0)),
        ('Volatility (%)', metrics.get('volatility', 0)),
        ('Total Trades', metrics.get('total_trades', 0)),
        ('Win Rate (%)', metrics.get('win_rate', 0)),
        ('Profit Factor', metrics.get('profit_factor', 0)),
        ('Average Trade (%)', metrics.get('avg_trade_pnl', 0)),
        ('Average Winner (%)', metrics.get('avg_winner', 0)),
        ('Average Loser (%)', metrics.get('avg_loser', 0)),
        ('Largest Winner (%)', metrics.get('largest_winner', 0)),
        ('Largest Loser (%)', metrics.get('largest_loser', 0)),
    ]
    
    # Create two-column layout
    col1_data = summary_data[:8]
    col2_data = summary_data[8:]
    
    # Headers
    sheet.write(start_row + 2, 0, 'Metric', formats['table_header'])
    sheet.write(start_row + 2, 1, 'Value', formats['table_header'])
    sheet.write(start_row + 2, 3, 'Metric', formats['table_header'])
    sheet.write(start_row + 2, 4, 'Value', formats['table_header'])
    
    # Headers for trading analysis on the right
    sheet.write(start_row + 2, 7, 'Trading Metric', formats['table_header'])
    sheet.write(start_row + 2, 8, 'Value', formats['table_header'])
    
    # Calculate trading analysis statistics
    trading_stats = []
    if trades:
        winning_trades = [t for t in trades if t.get('net_pnl_pct', 0) > 0]
        losing_trades = [t for t in trades if t.get('net_pnl_pct', 0) < 0]
        
        avg_winner = np.mean([t.get('net_pnl_pct', 0) for t in winning_trades]) if winning_trades else 0
        avg_loser = np.mean([t.get('net_pnl_pct', 0) for t in losing_trades]) if losing_trades else 0
        
        # Calculate leg analysis
        leg1_wins = len([t for t in trades if t.get('pnl_leg1', 0) > 0])
        leg2_wins = len([t for t in trades if t.get('pnl_leg2', 0) > 0])
        
        trading_stats = [
            ('Total Trades', len(trades)),
            ('Winning Trades', len(winning_trades)),
            ('Losing Trades', len(losing_trades)),
            ('Win Rate (%)', len(winning_trades) / len(trades) * 100 if trades else 0),
            ('Average Winner (%)', avg_winner),
            ('Average Loser (%)', avg_loser),
            ('Leg1 Win Rate (%)', leg1_wins / len(trades) * 100 if trades else 0),
            ('Leg2 Win Rate (%)', leg2_wins / len(trades) * 100 if trades else 0),
        ]
    
    # Fill data
    max_rows = max(len(col1_data), len(col2_data), len(trading_stats))
    for i in range(max_rows):
        row = start_row + 3 + i
        
        # Performance summary columns (left side)
        if i < len(col1_data):
            metric1, value1 = col1_data[i]
            sheet.write(row, 0, metric1, formats['text'])
            if isinstance(value1, (int, float)) and '%' in metric1:
                # Use percentage format and divide by 100 for proper display
                fmt = formats['positive'] if value1 > 0 else formats['negative'] if value1 < 0 else formats['percentage']
                safe_write_numeric(sheet, row, 1, value1/100, formats['percentage'])
            elif isinstance(value1, (int, float)):
                safe_write_numeric(sheet, row, 1, value1, formats['number'])
            else:
                sheet.write(row, 1, str(value1), formats['text'])
        
        if i < len(col2_data):
            metric2, value2 = col2_data[i]
            sheet.write(row, 3, metric2, formats['text'])
            if isinstance(value2, (int, float)) and '%' in metric2:
                # Use percentage format and divide by 100 for proper display
                fmt = formats['positive'] if value2 > 0 else formats['negative'] if value2 < 0 else formats['percentage']
                safe_write_numeric(sheet, row, 4, value2/100, formats['percentage'])
            elif isinstance(value2, (int, float)):
                safe_write_numeric(sheet, row, 4, value2, formats['number'])
            else:
                sheet.write(row, 4, str(value2), formats['text'])
        
        # Trading analysis column (right side)
        if i < len(trading_stats):
            metric, value = trading_stats[i]
            sheet.write(row, 7, metric, formats['text'])
            
            if '%' in metric:
                # Always use percentage format for percentage metrics
                safe_write_numeric(sheet, row, 8, value/100, formats['percentage'])
            else:
                safe_write_numeric(sheet, row, 8, value, formats['integer'])


def _add_enhanced_equity_curve_data(sheet, result, workbook, formats, safe_write_numeric, start_row):
    """Add enhanced equity curve data and Excel chart"""
    trades = result.get('trades', [])
    if not trades:
        return
    
    # Sort trades by entry time
    trades = sorted(trades, key=lambda x: x.get('entry_time', ''))
    
    # Calculate enhanced equity curve
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
        
        equity_data.append({
            'date': exit_time,
            'equity': cumulative_pnl,
            'trade_pnl': net_pnl_pct
        })
        
        cumulative_pnl += net_pnl_pct
        equity_data.append({
            'date': exit_time,
            'equity': cumulative_pnl,
            'trade_pnl': net_pnl_pct
        })
    
    if len(equity_data) < 2:
        return
    
    # Write equity curve data (left side, columns A-D)
    sheet.write(start_row, 0, 'EQUITY CURVE DATA', formats['section_title'])
    sheet.merge_range(f'A{start_row+1}:D{start_row+1}', 'EQUITY CURVE DATA', formats['section_title'])
    
    headers = ['Date', 'Cumulative Return (%)', 'Trade PnL (%)', 'Running Max']
    for i, header in enumerate(headers):
        sheet.write(start_row + 2, i, header, formats['table_header'])
    
    # Calculate running maximum for drawdown
    running_max = 0
    for i, point in enumerate(equity_data):
        row = start_row + 3 + i
        running_max = max(running_max, point['equity'])
        
        sheet.write(row, 0, point['date'], formats['date'])
        sheet.write(row, 1, point['equity']/100, formats['percentage'])
        sheet.write(row, 2, point['trade_pnl']/100, formats['percentage'])
        sheet.write(row, 3, running_max/100, formats['percentage'])
    
    # Create enhanced equity curve chart (positioned to the right of equity data)
    equity_chart = workbook.add_chart({'type': 'line'})
    
    data_end_row = start_row + 2 + len(equity_data)
    
    equity_chart.add_series({
        'name': 'Cumulative Return',
        'categories': [sheet.name, start_row + 4, 0, data_end_row, 0],
        'values': [sheet.name, start_row + 4, 1, data_end_row, 1],
        'line': {'color': '#4472C4', 'width': 2},
        'marker': {'type': 'circle', 'size': 4}
    })
    
    equity_chart.add_series({
        'name': 'Running Maximum',
        'categories': [sheet.name, start_row + 4, 0, data_end_row, 0],
        'values': [sheet.name, start_row + 4, 3, data_end_row, 3],
        'line': {'color': '#70AD47', 'width': 1, 'dash_type': 'dash'},
    })
    
    equity_chart.set_title({'name': f'{result["pair"]} - Equity Curve Analysis'})
    equity_chart.set_x_axis({'name': 'Date', 'date_axis': True})
    equity_chart.set_y_axis({'name': 'Cumulative Return (%)'})
    equity_chart.set_size({'width': 900, 'height': 450})
    
    # Position chart to the right of equity data (column F)
    sheet.insert_chart(f'F{start_row}', equity_chart)


def _add_enhanced_trade_details(sheet, result, formats, safe_write_numeric, start_row):
    """Add enhanced trade details with better formatting for pairs trading with both legs tracked"""
    trades = result.get('trades', [])
    if not trades:
        return
    
    # Position trade history table directly at start_row (no extra offset)
    # start_row already accounts for proper spacing from equity curve
    trade_start_row = start_row  # Use start_row directly
    trade_start_column = 5  # Start at column F (index 5)
    
    sheet.write(trade_start_row, trade_start_column, 'TRADE HISTORY - PAIRS TRADING', formats['section_title'])
    sheet.merge_range(f'{chr(65 + trade_start_column)}{trade_start_row+1}:{chr(65 + trade_start_column + 15)}{trade_start_row+1}', 'TRADE HISTORY - PAIRS TRADING', formats['section_title'])
    
    headers = [
        'Trade #', 'Entry Time', 'Exit Time', 'Direction', 'Symbol1', 'Symbol2',
        'Entry Price1', 'Entry Price2', 'Exit Price1', 'Exit Price2', 
        'Leg1 PnL (%)', 'Leg2 PnL (%)', 'Combined PnL (%)', 'Net PnL (%)', 
        'Costs (%)', 'Exit Reason', 'Bars Held', 'Cumulative PnL (%)'
    ]
    
    for col, header in enumerate(headers):
        sheet.write(trade_start_row + 2, col + trade_start_column, header, formats['table_header'])
    
    # Sort trades by entry time
    trades = sorted(trades, key=lambda x: x.get('entry_time', ''))
    
    cumulative_pnl = 0
    for row_idx, trade in enumerate(trades[:100], trade_start_row + 3):  # Limit to first 100 trades
        # Handle datetime conversion
        entry_time = trade.get('entry_time')
        exit_time = trade.get('exit_time')
        
        if hasattr(entry_time, 'tzinfo') and entry_time.tzinfo is not None:
            entry_time = entry_time.replace(tzinfo=None)
        if hasattr(exit_time, 'tzinfo') and exit_time.tzinfo is not None:
            exit_time = exit_time.replace(tzinfo=None)
        
        # Determine row format
        row_fmt = formats['row_even'] if row_idx % 2 == 0 else formats['row_odd']
        
        # All data now starts from trade_start_column (column 5)
        sheet.write(row_idx, trade_start_column + 0, row_idx - trade_start_row - 2, formats['integer'])  # Trade #
        sheet.write(row_idx, trade_start_column + 1, entry_time, formats['date'])  # Entry Time
        sheet.write(row_idx, trade_start_column + 2, exit_time, formats['date'])  # Exit Time
        sheet.write(row_idx, trade_start_column + 3, trade.get('direction', ''), formats['text'])  # Direction
        sheet.write(row_idx, trade_start_column + 4, trade.get('symbol1', ''), formats['text'])  # Symbol1
        sheet.write(row_idx, trade_start_column + 5, trade.get('symbol2', ''), formats['text'])  # Symbol2
        
        # Entry and exit prices for both legs
        safe_write_numeric(sheet, row_idx, trade_start_column + 6, trade.get('entry_price1', 0), formats['number'])  # Entry Price1
        safe_write_numeric(sheet, row_idx, trade_start_column + 7, trade.get('entry_price2', 0), formats['number'])  # Entry Price2
        safe_write_numeric(sheet, row_idx, trade_start_column + 8, trade.get('exit_price1', 0), formats['number'])  # Exit Price1
        safe_write_numeric(sheet, row_idx, trade_start_column + 9, trade.get('exit_price2', 0), formats['number'])  # Exit Price2
        
        # Individual leg P&L with color coding
        leg1_pnl = trade.get('pnl_leg1', 0)
        leg1_fmt = formats['positive'] if leg1_pnl > 0 else formats['negative']
        safe_write_numeric(sheet, row_idx, trade_start_column + 10, leg1_pnl/100, formats['percentage'])  # Leg1 PnL
        
        leg2_pnl = trade.get('pnl_leg2', 0)
        leg2_fmt = formats['positive'] if leg2_pnl > 0 else formats['negative']
        safe_write_numeric(sheet, row_idx, trade_start_column + 11, leg2_pnl/100, formats['percentage'])  # Leg2 PnL
        
        # Combined P&L with color coding
        combined_pnl = trade.get('pnl_pct', 0)
        combined_fmt = formats['positive'] if combined_pnl > 0 else formats['negative']
        safe_write_numeric(sheet, row_idx, trade_start_column + 12, combined_pnl/100, formats['percentage'])  # Combined PnL
        
        # Net P&L after costs
        net_pnl = trade.get('net_pnl_pct', 0)
        net_fmt = formats['positive'] if net_pnl > 0 else formats['negative']
        safe_write_numeric(sheet, row_idx, trade_start_column + 13, net_pnl/100, formats['percentage'])  # Net PnL
        
        # Costs
        costs = trade.get('costs_pct', 0)
        safe_write_numeric(sheet, row_idx, trade_start_column + 14, costs/100, formats['percentage'])  # Costs
        
        # Exit reason
        sheet.write(row_idx, trade_start_column + 15, trade.get('exit_reason', ''), formats['text'])  # Exit Reason
        
        # Bars held
        safe_write_numeric(sheet, row_idx, trade_start_column + 16, trade.get('bars_held', 0), formats['integer'])  # Bars Held
        
        # Cumulative PnL
        cumulative_pnl += net_pnl
        cum_fmt = formats['positive'] if cumulative_pnl > 0 else formats['negative']
        safe_write_numeric(sheet, row_idx, trade_start_column + 17, cumulative_pnl/100, formats['percentage'])  # Cumulative PnL
    
    # Add conditional formatting for PnL columns (adjusted for new column positions)
    if len(trades) > 0:
        last_trade_row = trade_start_row + 2 + len(trades[:100])
        
        # Leg1 PnL (column 10 from start = trade_start_column + 10)
        sheet.conditional_format(trade_start_row + 3, trade_start_column + 10, last_trade_row, trade_start_column + 10, {
            'type': '3_color_scale',
            'min_color': '#FF6B6B',
            'mid_color': '#FFFFFF',
            'max_color': '#4ECDC4'
        })
        
        # Leg2 PnL (column 11 from start = trade_start_column + 11)
        sheet.conditional_format(trade_start_row + 3, trade_start_column + 11, last_trade_row, trade_start_column + 11, {
            'type': '3_color_scale',
            'min_color': '#FF6B6B',
            'mid_color': '#FFFFFF',
            'max_color': '#4ECDC4'
        })
        
        # Combined PnL (column 12 from start = trade_start_column + 12)
        sheet.conditional_format(trade_start_row + 3, trade_start_column + 12, last_trade_row, trade_start_column + 12, {
            'type': '3_color_scale',
            'min_color': '#FF6B6B',
            'mid_color': '#FFFFFF',
            'max_color': '#4ECDC4'
        })
        
        # Net PnL (column 13 from start = trade_start_column + 13)
        sheet.conditional_format(trade_start_row + 3, trade_start_column + 13, last_trade_row, trade_start_column + 13, {
            'type': '3_color_scale',
            'min_color': '#FF6B6B',
            'mid_color': '#FFFFFF',
            'max_color': '#4ECDC4'
        })
        
        # Cumulative PnL (column 17 from start = trade_start_column + 17)
        sheet.conditional_format(trade_start_row + 3, trade_start_column + 17, last_trade_row, trade_start_column + 17, {
            'type': '3_color_scale',
            'min_color': '#FF6B6B',
            'mid_color': '#FFFFFF',
            'max_color': '#4ECDC4'
        })
    
    # Return the end row position for further content placement
    # Since we removed the summary table, return the end of trade history
    if len(trades) > 0:
        return trade_start_row + 3 + len(trades[:100])
    else:
        return trade_start_row + 3


# Helper functions for charts and analysis
def _create_equity_curve_chart(sheet, backtest_results, workbook, formats):
    """Create enhanced equity curve chart"""
    if 'portfolio_equity' not in backtest_results:
        return
    
    # Write equity data
    sheet.write(3, 0, 'PORTFOLIO EQUITY CURVE', formats['section_title'])
    sheet.merge_range('A4:D4', 'PORTFOLIO EQUITY CURVE', formats['section_title'])
    
    headers = ['Date', 'Equity', 'Drawdown', 'Running Max']
    for i, header in enumerate(headers):
        sheet.write(5, i, header, formats['table_header'])
    
    dates = backtest_results.get('portfolio_dates', [])
    equity = backtest_results.get('portfolio_equity', [])
    
    # Calculate drawdown
    running_max = [0]
    for i, val in enumerate(equity):
        if i == 0:
            running_max.append(val)
        else:
            running_max.append(max(running_max[-1], val))
    
    drawdown = [(equity[i] - running_max[i+1]) / running_max[i+1] * 100 if running_max[i+1] != 0 else 0 
                for i in range(len(equity))]
    
    # Write data
    for i, (date, eq, dd, rmax) in enumerate(zip(dates, equity, drawdown, running_max[1:])):
        row = 6 + i
        if hasattr(date, 'tzinfo') and date.tzinfo is not None:
            date = date.replace(tzinfo=None)
        sheet.write(row, 0, date, formats['date'])
        sheet.write(row, 1, eq, formats['number'])
        sheet.write(row, 2, dd/100, formats['percentage'])
        sheet.write(row, 3, rmax, formats['number'])
    
    # Create chart
    chart = workbook.add_chart({'type': 'line'})
    
    data_end_row = 5 + len(dates)
    
    chart.add_series({
        'name': 'Portfolio Equity',
        'categories': [sheet.name, 6, 0, data_end_row, 0],
        'values': [sheet.name, 6, 1, data_end_row, 1],
        'line': {'color': '#1f4e79', 'width': 2},
    })
    
    chart.set_title({'name': 'Portfolio Equity Curve'})
    chart.set_x_axis({'name': 'Date', 'date_axis': True})
    chart.set_y_axis({'name': 'Equity Value'})
    chart.set_size({'width': 600, 'height': 400})
    
    sheet.insert_chart('F3', chart)


def _create_drawdown_chart(sheet, backtest_results, workbook, formats):
    """Create drawdown analysis chart"""
    # Implementation for drawdown chart
    pass


def _create_performance_distribution(sheet, backtest_results, formats, safe_write_numeric):
    """Create performance distribution analysis"""
    # Implementation for performance distribution
    pass


def _create_monthly_returns_heatmap(sheet, backtest_results, formats, safe_write_numeric):
    """Create monthly returns heatmap"""
    # Implementation for monthly returns heatmap
    pass


def _add_performance_comparison(sheet, backtest_results, start_row, formats, safe_write_numeric):
    """Add performance comparison section"""
    # Implementation for performance comparison
    pass


def _add_correlation_matrix(sheet, backtest_results, start_row, formats, safe_write_numeric):
    """Add correlation matrix analysis"""
    # Implementation for correlation matrix
    pass


def _generate_executive_summary(workbook, backtest_results, config, formats, safe_write_numeric):
    """Deprecated - functionality moved to _generate_comprehensive_dashboard"""
    pass


def _generate_summary_sheet(workbook, backtest_results, config, formats, safe_write_numeric):
    """Deprecated - functionality moved to _generate_comprehensive_dashboard"""
    pass


def _generate_risk_analysis_sheet(workbook, backtest_results, formats, safe_write_numeric):
    """Deprecated - functionality moved to _generate_comprehensive_dashboard"""
    pass
