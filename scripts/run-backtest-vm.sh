#!/bin/bash
# Script to run backtest on GCP VM with proper InfluxDB connectivity
# Usage: ./run-backtest-vm.sh [additional-docker-args] [-- backtest-args]

set -e

# Check if we're on the VM and have the necessary setup
if [ ! -f "/opt/trading-system/.env" ]; then
    echo "❌ This script should be run on the GCP VM with trading system deployed"
    echo "❌ Missing /opt/trading-system/.env file"
    exit 1
fi

# Determine if we need sudo for docker commands
USE_SUDO=""
if ! docker info >/dev/null 2>&1; then
    echo "Using sudo for Docker commands..."
    USE_SUDO="sudo"
fi

# Get the trading system image from running container
IMAGE_NAME=$($USE_SUDO docker inspect trading-system --format='{{.Config.Image}}' 2>/dev/null || echo "")

if [ -z "$IMAGE_NAME" ]; then
    echo "❌ Could not determine trading system image. Is the trading-system container running?"
    echo "Try: docker ps | grep trading-system"
    exit 1
fi

echo "🔍 Using image: $IMAGE_NAME"

# Check if trading-network exists, create if not
if ! $USE_SUDO docker network inspect trading-network >/dev/null 2>&1; then
    echo "🌐 Creating trading-network..."
    $USE_SUDO docker network create trading-network
fi

# Parse arguments
DOCKER_ARGS=""
BACKTEST_ARGS="--mode backtest --data-provider ctrader"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --)
            shift
            BACKTEST_ARGS="$@"
            break
            ;;
        *)
            DOCKER_ARGS="$DOCKER_ARGS $1"
            shift
            ;;
    esac
done

echo "🚀 Starting backtest container..."
echo "📊 Docker args: $DOCKER_ARGS"
echo "🎯 Backtest args: $BACKTEST_ARGS"

# Run backtest container with proper network connectivity
$USE_SUDO docker run --rm \
  --name "trading-system-backtest-$(date +%s)" \
  --network trading-network \
  --env-file /opt/trading-system/.env \
  -e TRADING_MODE=backtest \
  -v /opt/trading-system/logs:/app/logs \
  -v /opt/trading-system/backtest_reports:/app/backtest_reports \
  -v /opt/trading-system/cache:/app/cache \
  -v /opt/trading-system/pairs_config:/app/pairs_config \
  --memory=2g \
  --cpus=2 \
  --log-driver=json-file \
  --log-opt max-size=100m \
  --log-opt max-file=5 \
  $DOCKER_ARGS \
  --entrypoint="" \
  "$IMAGE_NAME" \
  python -m scripts.pair_trading.main $BACKTEST_ARGS

echo "✅ Backtest completed!"
echo "📊 Check backtest reports in: /opt/trading-system/backtest_reports/"
echo "📋 Check logs in: /opt/trading-system/logs/"
