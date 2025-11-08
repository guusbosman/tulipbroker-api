# TulipBroker — Fundamental Logic & Engineering Rules

> Use this document as a **prompt for code assistants** and as the single source of truth for the system’s behavior. Everything here is normative. If code and this doc disagree, **this doc wins** until amended in PR.

---

## 1) Domain & Scope
- Simulates a **brokerage order entry + matching** pipeline for a single instrument (extendable to many).
- Not real money. Focus: **resiliency, consistency, latency** under multi‑AZ / multi‑Region deployments.

## 2) Core Concepts
- **Order**: { orderId, clientId, idempotencyKey, side(BUY|SELL), qty, price, timeInForce(GTC|IOC), ts }
- **OrderBook**: in‑memory best-bid/offer with price levels and FIFO within a level.
- **Event**: immutable record on the event bus (`OrderAccepted`, `OrderCancelled`, `TradeExecuted`, `Reconciled`, etc.).
- **Trade**: result of matching two opposite orders at a price/qty.

## 3) Golden Invariants (MUST HOLD)
1. **Idempotency**: (`clientId`, `idempotencyKey`) identifies one logical order submit. Applying the same pair multiple times **MUST NOT** create duplicates.
2. **No Double Fills**: An order leg cannot appear in two trades whose summed qty exceeds the order qty.
3. **Price-Time Priority**: Match best price first; within price level, older orders first.
4. **Monotonic Sequences**: Every market event has a strictly increasing `seq` within its shard.
5. **Durability**: Every accepted order and executed trade is durably stored in DynamoDB before being acknowledged to the client.
6. **At-Least-Once Events**: Consumers must tolerate duplicates; all handlers are **idempotent**.
7. **Clock Independence**: Do not rely on synchronized clocks for correctness; only for metrics.

## 4) Lifecycle (Happy Path)
1. **Submit** → Validate → Conditional write to `Orders` (DDB) → enqueue `OrderAccepted` to SQS FIFO (dedupe) → 201 to client.
2. **Match – Phase 1 (SimFill)** → Lambda/Step Functions consumer reads FIFO, deterministically decides fills/cancels (respecting price/time rules), persists results to `Orders`/`Trades` (DDB), then publishes events/WebSocket notifications. **Phase 2** swaps in the real matcher (ECS/EKS) without changing external contracts.
3. **Cancel** → Conditional update order status in DDB if still open → emit `OrderCancelled`.

## 5) Idempotency & Deduplication Rules
- **Primary key**: `pk = ORDER#<orderId>`, `sk = pk`.
- **Idempotency key**: `IK = hash(clientId + idempotencyKey)` stored on the order item.
- **Write guard**: `ConditionExpression: attribute_not_exists(pk)` on the first write (or UpdateItem with equivalent condition).
- **Queue dedupe**: `MessageDeduplicationId = IK`; `MessageGroupId = market|symbol` for strict ordering.
- **Retries**: All public handlers must be **safe to retry**. Never mutate on read paths.

## 6) Matching Engine Rules
- **Cross conditions**: BUY crosses when `buy.price >= bestAsk.price`; SELL crosses when `sell.price <= bestBid.price`.
- **Trade price**: price of the **resting** order (maker‑taker model) unless specified otherwise.
- **Partial fills** allowed; remaining qty stays on book unless TIF=IOC.
- **Order states**: `PENDING → ACCEPTED → (OPEN | CANCELLED | FILLED | PARTIALLY_FILLED)`.
- **Fairness**: FIFO within price level by `acceptedAt` (the timestamp when DDB write succeeded), not client‑clock.
- **Phase 1 SimFill**: Simulator MUST be deterministic per order (seed = orderId/idempotency hash), persist trades before emitting, and still honor all invariants above so recordings stay valid when the real engine arrives.

## 7) Multi‑Region Consistency
- **Model A (default)**: Single logical shard with *one active Region as leader*. Followers accept reads, forward writes, and mirror state via **DynamoDB Global Tables**.
- **Failover**: If leader unhealthy, Route 53 flips; new leader elected; consumers replay from Streams to reconcile.
- **Reconciliation**: If optimistic writes occurred in follower(s), produce `Reconciled` events with compensating cancels; never delete history.

## 8) Storage Model
- **DynamoDB Global Tables**
  - `Orders(pk, sk, status, side, qty, price, filledQty, idempotencyKey, clientId, createdAt, region, acceptedAz)`
  - `Trades(pk=TRADE#id, sk, buyOrderId, sellOrderId, price, qty, ts, region, fillAz)`
  - **Streams** feed reconciliation & analytics.
- **Redis (ElastiCache)**: optional snapshot of top‑of‑book; pub/sub for fanout. Cache misses are OK.
- **SQS FIFO**: ordered event bus per symbol/shard; consumers scale by `MessageGroupId`.

## 9) APIs (HTTP/WebSocket)
- `POST /order` → 200/201 `{ orderId }` on acceptance; 409 on duplicate idempotency key.
- `POST /cancel` → 200 on success, 404 if order not found/open.
- `GET /book` → top‑of‑book + 5 levels depth.
- WebSocket topics: `trades`, `book`, `orderStatus`.
- **Auth**: Cognito JWT required; enforce clientId from token, not body.

## 10) Errors & Status Codes
- `400` validation; `401/403` auth; `404` order not found; `409` idempotency conflict; `5xx` transient.
- Never leak internal exceptions; return correlation id + log with structured context.

## 11) Observability & SLOs
- Metrics (namespace `TulipBroker`): `Http5xxRate`, `LatencyP50/P95/P99`, `EventLag`, `WSDisconnectRate`, `DoubleFillViolations`.
- Tracing: X‑Ray spans around DDB, SQS, Redis, and matcher loop.
- **SLOs** (pre‑prod & prod): p95 HTTP ≤ 250 ms; error rate ≤ 1%; reconnect ≤ 5 s; zero double fills.

## 12) Resiliency & Chaos Rules
- FIS experiments **must** include stop conditions: CloudWatch Alarms on SLOs.
- Allowed failures in pre‑prod: ECS task stop (AZ‑scoped), Redis failover, API GW latency, DDB capacity reduction, inter‑Region route blackhole.
- Client behavior: exponential backoff, jitter, region re‑pin by health signal; UI shows "syncing…" and ghost states until confirmed.

## 13) Testing Policy
- **Unit tests**: pure functions (matching, price‑time ordering, idempotency guards).
- **Integration tests**: submit+retry does not duplicate; cancel semantics; partial fill math.
- **Invariant tests**: off‑line ledger reconciliation finds **zero** double fills.
- **Load tests** (k6): smoke and stress profiles; exported to S3.
- **Pipeline gate**: Step Functions calls Assertion Lambda; must return `{ pass: true }`.

## 14) Performance Guidelines
- Avoid hot partitions: randomize ORDER ids; use narrow projections; batch writes where safe.
- Redis is an **optimization**; correctness cannot depend on it.
- WebSocket backpressure: drop to HTTP long‑poll if socket unstable.

## 15) Coding Standards
- TypeScript strict mode. No `any` in domain logic.
- All handlers idempotent. Side effects (publishing) come **after** durable write.
- Structured logs (JSON) with `requestId`, `clientId`, `orderId`, `region`, `seq`.

## 16) Configuration & Flags
- Env vars: `ORDERS_TABLE`, `TRADES_TABLE`, `EVENTS_FIFO_URL`, `REGION_ROLE`, `LEADER_REGION`, `FEATURE_FLAGS`.
- Feature flags: `injectLatencyMs`, `forceLeader`, `denyWrites` (test only).

## 17) Runbooks (Condensed)
- **Idempotency breach**: stop writers, run reconciliation scanner, emit compensating cancels, restore dedupe config.
- **Redis loss**: invalidate caches, continue from DDB; scale consumers; no data loss.
- **Region failover**: confirm Route 53 health flip, promote new leader, replay Streams, watch `EventLag`.

## 18) Security
- Enforce least privilege per component; SQS/DDB KMS encryption enabled.
- Validate all inputs server‑side; never trust client‑provided `clientId`.

## 19) Acceptance Criteria (Definition of Done)
- All unit/integration/invariant tests pass.
- Assertion Lambda returns `{ pass: true }` under smoke + chaos suite.
- No `console.error` in client; WebSocket reconnect logic covered by tests.
- Dashboards & alarms deployed with the stack.

---

### Appendix A — Pseudocode: Conditional Accept
```ts
function acceptOrder(cmd) {
  const pk = `ORDER#${cmd.orderId}`;
  const ik = sha256(cmd.clientId + cmd.idempotencyKey);

  ddb.put({
    TableName: ORDERS_TABLE,
    Item: {...cmd, pk, sk: pk, status: "ACCEPTED", idempotencyKey: ik, createdAt: now()},
    ConditionExpression: "attribute_not_exists(pk)",
  });

  sqs.sendMessage({
    QueueUrl: EVENTS_FIFO_URL,
    MessageGroupId: `market-${cmd.symbol}`,
    MessageDeduplicationId: ik,
    MessageBody: { type: "OrderAccepted", pk },
  });
}
```

### Appendix B — Reconciliation Invariant
```
For each order O:
  sum(trade.qty where trade.orderId == O.id) <= O.qty
```

> If you need to deviate from these rules, open a PR updating this document first.
