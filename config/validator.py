"""
Configuration Validator
=======================

Validates configuration for production deployment and provides monitoring capabilities.
"""

import logging
import re
from typing import List, Tuple, Dict, Any
from dataclasses import asdict

logger = logging.getLogger(__name__)


class ConfigValidator:
    """Validate configuration for production deployment"""
    
    @staticmethod
    def validate_config(config) -> Tuple[bool, List[str]]:
        """
        Validate configuration and return (is_valid, errors)
        
        Args:
            config: TradingConfig instance
            
        Returns:
            Tuple of (is_valid: bool, errors: List[str])
        """
        errors = []
        
        # Validate required fields
        if not config.data_provider:
            errors.append("DATA_PROVIDER is required")
        
        if not config.broker:
            errors.append("BROKER is required")
            
        if not config.pairs:
            errors.append("TRADING_PAIRS cannot be empty")
        
        # Validate data provider
        valid_providers = ['ctrader', 'mt5']
        if config.data_provider.lower() not in valid_providers:
            errors.append(f"DATA_PROVIDER must be one of: {valid_providers}, got: {config.data_provider}")
        
        # Validate broker
        valid_brokers = ['ctrader', 'mt5']
        if config.broker.lower() not in valid_brokers:
            errors.append(f"BROKER must be one of: {valid_brokers}, got: {config.broker}")
        
        # Validate interval format
        valid_intervals = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']
        if config.interval not in valid_intervals:
            errors.append(f"TRADING_INTERVAL must be one of: {valid_intervals}, got: {config.interval}")
        
        # Validate date format (YYYY-MM-DD)
        date_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}$')
        if not date_pattern.match(config.start_date):
            errors.append(f"TRADING_START_DATE must be in YYYY-MM-DD format, got: {config.start_date}")
        
        if config.end_date and not date_pattern.match(config.end_date):
            errors.append(f"TRADING_END_DATE must be in YYYY-MM-DD format, got: {config.end_date}")
        
        # Validate strategy parameters
        if config.z_entry <= 0:
            errors.append(f"Z_ENTRY must be positive, got: {config.z_entry}")
            
        if config.z_exit <= 0:
            errors.append(f"Z_EXIT must be positive, got: {config.z_exit}")
            
        if config.z_period <= 0:
            errors.append(f"Z_PERIOD must be positive, got: {config.z_period}")
            
        if config.z_entry <= config.z_exit:
            errors.append(f"Z_ENTRY ({config.z_entry}) must be greater than Z_EXIT ({config.z_exit})")
        
        # Validate risk management parameters
        if config.max_position_size <= 0:
            errors.append(f"MAX_POSITION_SIZE must be positive, got: {config.max_position_size}")
            
        if config.max_open_positions <= 0:
            errors.append(f"MAX_OPEN_POSITIONS must be positive, got: {config.max_open_positions}")
            
        if config.initial_portfolio_value <= 0:
            errors.append(f"INITIAL_PORTFOLIO_VALUE must be positive, got: {config.initial_portfolio_value}")
        
        if config.take_profit_perc <= 0:
            errors.append(f"TAKE_PROFIT_PERC must be positive, got: {config.take_profit_perc}")
            
        if config.stop_loss_perc <= 0:
            errors.append(f"STOP_LOSS_PERC must be positive, got: {config.stop_loss_perc}")
        
        if config.max_commission_perc < 0 or config.max_commission_perc > 100:
            errors.append(f"MAX_COMMISSION_PERC must be between 0 and 100, got: {config.max_commission_perc}")
        
        if config.max_pair_drawdown_perc <= 0 or config.max_pair_drawdown_perc > 100:
            errors.append(f"MAX_PAIR_DRAWDOWN_PERC must be between 0 and 100, got: {config.max_pair_drawdown_perc}")
            
        if config.max_portfolio_drawdown_perc <= 0 or config.max_portfolio_drawdown_perc > 100:
            errors.append(f"MAX_PORTFOLIO_DRAWDOWN_PERC must be between 0 and 100, got: {config.max_portfolio_drawdown_perc}")
        
        # Validate pairs format
        for pair in config.pairs:
            if '-' not in pair:
                errors.append(f"Invalid pair format: {pair} (should be 'SYMBOL1-SYMBOL2')")
            else:
                parts = pair.split('-')
                if len(parts) != 2:
                    errors.append(f"Invalid pair format: {pair} (should have exactly one '-')")
                elif not all(part.strip() for part in parts):
                    errors.append(f"Invalid pair format: {pair} (symbols cannot be empty)")
        
        # Validate statistical test parameters
        if config.enable_adf:
            if config.max_adf_pval <= 0 or config.max_adf_pval > 1:
                errors.append(f"MAX_ADF_PVAL must be between 0 and 1, got: {config.max_adf_pval}")
            
            if config.adf_period <= 0:
                errors.append(f"ADF_PERIOD must be positive, got: {config.adf_period}")
        
        if config.enable_johansen:
            valid_crit_levels = [90, 95, 99]
            if config.johansen_crit_level not in valid_crit_levels:
                errors.append(f"JOHANSEN_CRIT_LEVEL must be one of {valid_crit_levels}, got: {config.johansen_crit_level}")
        
        if config.enable_correlation:
            if config.min_corr < -1 or config.min_corr > 1:
                errors.append(f"MIN_CORR must be between -1 and 1, got: {config.min_corr}")
            
            if config.corr_period <= 0:
                errors.append(f"CORR_PERIOD must be positive, got: {config.corr_period}")
        
        if config.enable_vol_ratio:
            if config.vol_ratio_max <= 0:
                errors.append(f"VOL_RATIO_MAX must be positive, got: {config.vol_ratio_max}")
        
        # Validate system parameters
        if config.max_workers <= 0:
            errors.append(f"MAX_WORKERS must be positive, got: {config.max_workers}")
        
        # Validate log level
        valid_log_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if config.log_level.upper() not in valid_log_levels:
            errors.append(f"LOG_LEVEL must be one of {valid_log_levels}, got: {config.log_level}")
        
        # Validate MT5 parameters if MT5 is being used
        if config.data_provider.lower() == 'mt5' or config.broker.lower() == 'mt5':
            if not config.mt5_server:
                errors.append("MT5_SERVER is required when using MT5")
        
        is_valid = len(errors) == 0
        
        if not is_valid:
            logger.error("Configuration validation failed:")
            for error in errors:
                logger.error(f"  - {error}")
        else:
            logger.info("Configuration validation passed")
            
        return is_valid, errors
    
    @staticmethod
    def log_config_summary(config):
        """Log configuration summary for monitoring"""
        logger.info("=" * 60)
        logger.info("TRADING CONFIGURATION SUMMARY")
        logger.info("=" * 60)
        
        # Core configuration
        logger.info("📊 Core Configuration:")
        logger.info(f"  Data Provider: {config.data_provider}")
        logger.info(f"  Broker: {config.broker}")
        logger.info(f"  Interval: {config.interval}")
        logger.info(f"  Start Date: {config.start_date}")
        if config.end_date:
            logger.info(f"  End Date: {config.end_date}")
        
        # Trading pairs
        logger.info(f"\n💱 Trading Pairs ({len(config.pairs)}):")
        for i, pair in enumerate(config.pairs, 1):
            logger.info(f"  {i:2d}. {pair}")
        
        # Strategy parameters
        logger.info(f"\n📈 Strategy Parameters:")
        logger.info(f"  Z-Score Entry: {config.z_entry}")
        logger.info(f"  Z-Score Exit: {config.z_exit}")
        logger.info(f"  Z-Score Period: {config.z_period}")
        logger.info(f"  Dynamic Z-Score: {config.dynamic_z}")
        
        # Statistical tests
        tests_enabled = []
        if config.enable_adf:
            tests_enabled.append(f"ADF (p-val < {config.max_adf_pval})")
        if config.enable_johansen:
            tests_enabled.append(f"Johansen ({config.johansen_crit_level}%)")
        if config.enable_correlation:
            tests_enabled.append(f"Correlation (> {config.min_corr})")
        if config.enable_vol_ratio:
            tests_enabled.append(f"Vol Ratio (< {config.vol_ratio_max})")
        
        if tests_enabled:
            logger.info(f"\n🧪 Statistical Tests:")
            for test in tests_enabled:
                logger.info(f"  ✅ {test}")
        else:
            logger.info(f"\n🧪 Statistical Tests: None enabled")
        
        # Risk management
        logger.info(f"\n⚖️ Risk Management:")
        logger.info(f"  Take Profit: {config.take_profit_perc}%")
        logger.info(f"  Stop Loss: {config.stop_loss_perc}%")
        logger.info(f"  Trailing Stop: {config.trailing_stop_perc}%")
        logger.info(f"  Max Position Size: ${config.max_position_size:,.0f}")
        logger.info(f"  Max Open Positions: {config.max_open_positions}")
        logger.info(f"  Max Portfolio Exposure: ${config.max_monetary_exposure:,.0f}")
        logger.info(f"  Max Commission: {config.max_commission_perc}%")
        
        # Portfolio
        logger.info(f"\n💰 Portfolio:")
        logger.info(f"  Initial Value: ${config.initial_portfolio_value:,.0f}")
        logger.info(f"  Max Pair Drawdown: {config.max_pair_drawdown_perc}%")
        logger.info(f"  Max Portfolio Drawdown: {config.max_portfolio_drawdown_perc}%")
        
        # System
        logger.info(f"\n⚙️ System:")
        logger.info(f"  Log Level: {config.log_level}")
        logger.info(f"  Multiprocessing: {config.use_multiprocessing}")
        if config.use_multiprocessing:
            logger.info(f"  Max Workers: {config.max_workers}")
        
        logger.info("=" * 60)
    
    @staticmethod
    def get_config_health_check(config) -> Dict[str, Any]:
        """
        Get configuration health check data for monitoring
        
        Returns:
            Dictionary with health check information
        """
        is_valid, errors = ConfigValidator.validate_config(config)
        
        # Count enabled features
        statistical_tests = sum([
            config.enable_adf,
            config.enable_johansen,
            config.enable_correlation,
            config.enable_vol_ratio
        ])
        
        # Risk level assessment
        risk_level = "LOW"
        if config.max_portfolio_drawdown_perc > 15:
            risk_level = "HIGH"
        elif config.max_portfolio_drawdown_perc > 10:
            risk_level = "MEDIUM"
        
        return {
            "validation": {
                "is_valid": is_valid,
                "error_count": len(errors),
                "errors": errors
            },
            "configuration": {
                "data_provider": config.data_provider,
                "broker": config.broker,
                "pair_count": len(config.pairs),
                "statistical_tests_enabled": statistical_tests,
                "multiprocessing_enabled": config.use_multiprocessing
            },
            "risk_assessment": {
                "risk_level": risk_level,
                "max_portfolio_drawdown": config.max_portfolio_drawdown_perc,
                "max_position_size": config.max_position_size,
                "max_open_positions": config.max_open_positions
            },
            "system": {
                "log_level": config.log_level,
                "max_workers": config.max_workers if config.use_multiprocessing else 1
            }
        }
    
    @staticmethod
    def validate_environment_variables() -> Tuple[bool, List[str], List[str]]:
        """
        Validate that all required environment variables are set
        
        Returns:
            Tuple of (is_valid, missing_required, missing_optional)
        """
        import os
        
        # Required environment variables
        required_vars = [
            'DATA_PROVIDER',
            'BROKER',
            'TRADING_PAIRS'
        ]
        
        # Optional but recommended environment variables
        optional_vars = [
            'TRADING_INTERVAL',
            'TRADING_START_DATE',
            'Z_ENTRY',
            'Z_EXIT',
            'MAX_POSITION_SIZE',
            'MAX_OPEN_POSITIONS',
            'INITIAL_PORTFOLIO_VALUE',
            'LOG_LEVEL'
        ]
        
        missing_required = []
        missing_optional = []
        
        for var in required_vars:
            if not os.getenv(var):
                missing_required.append(var)
        
        for var in optional_vars:
            if not os.getenv(var):
                missing_optional.append(var)
        
        is_valid = len(missing_required) == 0
        
        if missing_required:
            logger.error("Missing required environment variables:")
            for var in missing_required:
                logger.error(f"  - {var}")
        
        if missing_optional:
            logger.warning("Missing optional environment variables (using defaults):")
            for var in missing_optional:
                logger.warning(f"  - {var}")
        
        return is_valid, missing_required, missing_optional
