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
