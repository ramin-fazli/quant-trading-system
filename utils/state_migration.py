"""
Migration Utility for Trading State Management

This module provides utilities to migrate from the existing file-based
state management to the new database-backed system.
"""

import os
import json
import logging
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pathlib import Path

from .state_manager import StateManager, TradingStateManager
from .enhanced_state_manager import EnhancedTradingStateManager
from .state_database import StateType, StateOperationType

logger = logging.getLogger(__name__)


class StateMigrator:
    """Utility class for migrating states from file-based to database-backed storage."""
    
    def __init__(self, 
                 source_file_path: str,
                 target_manager: EnhancedTradingStateManager,
                 user_id: str = "migration"):
        """
        Initialize the state migrator.
        
        Args:
            source_file_path: Path to the source state file
            target_manager: Target enhanced state manager
            user_id: User ID for migration audit trail
        """
        self.source_file_path = source_file_path
        self.target_manager = target_manager
        self.user_id = user_id
        self.migration_log: List[Dict[str, Any]] = []
        
    def migrate_trading_state(self, backup_original: bool = True) -> bool:
        """
        Migrate trading state from file to database.
        
        Args:
            backup_original: Whether to create a backup of the original file
            
        Returns:
            bool: True if migration successful
        """
        try:
            logger.info(f"Starting migration from {self.source_file_path}")
            
            # Initialize target manager
            if not self.target_manager.initialize():
                logger.error("Failed to initialize target state manager")
                return False
            
            # Load source state
            source_manager = TradingStateManager(self.source_file_path)
            source_state = source_manager.load_trading_state()
            
            if not source_state:
                logger.warning("No source state found to migrate")
                return True  # Nothing to migrate is not an error
            
            # Create backup if requested
            if backup_original and os.path.exists(self.source_file_path):
                backup_path = f"{self.source_file_path}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                import shutil
                shutil.copy2(self.source_file_path, backup_path)
                logger.info(f"Created backup: {backup_path}")
            
            # Migrate data
            success = True
            
            # Migrate active positions
            active_positions = source_state.get('active_positions', {})
            for pair, position_data in active_positions.items():
                if self._migrate_position(pair, position_data):
                    self.migration_log.append({
                        'type': 'position',
                        'pair': pair,
                        'status': 'success',
                        'timestamp': datetime.now().isoformat()
                    })
                else:
                    success = False
                    self.migration_log.append({
                        'type': 'position',
                        'pair': pair,
                        'status': 'failed',
                        'timestamp': datetime.now().isoformat()
                    })
            
            # Migrate pair states
            pair_states = source_state.get('pair_states', {})
            for pair, pair_data in pair_states.items():
                if self._migrate_pair_state(pair, pair_data):
                    self.migration_log.append({
                        'type': 'pair_state',
                        'pair': pair,
                        'status': 'success',
                        'timestamp': datetime.now().isoformat()
                    })
                else:
                    success = False
                    self.migration_log.append({
                        'type': 'pair_state',
                        'pair': pair,
                        'status': 'failed',
                        'timestamp': datetime.now().isoformat()
                    })
            
            # Migrate portfolio data
            portfolio_data = {k: v for k, v in source_state.items() 
                            if k not in ['active_positions', 'pair_states', 'last_save_time']}
            
            if portfolio_data and self._migrate_portfolio_data(portfolio_data):
                self.migration_log.append({
                    'type': 'portfolio',
                    'status': 'success',
                    'timestamp': datetime.now().isoformat()
                })
            elif portfolio_data:
                success = False
                self.migration_log.append({
                    'type': 'portfolio',
                    'status': 'failed',
                    'timestamp': datetime.now().isoformat()
                })
            
            # Create migration snapshot
            snapshot_description = f"Migration from {self.source_file_path} on {datetime.now().isoformat()}"
            if self.target_manager.create_snapshot(snapshot_description):
                logger.info("Migration snapshot created successfully")
            else:
                logger.warning("Failed to create migration snapshot")
            
            # Log migration results
            self._log_migration_results()
            
            if success:
                logger.info("Migration completed successfully")
            else:
                logger.warning("Migration completed with some failures")
            
            return success
            
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            return False
    
    def _migrate_position(self, pair: str, position_data: Dict[str, Any]) -> bool:
        """Migrate a single position."""
        try:
            # Prepare position data for database storage
            db_position_data = self._prepare_position_for_db(position_data)
            
            # Use the target manager's async methods
            async def save_position():
                return await self.target_manager.db_state_manager.save_state(
                    state_id=f"position_{pair}",
                    state_type=StateType.POSITION,
                    data=db_position_data,
                    operation=StateOperationType.CREATE,
                    user_id=self.user_id,
                    description=f"Migrated position for {pair}"
                )
            
            version_id = self.target_manager._run_async(save_position())
            return version_id is not None
            
        except Exception as e:
            logger.error(f"Failed to migrate position {pair}: {e}")
            return False
    
    def _migrate_pair_state(self, pair: str, pair_data: Dict[str, Any]) -> bool:
        """Migrate a single pair state."""
        try:
            # Prepare pair data for database storage
            db_pair_data = self.target_manager._prepare_pair_data_for_db(pair_data)
            
            # Use the target manager's async methods
            async def save_pair_state():
                return await self.target_manager.db_state_manager.save_state(
                    state_id=f"pair_{pair}",
                    state_type=StateType.PAIR_STATE,
                    data=db_pair_data,
                    operation=StateOperationType.CREATE,
                    user_id=self.user_id,
                    description=f"Migrated pair state for {pair}"
                )
            
            version_id = self.target_manager._run_async(save_pair_state())
            return version_id is not None
            
        except Exception as e:
            logger.error(f"Failed to migrate pair state {pair}: {e}")
            return False
    
    def _migrate_portfolio_data(self, portfolio_data: Dict[str, Any]) -> bool:
        """Migrate portfolio data."""
        try:
            # Use the target manager's async methods
            async def save_portfolio():
                return await self.target_manager.db_state_manager.save_state(
                    state_id="portfolio_main",
                    state_type=StateType.PORTFOLIO,
                    data=portfolio_data,
                    operation=StateOperationType.CREATE,
                    user_id=self.user_id,
                    description="Migrated portfolio data"
                )
            
            version_id = self.target_manager._run_async(save_portfolio())
            return version_id is not None
            
        except Exception as e:
            logger.error(f"Failed to migrate portfolio data: {e}")
            return False
    
    def _prepare_position_for_db(self, position_data: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare position data for database storage."""
        db_data = {}
        
        for key, value in position_data.items():
            if isinstance(value, datetime):
                db_data[key] = value.isoformat()
            elif hasattr(value, 'isoformat'):  # datetime-like objects
                db_data[key] = value.isoformat()
            else:
                db_data[key] = value
        
        return db_data
    
    def _log_migration_results(self):
        """Log migration results summary."""
        total_items = len(self.migration_log)
        successful_items = len([item for item in self.migration_log if item['status'] == 'success'])
        failed_items = total_items - successful_items
        
        logger.info(f"Migration Results:")
        logger.info(f"  Total items: {total_items}")
        logger.info(f"  Successful: {successful_items}")
        logger.info(f"  Failed: {failed_items}")
        
        if failed_items > 0:
            logger.warning("Failed items:")
            for item in self.migration_log:
                if item['status'] == 'failed':
                    logger.warning(f"  {item['type']}: {item.get('pair', 'N/A')}")
    
    def get_migration_report(self) -> Dict[str, Any]:
        """Get detailed migration report."""
        total_items = len(self.migration_log)
        successful_items = len([item for item in self.migration_log if item['status'] == 'success'])
        failed_items = total_items - successful_items
        
        return {
            'total_items': total_items,
            'successful_items': successful_items,
            'failed_items': failed_items,
            'success_rate': successful_items / total_items if total_items > 0 else 0,
            'migration_log': self.migration_log,
            'source_file': self.source_file_path,
            'migration_time': datetime.now().isoformat()
        }


class BatchMigrator:
    """Utility for batch migration of multiple state files."""
    
    def __init__(self, target_manager: EnhancedTradingStateManager):
        self.target_manager = target_manager
        self.migration_reports: List[Dict[str, Any]] = []
    
    def migrate_directory(self, directory_path: str, 
                         file_pattern: str = "*.json",
                         backup_originals: bool = True) -> bool:
        """
        Migrate all state files in a directory.
        
        Args:
            directory_path: Path to directory containing state files
            file_pattern: Pattern to match state files
            backup_originals: Whether to backup original files
            
        Returns:
            bool: True if all migrations successful
        """
        try:
            directory = Path(directory_path)
            if not directory.exists():
                logger.error(f"Directory does not exist: {directory_path}")
                return False
            
            # Find all matching files
            state_files = list(directory.glob(file_pattern))
            if not state_files:
                logger.warning(f"No files found matching pattern {file_pattern} in {directory_path}")
                return True
            
            logger.info(f"Found {len(state_files)} files to migrate")
            
            overall_success = True
            
            for file_path in state_files:
                logger.info(f"Migrating {file_path}")
                
                migrator = StateMigrator(
                    str(file_path),
                    self.target_manager,
                    user_id=f"batch_migration_{datetime.now().strftime('%Y%m%d')}"
                )
                
                success = migrator.migrate_trading_state(backup_originals)
                if not success:
                    overall_success = False
                
                # Store migration report
                report = migrator.get_migration_report()
                report['file_path'] = str(file_path)
                self.migration_reports.append(report)
            
            # Log overall results
            self._log_batch_results()
            
            return overall_success
            
        except Exception as e:
            logger.error(f"Batch migration failed: {e}")
            return False
    
    def _log_batch_results(self):
        """Log batch migration results."""
        total_files = len(self.migration_reports)
        successful_files = len([r for r in self.migration_reports if r['failed_items'] == 0])
        
        total_items = sum(r['total_items'] for r in self.migration_reports)
        successful_items = sum(r['successful_items'] for r in self.migration_reports)
        
        logger.info(f"Batch Migration Results:")
        logger.info(f"  Files processed: {total_files}")
        logger.info(f"  Files successful: {successful_files}")
        logger.info(f"  Total items: {total_items}")
        logger.info(f"  Successful items: {successful_items}")
        logger.info(f"  Overall success rate: {successful_items/total_items:.1%}" if total_items > 0 else "  No items processed")
    
    def get_batch_report(self) -> Dict[str, Any]:
        """Get comprehensive batch migration report."""
        total_files = len(self.migration_reports)
        successful_files = len([r for r in self.migration_reports if r['failed_items'] == 0])
        
        total_items = sum(r['total_items'] for r in self.migration_reports)
        successful_items = sum(r['successful_items'] for r in self.migration_reports)
        
        return {
            'total_files': total_files,
            'successful_files': successful_files,
            'total_items': total_items,
            'successful_items': successful_items,
            'overall_success_rate': successful_items / total_items if total_items > 0 else 0,
            'file_reports': self.migration_reports,
            'batch_time': datetime.now().isoformat()
        }


def migrate_existing_state(source_file_path: str,
                          database_type: str = 'influxdb',
                          database_config: Optional[Dict[str, Any]] = None,
                          backup_original: bool = True) -> bool:
    """
    Convenience function to migrate an existing state file to database.
    
    Args:
        source_file_path: Path to the source state file
        database_type: Type of target database
        database_config: Database configuration
        backup_original: Whether to backup the original file
        
    Returns:
        bool: True if migration successful
    """
    try:
        # Create enhanced state manager
        from .enhanced_state_manager import create_enhanced_trading_state_manager
        
        target_manager = create_enhanced_trading_state_manager(
            database_type=database_type,
            database_config=database_config
        )
        
        # Create migrator
        migrator = StateMigrator(source_file_path, target_manager)
        
        # Perform migration
        success = migrator.migrate_trading_state(backup_original)
        
        # Print report
        report = migrator.get_migration_report()
        print(f"Migration Report:")
        print(f"  Source: {source_file_path}")
        print(f"  Success Rate: {report['success_rate']:.1%}")
        print(f"  Items Migrated: {report['successful_items']}/{report['total_items']}")
        
        # Cleanup
        target_manager.cleanup()
        
        return success
        
    except Exception as e:
        logger.error(f"Migration convenience function failed: {e}")
        return False


def validate_migration(source_file_path: str,
                      target_manager: EnhancedTradingStateManager) -> Dict[str, Any]:
    """
    Validate that migration was successful by comparing source and target data.
    
    Args:
        source_file_path: Path to the source state file
        target_manager: Target enhanced state manager
        
    Returns:
        Dict with validation results
    """
    try:
        # Load source data
        source_manager = TradingStateManager(source_file_path)
        source_state = source_manager.load_trading_state()
        
        # Load target data
        target_state = target_manager.load_trading_state()
        
        if not source_state and not target_state:
            return {'valid': True, 'message': 'Both source and target are empty'}
        
        if not source_state:
            return {'valid': False, 'message': 'Source state is empty but target has data'}
        
        if not target_state:
            return {'valid': False, 'message': 'Target state is empty but source has data'}
        
        # Compare positions
        source_positions = source_state.get('active_positions', {})
        target_positions = target_state.get('active_positions', {})
        
        position_issues = []
        for pair in set(source_positions.keys()) | set(target_positions.keys()):
            if pair not in source_positions:
                position_issues.append(f"Position {pair} missing from source")
            elif pair not in target_positions:
                position_issues.append(f"Position {pair} missing from target")
            else:
                # Compare key fields
                source_pos = source_positions[pair]
                target_pos = target_positions[pair]
                
                for key in ['symbol1', 'symbol2', 'direction']:
                    if source_pos.get(key) != target_pos.get(key):
                        position_issues.append(f"Position {pair}.{key} mismatch")
        
        # Compare pair states
        source_pairs = source_state.get('pair_states', {})
        target_pairs = target_state.get('pair_states', {})
        
        pair_issues = []
        for pair in set(source_pairs.keys()) | set(target_pairs.keys()):
            if pair not in source_pairs:
                pair_issues.append(f"Pair state {pair} missing from source")
            elif pair not in target_pairs:
                pair_issues.append(f"Pair state {pair} missing from target")
        
        # Overall validation
        all_issues = position_issues + pair_issues
        is_valid = len(all_issues) == 0
        
        return {
            'valid': is_valid,
            'position_count_source': len(source_positions),
            'position_count_target': len(target_positions),
            'pair_count_source': len(source_pairs),
            'pair_count_target': len(target_pairs),
            'issues': all_issues,
            'message': 'Validation passed' if is_valid else f'Found {len(all_issues)} issues'
        }
        
    except Exception as e:
        return {
            'valid': False,
            'message': f'Validation failed: {str(e)}',
            'issues': [str(e)]
        }


# CLI-style usage example
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Migrate trading states to database')
    parser.add_argument('source', help='Source state file or directory')
    parser.add_argument('--database', choices=['influxdb', 'postgresql'], 
                       default='influxdb', help='Target database type')
    parser.add_argument('--batch', action='store_true', 
                       help='Batch migrate all files in directory')
    parser.add_argument('--validate', action='store_true',
                       help='Validate migration after completion')
    parser.add_argument('--no-backup', action='store_true',
                       help='Skip creating backup of original files')
    
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(level=logging.INFO, 
                       format='%(asctime)s - %(levelname)s - %(message)s')
    
    try:
        if args.batch:
            # Batch migration
            from .enhanced_state_manager import create_enhanced_trading_state_manager
            
            manager = create_enhanced_trading_state_manager(database_type=args.database)
            batch_migrator = BatchMigrator(manager)
            
            success = batch_migrator.migrate_directory(
                args.source,
                backup_originals=not args.no_backup
            )
            
            report = batch_migrator.get_batch_report()
            print(json.dumps(report, indent=2, default=str))
            
            manager.cleanup()
            
        else:
            # Single file migration
            success = migrate_existing_state(
                args.source,
                database_type=args.database,
                backup_original=not args.no_backup
            )
            
            if args.validate and success:
                from .enhanced_state_manager import create_enhanced_trading_state_manager
                
                manager = create_enhanced_trading_state_manager(database_type=args.database)
                validation_result = validate_migration(args.source, manager)
                print(f"Validation: {json.dumps(validation_result, indent=2)}")
                manager.cleanup()
        
        exit(0 if success else 1)
        
    except KeyboardInterrupt:
        print("Migration interrupted by user")
        exit(1)
    except Exception as e:
        print(f"Migration failed: {e}")
        exit(1)
