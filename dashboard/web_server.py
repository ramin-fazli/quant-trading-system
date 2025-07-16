"""
Web Server - Flask-based web server for dashboard interface

Provides RESTful API endpoints and serves the dashboard web interface.
"""

import logging
import json
import numpy as np
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path

from flask import Flask, render_template, jsonify, request, send_from_directory, send_file
from flask_cors import CORS

logger = logging.getLogger(__name__)


class DashboardServer:
    """
    Flask web server for trading dashboard
    
    Provides web interface and RESTful API for accessing trading data
    and dashboard functionality.
    """
    
    def __init__(self, app: Flask, config):
        self.app = app
        self.config = config
        
        # Setup CORS if enabled
        if config.cors_enabled:
            CORS(app)
        
        # Data references (will be set when routes are registered)
        self.data_adapter = None
        self.chart_generator = None
        
        self._setup_error_handlers()
        
        logger.info("DashboardServer initialized")
    
    def register_routes(self, data_adapter, chart_generator):
        """Register all Flask routes"""
        self.data_adapter = data_adapter
        self.chart_generator = chart_generator
        
        # Main dashboard routes
        self._register_page_routes()
        
        # API routes
        self._register_api_routes()
        
        # Static file routes
        self._register_static_routes()
        
        logger.info("Dashboard routes registered")
    
    def _register_page_routes(self):
        """Register page routes"""
        
        @self.app.route('/')
        def index():
            """Main dashboard page"""
            return render_template('index.html', config=self.config)
        
        @self.app.route('/backtest')
        def backtest_page():
            """Backtest results page"""
            return render_template('backtest.html', config=self.config)
        
        @self.app.route('/live')
        def live_page():
            """Live trading page"""
            return render_template('live.html', config=self.config)
        
        @self.app.route('/portfolio')
        def portfolio_page():
            """Portfolio overview page"""
            return render_template('portfolio.html', config=self.config)
        
        @self.app.route('/pairs')
        def pairs_page():
            """Pairs analysis page"""
            return render_template('pairs.html', config=self.config)
        
        @self.app.route('/settings')
        def settings_page():
            """Settings page"""
            return render_template('settings.html', config=self.config)
        
        @self.app.route('/reports')
        def reports_page():
            """Reports download page"""
            return render_template('reports.html', config=self.config)
    
    def _register_api_routes(self):
        """Register API routes"""
        
        @self.app.route('/api/status')
        def api_status():
            """Get dashboard status"""
            try:
                status = {
                    'status': 'running',
                    'timestamp': datetime.now().isoformat(),
                    'version': '1.0.0',
                    'websocket_enabled': self.config.websocket_enabled
                }
                return jsonify(status)
            except Exception as e:
                logger.error(f"Error getting status: {e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/backtest/summary')
        def api_backtest_summary():
            """Get backtest summary data"""
            try:
                # Get data from data adapter
                if not hasattr(self.data_adapter, 'backtest_data') or not self.data_adapter.backtest_data:
                    # Return default/empty data with a helpful message instead of 404
                    logger.warning("No backtest data available in data adapter - returning default values")
                    return jsonify({
                        'portfolio_metrics': {
                            'total_return': 0.0,
                            'portfolio_return': 0.0,
                            'portfolio_sharpe': 0.0,
                            'portfolio_max_drawdown': 0.0,
                            'total_trades': 0,
                            'portfolio_win_rate': 0.0,
                            'profit_factor': 0.0,
                            'total_pairs': 0
                        },
                        'summary': {
                            'status': 'No recent backtest data available',
                            'mode': 'live_trading',
                            'message': 'Run a backtest to see results here, or check if InfluxDB contains recent backtest data'
                        },
                        'timestamp': datetime.now().isoformat()
                    })
                
                backtest_data = getattr(self.data_adapter, 'backtest_data', {})
                
                # Ensure we have valid portfolio metrics
                portfolio_metrics = backtest_data.get('portfolio_metrics', {})
                if not portfolio_metrics:
                    portfolio_metrics = {
                        'total_return': 0.0,
                        'portfolio_return': 0.0,
                        'portfolio_sharpe': 0.0,
                        'portfolio_max_drawdown': 0.0,
                        'total_trades': 0,
                        'portfolio_win_rate': 0.0,
                        'profit_factor': 0.0,
                        'total_pairs': len(backtest_data.get('pairs', []))
                    }
                
                summary = {
                    'portfolio_metrics': portfolio_metrics,
                    'summary': backtest_data.get('summary', {
                        'status': 'Backtest data loaded from InfluxDB',
                        'mode': 'live_trading'
                    }),
                    'timestamp': backtest_data.get('timestamp', datetime.now().isoformat())
                }
                
                return jsonify(summary)
            except Exception as e:
                logger.error(f"Error getting backtest summary: {e}")
                return jsonify({
                    'portfolio_metrics': {
                        'total_return': 0.0,
                        'portfolio_return': 0.0,
                        'portfolio_sharpe': 0.0,
                        'portfolio_max_drawdown': 0.0,
                        'total_trades': 0,
                        'portfolio_win_rate': 0.0,
                        'profit_factor': 0.0,
                        'total_pairs': 0
                    },
                    'summary': {
                        'status': 'Error loading backtest data',
                        'mode': 'live_trading',
                        'error': str(e)
                    },
                    'timestamp': datetime.now().isoformat()
                }), 200  # Return 200 instead of 500 to prevent frontend errors
        
        @self.app.route('/api/backtest/pairs')
        def api_backtest_pairs():
            """Get backtest pairs data"""
            try:
                # Get pagination parameters
                page = request.args.get('page', 1, type=int)
                per_page = request.args.get('per_page', 50, type=int)
                sort_by = request.args.get('sort_by', 'total_return')
                sort_order = request.args.get('sort_order', 'desc')
                
                if not hasattr(self.data_adapter, 'backtest_data') or not self.data_adapter.backtest_data:
                    logger.warning("No backtest pairs data available - returning empty list")
                    return jsonify({
                        'pairs': [],
                        'pagination': {
                            'page': page,
                            'per_page': per_page,
                            'total_pages': 0,
                            'total_items': 0,
                            'has_next': False,
                            'has_prev': False
                        },
                        'message': 'No backtest pairs data available - run a backtest or check InfluxDB connection'
                    })
                
                backtest_data = getattr(self.data_adapter, 'backtest_data', {})
                pairs = backtest_data.get('pairs', [])
                
                if not pairs:
                    logger.warning("Backtest data exists but contains no pairs")
                    return jsonify({
                        'pairs': [],
                        'pagination': {
                            'page': page,
                            'per_page': per_page,
                            'total_pages': 0,
                            'total_items': 0,
                            'has_next': False,
                            'has_prev': False
                        },
                        'message': 'Backtest data loaded but contains no pairs'
                    })
                
                # Sort pairs
                reverse = sort_order.lower() == 'desc'
                if sort_by in ['total_return', 'sharpe_ratio', 'total_trades', 'win_rate']:
                    pairs = sorted(pairs, 
                                 key=lambda x: x.get('metrics', {}).get(sort_by, 0), 
                                 reverse=reverse)
                
                # Paginate
                start_idx = (page - 1) * per_page
                end_idx = start_idx + per_page
                paginated_pairs = pairs[start_idx:end_idx]
                
                response = {
                    'pairs': paginated_pairs,
                    'pagination': {
                        'page': page,
                        'per_page': per_page,
                        'total': len(pairs),
                        'pages': (len(pairs) + per_page - 1) // per_page
                    }
                }
                
                return jsonify(response)
            except Exception as e:
                logger.error(f"Error getting backtest pairs: {e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/backtest/pair/<pair_name>')
        def api_backtest_pair_detail(pair_name):
            """Get detailed data for specific pair"""
            try:
                if not hasattr(self.data_adapter, 'backtest_data'):
                    return jsonify({'error': 'No backtest data available'}), 404
                
                backtest_data = getattr(self.data_adapter, 'backtest_data', {})
                pairs = backtest_data.get('pairs', [])
                
                # Find the specific pair
                pair_data = None
                for pair in pairs:
                    if pair['pair'] == pair_name:
                        pair_data = pair
                        break
                
                if not pair_data:
                    return jsonify({'error': f'Pair {pair_name} not found'}), 404
                
                return jsonify(pair_data)
            except Exception as e:
                logger.error(f"Error getting pair detail: {e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/backtest/charts')
        def api_backtest_charts():
            """Get backtest chart data"""
            try:
                if not hasattr(self.data_adapter, 'backtest_data') or not self.data_adapter.backtest_data:
                    logger.warning("No backtest chart data available - returning empty charts")
                    return jsonify({
                        'equity_curve': [],
                        'drawdown_curve': [],
                        'performance_distribution': [],
                        'monthly_returns': [],
                        'message': 'No backtest chart data available - run a backtest to see charts'
                    })
                
                backtest_data = getattr(self.data_adapter, 'backtest_data', {})
                
                charts = {
                    'equity_curve': backtest_data.get('equity_curve', []),
                    'drawdown_curve': backtest_data.get('drawdown_curve', []),
                    'performance_distribution': backtest_data.get('performance_distribution', []),
                    'monthly_returns': backtest_data.get('monthly_returns', [])
                }
                
                return jsonify(charts)
            except Exception as e:
                logger.error(f"Error getting backtest charts: {e}")
                return jsonify({
                    'equity_curve': [],
                    'drawdown_curve': [],
                    'performance_distribution': [],
                    'monthly_returns': [],
                    'error': str(e)
                }), 200  # Return 200 instead of 500 to prevent frontend errors
        
        @self.app.route('/api/live/data')
        def api_live_data():
            """Get live market data"""
            try:
                if not self.data_adapter:
                    return jsonify({'error': 'Data adapter not available'}), 500
                
                # Get latest live data
                live_data = self.data_adapter.get_live_data_update()
                
                if not live_data:
                    return jsonify({'error': 'No live data available'}), 404
                
                return jsonify(live_data)
            except Exception as e:
                logger.error(f"Error getting live data: {e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/portfolio')
        def api_portfolio():
            """Get portfolio data"""
            try:
                if not hasattr(self.data_adapter, 'portfolio_cache'):
                    return jsonify({'error': 'No portfolio data available'}), 404
                
                portfolio_data = getattr(self.data_adapter, 'portfolio_cache', {})
                
                return jsonify(portfolio_data)
            except Exception as e:
                logger.error(f"Error getting portfolio data: {e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/config')
        def api_config():
            """Get dashboard configuration"""
            try:
                config_data = {
                    'theme': self.config.theme,
                    'auto_refresh': self.config.auto_refresh,
                    'update_interval': self.config.update_interval,
                    'chart_height': self.config.chart_height,
                    'websocket_enabled': self.config.websocket_enabled
                }
                return jsonify(config_data)
            except Exception as e:
                logger.error(f"Error getting config: {e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/config', methods=['POST'])
        def api_update_config():
            """Update dashboard configuration"""
            try:
                config_updates = request.get_json()
                
                # Update configuration
                for key, value in config_updates.items():
                    if hasattr(self.config, key):
                        setattr(self.config, key, value)
                
                return jsonify({'status': 'success', 'message': 'Configuration updated'})
            except Exception as e:
                logger.error(f"Error updating config: {e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/pairs/analysis')
        def api_pairs_analysis():
            """Get pairs analysis data"""
            try:
                # Get pairs from backtest data if available
                if hasattr(self.data_adapter, 'backtest_data'):
                    backtest_data = getattr(self.data_adapter, 'backtest_data', {})
                    pairs = backtest_data.get('pairs', [])
                    
                    # Create analysis summary
                    analysis = {
                        'total_pairs': len(pairs),
                        'profitable_pairs': len([p for p in pairs if p.get('metrics', {}).get('total_return', 0) > 0]),
                        'avg_return': np.mean([p.get('metrics', {}).get('total_return', 0) for p in pairs]) if pairs else 0,
                        'avg_sharpe': np.mean([p.get('metrics', {}).get('sharpe_ratio', 0) for p in pairs]) if pairs else 0,
                        'pairs_data': pairs[:20]  # Limit to top 20 for performance
                    }
                    
                    return jsonify(analysis)
                else:
                    # Return empty analysis if no data
                    return jsonify({
                        'total_pairs': 0,
                        'profitable_pairs': 0,
                        'avg_return': 0,
                        'avg_sharpe': 0,
                        'pairs_data': []
                    })
                    
            except Exception as e:
                logger.error(f"Error getting pairs analysis: {e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/reports')
        def api_reports_list():
            """Get list of available Excel reports"""
            try:
                reports_dir = Path(__file__).parent.parent / 'backtest_reports'
                if not reports_dir.exists():
                    return jsonify({'reports': []})
                
                reports = []
                for file_path in reports_dir.glob('*.xlsx'):
                    stat = file_path.stat()
                    reports.append({
                        'filename': file_path.name,
                        'size': stat.st_size,
                        'created': datetime.fromtimestamp(stat.st_ctime).isoformat(),
                        'modified': datetime.fromtimestamp(stat.st_mtime).isoformat()
                    })
                
                # Sort by creation time (newest first)
                reports.sort(key=lambda x: x['created'], reverse=True)
                
                return jsonify({'reports': reports})
            except Exception as e:
                logger.error(f"Error listing reports: {e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/download/report/<filename>')
        def api_download_report(filename):
            """Download Excel report"""
            try:
                # Validate filename to prevent directory traversal
                if '..' in filename or '/' in filename or '\\' in filename:
                    return jsonify({'error': 'Invalid filename'}), 400
                
                reports_dir = Path(__file__).parent.parent / 'backtest_reports'
                file_path = reports_dir / filename
                
                if not file_path.exists() or not file_path.is_file():
                    return jsonify({'error': 'Report not found'}), 404
                
                if not filename.endswith('.xlsx'):
                    return jsonify({'error': 'Invalid file type'}), 400
                
                return send_file(
                    file_path,
                    as_attachment=True,
                    download_name=filename,
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
            except Exception as e:
                logger.error(f"Error downloading report {filename}: {e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/reports/<filename>', methods=['DELETE'])
        def api_delete_report(filename):
            """Delete Excel report"""
            try:
                # Validate filename to prevent directory traversal
                if '..' in filename or '/' in filename or '\\' in filename:
                    return jsonify({'error': 'Invalid filename'}), 400
                
                reports_dir = Path(__file__).parent.parent / 'backtest_reports'
                file_path = reports_dir / filename
                
                if not file_path.exists() or not file_path.is_file():
                    return jsonify({'error': 'Report not found'}), 404
                
                file_path.unlink()  # Delete file
                
                return jsonify({'status': 'success', 'message': f'Report {filename} deleted'})
            except Exception as e:
                logger.error(f"Error deleting report {filename}: {e}")
                return jsonify({'error': str(e)}), 500
        
        @self.app.route('/api/pairs/run-analysis', methods=['POST'])
        def api_pairs_run_analysis():
            """Run pairs analysis with specified parameters"""
            try:
                # Get parameters from request
                params = request.get_json() or {}
                lookback_period = params.get('lookback_period', 252)
                zscore_window = params.get('zscore_window', 20)
                correlation_threshold = params.get('correlation_threshold', 0.7)
                cointegration_test = params.get('cointegration_test', 'adf')
                
                logger.info(f"Running pairs analysis with params: {params}")
                
                # For now, return the existing analysis data
                # TODO: Implement real-time analysis with these parameters
                if hasattr(self.data_adapter, 'backtest_data'):
                    backtest_data = getattr(self.data_adapter, 'backtest_data', {})
                    pairs = backtest_data.get('pairs', [])
                    
                    # Filter pairs based on correlation threshold
                    filtered_pairs = [p for p in pairs if p.get('metrics', {}).get('correlation', 0) >= correlation_threshold]
                    
                    analysis_result = {
                        'status': 'success',
                        'parameters': params,
                        'total_pairs_analyzed': len(pairs),
                        'pairs_results': filtered_pairs[:50],  # Limit results
                        'summary': {
                            'total_pairs': len(filtered_pairs),
                            'profitable_pairs': len([p for p in filtered_pairs if p.get('metrics', {}).get('total_return', 0) > 0]),
                            'avg_return': np.mean([p.get('metrics', {}).get('total_return', 0) for p in filtered_pairs]) if filtered_pairs else 0,
                            'avg_sharpe': np.mean([p.get('metrics', {}).get('sharpe_ratio', 0) for p in filtered_pairs]) if filtered_pairs else 0,
                        }
                    }
                    
                    return jsonify(analysis_result)
                else:
                    return jsonify({
                        'status': 'success',
                        'parameters': params,
                        'total_pairs_analyzed': 0,
                        'pairs_results': [],
                        'summary': {
                            'total_pairs': 0,
                            'profitable_pairs': 0,
                            'avg_return': 0,
                            'avg_sharpe': 0,
                        }
                    })
                    
            except Exception as e:
                logger.error(f"Error running pairs analysis: {e}")
                return jsonify({
                    'status': 'error',
                    'message': str(e),
                    'pairs_results': [],
                    'summary': {}
                }), 500

    def _register_static_routes(self):
        """Register static file routes"""
        
        @self.app.route('/static/<path:filename>')
        def static_files(filename):
            """Serve static files"""
            try:
                static_dir = Path(__file__).parent / 'static'
                return send_from_directory(static_dir, filename)
            except Exception as e:
                logger.error(f"Error serving static file {filename}: {e}")
                return jsonify({'error': 'File not found'}), 404
    
    def _setup_error_handlers(self):
        """Setup error handlers"""
        
        @self.app.errorhandler(404)
        def not_found(error):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Endpoint not found'}), 404
            else:
                return render_template('error.html', 
                                     error='Page not found', 
                                     code=404), 404
        
        @self.app.errorhandler(500)
        def internal_error(error):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Internal server error'}), 500
            else:
                return render_template('error.html', 
                                     error='Internal server error', 
                                     code=500), 500
        
        @self.app.errorhandler(Exception)
        def handle_exception(e):
            logger.error(f"Unhandled exception: {e}")
            if request.path.startswith('/api/'):
                return jsonify({'error': 'An unexpected error occurred'}), 500
            else:
                return render_template('error.html', 
                                     error='An unexpected error occurred', 
                                     code=500), 500
