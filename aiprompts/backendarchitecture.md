| Field   | Value                              |
|---------|------------------------------------|
| Context | Single-market backend architecture |
| Author  | Guus (with Codex notes)            |
| Status  | Current as of **2025-11-14**       |

## Architectural Intent

- Favor managed services and Lambda where possible, with Python as the primary runtime.
- Design for multi-Region resilience even while operating a single market.

## Phase Roadmap

| Capability                | Phase 1 — **SimFill** (current)                                                                                           | Phase 2 — **Real Matcher** (future)                                                                                   |
|---------------------------|---------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------|
| Ingress façade            | API Gateway (HTTP/WebSocket) → Lambda                                                                                    | API Gateway → ECS/Fargate façade                                                                                       |
| Matching / fills          | Lambda or Step Functions simulated fill worker consuming SQS FIFO, deterministic `TradeExecuted` / `OrderCancelled` events | ECS/EKS matching engine with Redis snapshots; downstream contracts remain unchanged                                     |
| Storage of orders/events  | DynamoDB (single-Region today, planned Global Tables)                                                                    | DynamoDB Global Tables with reconciler; Redis snapshots for low-latency book, Aurora Global optional for analytics     |
| Market event bus          | SQS FIFO (ordered, replayable)                                                                                           | SQS FIFO + Redis pub/sub + optional Kinesis/MSK for telemetry                                                          |
| Clients                   | Route53 latency-routing + CloudFront                                                                                     | Same, plus regional failover policies                                                                                  |

## Per-Region Data Plane

1. Clients → CloudFront → Route53 latency routing.
2. API Gateway (HTTP + WebSocket) → Lambda façade (Phase 1) or ECS/Fargate façade (Phase 2).
3. Simulated fill worker (Phase 1) consumes SQS FIFO, writes outcomes back to DynamoDB, emits events.
4. Matching engine (Phase 2) runs on ECS/EKS, maintains local book, publishes fills/cancels.

## Durable Stores & Buses

| Service              | Purpose                                                                  | Notes                                      |
|----------------------|--------------------------------------------------------------------------|--------------------------------------------|
| DynamoDB             | Orders, trades, event ledger                                             | Global Tables planned for multi-Region     |
| Redis (ElastiCache)  | In-memory order book snapshots, pub/sub fanout                           | Primarily Phase 2                          |
| SQS FIFO             | Ordered event bus for reconciliation and deterministic replay             | Today’s backbone                           |
| Kinesis / MSK        | Optional telemetry / market feed fan-out                                 | Roadmap                                    |
| Aurora Global        | Optional analytics / ledger                                               | Roadmap                                    |

Observability: CloudWatch metrics, X-Ray traces, OpenSearch for log search.

## Cross-Region Story

1. DynamoDB Global Tables replicate accepted orders/events (future enablement).
2. Reconciliation service consumes Streams/SQS to resolve divergent results with idempotent replays.
3. Route53 + CloudFront steer clients away from unhealthy Regions.

## Failure Scenarios (UI expectations)

1. **AZ failure**: affected matching tasks die, load shifts to remaining AZs; UI blips (frozen orders, minor latency spike) then recovers.
2. **Region outage**: orders to that Region fail/queue; UI shows “queued → retrying” or dimmed book for region-owned shard.
3. **Inter-region partition**: Regions match independently; later reconciliation may emit compensations (UI flags trades orange, shows reconcile panel).
4. **DynamoDB throttling**: writes slow, SQS backlog grows; UI shows backpressure banner, higher accept latency, “Awaiting simulated fill” badges.
5. **Matcher CPU spike**: orders delayed/time out; UI surfaces higher time-to-accept, possible duplicate submissions if retries lack idempotency.

## Fault-Injection Playbook

- Stop ECS tasks per AZ (simulate AZ loss).
- Blackhole network between Regions (partition).
- Inject latency at API Gateway/subnets (jitter).
- Throttle DynamoDB or inject Lambda latency.
- Force ElastiCache failover to observe reconnects.

Watch: order latency, queued events, trade tape divergence, reconciliation logs.

## Data Model & Events

- **Orders (DynamoDB)**: `pk=ORDER#<id>`; attrs include client, side, qty, price, status, region, acceptedAz, seq, idempotencyKey, simulationSeed.
- **Trades (DynamoDB)**: `pk=TRADE#<id>`; attrs include buyOrder, sellOrder, qty, price, timestamp, region, fillAz.
- **Events stream**: ordered events `EVT#<ts>#<id>` pushed to SQS FIFO for deterministic processing.
- **Redis snapshots**: fast book display, periodic persistence to DynamoDB.
- **Keys & sequencing**: idempotency keys plus monotonic sequence numbers prevent double fills during retries.

## Current vs Future State (Summary)

- **Current (Nov 2025)**: Phase 1 live in `us-east-2` — Lambda façade, DynamoDB orders, SQS FIFO, SimFill worker, simple UI resiliency cues.
- **Future**: Phase 2 introduces ECS/EKS matcher, Redis snapshots, optional Aurora analytics, while retaining external APIs and strengthening multi-Region reconciliation.
