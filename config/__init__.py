"""
Configuration Module for Trading System
======================================

Provides both legacy file-based configuration and new environment-based configuration.
The production-ready approach uses environment variables for all configuration.

Key Features:
- Environment-first configuration (production-ready)
- No file system dependencies during runtime
- Fast startup with minimal I/O operations
- Comprehensive validation
- Backward compatibility with legacy file-based config
- Configuration health monitoring

Usage:
    from config import get_config
    
    config = get_config()
    print(f"Trading pairs: {config.pairs}")
    print(f"Data provider: {config.data_provider}")

Environment Variables:
    All configuration parameters are driven by environment variables.
    See the .env file for available parameters.
"""

import os
import logging
from typing import Union

# Import both configuration systems
try:
    from .production_config import TradingConfig as ProductionConfig, get_config as get_production_config, force_config_update
    from .validator import ConfigValidator
    PRODUCTION_CONFIG_AVAILABLE = True
except ImportError as e:
    logging.error(f"Failed to import production config: {e}")
    PRODUCTION_CONFIG_AVAILABLE = False

# Legacy imports for backward compatibility
try:
    from .legacy_config import TradingConfig as LegacyConfig
    LEGACY_CONFIG_AVAILABLE = True
except ImportError:
    LEGACY_CONFIG_AVAILABLE = False

logger = logging.getLogger(__name__)

# Determine which configuration system to use
USE_PRODUCTION_CONFIG = os.getenv('USE_PRODUCTION_CONFIG', 'true').lower() == 'true'

if USE_PRODUCTION_CONFIG and PRODUCTION_CONFIG_AVAILABLE:
    # Use the new production-ready configuration
    TradingConfig = ProductionConfig
    logger.debug("Using production-ready environment-based configuration")
elif LEGACY_CONFIG_AVAILABLE:
    # Use legacy file-based configuration
    TradingConfig = LegacyConfig
    logger.debug("Using legacy file-based configuration")
else:
    # Fallback to a basic configuration if neither is available
    logger.error("Neither production nor legacy config available, using fallback")
    
    class FallbackConfig:
        def __init__(self):
            self.data_provider = os.getenv('DATA_PROVIDER', 'ctrader')
            self.broker = os.getenv('BROKER', 'ctrader')
            self.pairs = ['BTCUSD-ETHUSD', 'BTCUSD-SOLUSD', 'ETHUSD-SOLUSD']
            self.log_level = os.getenv('LOG_LEVEL', 'WARNING')
    
    TradingConfig = FallbackConfig


def get_config():
    """
    Get configuration instance.
    Returns production config by default, legacy config if specified.
    
    Returns:
        TradingConfig: Validated configuration instance
        
    Raises:
        ValueError: If configuration validation fails
    """
    if USE_PRODUCTION_CONFIG and PRODUCTION_CONFIG_AVAILABLE:
        config = get_production_config()
        
        # Validate configuration if validator is available
        if 'ConfigValidator' in globals():
            is_valid, errors = ConfigValidator.validate_config(config)
            if not is_valid:
                logger.error("Configuration validation failed!")
                for error in errors:
                    logger.error(f"  - {error}")
                raise ValueError(f"Invalid configuration: {len(errors)} error(s). Check your environment variables.")
        
        # Log configuration summary in debug mode or if explicitly requested
        if hasattr(config, 'log_level') and config.log_level.upper() == 'DEBUG':
            if 'ConfigValidator' in globals():
                ConfigValidator.log_config_summary(config)
        else:
            # Always log a brief summary
            logger.info(f"Configuration loaded: {config.data_provider} data, {config.broker} broker, {len(config.pairs)} pairs")
        
        return config
    elif LEGACY_CONFIG_AVAILABLE:
        # Legacy configuration
        from .legacy_config import TradingConfig as LegacyTradingConfig
        return LegacyTradingConfig()
    else:
        # Fallback configuration
        return TradingConfig()


def validate_environment() -> bool:
    """
    Validate that all required environment variables are set.
    
    Returns:
        bool: True if all required variables are set
    """
    if not PRODUCTION_CONFIG_AVAILABLE or 'ConfigValidator' not in globals():
        logger.warning("Configuration validation not available")
        return True
    
    is_valid, missing_required, missing_optional = ConfigValidator.validate_environment_variables()
    
    if not is_valid:
        logger.error("Environment validation failed!")
        logger.error("Set the following required environment variables in your .env file:")
        for var in missing_required:
            logger.error(f"  {var}=<value>")
        return False
    
    if missing_optional:
        logger.info(f"Using defaults for {len(missing_optional)} optional environment variables")
        if logger.isEnabledFor(logging.DEBUG):
            for var in missing_optional:
                logger.debug(f"  {var} (using default)")
    
    return True


def get_config_health_check():
    """
    Get configuration health check data for monitoring.
    
    Returns:
        dict: Health check information including validation status,
              configuration summary, and risk assessment
    """
    try:
        config = get_config()
        if PRODUCTION_CONFIG_AVAILABLE and 'ConfigValidator' in globals():
            return ConfigValidator.get_config_health_check(config)
        else:
            return {
                "validation": {"is_valid": True, "error_count": 0, "errors": []},
                "configuration": {"data_provider": config.data_provider, "broker": config.broker},
                "risk_assessment": {"risk_level": "UNKNOWN"},
                "system": {"log_level": getattr(config, 'log_level', 'WARNING')}
            }
    except Exception as e:
        return {
            "validation": {
                "is_valid": False,
                "error_count": 1,
                "errors": [str(e)]
            },
            "configuration": {},
            "risk_assessment": {},
            "system": {}
        }


def log_config_summary():
    """Log detailed configuration summary for debugging"""
    try:
        config = get_config()
        if PRODUCTION_CONFIG_AVAILABLE and 'ConfigValidator' in globals():
            ConfigValidator.log_config_summary(config)
        else:
            logger.info(f"Configuration: {config.data_provider} data, {config.broker} broker")
    except Exception as e:
        logger.error(f"Failed to log configuration summary: {e}")


def reload_config():
    """
    Force reload configuration from environment variables.
    Useful for configuration changes during runtime.
    
    Returns:
        TradingConfig: Newly loaded configuration
    """
    if PRODUCTION_CONFIG_AVAILABLE:
        force_config_update()
    return get_config()


# Backward compatibility functions
def update_config(**kwargs):
    """
    Update configuration (legacy compatibility).
    Note: In production config, this doesn't persist changes.
    Use environment variables for persistent configuration.
    """
    logger.warning("update_config() called but production config is read-only")
    logger.warning("Use environment variables to change configuration")


def reset_config_to_defaults():
    """Reset configuration to defaults (legacy compatibility)"""
    logger.warning("reset_config_to_defaults() called but production config uses environment defaults")
    logger.info("To reset to defaults, remove environment variables from .env file")


# Provide force_config_update for backward compatibility
if PRODUCTION_CONFIG_AVAILABLE:
    # Import the actual function
    pass  # Already imported above
else:
    # Provide a dummy function
    def force_config_update():
        logger.warning("force_config_update not available without production config")


# Export main classes and functions
__all__ = [
    'TradingConfig',
    'get_config', 
    'force_config_update',
    'validate_environment',
    'get_config_health_check',
    'log_config_summary',
    'reload_config',
    # Legacy compatibility
    'update_config',
    'reset_config_to_defaults'
]

# Add ConfigValidator to exports if available
if PRODUCTION_CONFIG_AVAILABLE and 'ConfigValidator' in globals():
    __all__.append('ConfigValidator')
