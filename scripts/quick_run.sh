#!/bin/bash
# Quick helper script for common trading system operations
# Usage: ./quick_run.sh [mode] [data_provider] [broker]

# Default values
MODE=${1:-live}
DATA_PROVIDER=${2:-ctrader}
BROKER=${3:-ctrader}

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}🚀 Quick Trading System Launcher${NC}"
echo -e "${BLUE}=================================${NC}"
echo -e "${GREEN}Mode:${NC} $MODE"
echo -e "${GREEN}Data Provider:${NC} $DATA_PROVIDER"
echo -e "${GREEN}Broker:${NC} $BROKER"
echo ""

# Check if we're in Docker or local environment
if [ -f /.dockerenv ]; then
    # Inside Docker container
    echo -e "${YELLOW}Running inside Docker container...${NC}"
    ./scripts/run_script.sh main --mode $MODE --data-provider $DATA_PROVIDER --broker $BROKER
else
    # Outside Docker - use docker-compose
    echo -e "${YELLOW}Running via Docker Compose...${NC}"
    docker-compose exec trading-system ./scripts/run_script.sh main --mode $MODE --data-provider $DATA_PROVIDER --broker $BROKER
fi
