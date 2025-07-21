"""
Configuration for Enhanced State Management System

This module provides configuration management for the database-backed
state management system with environment variable support.
"""

import os
import json
import logging
from typing import Dict, Any, Optional, Union
from dataclasses import dataclass, asdict
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DatabaseConfig:
    """Database configuration for state management."""
    database_type: str = 'influxdb'
    
    # InfluxDB Configuration
    influxdb_url: str = 'http://localhost:8086'
    influxdb_token: str = ''
    influxdb_org: str = 'trading-org'
    influxdb_bucket: str = 'trading-states'
    
    # PostgreSQL Configuration
    postgresql_host: str = 'localhost'
    postgresql_port: int = 5432
    postgresql_database: str = 'trading_states'
    postgresql_username: str = 'postgres'
    postgresql_password: str = ''
    
    # Connection settings
    connection_timeout: int = 30
    retry_attempts: int = 3
    retry_delay: float = 1.0
    
    def get_connection_config(self) -> Dict[str, Any]:
        """Get connection configuration for the selected database type."""
        if self.database_type == 'influxdb':
            return {
                'url': self.influxdb_url,
                'token': self.influxdb_token,
                'org': self.influxdb_org,
                'bucket': self.influxdb_bucket
            }
        elif self.database_type == 'postgresql':
            return {
                'connection_string': f"postgresql://{self.postgresql_username}:{self.postgresql_password}@{self.postgresql_host}:{self.postgresql_port}/{self.postgresql_database}"
            }
        else:
            raise ValueError(f"Unsupported database type: {self.database_type}")


@dataclass
class APIConfig:
    """API configuration for state management."""
    enabled: bool = False
    host: str = '0.0.0.0'
    port: int = 8000
    cors_origins: list = None
    api_key: Optional[str] = None
    rate_limit_per_minute: int = 100
    
    def __post_init__(self):
        if self.cors_origins is None:
            self.cors_origins = ["*"]


@dataclass
class CacheConfig:
    """Cache configuration for state management."""
    enabled: bool = True
    ttl_seconds: int = 300  # 5 minutes
    max_size: int = 1000
    cleanup_interval: int = 60  # 1 minute


@dataclass
class StateManagementConfig:
    """Complete configuration for enhanced state management."""
    database: DatabaseConfig
    api: APIConfig
    cache: CacheConfig
    
    # General settings
    enable_validation: bool = True
    enable_file_fallback: bool = True
    fallback_directory: str = './state_fallback'
    log_level: str = 'INFO'
    backup_retention_days: int = 30
    
    # Migration settings
    migration_batch_size: int = 100
    migration_user_id: str = 'system_migration'
    
    @classmethod
    def from_env(cls, env_prefix: str = 'TRADING_STATE_') -> 'StateManagementConfig':
        """Create configuration from environment variables."""
        
        # Database configuration
        db_config = DatabaseConfig(
            database_type=os.getenv(f'{env_prefix}DB_TYPE', 'influxdb'),
            
            # InfluxDB
            influxdb_url=os.getenv(f'{env_prefix}INFLUXDB_URL', 'http://localhost:8086'),
            influxdb_token=os.getenv(f'{env_prefix}INFLUXDB_TOKEN', ''),
            influxdb_org=os.getenv(f'{env_prefix}INFLUXDB_ORG', 'trading-org'),
            influxdb_bucket=os.getenv(f'{env_prefix}INFLUXDB_BUCKET', 'trading-states'),
            
            # PostgreSQL
            postgresql_host=os.getenv(f'{env_prefix}POSTGRES_HOST', 'localhost'),
            postgresql_port=int(os.getenv(f'{env_prefix}POSTGRES_PORT', '5432')),
            postgresql_database=os.getenv(f'{env_prefix}POSTGRES_DB', 'trading_states'),
            postgresql_username=os.getenv(f'{env_prefix}POSTGRES_USER', 'postgres'),
            postgresql_password=os.getenv(f'{env_prefix}POSTGRES_PASSWORD', ''),
            
            # Connection
            connection_timeout=int(os.getenv(f'{env_prefix}CONNECTION_TIMEOUT', '30')),
            retry_attempts=int(os.getenv(f'{env_prefix}RETRY_ATTEMPTS', '3')),
            retry_delay=float(os.getenv(f'{env_prefix}RETRY_DELAY', '1.0'))
        )
        
        # API configuration
        api_config = APIConfig(
            enabled=os.getenv(f'{env_prefix}API_ENABLED', 'false').lower() == 'true',
            host=os.getenv(f'{env_prefix}API_HOST', '0.0.0.0'),
            port=int(os.getenv(f'{env_prefix}API_PORT', '8000')),
            cors_origins=os.getenv(f'{env_prefix}API_CORS_ORIGINS', '*').split(','),
            api_key=os.getenv(f'{env_prefix}API_KEY'),
            rate_limit_per_minute=int(os.getenv(f'{env_prefix}API_RATE_LIMIT', '100'))
        )
        
        # Cache configuration
        cache_config = CacheConfig(
            enabled=os.getenv(f'{env_prefix}CACHE_ENABLED', 'true').lower() == 'true',
            ttl_seconds=int(os.getenv(f'{env_prefix}CACHE_TTL', '300')),
            max_size=int(os.getenv(f'{env_prefix}CACHE_MAX_SIZE', '1000')),
            cleanup_interval=int(os.getenv(f'{env_prefix}CACHE_CLEANUP_INTERVAL', '60'))
        )
        
        return cls(
            database=db_config,
            api=api_config,
            cache=cache_config,
            
            # General settings
            enable_validation=os.getenv(f'{env_prefix}ENABLE_VALIDATION', 'true').lower() == 'true',
            enable_file_fallback=os.getenv(f'{env_prefix}ENABLE_FILE_FALLBACK', 'true').lower() == 'true',
            fallback_directory=os.getenv(f'{env_prefix}FALLBACK_DIR', './state_fallback'),
            log_level=os.getenv(f'{env_prefix}LOG_LEVEL', 'INFO'),
            backup_retention_days=int(os.getenv(f'{env_prefix}BACKUP_RETENTION_DAYS', '30')),
            
            # Migration settings
            migration_batch_size=int(os.getenv(f'{env_prefix}MIGRATION_BATCH_SIZE', '100')),
            migration_user_id=os.getenv(f'{env_prefix}MIGRATION_USER_ID', 'system_migration')
        )
    
    @classmethod
    def from_file(cls, config_path: str) -> 'StateManagementConfig':
        """Load configuration from JSON file."""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            
            # Create configurations from dict
            db_config = DatabaseConfig(**config_data.get('database', {}))
            api_config = APIConfig(**config_data.get('api', {}))
            cache_config = CacheConfig(**config_data.get('cache', {}))
            
            # Remove nested configs from main config
            main_config = {k: v for k, v in config_data.items() 
                          if k not in ['database', 'api', 'cache']}
            
            return cls(
                database=db_config,
                api=api_config,
                cache=cache_config,
                **main_config
            )
            
        except Exception as e:
            logger.error(f"Failed to load configuration from file {config_path}: {e}")
            raise
    
    def to_file(self, config_path: str):
        """Save configuration to JSON file."""
        try:
            config_dict = {
                'database': asdict(self.database),
                'api': asdict(self.api),
                'cache': asdict(self.cache),
                'enable_validation': self.enable_validation,
                'enable_file_fallback': self.enable_file_fallback,
                'fallback_directory': self.fallback_directory,
                'log_level': self.log_level,
                'backup_retention_days': self.backup_retention_days,
                'migration_batch_size': self.migration_batch_size,
                'migration_user_id': self.migration_user_id
            }
            
            # Ensure directory exists
            config_dir = Path(config_path).parent
            config_dir.mkdir(parents=True, exist_ok=True)
            
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config_dict, f, indent=4, ensure_ascii=False)
            
            logger.info(f"Configuration saved to {config_path}")
            
        except Exception as e:
            logger.error(f"Failed to save configuration to file {config_path}: {e}")
            raise
    
    def validate(self) -> bool:
        """Validate the configuration."""
        try:
            # Validate database type
            if self.database.database_type not in ['influxdb', 'postgresql']:
                raise ValueError(f"Invalid database type: {self.database.database_type}")
            
            # Validate database-specific configuration
            if self.database.database_type == 'influxdb':
                if not self.database.influxdb_url:
                    raise ValueError("InfluxDB URL is required")
                if not self.database.influxdb_token:
                    logger.warning("InfluxDB token is empty")
                if not self.database.influxdb_org:
                    raise ValueError("InfluxDB organization is required")
                if not self.database.influxdb_bucket:
                    raise ValueError("InfluxDB bucket is required")
            
            elif self.database.database_type == 'postgresql':
                if not self.database.postgresql_host:
                    raise ValueError("PostgreSQL host is required")
                if not self.database.postgresql_database:
                    raise ValueError("PostgreSQL database name is required")
                if not self.database.postgresql_username:
                    raise ValueError("PostgreSQL username is required")
            
            # Validate API configuration
            if self.api.enabled:
                if not (1 <= self.api.port <= 65535):
                    raise ValueError(f"Invalid API port: {self.api.port}")
                if self.api.rate_limit_per_minute <= 0:
                    raise ValueError("API rate limit must be positive")
            
            # Validate cache configuration
            if self.cache.enabled:
                if self.cache.ttl_seconds <= 0:
                    raise ValueError("Cache TTL must be positive")
                if self.cache.max_size <= 0:
                    raise ValueError("Cache max size must be positive")
            
            # Validate general settings
            if self.backup_retention_days <= 0:
                raise ValueError("Backup retention days must be positive")
            
            logger.info("Configuration validation passed")
            return True
            
        except Exception as e:
            logger.error(f"Configuration validation failed: {e}")
            return False
    
    def get_log_config(self) -> Dict[str, Any]:
        """Get logging configuration."""
        return {
            'level': getattr(logging, self.log_level.upper()),
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            'handlers': [
                {
                    'type': 'file',
                    'filename': os.path.join(self.fallback_directory, 'state_management.log'),
                    'encoding': 'utf-8'
                },
                {
                    'type': 'console'
                }
            ]
        }


def create_default_config() -> StateManagementConfig:
    """Create a default configuration."""
    return StateManagementConfig(
        database=DatabaseConfig(),
        api=APIConfig(),
        cache=CacheConfig()
    )


def load_config(config_path: Optional[str] = None, 
               env_prefix: str = 'TRADING_STATE_') -> StateManagementConfig:
    """
    Load configuration with fallback priority:
    1. From file if specified
    2. From environment variables
    3. Default configuration
    """
    try:
        # Try to load from file first
        if config_path and os.path.exists(config_path):
            logger.info(f"Loading configuration from file: {config_path}")
            config = StateManagementConfig.from_file(config_path)
        else:
            # Load from environment variables
            logger.info("Loading configuration from environment variables")
            config = StateManagementConfig.from_env(env_prefix)
        
        # Validate configuration
        if not config.validate():
            logger.warning("Configuration validation failed, using defaults where possible")
        
        return config
        
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        logger.info("Using default configuration")
        return create_default_config()


def setup_logging(config: StateManagementConfig):
    """Setup logging based on configuration."""
    try:
        log_config = config.get_log_config()
        
        # Ensure log directory exists
        if config.fallback_directory:
            os.makedirs(config.fallback_directory, exist_ok=True)
        
        # Configure logging
        logging.basicConfig(
            level=log_config['level'],
            format=log_config['format'],
            handlers=[
                logging.FileHandler(
                    os.path.join(config.fallback_directory, 'state_management.log'),
                    encoding='utf-8'
                ),
                logging.StreamHandler()
            ]
        )
        
        # Set specific logger levels
        logging.getLogger('asyncio').setLevel(logging.WARNING)
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        
        logger.info(f"Logging configured with level: {config.log_level}")
        
    except Exception as e:
        print(f"Failed to setup logging: {e}")
        # Fallback to basic logging
        logging.basicConfig(level=logging.INFO)


# Example configuration files

def create_example_configs():
    """Create example configuration files."""
    
    # Development configuration
    dev_config = StateManagementConfig(
        database=DatabaseConfig(
            database_type='influxdb',
            influxdb_url='http://localhost:8086',
            influxdb_token='dev-token-here',
            influxdb_org='trading-dev',
            influxdb_bucket='trading-states-dev'
        ),
        api=APIConfig(
            enabled=True,
            host='localhost',
            port=8000,
            cors_origins=['http://localhost:3000', 'http://localhost:8080']
        ),
        cache=CacheConfig(
            enabled=True,
            ttl_seconds=60,  # Shorter TTL for development
            max_size=100
        ),
        enable_validation=True,
        enable_file_fallback=True,
        fallback_directory='./dev_state_fallback',
        log_level='DEBUG'
    )
    
    # Production configuration
    prod_config = StateManagementConfig(
        database=DatabaseConfig(
            database_type='postgresql',
            postgresql_host='prod-db-host',
            postgresql_database='trading_states_prod',
            postgresql_username='trading_user',
            postgresql_password='${POSTGRES_PASSWORD}',  # Use env var
            connection_timeout=60,
            retry_attempts=5
        ),
        api=APIConfig(
            enabled=True,
            host='0.0.0.0',
            port=8000,
            cors_origins=['https://trading-dashboard.company.com'],
            api_key='${API_KEY}',  # Use env var
            rate_limit_per_minute=1000
        ),
        cache=CacheConfig(
            enabled=True,
            ttl_seconds=600,  # 10 minutes
            max_size=10000,
            cleanup_interval=300  # 5 minutes
        ),
        enable_validation=True,
        enable_file_fallback=False,  # No fallback in production
        log_level='INFO',
        backup_retention_days=90
    )
    
    return {
        'development': dev_config,
        'production': prod_config
    }


if __name__ == "__main__":
    # Example usage
    
    # Create example configs
    configs = create_example_configs()
    
    # Save example configs
    for env_name, config in configs.items():
        config.to_file(f"config_{env_name}.json")
        print(f"Created {env_name} configuration file")
    
    # Load and validate config
    config = load_config()
    print(f"Loaded configuration with database type: {config.database.database_type}")
    
    # Setup logging
    setup_logging(config)
    print("Logging configured")
