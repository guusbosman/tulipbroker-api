#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------------------------
# Install and start a 3-node YugabyteDB cluster on existing GCP VMs.
# Assumes instances exist (yb-node-1/2/3) and are reachable via gcloud SSH.
# Uses yugabyted (simplest for a POC) in *non-secure* mode.
# ------------------------------------------------------------------------------

PROJECT_ID="${PROJECT_ID:-guus-yugabyte}"
REGION="${REGION:-us-east5}"
ZONES=(${ZONES:-us-east5-a us-east5-b us-east5-c})
INSTANCE_PREFIX="${INSTANCE_PREFIX:-yb-node}"

YB_VERSION="${YB_VERSION:-2025.2.0.1}"
YB_BUILD="${YB_BUILD:-b1}"
YB_TARBALL="yugabyte-${YB_VERSION}-${YB_BUILD}-linux-x86_64.tar.gz"
YB_URL="https://software.yugabyte.com/releases/${YB_VERSION}/${YB_TARBALL}"
YB_DIR="yugabyte-${YB_VERSION}"

BASE_DIR="${BASE_DIR:-/home/${USER}/yb-data}"

echo "==> Using project ${PROJECT_ID}"
gcloud config set project "${PROJECT_ID}" >/dev/null

echo "==> Resolving instances"
INSTANCES=()
for idx in 1 2 3; do
  INSTANCES+=("${INSTANCE_PREFIX}-${idx}")
done

declare -A NODE_IPS
for idx in 1 2 3; do
  NAME="${INSTANCE_PREFIX}-${idx}"
  ZONE="${ZONES[$((idx-1))]}"
  IP=$(gcloud compute instances describe "${NAME}" --zone "${ZONE}" \
    --format 'value(networkInterfaces[0].networkIP)')
  if [[ -z "${IP}" ]]; then
    echo "Failed to resolve IP for ${NAME} in ${ZONE}" >&2
    exit 1
  fi
  NODE_IPS["${NAME}"]="${IP}"
  echo "  - ${NAME} (${ZONE}) => ${IP}"
done

echo "==> Installing YugabyteDB on nodes"
for idx in 1 2 3; do
  NAME="${INSTANCE_PREFIX}-${idx}"
  ZONE="${ZONES[$((idx-1))]}"
  echo "  - ${NAME}"
  gcloud compute ssh "${NAME}" --zone "${ZONE}" --command "bash -lc '
    set -euo pipefail
    if ! command -v wget >/dev/null 2>&1; then
      sudo apt-get update -y
      sudo apt-get install -y wget tar
    fi
    if [[ ! -d ${YB_DIR} ]]; then
      wget -q ${YB_URL}
      tar xvfz ${YB_TARBALL}
      cd ${YB_DIR}
      ./bin/post_install.sh
    fi
  '"
done

echo "==> Starting YugabyteDB cluster (non-secure)"
FIRST_NODE="${INSTANCE_PREFIX}-1"
FIRST_ZONE="${ZONES[0]}"
FIRST_IP="${NODE_IPS[${FIRST_NODE}]}"

gcloud compute ssh "${FIRST_NODE}" --zone "${FIRST_ZONE}" --command "bash -lc '
  set -euo pipefail
  cd ${YB_DIR}
  ./bin/yugabyted start \
    --advertise_address=${FIRST_IP} \
    --cloud_location=gcp.${REGION}.${ZONES[0]} \
    --fault_tolerance=zone \
    --base_dir=${BASE_DIR}/node1
'"

for idx in 2 3; do
  NAME="${INSTANCE_PREFIX}-${idx}"
  ZONE="${ZONES[$((idx-1))]}"
  IP="${NODE_IPS[${NAME}]}"
  gcloud compute ssh "${NAME}" --zone "${ZONE}" --command "bash -lc '
    set -euo pipefail
    cd ${YB_DIR}
    ./bin/yugabyted start \
      --advertise_address=${IP} \
      --join=${FIRST_IP} \
      --cloud_location=gcp.${REGION}.${ZONE} \
      --fault_tolerance=zone \
      --base_dir=${BASE_DIR}/node${idx}
  '"
done

echo "==> Configuring data placement (RF=3 across zones)"
gcloud compute ssh "${FIRST_NODE}" --zone "${FIRST_ZONE}" --command "bash -lc '
  set -euo pipefail
  cd ${YB_DIR}
  ./bin/yugabyted configure data_placement \
    --fault_tolerance=zone \
    --constraint_value=gcp.${REGION}.${ZONES[0]},gcp.${REGION}.${ZONES[1]},gcp.${REGION}.${ZONES[2]} \
    --rf=3 \
    --base_dir=${BASE_DIR}/node1
'"

echo "==> Cluster started"
echo "Master UI:  http://${FIRST_IP}:7000"
echo "TServer UI: http://${FIRST_IP}:9000"
echo "YSQL:       ${FIRST_IP}:5433"
echo "YCQL:       ${FIRST_IP}:9042"

echo ""
echo "NOTE: This is a non-secure cluster for POC use only."
