"""
Chart Generator - Creates interactive charts for dashboard visualization

Generates various types of charts including equity curves, performance distributions,
drawdown analysis, and custom visualizations using Plotly.
"""

import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union
import base64
import io

import numpy as np
import pandas as pd

# Chart generation (will be imported when available)
try:
    import plotly.graph_objects as go
    import plotly.express as px
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    logger.warning("Plotly not available - charts will be generated as data only")

logger = logging.getLogger(__name__)


class ChartGenerator:
    """
    Generator for interactive charts and visualizations
    
    Creates various chart types for trading dashboard including
    performance charts, risk analysis, and custom visualizations.
    """
    
    def __init__(self, config):
        self.config = config
        
        # Chart styling
        self.colors = {
            'primary': '#1f77b4',
            'success': '#2ca02c',
            'danger': '#d62728',
            'warning': '#ff7f0e',
            'info': '#17a2b8',
            'secondary': '#6c757d',
            'background': '#2e2e2e' if config.theme == 'dark' else '#ffffff',
            'text': '#ffffff' if config.theme == 'dark' else '#000000'
        }
        
        # Default layout
        self.default_layout = {
            'height': config.chart_height,
            'template': 'plotly_dark' if config.theme == 'dark' else 'plotly_white',
            'margin': dict(l=50, r=50, t=50, b=50),
            'showlegend': True
        }
        
        logger.info(f"ChartGenerator initialized (Plotly available: {PLOTLY_AVAILABLE})")
    
    def generate_backtest_charts(self, backtest_data: Dict) -> Dict:
        """
        Generate all charts for backtest results
        
        Args:
            backtest_data: Processed backtest data
            
        Returns:
            Dictionary containing all chart configurations
        """
        try:
            logger.info("Generating backtest charts")
            
            charts = {}
            
            # Portfolio equity curve
            if backtest_data.get('equity_curve'):
                charts['equity_curve'] = self.create_equity_curve_chart(
                    backtest_data['equity_curve']
                )
            
            # Drawdown chart
            if backtest_data.get('drawdown_curve'):
                charts['drawdown'] = self.create_drawdown_chart(
                    backtest_data['drawdown_curve']
                )
            
            # Performance distribution
            charts['performance_distribution'] = self.create_performance_distribution_chart(
                backtest_data.get('pairs', [])
            )
            
            # Risk metrics radar
            charts['risk_radar'] = self.create_risk_radar_chart(
                backtest_data.get('risk_metrics', {})
            )
            
            # Pairs performance comparison
            charts['pairs_comparison'] = self.create_pairs_comparison_chart(
                backtest_data.get('pairs', [])
            )
            
            # Monthly returns heatmap
            charts['monthly_returns'] = self.create_monthly_returns_heatmap(
                backtest_data.get('equity_curve', [])
            )
            
            # Trade analysis
            charts['trade_analysis'] = self.create_trade_analysis_chart(
                backtest_data.get('trade_analysis', {})
            )
            
            logger.info(f"Generated {len(charts)} backtest charts")
            return charts
            
        except Exception as e:
            logger.error(f"Failed to generate backtest charts: {e}")
            return {}
    
    def create_equity_curve_chart(self, equity_data: List[Dict]) -> Dict:
        """Create equity curve chart"""
        try:
            if not PLOTLY_AVAILABLE or not equity_data:
                return self._create_data_only_chart(equity_data, 'equity_curve')
            
            timestamps = [point['timestamp'] for point in equity_data]
            values = [point['value'] for point in equity_data]
            
            fig = go.Figure()
            
            # Add equity curve
            fig.add_trace(go.Scatter(
                x=timestamps,
                y=values,
                mode='lines',
                name='Portfolio Equity',
                line=dict(color=self.colors['primary'], width=2),
                hovertemplate='<b>%{y:.2f}</b><br>%{x}<extra></extra>'
            ))
            
            # Calculate drawdown
            peak = np.maximum.accumulate(values)
            drawdown = (values - peak) / peak * 100
            
            # Add drawdown fill
            fig.add_trace(go.Scatter(
                x=timestamps,
                y=drawdown,
                mode='lines',
                name='Drawdown %',
                line=dict(color=self.colors['danger'], width=1),
                fill='tonexty',
                fillcolor='rgba(214, 39, 40, 0.3)',
                yaxis='y2',
                hovertemplate='<b>%{y:.2f}%</b><br>%{x}<extra></extra>'
            ))
            
            # Update layout
            fig.update_layout(
                **self.default_layout,
                title='Portfolio Equity Curve',
                xaxis_title='Date',
                yaxis_title='Equity Value',
                yaxis2=dict(
                    title='Drawdown %',
                    overlaying='y',
                    side='right',
                    range=[min(drawdown) * 1.1, 0]
                )
            )
            
            return {
                'type': 'plotly',
                'config': fig.to_dict(),
                'data': equity_data
            }
            
        except Exception as e:
            logger.error(f"Failed to create equity curve chart: {e}")
            return self._create_data_only_chart(equity_data, 'equity_curve')
    
    def create_drawdown_chart(self, drawdown_data: List[Dict]) -> Dict:
        """Create drawdown analysis chart"""
        try:
            if not PLOTLY_AVAILABLE or not drawdown_data:
                return self._create_data_only_chart(drawdown_data, 'drawdown')
            
            timestamps = [point['timestamp'] for point in drawdown_data]
            values = [point['value'] for point in drawdown_data]
            
            fig = go.Figure()
            
            # Add drawdown curve
            fig.add_trace(go.Scatter(
                x=timestamps,
                y=values,
                mode='lines',
                name='Drawdown %',
                line=dict(color=self.colors['danger'], width=2),
                fill='tozeroy',
                fillcolor='rgba(214, 39, 40, 0.3)',
                hovertemplate='<b>%{y:.2f}%</b><br>%{x}<extra></extra>'
            ))
            
            # Add zero line
            fig.add_hline(y=0, line_dash="dash", line_color="gray")
            
            # Update layout
            fig.update_layout(
                **self.default_layout,
                title='Drawdown Analysis',
                xaxis_title='Date',
                yaxis_title='Drawdown %',
                yaxis=dict(range=[min(values) * 1.1, 0])
            )
            
            return {
                'type': 'plotly',
                'config': fig.to_dict(),
                'data': drawdown_data
            }
            
        except Exception as e:
            logger.error(f"Failed to create drawdown chart: {e}")
            return self._create_data_only_chart(drawdown_data, 'drawdown')
    
    def create_performance_distribution_chart(self, pairs_data: List[Dict]) -> Dict:
        """Create performance distribution chart"""
        try:
            if not PLOTLY_AVAILABLE or not pairs_data:
                return self._create_data_only_chart(pairs_data, 'performance_distribution')
            
            returns = [pair['metrics']['total_return'] for pair in pairs_data]
            
            fig = make_subplots(
                rows=2, cols=1,
                subplot_titles=('Returns Distribution', 'Box Plot'),
                vertical_spacing=0.1
            )
            
            # Histogram
            fig.add_trace(
                go.Histogram(
                    x=returns,
                    nbinsx=20,
                    name='Returns Distribution',
                    marker_color=self.colors['primary'],
                    opacity=0.7
                ),
                row=1, col=1
            )
            
            # Box plot
            fig.add_trace(
                go.Box(
                    x=returns,
                    name='Returns',
                    marker_color=self.colors['info'],
                    boxpoints='outliers'
                ),
                row=2, col=1
            )
            
            # Update layout
            layout = self.default_layout.copy()
            layout.update({
                'title': 'Performance Distribution Analysis',
                'showlegend': False
            })
            fig.update_layout(**layout)
            
            return {
                'type': 'plotly',
                'config': fig.to_dict(),
                'data': {'returns': returns}
            }
            
        except Exception as e:
            logger.error(f"Failed to create performance distribution chart: {e}")
            return self._create_data_only_chart(pairs_data, 'performance_distribution')
    
    def create_risk_radar_chart(self, risk_metrics: Dict) -> Dict:
        """Create risk metrics radar chart"""
        try:
            if not PLOTLY_AVAILABLE or not risk_metrics:
                return self._create_data_only_chart(risk_metrics, 'risk_radar')
            
            # Prepare metrics for radar chart
            metrics = [
                'Sharpe Ratio',
                'Sortino Ratio',
                'Calmar Ratio',
                'Max Drawdown',
                'Volatility',
                'VaR 95%'
            ]
            
            # Normalize values (0-100 scale)
            values = []
            for metric in metrics:
                if metric == 'Sharpe Ratio':
                    val = min(risk_metrics.get('sharpe_ratio', 0) * 50, 100)
                elif metric == 'Sortino Ratio':
                    val = min(risk_metrics.get('sortino_ratio', 0) * 50, 100)
                elif metric == 'Calmar Ratio':
                    val = min(risk_metrics.get('calmar_ratio', 0) * 100, 100)
                elif metric == 'Max Drawdown':
                    val = max(100 - abs(risk_metrics.get('maximum_drawdown', 0)) * 5, 0)
                elif metric == 'Volatility':
                    val = max(100 - risk_metrics.get('portfolio_volatility', 0) * 5, 0)
                elif metric == 'VaR 95%':
                    val = max(100 - abs(risk_metrics.get('value_at_risk_95', 0)) * 10, 0)
                else:
                    val = 50
                values.append(val)
            
            fig = go.Figure()
            
            fig.add_trace(go.Scatterpolar(
                r=values,
                theta=metrics,
                fill='toself',
                name='Risk Profile',
                line_color=self.colors['primary']
            ))
            
            fig.update_layout(
                **self.default_layout,
                title='Risk Metrics Profile',
                polar=dict(
                    radialaxis=dict(
                        visible=True,
                        range=[0, 100]
                    )
                )
            )
            
            return {
                'type': 'plotly',
                'config': fig.to_dict(),
                'data': dict(zip(metrics, values))
            }
            
        except Exception as e:
            logger.error(f"Failed to create risk radar chart: {e}")
            return self._create_data_only_chart(risk_metrics, 'risk_radar')
    
    def create_pairs_comparison_chart(self, pairs_data: List[Dict]) -> Dict:
        """Create pairs performance comparison chart"""
        try:
            if not PLOTLY_AVAILABLE or not pairs_data:
                return self._create_data_only_chart(pairs_data, 'pairs_comparison')
            
            # Top 20 pairs by return
            top_pairs = sorted(pairs_data, 
                             key=lambda x: x['metrics']['total_return'], 
                             reverse=True)[:20]
            
            pairs = [pair['pair'] for pair in top_pairs]
            returns = [pair['metrics']['total_return'] for pair in top_pairs]
            sharpe = [pair['metrics']['sharpe_ratio'] for pair in top_pairs]
            
            fig = make_subplots(
                rows=1, cols=2,
                subplot_titles=('Total Return %', 'Sharpe Ratio'),
                horizontal_spacing=0.1
            )
            
            # Returns bar chart
            fig.add_trace(
                go.Bar(
                    x=returns,
                    y=pairs,
                    orientation='h',
                    name='Returns %',
                    marker_color=[self.colors['success'] if r > 0 else self.colors['danger'] for r in returns]
                ),
                row=1, col=1
            )
            
            # Sharpe ratio bar chart
            fig.add_trace(
                go.Bar(
                    x=sharpe,
                    y=pairs,
                    orientation='h',
                    name='Sharpe Ratio',
                    marker_color=self.colors['info']
                ),
                row=1, col=2
            )
            
            layout = self.default_layout.copy()
            layout.update({
                'title': 'Top 20 Pairs Performance Comparison',
                'showlegend': False
            })
            fig.update_layout(**layout)
            
            return {
                'type': 'plotly',
                'config': fig.to_dict(),
                'data': {'pairs': pairs, 'returns': returns, 'sharpe': sharpe}
            }
            
        except Exception as e:
            logger.error(f"Failed to create pairs comparison chart: {e}")
            return self._create_data_only_chart(pairs_data, 'pairs_comparison')
    
    def create_monthly_returns_heatmap(self, equity_data: List[Dict]) -> Dict:
        """Create monthly returns heatmap"""
        try:
            if not PLOTLY_AVAILABLE or not equity_data:
                return self._create_data_only_chart(equity_data, 'monthly_returns')
            
            # Convert to DataFrame for easier processing
            df = pd.DataFrame(equity_data)
            if df.empty:
                return self._create_data_only_chart(equity_data, 'monthly_returns')
            
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
            
            # Calculate monthly returns
            monthly_returns = df['value'].resample('M').last().pct_change() * 100
            monthly_returns = monthly_returns.dropna()
            
            if len(monthly_returns) < 2:
                return self._create_data_only_chart(equity_data, 'monthly_returns')
            
            # Create heatmap data
            monthly_data = []
            for date, ret in monthly_returns.items():
                monthly_data.append({
                    'year': date.year,
                    'month': date.strftime('%b'),
                    'return': ret
                })
            
            # Pivot for heatmap
            heatmap_df = pd.DataFrame(monthly_data)
            heatmap_pivot = heatmap_df.pivot(index='month', columns='year', values='return')
            
            fig = go.Figure(data=go.Heatmap(
                z=heatmap_pivot.values,
                x=heatmap_pivot.columns,
                y=heatmap_pivot.index,
                colorscale='RdYlGn',
                text=np.round(heatmap_pivot.values, 2),
                texttemplate='%{text}%',
                textfont={"size": 10},
                hovertemplate='<b>%{y} %{x}</b><br>Return: %{z:.2f}%<extra></extra>'
            ))
            
            fig.update_layout(
                **self.default_layout,
                title='Monthly Returns Heatmap',
                xaxis_title='Year',
                yaxis_title='Month'
            )
            
            return {
                'type': 'plotly',
                'config': fig.to_dict(),
                'data': monthly_data
            }
            
        except Exception as e:
            logger.error(f"Failed to create monthly returns heatmap: {e}")
            return self._create_data_only_chart(equity_data, 'monthly_returns')
    
    def create_trade_analysis_chart(self, trade_analysis: Dict) -> Dict:
        """Create trade analysis chart"""
        try:
            if not PLOTLY_AVAILABLE or not trade_analysis:
                return self._create_data_only_chart(trade_analysis, 'trade_analysis')
            
            # Prepare data
            metrics = ['Total Trades', 'Winning Trades', 'Losing Trades']
            values = [
                trade_analysis.get('total_trades', 0),
                trade_analysis.get('winning_trades', 0),
                trade_analysis.get('losing_trades', 0)
            ]
            
            colors = [self.colors['info'], self.colors['success'], self.colors['danger']]
            
            fig = go.Figure(data=[
                go.Bar(
                    x=metrics,
                    y=values,
                    marker_color=colors,
                    text=values,
                    textposition='auto'
                )
            ])
            
            fig.update_layout(
                **self.default_layout,
                title='Trade Analysis',
                xaxis_title='Trade Type',
                yaxis_title='Count'
            )
            
            return {
                'type': 'plotly',
                'config': fig.to_dict(),
                'data': trade_analysis
            }
            
        except Exception as e:
            logger.error(f"Failed to create trade analysis chart: {e}")
            return self._create_data_only_chart(trade_analysis, 'trade_analysis')
    
    def generate_portfolio_charts(self, portfolio_data: Dict) -> Dict:
        """Generate charts for portfolio data"""
        try:
            charts = {}
            
            # Portfolio composition pie chart
            if portfolio_data.get('positions'):
                charts['composition'] = self.create_portfolio_composition_chart(
                    portfolio_data['positions']
                )
            
            # P&L chart
            charts['pnl'] = self.create_pnl_chart(portfolio_data)
            
            return charts
            
        except Exception as e:
            logger.error(f"Failed to generate portfolio charts: {e}")
            return {}
    
    def create_portfolio_composition_chart(self, positions: List[Dict]) -> Dict:
        """Create portfolio composition pie chart"""
        try:
            if not PLOTLY_AVAILABLE or not positions:
                return self._create_data_only_chart(positions, 'composition')
            
            symbols = [pos['symbol'] for pos in positions]
            volumes = [abs(pos['volume']) for pos in positions]
            
            fig = go.Figure(data=[go.Pie(
                labels=symbols,
                values=volumes,
                hole=0.3,
                textinfo='label+percent',
                textposition='auto'
            )])
            
            fig.update_layout(
                **self.default_layout,
                title='Portfolio Composition'
            )
            
            return {
                'type': 'plotly',
                'config': fig.to_dict(),
                'data': {'symbols': symbols, 'volumes': volumes}
            }
            
        except Exception as e:
            logger.error(f"Failed to create portfolio composition chart: {e}")
            return self._create_data_only_chart(positions, 'composition')
    
    def create_pnl_chart(self, portfolio_data: Dict) -> Dict:
        """Create P&L chart"""
        try:
            if not PLOTLY_AVAILABLE:
                return self._create_data_only_chart(portfolio_data, 'pnl')
            
            # Simple P&L indicator
            pnl = portfolio_data.get('pnl', 0)
            daily_pnl = portfolio_data.get('daily_pnl', 0)
            
            fig = go.Figure()
            
            fig.add_trace(go.Indicator(
                mode="number+delta",
                value=pnl,
                delta={'reference': daily_pnl},
                title={'text': 'Total P&L'},
                number={'prefix': '$'}
            ))
            
            fig.update_layout(
                **self.default_layout,
                title='Portfolio P&L'
            )
            
            return {
                'type': 'plotly',
                'config': fig.to_dict(),
                'data': portfolio_data
            }
            
        except Exception as e:
            logger.error(f"Failed to create P&L chart: {e}")
            return self._create_data_only_chart(portfolio_data, 'pnl')
    
    def generate_custom_chart(self, data: Dict, chart_config: Dict) -> Dict:
        """Generate custom chart based on configuration"""
        try:
            chart_type = chart_config.get('type', 'line')
            
            if chart_type == 'line':
                return self._create_line_chart(data, chart_config)
            elif chart_type == 'bar':
                return self._create_bar_chart(data, chart_config)
            elif chart_type == 'scatter':
                return self._create_scatter_chart(data, chart_config)
            elif chart_type == 'heatmap':
                return self._create_heatmap_chart(data, chart_config)
            else:
                return self._create_data_only_chart(data, chart_type)
                
        except Exception as e:
            logger.error(f"Failed to generate custom chart: {e}")
            return self._create_data_only_chart(data, 'custom')
    
    def _create_line_chart(self, data: Dict, config: Dict) -> Dict:
        """Create custom line chart"""
        if not PLOTLY_AVAILABLE:
            return self._create_data_only_chart(data, 'line')
        
        try:
            x_field = config.get('x_field', 'x')
            y_field = config.get('y_field', 'y')
            
            chart_data = data.get('data', [])
            if isinstance(chart_data, dict):
                x_values = chart_data.get(x_field, [])
                y_values = chart_data.get(y_field, [])
            else:
                x_values = [item.get(x_field) for item in chart_data]
                y_values = [item.get(y_field) for item in chart_data]
            
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=x_values,
                y=y_values,
                mode='lines',
                name=config.get('title', 'Data'),
                line=dict(color=self.colors['primary'])
            ))
            
            fig.update_layout(
                **self.default_layout,
                title=config.get('title', 'Custom Chart'),
                xaxis_title=config.get('x_title', x_field),
                yaxis_title=config.get('y_title', y_field)
            )
            
            return {
                'type': 'plotly',
                'config': fig.to_dict(),
                'data': data
            }
            
        except Exception as e:
            logger.error(f"Failed to create line chart: {e}")
            return self._create_data_only_chart(data, 'line')
    
    def _create_bar_chart(self, data: Dict, config: Dict) -> Dict:
        """Create custom bar chart"""
        if not PLOTLY_AVAILABLE:
            return self._create_data_only_chart(data, 'bar')
        
        try:
            # Implementation similar to line chart but with bar traces
            # ... (implementation details)
            return self._create_data_only_chart(data, 'bar')
            
        except Exception as e:
            logger.error(f"Failed to create bar chart: {e}")
            return self._create_data_only_chart(data, 'bar')
    
    def _create_scatter_chart(self, data: Dict, config: Dict) -> Dict:
        """Create custom scatter chart"""
        if not PLOTLY_AVAILABLE:
            return self._create_data_only_chart(data, 'scatter')
        
        try:
            # Implementation for scatter plot
            # ... (implementation details)
            return self._create_data_only_chart(data, 'scatter')
            
        except Exception as e:
            logger.error(f"Failed to create scatter chart: {e}")
            return self._create_data_only_chart(data, 'scatter')
    
    def _create_heatmap_chart(self, data: Dict, config: Dict) -> Dict:
        """Create custom heatmap chart"""
        if not PLOTLY_AVAILABLE:
            return self._create_data_only_chart(data, 'heatmap')
        
        try:
            # Implementation for heatmap
            # ... (implementation details)
            return self._create_data_only_chart(data, 'heatmap')
            
        except Exception as e:
            logger.error(f"Failed to create heatmap chart: {e}")
            return self._create_data_only_chart(data, 'heatmap')
    
    def _create_data_only_chart(self, data: Any, chart_type: str) -> Dict:
        """Create data-only chart when Plotly is not available"""
        return {
            'type': 'data_only',
            'chart_type': chart_type,
            'data': data,
            'message': 'Chart visualization requires Plotly to be installed'
        }
