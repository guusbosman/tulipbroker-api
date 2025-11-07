#!/usr/bin/env bash
set -euo pipefail

STACK="tulipbroker-api-qa"
PROJECT="tulipbroker-api"
ENV="qa"
REGION="us-east-2"

ARTIFACT_BUCKET="${PROJECT}-${ENV}-artifacts-$(aws sts get-caller-identity --query Account --output text)-${REGION}"
ZIP="lambda.zip"
CODE_KEY="build/${ZIP}"
TEMPLATE="infra/api.yaml"

# Build Lambda package
rm -rf build && mkdir -p build
cp -R src build/
pushd build >/dev/null
zip -r ${ZIP} . >/dev/null
popd >/dev/null

# Ensure S3 bucket exists
if ! aws s3 ls "s3://${ARTIFACT_BUCKET}" >/dev/null 2>&1; then
  aws s3 mb "s3://${ARTIFACT_BUCKET}" --region "${REGION}"
fi

# Upload code
aws s3 cp "build/${ZIP}" "s3://${ARTIFACT_BUCKET}/${CODE_KEY}"

# Deploy stack
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "local")
BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
APP_VERSION="0.1.0"

aws cloudformation deploy \
  --region "${REGION}" \
  --template-file "${TEMPLATE}" \
  --stack-name "${STACK}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      Project=${PROJECT} Env=${ENV} \
      CodeBucket=${ARTIFACT_BUCKET} CodeKey=${CODE_KEY} \
      AppVersion=${APP_VERSION} GitSha=${GIT_SHA} BuildTime=${BUILD_TIME} \
      AllowedOrigin="*"

# Print outputs
aws cloudformation describe-stacks --stack-name "${STACK}" \
  --query "Stacks[0].Outputs[].[OutputKey,OutputValue]" --output table
