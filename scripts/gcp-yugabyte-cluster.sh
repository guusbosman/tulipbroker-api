#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------------------------
# YugabyteDB 3-node POC cluster on GCP (Compute Engine VMs)
# Region: us-east5 (closest to AWS us-east-2)
# Cheapest path: E2 shared-core + pd-standard disks
#
# Prereqs:
# - gcloud installed + initialized (gcloud init)
# - billing enabled on the project
# - existing SSH public key on this machine
# ------------------------------------------------------------------------------

PROJECT_ID="${PROJECT_ID:-}"
REGION="${REGION:-us-east5}"
ZONES=(${ZONES:-us-east5-a us-east5-b us-east5-c})

VPC_NAME="${VPC_NAME:-yb-vpc}"
SUBNET_NAME="${SUBNET_NAME:-yb-subnet}"
SUBNET_RANGE="${SUBNET_RANGE:-10.10.0.0/24}"

INSTANCE_PREFIX="${INSTANCE_PREFIX:-yb-node}"
MACHINE_TYPE="${MACHINE_TYPE:-e2-medium}"
DISK_TYPE="${DISK_TYPE:-pd-standard}"
DISK_SIZE_GB="${DISK_SIZE_GB:-50}"

IMAGE_FAMILY="${IMAGE_FAMILY:-ubuntu-2204-lts}"
IMAGE_PROJECT="${IMAGE_PROJECT:-ubuntu-os-cloud}"

SSH_USER="${SSH_USER:-$USER}"
SSH_PUBKEY_PATH="${SSH_PUBKEY_PATH:-$HOME/.ssh/id_ed25519.pub}"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "PROJECT_ID is required. Example: PROJECT_ID=my-gcp-project" >&2
  exit 1
fi

if [[ ! -f "${SSH_PUBKEY_PATH}" ]]; then
  echo "SSH public key not found at ${SSH_PUBKEY_PATH}" >&2
  exit 1
fi

echo "==> Setting gcloud project/region"
gcloud config set project "${PROJECT_ID}"
gcloud config set compute/region "${REGION}"

echo "==> Enabling Compute Engine API"
gcloud services enable compute.googleapis.com

echo "==> Creating VPC and subnet (if missing)"
if ! gcloud compute networks describe "${VPC_NAME}" >/dev/null 2>&1; then
  gcloud compute networks create "${VPC_NAME}" --subnet-mode=custom
fi

if ! gcloud compute networks subnets describe "${SUBNET_NAME}" --region "${REGION}" >/dev/null 2>&1; then
  gcloud compute networks subnets create "${SUBNET_NAME}" \
    --network "${VPC_NAME}" \
    --region "${REGION}" \
    --range "${SUBNET_RANGE}"
fi

echo "==> Firewall rules"
# Internal traffic between nodes
if ! gcloud compute firewall-rules describe yb-allow-internal >/dev/null 2>&1; then
  gcloud compute firewall-rules create yb-allow-internal \
    --network "${VPC_NAME}" \
    --allow tcp,udp,icmp \
    --source-ranges "${SUBNET_RANGE}" \
    --target-tags yugabyte
fi

# SSH access (restrict source ranges if possible)
if ! gcloud compute firewall-rules describe yb-allow-ssh >/dev/null 2>&1; then
  gcloud compute firewall-rules create yb-allow-ssh \
    --network "${VPC_NAME}" \
    --allow tcp:22 \
    --source-ranges "0.0.0.0/0" \
    --target-tags yugabyte
fi

# YugabyteDB ports (YSQL/YCQL/UI) - open to your IP for demo, adjust as needed
if ! gcloud compute firewall-rules describe yb-allow-ports >/dev/null 2>&1; then
  gcloud compute firewall-rules create yb-allow-ports \
    --network "${VPC_NAME}" \
    --allow tcp:7000,tcp:9000,tcp:5433,tcp:9042,tcp:6379 \
    --source-ranges "0.0.0.0/0" \
    --target-tags yugabyte
fi

echo "==> Creating instances"
for idx in 1 2 3; do
  ZONE="${ZONES[$((idx-1))]}"
  NAME="${INSTANCE_PREFIX}-${idx}"
  if gcloud compute instances describe "${NAME}" --zone "${ZONE}" >/dev/null 2>&1; then
    echo "   - ${NAME} already exists in ${ZONE}, skipping"
    continue
  fi

  gcloud compute instances create "${NAME}" \
    --zone "${ZONE}" \
    --machine-type "${MACHINE_TYPE}" \
    --image-family "${IMAGE_FAMILY}" \
    --image-project "${IMAGE_PROJECT}" \
    --boot-disk-type "${DISK_TYPE}" \
    --boot-disk-size "${DISK_SIZE_GB}GB" \
    --subnet "${SUBNET_NAME}" \
    --tags "yugabyte" \
    --metadata "ssh-keys=${SSH_USER}:$(cat "${SSH_PUBKEY_PATH}")"
done

echo "==> Done. Instances:"
gcloud compute instances list \
  --filter "name~'${INSTANCE_PREFIX}-' AND zone:(${ZONES[0]} ${ZONES[1]} ${ZONES[2]})" \
  --format "table(name,zone,networkInterfaces[0].networkIP,EXTERNAL_IP)"

cat <<'NEXTSTEPS'

Next steps (manual for now):
1) SSH to each node and install YugabyteDB (yugabyted) prerequisites.
2) Download and install YugabyteDB on each node.
3) Start a 3-node RF=3 cluster with placement info across zones.

I can add those install/start steps once you pick a YugabyteDB version
and confirm whether we should use yugabyted or manual services.

NEXTSTEPS
