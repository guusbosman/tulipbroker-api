for backend tech stack, I want to use Lambda where possible, written in Python

Backend architecture (single-market overview — multi-Region aware)

Clients → CloudFront → Route53 latency routing
Per Region:

API Gateway (WebSocket + HTTP) → ECS/Fargate (matching façade)

Matching engine (ECS/EKS): maintains local order book, executes matches.

Durable storage:

DynamoDB Global Tables for order metadata and persistent events (global replication)

ElastiCache Redis for fast in-memory order book snapshots & pub/sub fanout

SQS FIFO as ordered event bus for reconciliation + replay

Kinesis / MSK for telemetry & market feed fanout (option)

Aurora Global for analytics/ledger (optional)

Observability: CloudWatch metrics, X-Ray traces, OpenSearch for logs

Cross-Region:

DynamoDB Global Tables replicate accepted orders/events.

Reconciliation service consumes Streams and SQS to reconcile divergent matching results (idempotent replays + compensating cancels).

Route53 + CloudFront handle client failover when a Region is unhealthy.

Typical failure scenarios & what the UI shows

AZ failure inside Region (fast failover)

Matching tasks in one AZ die → load shifts to other AZ tasks.

UI: a few orders momentarily freeze, then resume; depth might briefly lose some liquidity; small stale RTT spike.

Region outage (one Region unreachable)

Orders routed to that Region fail or queue.

UI: orders submitted to that region show “queued → retrying” or are automatically rerouted; portion of order book (if shard was region-owned) goes dark/dim.

Network partition (inter-region link degraded)

Regions continue matching locally; when partition heals, you see reconciliation events: some local fills may be compensated or flagged as conflicted.

UI: visual “reorg” animation — trades flagged orange for conflict, a reconcile panel shows cancelled/compensated fills, account P&L adjustments logged.

Datastore throttling (DynamoDB throttles)

Writes slow down; SQS backlog increases.

UI: “backpressure” banner, queued events count rising, and visible latency increase for order acceptance.

Matching engine CPU spike (simulate via FIS)

Orders delayed, potential timeouts.

UI: time-to-accept increases, flash warnings, clients may see duplicate orders (if retries not idempotent).

FIS (Fault Injection) experiments to run

ECS task stop by AZ — simulate sudden AZ loss.

Network blackhole between Regions (simulate partition).

Inject latency on API Gateway or specific subnet (simulate jitter).

Throttle DynamoDB (reduce RCUs or inject artificial Lambda latency).

ElastiCache failover (promote replica) to see reconnection behavior.

For each experiment, watch: order latency, queued events count, trade tape divergence, reconciliation logs.

Data model & events (suggested)

Orders table (DDB): pk=ORDER#<id> attrs: client, side, qty, price, status, region, seq, idempotencyKey

Trades table (DDB): pk=TRADE#<id> attrs: buyOrder, sellOrder, qty, price, timestamp, region

Events stream: ordered market events EVT#<ts>#<id> pushed to SQS FIFO for ordered processing

Snapshots in Redis for fast book display; persisted periodically to DynamoDB

Idempotency keys + monotonic sequence numbers are critical for avoiding double fills during retries.