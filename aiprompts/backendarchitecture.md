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

## JIRA Ticket Draft — Add Multi-User Personas to TulipBroker (TB-201)

**Status:** ✅ *Implemented* — Persona dropdown, persona-enriched orders, and backend validation are live (see `src/handlers/orders.py`, `tulipbroker-ui/src/PersonaContext.tsx`).

**Summary:**  
Introduce user personas so Guus can switch between preconfigured traders in the UI, show their profile pictures, and persist the submitting user on every order without adding real authentication.

**Requirements**

1. **Client personas**
   - Render a top-right dropdown trigger (avatar + selected name). Trigger opens a list of all configured users; selecting a name switches the active persona immediately (no password needed).
   - Display the chosen user’s profile photo (small circle, ~32px) in the dropdown trigger and beside their name inside the list.
   - Active persona state influences both the order form submission (clientId/idempotency seed) and any UI elements showing “submitted by …”.
2. **Server contract**
   - Orders POST body must carry a `userId` (or reuse `clientId` but back it with a stable user registry).
   - Store canonical persona metadata (display name, avatar URL, short historical description) in a dedicated “Users” structure (JSON file or DynamoDB table) rather than repeating full details per order.
   - Lambda persists just the `userId` on each order; the GET `/api/orders` response should enrich rows with the current persona metadata (join against the users map) so older orders pick up updated avatars/descriptions automatically.
3. **Visual cues**
   - In the orders list, show the profile photo + user name for each entry (e.g., “Submitted by Carolus Clusius”).
   - Ensure profile photos are optimized (≤10 KB, 64×64) and stored somewhere accessible (e.g., `public/avatars`).

**Seed Personas (famous tulip figures)**

| userId | Display Name        | Avatar concept                         | Historical note |
|--------|---------------------|----------------------------------------|-----------------|
| clusius | Carolus Clusius     | Renaissance botanist portrait stylized | Popularized tulip cultivation in Europe |
| oosterwijck | Maria van Oosterwijck | Dutch flower painter pastel avatar    | Known for detailed floral still lifes   |
| leeuwenhoek | Antonie van Leeuwenhoek | Merchant-scientist with tulip lapel | Delft-based merchant, early tulip trader/scientist |

*(Feel free to swap avatar art as long as each is visually distinct and tulip-themed.)*

**Acceptance Criteria**

1. Dropdown shows the three personas, highlights the current selection, and switches context without page reload.
2. Submitting an order logs the chosen `userId` in Lambda (CloudWatch log includes `userId`).
3. GET `/api/orders` returns the user data, and the UI renders avatar + name in each row.
4. Unit tests cover: new handler validation (reject missing userId), persistence to Dynamo, and UI state switching.

**Out of scope (Phase 1)**

- No real auth or identity federation.
- No per-user permissions beyond selecting a persona.
- No persistence of custom avatars beyond the seeded three.

**Testing**

- Backend: pytest coverage for POST `/api/orders` enforcing `userId`, `_order_response_payload` enrichment via the user registry, and Dynamo persistence storing `userId`.
- Frontend: React Testing Library/Vitest verifying persona dropdown switching (state + avatar) and that order rows render the selected user’s name and avatar.

## JIRA Ticket Draft — User Management & Avatar Uploads (TB-202)

**Status:** ✅ *Implemented* — Settings → Users panel, avatar upload/preview, and `/api/personas` CRUD (DynamoDB-backed) shipped in the current repo.

**Summary:**  
Add a lightweight “Settings → Users” admin surface in the UI so Guus can create/edit personas (name + avatar). Include guidance for generating stylized avatars inspired by famous tulip figures and decide where to store those assets.

**Requirements**

1. **Settings screen updates**
   - Add a sidebar item (“Users”) under Settings.
   - Screen lists existing personas with avatar, display name, and a “switch” indicator showing which persona is currently active.
   - Provide a “New user” card (name input, optional description) plus a file-picker for avatar upload.
   - Allow editing/removing existing personas (Phase 1: simple inline form; confirm before delete).
2. **Avatar handling**
   - Accept PNG/JPEG uploads up to ~200 KB, auto-resize/crop to 128×128 circular thumbnails.
   - Store generated avatars under `tulipbroker-ui/public/avatars/<userId>.png` for baked-in assets; uploaded ones can reside in, e.g., `public/uploads/avatars` (until we add real storage).
   - For the seed personas, generate stylized cartoon portraits referencing their historical likeness:
     - **Carolus Clusius:** scholarly botanist in 16th-century attire holding a tulip bulb.
     - **Maria van Oosterwijck:** vibrant Dutch painter with tulip bouquet palette.
     - **Antonie van Leeuwenhoek:** merchant-scientist peering through a brass microscope with tulip lapel pin.
   - Suggested approach: use DALL·E / Midjourney prompts to produce cartoonish portraits, then export to PNG and place under `public/avatars/`.
3. **Backend/UI integration**
   - Update persona config (JSON file or API) to include avatar URL and optional bio.
   - Order submission should reference the persona’s `userId`, display name, and avatar path.

**Acceptance Criteria**

1. Settings → Users screen renders list + form and persists to persona config (mock JSON for now). Remove the existing hard-coded “Alex Trader” widget in settings in favor of the new persona list.
2. Uploading an avatar immediately shows a preview and stores the file locally.
3. The main app dropdown reflects new personas without code changes (data-driven).
4. Docs explain how to regenerate the three seed avatars with prompts + instructions on storing them.

## JIRA Ticket Draft — Per-Persona Cash Balance (TB-203)

**Status:** ⏳ *Not implemented yet* — No balance tables, Lambda, or UI widgets exist in the repo today.

**Intent**

Introduce a resilient cash-balance subsystem so each persona has a ledgered balance that increases when their sell orders fill and decreases when their buy orders fill. The balance should only move once an order reaches “ACCEPTED” status (simulated fill complete in Phase 1). Surface that balance in the UI (dedicated component + Portfolio Overview integration).

### Backend Requirements

1. **Balance storage**
   - Add a `balances` DynamoDB table (partition key `userId`, sort key `snapshotAt` for optional history) or extend the existing orders table with a `BALANCE#<userId>` PK.
   - Store fields: `userId`, `currentBalance`, `currency` (`fl`), `updatedAt`, plus optional historical ledger entries (`balanceEvents` stream for analytics).

2. **Mutation workflow**
   - Order lifecycle today: POST `/api/orders` → Dynamo write → simulated fill (Lambda/SQS) → status transitions.
   - New step: when an order transitions into `ACCEPTED`, enqueue a `BalanceAdjustment` event (SQS FIFO) describing `{ userId, orderId, side, quantity, price, delta }`.
   - A dedicated Lambda processes these events:
     - Calculates `delta = price * quantity` (floats converted to `Decimal`).
     - For `BUY`, subtract `delta`; for `SELL`, add `delta`.
     - Uses Dynamo conditional updates to ensure idempotency (check `lastOrderId` attribute matches).
     - Writes a ledger entry with `reason`, `orderId`, and resulting balance.
   - Future resilience: replicate balances via Dynamo Streams + Lambda to maintain a warm standby (or move to Global Tables).

3. **Failure handling**
   - If the balance update fails (conditional check), retry with exponential backoff; DLQ after N attempts.
   - Balance Lambda must be idempotent—check an `appliedEvents` map keyed by `orderId` before mutating.
   - Alarms on DLQ depth and balance mismatches.

4. **API surface**
   - Extend `/api/orders` POST response to include `balanceSnapshot` (optional).
   - Add new endpoint `/api/balances/{userId}` returning current balance + recent ledger entries.
   - Authentication: reuse persona context for now; future: enforce IAM/Cognito tokens.

### UI Requirements

1. **Balance widget**
   - In the header (next to persona dropdown), display “Cash Balance: fl X,XXX” with green/red sparkline of recent movements.
   - Clicking opens a modal showing the ledger (last 10 adjustments).

2. **Portfolio Overview integration**
   - Replace hard-coded “Total value” with `holdingsValue + cashBalance`.
   - Add a stacked bar showing `Cash vs Positions`.
   - When balance API call fails, surface a warning banner.

3. **Orders workflow**
   - After placing an order, refresh the balance widget (call `/api/balances/{userId}`).
   - Show inline message when funds insufficient (future enhancement; out-of-scope for Phase 1).

### Acceptance Criteria

1. `balances` table created (CloudFormation) with Lambda consumer + DLQ.
2. Balance updates occur exactly once per accepted order; retries/dlq instrumentation in place.
3. UI header shows live balance for active persona; Portfolio Overview adds cash to totals.
4. README / architecture docs updated with sequence diagrams + operational notes.

### Sequence Diagram (conceptual)

```
Client -> API POST /orders -> Dynamo (order record)
OrderAccepted event -> SQS FIFO -> Balance Lambda
Balance Lambda -> Dynamo (conditional update on balance row)
Balance Lambda -> emits BalanceUpdated -> (optional) notify UI via SSE/WebSocket
UI -> GET /api/balances/{userId} -> display cash widget + Portfolio total
```

**Asset storage proposal**

- `tulipbroker-ui/public/avatars/seed/…` for bundled cartoon portraits.
- `tulipbroker-ui/public/uploads/avatars/…` (gitignored) for locally uploaded images during dev.
- Future expansion: move to S3 or CDN, but this ticket keeps assets local.

**Testing**

- Component tests for the Users settings screen: listing personas, adding one, deleting one, and previewing an uploaded avatar.
- UI regression ensuring the old “Alex Trader” widget no longer appears and layout remains intact.
