Great question. Here’s a pragmatic way to make **resiliency tests automatic, visible, and blocking** after every change—using standard AWS services plus FIS.

# High-level flow

1. **Code pushed → CI** runs unit/integration tests and builds images/bundles.
2. **Ephemeral env** (or a shared pre-prod) is created by IaC.
3. **Blue/green deploy** to that env.
4. **Automated resiliency test suite** runs:

   * start synthetic load,
   * run FIS experiments with guardrails,
   * verify SLOs/health from CloudWatch/X-Ray/Route 53 checks.
5. **Gate**: if all checks pass, promote to prod; otherwise rollback + surface a rich report.

# Opinionated AWS blueprint

### CI/CD & environments

* **CodePipeline or GitHub Actions** → **CodeBuild** → **CDK/Terraform** to create/update:

  * “Preview” or **ephemeral environments** (stack per PR) *or* one **pre-prod**.
  * **ECS** (or EKS/App Runner/Lambda) blue/green with **CodeDeploy** + automatic rollback (or ECS deployment circuit breaker).

### Orchestration of tests

* **Step Functions** is your test conductor (one state machine per test suite):

  * Start/scale **load generators** (e.g., k6 in **Fargate**, or “Distributed Load Testing on AWS” solution).
  * Kick off **CloudWatch Synthetics** canaries for key user journeys.
  * Invoke **FIS** experiments (via pre-created templates) with **guardrail CloudWatch alarms**.
  * Poll/aggregate metrics and **assert SLOs** (p95 latency, error rate, availability, data consistency checks).
  * On failure: stop experiments/load, collect diagnostics, create **OpsCenter OpsItem**, post to **Slack** via **AWS Chatbot**, and fail the pipeline.

### Chaos/resiliency content (FIS)

Create **FIS templates** that simulate the failures you care about (scoped by tags!):

* **AZ loss**: stop a percentage of ECS tasks in one AZ; optional ElastiCache failover.
* **Network impairment**: blackhole route in a test subnet; inject API Gateway latency.
* **Data throttling**: reduce DynamoDB capacity in pre-prod or add artificial write delay via a feature-flagged Lambda.
* **Regional failover** (pre-prod only): take the regional endpoint unhealthy and verify Route 53 + client reconnection.

All templates must include **stop conditions** (e.g., `TargetTrackingScaling` alarms, `5XX` rate, `Latency p95`) so experiments abort automatically if things go sideways.

### Assertions (the “tests”)

* **API SLOs**: CloudWatch metric math on p95/p99 latency, 4xx/5xx, successful matches per second.
* **Client-visible SLOs**: Synthetics step success, time-to-first-fill, reconnect time.
* **Data integrity**: Step Functions task runs **consistency queries** (e.g., replay events from SQS/DDB Streams into a validator Lambda; assert no duplicate fills, monotonic sequences).
* **Availability**: Route 53 health checks remain green; WebSocket connection error rate below threshold.

### Visibility & reports

* **Single “Resiliency Test Run” dashboard** in **CloudWatch** (widgets pinned to the exact env/stack).
* **CodeBuild Reports** (JUnit) for pass/fail assertions.
* **S3** for artifacts: k6 summaries, FIS execution JSON, canary HAR files, X-Ray trace summaries.
* Query history in **Athena** (Glue table over S3 results) and a **QuickSight** dashboard for trends.
* Notifications via **SNS → Slack/Email**. On failure, auto-open an **OpsCenter** item with links to the run, dashboards, and logs.

### Continuous posture (outside the pipeline)

* **AWS Resilience Hub**: baseline assessment + recommended FIS experiments; run nightly/weekly.
* **CloudWatch Synthetics** 24/7 on prod (no chaos), gated “game days” in a maintenance window with FIS + strict guardrails.
* **Feature flags** (CloudWatch Evidently/LaunchDarkly) to inject controlled latency/failure without redeploy.

---

## Concrete pieces you can copy-paste

### 1) Step Functions (resiliency test suite skeleton)

```json
{
  "Comment": "Post-deploy resiliency suite",
  "StartAt": "StartLoad",
  "States": {
    "StartLoad": {
      "Type": "Task",
      "Resource": "arn:aws:states:::ecs:runTask.sync",
      "Parameters": {
        "Cluster": "<load-cluster-arn>",
        "TaskDefinition": "<k6-task-def>",
        "LaunchType": "FARGATE",
        "NetworkConfiguration": { "AwsvpcConfiguration": { "AssignPublicIp": "ENABLED", "Subnets": ["<subnet>"] } },
        "Overrides": { "ContainerOverrides": [{ "Name": "k6", "Command": ["run","/scripts/smoke.js","--duration","3m"] }] }
      },
      "Next": "RunFIS"
    },
    "RunFIS": {
      "Type": "Task",
      "Resource": "arn:aws:states:::aws-sdk:fis:startExperiment",
      "Parameters": { "ExperimentTemplateId": "<fis-template-az-failure>" },
      "Next": "Wait"
    },
    "Wait": { "Type": "Wait", "Seconds": 120, "Next": "AssertSLOs" },
    "AssertSLOs": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": { "FunctionName": "<assertion-lambda>", "Payload": { "slo": { "p95Ms": 250, "errorRate": 0.01 } } },
      "ResultPath": "$.assert",
      "Next": "PassGate"
    },
    "PassGate": {
      "Type": "Choice",
      "Choices": [{ "Variable": "$.assert.Payload.pass", "BooleanEquals": true, "Next": "StopLoad" }],
      "Default": "Fail"
    },
    "StopLoad": { "Type": "Succeed" },
    "Fail": {
      "Type": "Task",
      "Resource": "arn:aws:states:::sns:publish",
      "Parameters": { "TopicArn": "<alerts-topic>", "Message": "Resiliency suite FAILED" },
      "End": true
    }
  }
}
```

### 2) FIS template (partial — ECS tasks in one AZ)

```json
{
  "description": "Kill 30% of ECS tasks in one AZ",
  "targets": {
    "TasksAzA": {
      "resourceType": "aws:ecs:task",
      "resourceTags": { "App": "PaperBroker", "Tier": "GameState" },
      "selectionMode": "PERCENT(30)"
    }
  },
  "actions": {
    "StopTasks": {
      "actionId": "aws:ecs:stop-task",
      "parameters": { "reason": "AZ fail test" },
      "targets": { "Tasks": "TasksAzA" }
    }
  },
  "stopConditions": [
    { "source": "aws:cloudwatch:alarm", "value": "<CriticalLatencyAlarmArn>" }
  ],
  "roleArn": "<fis-role-arn>"
}
```

### 3) Pipeline gate (CodePipeline stage sketch)

* **Source → Build → Deploy-PreProd (blue/green)**
* **ResiliencySuite (Step Functions Invoke)** — **must pass**
* **Manual Approval** (optional)
* **Deploy-Prod (blue/green)**

---

## Guardrails & tips

* **Tag everything** (App, Env, Component) so FIS targets are scoped tightly.
* Keep **blast radius small** in pre-prod; use realistic data but scrub PII.
* Make assertions **fast** (<10–15 min) so pipeline lead time stays reasonable; reserve heavy chaos for nightly runs.
* Store a **“golden SLO profile”** per service and compare deltas to catch regressions even if absolute thresholds are met.
* For multi-Region, run **two suites**: intra-Region (AZ fail) and inter-Region (failover) with dedicated Route 53 health checks.

---

If you want, I can:

* generate a **CDK app** that stands up the Step Functions + FIS templates + CodePipeline wiring, or
* add a **k6 script** tailored to your endpoints (order submit, order book stream, cancel), plus a Lambda **assertion** function that checks CloudWatch metrics for p95 and error rate during the experiment.

Any preferences on CI (CodePipeline vs GitHub Actions) and whether you want **ephemeral envs per PR**?


Here’s a **perfectly realistic “bad change”** for PaperBroker that will light up your CI + resiliency suite.

## Bug scenario: idempotency guard accidentally removed → **duplicate orders / double fills under retry**

### What it looked like before (correct)

We wrote each order once using a **conditional put** in DynamoDB that enforces idempotency:

```ts
// src/matching/submitOrder.ts (good)
export async function submitOrder(cmd: SubmitOrderCmd) {
  const pk = `ORDER#${cmd.orderId}`;
  const ik = `CLIENT#${cmd.clientId}#IK#${cmd.idempotencyKey}`;

  await ddb.put({
    TableName: process.env.ORDERS_TABLE!,
    Item: {
      pk, sk: pk,
      clientId: cmd.clientId,
      side: cmd.side, qty: cmd.qty, price: cmd.price,
      status: "ACCEPTED",
      idempotencyKey: ik,
      createdAt: Date.now(),
    },
    // Prevent duplicates on retries (network flaps / 5xx)
    ConditionExpression: "attribute_not_exists(pk)",
  }).promise();

  // enqueue into SQS FIFO (group=market) for matching
  await sqs.sendMessage({
    QueueUrl: process.env.EVENTS_FIFO_URL!,
    MessageGroupId: `market-${cmd.symbol}`,
    MessageDeduplicationId: ik, // second guard
    MessageBody: JSON.stringify({ type: "OrderAccepted", pk }),
  }).promise();

  return { ok: true };
}
```

### The “innocent” change that introduces the bug

A dev wanted to attach a tracing tag and “simplify” the write by switching to an **upsert** (UpdateItem)… and accidentally **removed the conditional write** and the SQS dedupe:

```diff
- await ddb.put({ ... , ConditionExpression: "attribute_not_exists(pk)" }).promise();
+ await ddb.update({
+   TableName: process.env.ORDERS_TABLE!,
+   Key: { pk, sk: pk },
+   UpdateExpression: "SET clientId=:c, side=:s, qty=:q, price=:p, #st=:st, trace=:t, createdAt = if_not_exists(createdAt, :ts)",
+   ExpressionAttributeValues: {
+     ":c": cmd.clientId, ":s": cmd.side, ":q": cmd.qty, ":p": cmd.price,
+     ":st": "ACCEPTED", ":t": cmd.traceId, ":ts": Date.now()
+   },
+   ExpressionAttributeNames: { "#st": "status" }
+ }).promise();
...
- MessageDeduplicationId: ik,
+ // (forgot dedupe)
```

**Impact:** if the client retries (e.g., brief 502, AZ disruption), the same `orderId/idempotencyKey` can be **accepted multiple times**, and the events hit the matcher several times → **double fills**.

---

## Tests you add to your repo (these should FAIL with the buggy change)

### 1) Unit test: idempotency write is conditional

```ts
// tests/submitOrder.unit.test.ts
import { submitOrder } from "../src/matching/submitOrder";
import * as ddb from "../src/lib/ddb";
import * as sqs from "../src/lib/sqs";

test("submitOrder enforces idempotency on first write", async () => {
  const spyPut = jest.spyOn(ddb, "put").mockResolvedValue({} as any);
  const spyUpd = jest.spyOn(ddb, "update").mockResolvedValue({} as any); // should NOT be called
  const spySqs = jest.spyOn(sqs, "sendMessage").mockResolvedValue({} as any);

  await submitOrder({
    orderId: "o-1",
    clientId: "c-1",
    idempotencyKey: "ik-1",
    side: "BUY", qty: 10, price: 111.11, symbol: "PBKR",
  });

  // Assert it used conditional Put with dedupe
  const putParams = spyPut.mock.calls[0][0];
  expect(putParams.ConditionExpression).toBe("attribute_not_exists(pk)");

  const sqsParams = spySqs.mock.calls[0][0];
  expect(sqsParams.MessageDeduplicationId).toContain("ik-1");

  expect(spyUpd).not.toHaveBeenCalled();
});
```

### 2) Integration test: retry does NOT create duplicates

```ts
// tests/idempotency.integration.test.ts
import { submitOrder } from "../src/matching/submitOrder";
import { getOrderById, getEventsByOrder } from "./utils";

it("retrying the same order (same idempotencyKey) does not duplicate", async () => {
  const cmd = { orderId:"o-2", clientId:"c-1", idempotencyKey:"ik-2", side:"SELL", qty:5, price:111.4, symbol:"PBKR" };
  await submitOrder(cmd);
  // Simulate network retry
  await submitOrder(cmd);

  const order = await getOrderById("o-2");
  expect(order.status).toBe("ACCEPTED");

  const events = await getEventsByOrder("o-2");
  // Exactly one OrderAccepted should exist
  expect(events.filter(e => e.type === "OrderAccepted")).toHaveLength(1);
});
```

### 3) Matcher invariant test: **no double fills**

```ts
// tests/matcher.invariant.test.ts
import { reconcileLedger } from "../src/ledger/reconcile";
import { simulateMatchBurst } from "./utils";

it("never produces double fills for the same order leg", async () => {
  await simulateMatchBurst({ orders: 100, retryProbability: 0.2 }); // triggers retries
  const violations = await reconcileLedger(); // scans trades vs orders
  expect(violations.doubleFills).toHaveLength(0);
});
```

---

## k6 load (CI Step Functions → Fargate)

```js
// load/smoke-idem.js
import http from "k6/http";
import { check, sleep } from "k6";

export const options = { vus: 20, duration: "2m" };

export default function () {
  const key = `${__VU}-${Date.now()}`; // idempotency key
  const body = JSON.stringify({ orderId: key, idempotencyKey: key, side: "BUY", qty: 10, price: 111.11, symbol: "PBKR" });

  // send once, then retry quickly (simulate transient error)
  const r1 = http.post(`${__ENV.API}/order`, body, { headers: { "Content-Type": "application/json" }});
  sleep(Math.random() * 0.2);
  const r2 = http.post(`${__ENV.API}/order`, body, { headers: { "Content-Type": "application/json" }});

  check(r1, { "201 accepted": (res) => res.status === 201 || res.status === 200 });
  check(r2, { "retry not duplicate": (res) => [200,201,409].includes(res.status) });
}
```

---

## Assertion Lambda (pipeline gate) – FAILS when bug is present

```ts
// assert-slos.ts
import { CloudWatch } from "aws-sdk";
import { scanViolations } from "./lib/consistency";

export const handler = async () => {
  // SLOs
  const p95LatOk = await cwBelow("LatencyP95Ms", 250);
  const errRateOk = await cwBelow("Http5xxRate", 0.01);

  // Data integrity: no double fills in last N minutes
  const { doubleFills } = await scanViolations({ minutes: 5 });

  const pass = p95LatOk && errRateOk && doubleFills === 0;

  return { pass, details: { p95LatOk, errRateOk, doubleFills } };
};

async function cwBelow(metricName: string, threshold: number) {
  const cw = new CloudWatch();
  const r = await cw.getMetricStatistics({
    Namespace: "PaperBroker",
    MetricName: metricName,
    StartTime: new Date(Date.now() - 5 * 60 * 1000),
    EndTime: new Date(),
    Period: 60,
    Statistics: ["Average"],
  }).promise();

  const v = r.Datapoints?.slice(-1)[0]?.Average ?? 0;
  return v <= threshold;
}
```

With the buggy change, `doubleFills > 0` → **Step Functions gate fails → pipeline fails**. Your CloudWatch dashboard will also show **trades > orders** anomalies.

---

## What you’ll see when you run the pipeline with this bug

* **Unit tests** fail (the import path used UpdateItem, missing `ConditionExpression`, missing SQS dedupe).
* If unit tests missed it, **integration test** fails (two “OrderAccepted” for same order).
* If that slipped, **k6 + FIS** under light chaos causes retries → assertion Lambda finds **double fills** → gate fails.
* Report includes:

  * failing Jest/JUnit,
  * k6 summary,
  * FIS execution JSON,
  * violation counts from the consistency scan.

---

## The proper fix (what the dev will push)

* Revert to **conditional put** or **UpdateItem with a condition**:

```ts
ConditionExpression: "attribute_not_exists(pk)"
```

* Restore **SQS `MessageDeduplicationId`** = clientId + idempotencyKey (or sha256).
* Add a **DDB GSI** on `idempotencyKey` if you support client-side idempotency across `orderId` reuse.

---

## Want me to

* drop these tests and the k6 script into a **ready-to-run repo skeleton**,
* or wire them into your **existing CodePipeline/Step Functions** template?

And to confirm expectations: is your rule **“an order with the same `(clientId, idempotencyKey)` MUST be applied exactly once, even if `orderId` is retried”**? If it’s different, tell me and I’ll adjust the tests.
