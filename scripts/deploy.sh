#!/usr/bin/env bash
set -euo pipefail

STACK="tulipbroker-api-qa"
PROJECT="tulipbroker-api"
ENV="qa"
REGION="us-east-2"

GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "local")
BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
APP_VERSION="0.1.0"


ARTIFACT_BUCKET="${PROJECT}-${ENV}-artifacts-$(aws sts get-caller-identity --query Account --output text)-${REGION}"
ZIP="lambda.zip"
CODE_KEY="build/lambda-${GIT_SHA}.zip"
TEMPLATE="infra/api.yaml"

# Clean build dirs
rm -rf build/package build/lambda.zip
mkdir -p build/package

# (Optional) include dependencies
# pip install -r requirements.txt -t build/package

# Copy app code to package root (NO leading "src/")
cp -R src/* build/package/

# Create the zip with code at root
( cd build/package && zip -r ../lambda.zip . )

# sanity check: handlers present at root
test -f "build/package/handlers/config.py" || { echo "handlers/config.py missing at zip root"; exit 1; }

unzip -l build/lambda.zip

# Ensure S3 bucket exists
if ! aws s3 ls "s3://${ARTIFACT_BUCKET}" >/dev/null 2>&1; then
  aws s3 mb "s3://${ARTIFACT_BUCKET}" --region "${REGION}"
fi

# -------- Validate CloudFormation template --------
echo "==> Validating CloudFormation template"
aws cloudformation validate-template --template-body "file://${TEMPLATE}" >/dev/null
echo "   - Template is valid ✅"

# Optional: run cfn-lint if you have it installed
if command -v cfn-lint >/dev/null; then
  echo "   - Running cfn-lint (optional)"
  cfn-lint "${TEMPLATE}"
fi

# Upload code
aws s3 cp "build/${ZIP}" "s3://${ARTIFACT_BUCKET}/${CODE_KEY}"


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

# (Optional) sanity invoke
API_ID="$(aws apigatewayv2 get-apis --region "${REGION}" \
          --query "Items[?Name=='${STACK}'].ApiId|[0]" --output text)"
echo "Health: https://${API_ID}.execute-api.${REGION}.amazonaws.com/health"

echo "==> Deployment complete ✅"