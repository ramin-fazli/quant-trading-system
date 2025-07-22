#!/bin/bash
# Simple EC2 Setup Script for Trading System
set -e

echo "🚀 Setting up Trading System on EC2"

# Update system
sudo apt-get update -y

# Install Docker
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker ubuntu
    sudo systemctl enable docker
    sudo systemctl start docker
    rm get-docker.sh
fi

# Install Docker Compose
if ! command -v docker-compose &> /dev/null; then
    echo "Installing Docker Compose..."
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
fi

# Install useful tools
sudo apt-get install -y htop curl wget git

# Create application directory
sudo mkdir -p /opt/trading-system
sudo chown ubuntu:ubuntu /opt/trading-system

# Create logs directory with proper permissions
mkdir -p /opt/trading-system/logs
mkdir -p /opt/trading-system/backtest_reports
mkdir -p /opt/trading-system/data

echo "✅ EC2 setup completed!"
echo "📝 Next steps:"
echo "1. Configure GitHub secrets in your repository"
echo "2. Push your code to trigger deployment"
echo "3. Access dashboard at: http://$(curl -s http://checkip.amazonaws.com/):8050"
