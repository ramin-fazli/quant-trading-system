# GCP VM Deployment Guide

This guide provides step-by-step instructions for deploying the Trading System to a Google Cloud Platform (GCP) VM using the optimized CI/CD pipeline.

## 📋 Prerequisites

1. **Google Cloud Platform Account**
   - Active GCP project with billing enabled
   - Sufficient quotas for Compute Engine instances

2. **Local Tools** (for VM creation)
   - Google Cloud CLI (`gcloud`) installed and configured
   - Git repository cloned locally

3. **GitHub Repository**
   - Fork or clone of the trading system repository
   - Admin access to configure secrets

## 🚀 Quick Setup

### Step 1: Create GCP VM

1. **Configure your environment:**
   ```bash
   export GCP_PROJECT_ID="your-project-id"
   export VM_NAME="trading-system-vm"
   export ZONE="europe-west3-a"
   ```

2. **Create the VM:**
   ```bash
   cd scripts/
   chmod +x create-gcp-vm.sh setup-gcp-vm.sh
   ./create-gcp-vm.sh
   ```

   This script will:
   - Create a VM instance with optimal configuration
   - Set up firewall rules
   - Install Docker, Google Cloud CLI, and dependencies
   - Configure monitoring and security

### Step 2: Create Service Account

1. **Create a service account:**
   ```bash
   gcloud iam service-accounts create trading-system-deployer \
     --description="Service account for trading system deployment" \
     --display-name="Trading System Deployer"
   ```

2. **Grant necessary permissions:**
   ```bash
   gcloud projects add-iam-policy-binding $GCP_PROJECT_ID \
     --member="serviceAccount:trading-system-deployer@$GCP_PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/compute.instanceAdmin.v1"

   gcloud projects add-iam-policy-binding $GCP_PROJECT_ID \
     --member="serviceAccount:trading-system-deployer@$GCP_PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/artifactregistry.admin"

   gcloud projects add-iam-policy-binding $GCP_PROJECT_ID \
     --member="serviceAccount:trading-system-deployer@$GCP_PROJECT_ID.iam.gserviceaccount.com" \
     --role="roles/storage.admin"
   ```

3. **Create and download service account key:**
   ```bash
   gcloud iam service-accounts keys create trading-system-key.json \
     --iam-account=trading-system-deployer@$GCP_PROJECT_ID.iam.gserviceaccount.com
   ```

### Step 3: Configure GitHub Secrets

Navigate to your GitHub repository → Settings → Secrets and variables → Actions, then add:

#### Required Secrets:
- `GCP_SA_KEY`: Content of `trading-system-key.json` file
- `GCP_PROJECT_ID`: Your GCP project ID
- `VM_EXTERNAL_IP`: External IP of your VM (from Step 1 output)

#### Trading System Secrets:
- `INFLUXDB_URL`: InfluxDB connection URL
- `INFLUXDB_TOKEN`: InfluxDB authentication token
- `INFLUXDB_ORG`: InfluxDB organization
- `INFLUXDB_BUCKET`: InfluxDB bucket name
- `MT5_LOGIN`: MetaTrader 5 login
- `MT5_PASSWORD`: MetaTrader 5 password
- `MT5_SERVER`: MetaTrader 5 server
- `CTRADER_CLIENT_ID`: cTrader API client ID
- `CTRADER_CLIENT_SECRET`: cTrader API client secret
- `CTRADER_ACCESS_TOKEN`: cTrader API access token
- `TELEGRAM_BOT_TOKEN`: Telegram bot token for notifications
- `TELEGRAM_CHAT_ID`: Telegram chat ID for notifications

### Step 4: Deploy

1. **Push to trigger deployment:**
   ```bash
   git add .
   git commit -m "Deploy to GCP VM"
   git push origin version/2.3
   ```

2. **Monitor deployment:**
   - Go to GitHub → Actions tab
   - Watch the "Deploy Trading System to GCP VM" workflow
   - Check all three jobs: Build, Deploy, and Cleanup

3. **Verify deployment:**
   ```bash
   # SSH to VM
   gcloud compute ssh $VM_NAME --zone=$ZONE

   # Check service status
   /usr/local/bin/trading-system-health.sh

   # View logs
   docker logs trading-system
   ```

## 🔧 Advanced Configuration

### VM Specifications

Default configuration:
- **Machine Type**: e2-standard-4 (4 vCPUs, 16 GB RAM)
- **Disk**: 50 GB SSD
- **Region**: europe-west3
- **OS**: Ubuntu 22.04 LTS

To customize, edit variables in `create-gcp-vm.sh`:
```bash
MACHINE_TYPE="e2-standard-8"  # 8 vCPUs, 32 GB RAM
BOOT_DISK_SIZE="100GB"        # Larger disk
DISK_TYPE="pd-ssd"            # SSD instead of standard
```

### Security Configuration

The setup includes:
- **Firewall**: Only ports 22 (SSH), 8050 (Dashboard), 8080 (API) open
- **Fail2ban**: Protection against brute force attacks
- **UFW**: Uncomplicated Firewall configured
- **Docker**: Rootless container execution
- **OS Login**: Google Cloud OS Login enabled

### Monitoring and Maintenance

Automated monitoring includes:
- **Health checks**: Every 5 minutes
- **Log rotation**: Daily, 30-day retention
- **Backups**: Daily at 2 AM
- **Resource monitoring**: Memory and disk usage alerts

### Manual Operations

Useful commands for manual management:

```bash
# SSH to VM
gcloud compute ssh trading-system-vm --zone=europe-west3-a

# Check service health
sudo /usr/local/bin/trading-system-health.sh

# View container logs
docker logs trading-system --tail 100 -f

# Restart service
docker restart trading-system

# Update service manually
sudo /usr/local/bin/trading-system-update.sh

# Create manual backup
sudo /usr/local/bin/trading-system-backup.sh

# Monitor resources
htop
docker stats
```

## 🔍 Troubleshooting

### Common Issues

1. **Deployment fails with authentication error**
   - Verify `GCP_SA_KEY` secret is correctly formatted JSON
   - Check service account permissions

2. **Container won't start**
   - Check environment variables in VM
   - Verify Docker image was pulled successfully
   - Check container logs: `docker logs trading-system`

3. **Service not accessible**
   - Verify firewall rules: `sudo ufw status`
   - Check VM external IP is correct
   - Ensure container is binding to 0.0.0.0, not localhost

4. **Out of memory errors**
   - Increase VM memory or reduce container memory limit
   - Check for memory leaks in application logs

### Log Locations

- **Container logs**: `docker logs trading-system`
- **System logs**: `/var/log/trading-system/`
- **Application logs**: `/opt/trading-system/logs/`
- **Monitor logs**: `/var/log/trading-system/monitor.log`

### Performance Tuning

For high-frequency trading:
```bash
# Increase VM size
MACHINE_TYPE="c2-standard-8"  # Compute-optimized

# Use SSD storage
DISK_TYPE="pd-ssd"

# Optimize network
# Consider Premium Tier networking for lower latency
```

## 🔄 Updating the System

### Automatic Updates
- Push to `version/2.3` branch triggers automatic deployment
- No downtime deployment with health checks
- Automatic rollback on deployment failure

### Manual Updates
```bash
# SSH to VM
gcloud compute ssh trading-system-vm --zone=europe-west3-a

# Run update script
sudo /usr/local/bin/trading-system-update.sh

# Or manually pull and restart
docker pull europe-west3-docker.pkg.dev/PROJECT_ID/trading-system/pair-trading-system:latest
docker stop trading-system
docker rm trading-system
# Run deployment script again
```

## 📊 Monitoring

### Built-in Monitoring
- Container health checks every 5 minutes
- Resource usage monitoring
- Automatic restart on failures
- Log aggregation and rotation

### External Monitoring (Optional)
Consider setting up:
- Google Cloud Monitoring
- Prometheus + Grafana
- Uptime monitoring services
- Log aggregation (ELK stack)

## 🔒 Security Best Practices

1. **Regular Updates**
   ```bash
   # Update VM packages monthly
   sudo apt update && sudo apt upgrade -y
   ```

2. **SSL/TLS Setup**
   - Use a reverse proxy (nginx) with SSL certificates
   - Let's Encrypt for free certificates

3. **Network Security**
   - Consider VPC with private subnets
   - Use Cloud NAT for outbound traffic
   - Implement WAF for web traffic

4. **Access Control**
   - Use Google Cloud IAM for access management
   - Enable 2FA for all accounts
   - Rotate service account keys regularly

## 📞 Support

For issues related to:
- **GCP Infrastructure**: Check Google Cloud Console
- **GitHub Actions**: Check Actions tab in repository
- **Application**: Check container logs and application documentation

## 📝 Cost Optimization

Estimated monthly costs (europe-west3):
- **e2-standard-4**: ~$120/month
- **50GB SSD**: ~$8/month
- **Network egress**: Variable based on usage
- **Artifact Registry**: ~$0.10/GB/month

Cost-saving tips:
- Use committed use discounts for production
- Schedule VM shutdown during non-trading hours
- Use preemptible instances for development
- Monitor and optimize resource usage
