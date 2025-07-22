#!/bin/bash
# Remote script executor for EC2 deployment
# Usage: ./remote_run.sh <ec2_host> <script_name> [args]

EC2_HOST=$1
SCRIPT_NAME=$2
SCRIPT_ARGS="${@:3}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

if [ -z "$EC2_HOST" ] || [ -z "$SCRIPT_NAME" ]; then
    echo -e "${RED}Error: EC2 host and script name are required${NC}"
    echo ""
    echo "Usage: ./remote_run.sh <ec2_host> <script_name> [args]"
    echo ""
    echo "Examples:"
    echo "  ./remote_run.sh ec2-user@your-ec2-ip main --mode live"
    echo "  ./remote_run.sh ec2-user@your-ec2-ip backtest --symbol EURUSD"
    echo "  ./remote_run.sh ec2-user@your-ec2-ip health-check"
    echo ""
    exit 1
fi

echo -e "${BLUE}🌐 Remote Script Executor${NC}"
echo -e "${BLUE}=========================${NC}"
echo -e "${GREEN}Target:${NC} $EC2_HOST"
echo -e "${GREEN}Script:${NC} $SCRIPT_NAME"
echo -e "${GREEN}Args:${NC} $SCRIPT_ARGS"
echo ""

echo -e "${YELLOW}Connecting to EC2 instance...${NC}"

# Execute the script on remote EC2
ssh -o StrictHostKeyChecking=no $EC2_HOST "cd ~/trading-system && docker-compose exec -T trading-system ./scripts/run_script.sh $SCRIPT_NAME $SCRIPT_ARGS"

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✅ Remote script execution completed successfully${NC}"
else
    echo ""
    echo -e "${RED}❌ Remote script execution failed${NC}"
    exit 1
fi
