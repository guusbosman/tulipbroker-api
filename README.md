# 🌷 TulipBroker API

**TulipBroker API** is a lightweight Python backend deployed on **AWS Lambda** and exposed via **Amazon API Gateway (HTTP API)**.  
It provides configuration and health endpoints for the TulipBroker UI and is designed to scale serverlessly with minimal maintenance.

---

## 🚀 Overview

| Component | Description |
|------------|-------------|
| **AWS Lambda** | Runs the backend Python code (`config`, `health` endpoints). |
| **API Gateway (HTTP API)** | Exposes public REST endpoints with built-in CORS support. |
| **CloudFormation** | Manages infrastructure as code (IaC). |
| **S3** | Stores the Lambda deployment package (`lambda.zip`). |
| **Python 3.12** | Runtime for all backend code. |

---

## 📁 Directory Structure

```
tulipbroker-api/
├─ src/
│  └─ handlers/
│     ├─ health.py          # GET /health – returns {"status": "ok"}
│     └─ config.py          # GET /api/config – returns version/env info
├─ infra/
│  └─ api.yaml              # CloudFormation template (Lambda + API Gateway)
├─ scripts/
│  └─ deploy.sh             # Build + upload + deploy automation script
├─ requirements.txt         # Optional dependencies
└─ README.md                # This file
```

---

## 🧩 Endpoints

| Endpoint | Method | Purpose |
|-----------|---------|---------|
| `/health` | GET | Basic health check endpoint. |
| `/api/config` | GET | Returns version, region, and environment metadata. |
| `/api/orders` | POST | Submits an order and returns the accepted `orderId`. |
| `/api/orders` | GET | Returns the most recent orders (Phase 1 scan). |
| `/api/metrics/pulse` | GET | Aggregated order metrics for the UI market pulse chart. |

Example response from `/api/config`:
```json
{
  "version": "0.1.0",
  "env": "qa",
  "region": "us-east-2",
  "commit": "e3a4d21",
  "buildTime": "2025-11-07T21:34:00Z"
}
```

---

## ⚙️ Deployment

### 1. Prerequisites

- AWS CLI v2 configured with valid credentials (`aws configure sso` recommended)
- `zip`, `bash`, and `git` installed
- Python 3.12+ locally (for testing or adding libs)

### 2. Create initial structure

```bash
mkdir -p tulipbroker-api/{src/handlers,infra,scripts}
touch tulipbroker-api/{requirements.txt,README.md}
touch tulipbroker-api/src/handlers/{health.py,config.py}
touch tulipbroker-api/infra/api.yaml
touch tulipbroker-api/scripts/deploy.sh
```

### 3. Deploy the stack

From the root of the backend directory:

```bash
cd tulipbroker-api
./scripts/deploy.sh
```

The script will:
1. Package your code into `lambda.zip`
2. Upload it to an S3 artifact bucket
3. Deploy the CloudFormation stack
4. Print the API URLs, for example:

```
-------------------------------------------------
| Output Key      | Output Value                |
|-----------------|-----------------------------|
| ApiBaseUrl      | https://abc123.execute-api.us-east-2.amazonaws.com |
-------------------------------------------------
```

Test it:
```bash
curl https://abc123.execute-api.us-east-2.amazonaws.com/health
```

---

## 🧠 Environment Variables

The Lambda function automatically sets these variables:

| Variable | Example | Purpose |
|-----------|----------|----------|
| `APP_VERSION` | `0.1.0` | Application version |
| `APP_ENV` | `qa` | Environment identifier |
| `AWS_REGION` | `us-east-2` | Lambda region |
| `GIT_SHA` | `e3a4d21` | Commit hash (set at deploy) |
| `BUILD_TIME` | `2025-11-07T21:34:00Z` | UTC timestamp of build |

---

## 🧹 Cleanup

To delete all backend resources:

```bash
aws cloudformation delete-stack --stack-name tulipbroker-api-qa
```

To remove the S3 artifact bucket (optional):
```bash
aws s3 rb s3://tulipbroker-api-qa-artifacts-<account>-<region> --force
```

--- 

## 🖥️ New Laptop Setup

### 1. Prerequisites
- Docker Desktop or Docker Engine (verify with `docker ps`)
- Python 3.12+, `python3-venv`, `pip`, `jq`, Git, AWS CLI v2
- Node.js 20+ and npm (for the UI)
- Optional: `sam` CLI if you plan to run the API locally via SAM

> Debian/Ubuntu note: avoid the `externally-managed-environment` error by always using a virtual environment before running `pip install`.

### 2. Clone the projects
```bash
mkdir -p ~/dev && cd ~/dev
git clone git@github.com:your-org/tulipbroker-api.git
git clone git@github.com:your-org/tulipbroker-ui.git
```
You should end up with parallel directories: `~/dev/tulipbroker-api` and `~/dev/tulipbroker-ui`.

### 3. API project bootstrap
```bash
cd ~/dev/tulipbroker-api
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install localstack awscli-local
```
1. **Start Docker** (log out/in if your user is not yet in the `docker` group).
2. **Launch LocalStack**: `localstack start -d`
3. **Check LocalStack**:
   ```bash
   awslocal sts get-caller-identity
   awslocal dynamodb list-tables
   ```
4. **Deploy the stack inside LocalStack**:
   ```bash
   awslocal cloudformation deploy \
     --stack-name tulipbroker-local \
     --template-file infra/api.yaml \
     --capabilities CAPABILITY_IAM \
     --parameter-overrides Project=tulipbroker-api Env=local
   ```
   This creates the DynamoDB table (with the idempotency GSI), SQS FIFO queue, Lambda, and API Gateway endpoints inside LocalStack.
5. **Discover the local API endpoint**:
   ```bash
   awslocal apigatewayv2 get-apis
   ```
   Note the `ApiEndpoint` URL for the next steps.
6. **Exercise the happy path + idempotency**:
   ```bash
   ORDER_BODY='{"clientId":"local-test","side":"BUY","price":123.45,"quantity":5,"timeInForce":"GTC","idempotencyKey":"local-demo-001"}'
   curl -sS -D - -o response.json -H 'Content-Type: application/json' \
     -X POST "$API_ENDPOINT/api/orders" -d "$ORDER_BODY"
   curl -sS -D - -o response.json -H 'Content-Type: application/json' \
     -X POST "$API_ENDPOINT/api/orders" -d "$ORDER_BODY"
   awslocal dynamodb query \
     --table-name tulipbroker-api-local-orders \
     --index-name IdempotencyKeyIndex \
     --key-condition-expression 'idempotencyKey = :k' \
     --expression-attribute-values '{":k": {"S": "local-demo-001"}}'
   ```
   Expect the first call to return 201 and the second to return 200 with the same `orderId`.

### 4. UI project bootstrap
```bash
cd ~/dev/tulipbroker-ui
npm install
cp .env.example .env.local
```
Set `VITE_API_BASE_URL` inside `.env.local` to the LocalStack API endpoint (or the deployed AWS URL). Then:
```bash
npm run dev
```
Visit the dev server URL (default `http://localhost:5173`) and confirm the UI can talk to the API.

### 5. Combined smoke test checklist
1. `docker ps` works.
2. LocalStack running (`localstack status services` should show `running`).
3. API stack deployed (use `awslocal cloudformation list-stacks`).
4. Run API smoke script (two POSTs with the same `idempotencyKey`).
5. Start UI (`npm run dev`) and load it in the browser.

### 6. Troubleshooting quick hits
- **Docker permission denied**: `sudo usermod -aG docker $USER && newgrp docker`.
- **pip “externally managed”**: always install into `.venv`.
- **LocalStack endpoint URLs**: add optional env vars (e.g., `DYNAMODB_ENDPOINT_URL`) if you need to point `boto3` at LocalStack; by default it will talk to AWS.
- **Stack cleanup**: `awslocal cloudformation delete-stack --stack-name tulipbroker-local` and `localstack stop` when finished.

## 🔮 Future Roadmap

- [ ] Add router Lambda (single entrypoint for all endpoints)
- [ ] Add `/api/version` and `/api/metrics`
- [ ] Integrate with DynamoDB or Aurora Serverless
- [ ] Add CI/CD workflow (GitHub Actions)
- [ ] Replace wildcard CORS with domain-based config

---

## 👨‍💻 Author

**Guus Bosman**  
Created as part of the **TulipBroker** project.  
Built for simplicity, speed, and maintainability.

---

### License
MIT License © 2025 Guus Bosman
