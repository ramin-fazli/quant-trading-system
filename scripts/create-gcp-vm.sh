#!/bin/bash
# GCP VM Creation Script for Trading System
# This script creates a properly configured VM instance for the trading system

set -e

# Configuration - Modify these variables as needed
PROJECT_ID="${GCP_PROJECT_ID:-your-project-id}"
VM_NAME="${VM_NAME:-trading-system-vm}"
ZONE="${ZONE:-europe-west3-a}"
REGION="${REGION:-europe-west3}"
MACHINE_TYPE="${MACHINE_TYPE:-e2-standard-4}"  # 4 vCPUs, 16 GB memory
BOOT_DISK_SIZE="${BOOT_DISK_SIZE:-50GB}"
DISK_TYPE="${DISK_TYPE:-pd-standard}"
IMAGE_FAMILY="${IMAGE_FAMILY:-ubuntu-2204-lts}"
IMAGE_PROJECT="${IMAGE_PROJECT:-ubuntu-os-cloud}"
NETWORK="${NETWORK:-default}"
SUBNET="${SUBNET:-default}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    error "gcloud CLI is not installed. Please install it first."
fi

# Check if user is authenticated
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q "@"; then
    error "Not authenticated with gcloud. Please run 'gcloud auth login' first."
fi

# Set project if provided
if [ "$PROJECT_ID" != "your-project-id" ]; then
    log "Setting project to: $PROJECT_ID"
    gcloud config set project "$PROJECT_ID"
else
    PROJECT_ID=$(gcloud config get-value project)
    if [ -z "$PROJECT_ID" ]; then
        error "No project set. Please set PROJECT_ID environment variable or run 'gcloud config set project YOUR_PROJECT_ID'"
    fi
fi

log "🚀 Creating GCP VM for Trading System"
log "Project: $PROJECT_ID"
log "VM Name: $VM_NAME"
log "Zone: $ZONE"
log "Machine Type: $MACHINE_TYPE"

# Check if VM already exists
if gcloud compute instances describe "$VM_NAME" --zone="$ZONE" &>/dev/null; then
    warning "VM '$VM_NAME' already exists in zone '$ZONE'"
    read -p "Do you want to delete and recreate it? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        log "Deleting existing VM..."
        gcloud compute instances delete "$VM_NAME" --zone="$ZONE" --quiet
    else
        error "Aborted. VM already exists."
    fi
fi

# Enable required APIs
log "🔧 Enabling required Google Cloud APIs..."
gcloud services enable compute.googleapis.com
gcloud services enable artifactregistry.googleapis.com
gcloud services enable cloudbuild.googleapis.com

# Create firewall rules if they don't exist
log "🔥 Creating firewall rules..."

# Dashboard port (8050)
if ! gcloud compute firewall-rules describe trading-system-dashboard &>/dev/null; then
    gcloud compute firewall-rules create trading-system-dashboard \
        --allow tcp:8050 \
        --source-ranges 0.0.0.0/0 \
        --description "Allow access to trading system dashboard" \
        --target-tags trading-system
fi

# API port (8080)
if ! gcloud compute firewall-rules describe trading-system-api &>/dev/null; then
    gcloud compute firewall-rules create trading-system-api \
        --allow tcp:8080 \
        --source-ranges 0.0.0.0/0 \
        --description "Allow access to trading system API" \
        --target-tags trading-system
fi

# SSH access (if not using default)
if ! gcloud compute firewall-rules describe allow-ssh &>/dev/null; then
    gcloud compute firewall-rules create allow-ssh \
        --allow tcp:22 \
        --source-ranges 0.0.0.0/0 \
        --description "Allow SSH access"
fi

# Create the VM instance
log "🖥️ Creating VM instance..."
gcloud compute instances create "$VM_NAME" \
    --zone="$ZONE" \
    --machine-type="$MACHINE_TYPE" \
    --network-interface=network-tier=PREMIUM,subnet="$SUBNET" \
    --metadata=enable-oslogin=true \
    --maintenance-policy=MIGRATE \
    --provisioning-model=STANDARD \
    --service-account=default \
    --scopes=https://www.googleapis.com/auth/cloud-platform \
    --tags=trading-system,http-server,https-server \
    --create-disk=auto-delete=yes,boot=yes,device-name="$VM_NAME",image=projects/"$IMAGE_PROJECT"/global/images/family/"$IMAGE_FAMILY",mode=rw,size="$BOOT_DISK_SIZE",type=projects/"$PROJECT_ID"/zones/"$ZONE"/diskTypes/"$DISK_TYPE" \
    --no-shielded-secure-boot \
    --shielded-vtpm \
    --shielded-integrity-monitoring \
    --reservation-affinity=any

# Wait for VM to be ready
log "⏳ Waiting for VM to be ready..."
sleep 30

# Get VM details
VM_EXTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" \
    --zone="$ZONE" \
    --format="value(networkInterfaces[0].accessConfigs[0].natIP)")

VM_INTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" \
    --zone="$ZONE" \
    --format="value(networkInterfaces[0].networkIP)")

success "VM created successfully!"

# Download and run setup script
log "📥 Setting up VM with trading system requirements..."

# Wait a bit more for SSH to be ready
sleep 60

# Copy setup script to VM
gcloud compute scp ./setup-gcp-vm.sh "$VM_NAME":~/setup-gcp-vm.sh \
    --zone="$ZONE" \
    --ssh-flag="-o StrictHostKeyChecking=no"

# Run setup script on VM
log "🔧 Running setup script on VM..."
gcloud compute ssh "$VM_NAME" \
    --zone="$ZONE" \
    --command="chmod +x ~/setup-gcp-vm.sh && sudo ~/setup-gcp-vm.sh" \
    --ssh-flag="-o StrictHostKeyChecking=no"

# Display summary
echo
success "🎉 GCP VM setup completed successfully!"
echo
echo "📋 VM Details:"
echo "  • Name: $VM_NAME"
echo "  • Zone: $ZONE"
echo "  • Machine Type: $MACHINE_TYPE"
echo "  • External IP: $VM_EXTERNAL_IP"
echo "  • Internal IP: $VM_INTERNAL_IP"
echo
echo "🔗 Service URLs (after deployment):"
echo "  • Dashboard: http://$VM_EXTERNAL_IP:8050"
echo "  • API: http://$VM_EXTERNAL_IP:8080"
echo
echo "🔧 Useful Commands:"
echo "  • SSH to VM: gcloud compute ssh $VM_NAME --zone=$ZONE"
echo "  • Copy files: gcloud compute scp LOCAL_FILE $VM_NAME:~/ --zone=$ZONE"
echo "  • View logs: gcloud compute ssh $VM_NAME --zone=$ZONE --command='docker logs trading-system'"
echo
echo "📝 Next Steps:"
echo "  1. Set up GitHub secrets for deployment:"
echo "     - GCP_SA_KEY: Service account key JSON"
echo "     - GCP_PROJECT_ID: $PROJECT_ID"
echo "     - VM_EXTERNAL_IP: $VM_EXTERNAL_IP"
echo "     - Add all your trading system secrets (MT5, cTrader, InfluxDB, etc.)"
echo "  2. Push code to trigger deployment"
echo "  3. Monitor deployment in GitHub Actions"
echo "  4. Access dashboard at: http://$VM_EXTERNAL_IP:8050"
echo
warning "💡 Remember to:"
echo "  • Configure your secrets in GitHub repository settings"
echo "  • Set up proper SSL/TLS certificates for production"
echo "  • Configure domain name if needed"
echo "  • Set up monitoring and alerting"
echo "  • Review and adjust firewall rules as needed"
