# TB-202 — User Management & Avatar Uploads (API + Terraform Proposal)

## Goals
- Provide CRUD APIs for personas so the UI can manage users without direct DynamoDB access.
- Serve persona metadata (name, avatar, bio, active flag) for both the Settings screen and order submission.
- Keep avatars lightweight (local public assets today; S3-ready shape later).

## Proposed API surface
- `GET /api/users`
  - Returns `{ items: User[] }` sorted by `updatedAt` desc.
  - Supports `?limit=50&lastKey=...` for pagination.
- `POST /api/users`
  - Body: `{ userId, userName, bio?, avatarUrl?, isActive? }`.
  - Validates 3–40 char `userId` (slug), requires `userName`.
  - Writes to the Users table; rejects duplicate `userId`.
- `PUT /api/users/{userId}`
  - Body fields same as POST (all optional except userId path param).
  - Allows toggling `isActive` and updating avatar URL.
- `DELETE /api/users/{userId}`
  - Soft delete: sets `isDeleted=true` and `isActive=false`; preserves history.
- `GET /api/users/seed`
  - Returns the three seeded personas (Clusius, Oosterwijck, Leeuwenhoek) from `personas.json` for local dev reset.
- `GET /api/orders` (change)
  - Already enriches orders with persona metadata; update to read from the Users table first, then fall back to `personas.json`.
- `POST /api/orders` (change)
  - Continue requiring `userId`; validate existence against Users table (404).

### Data model
`Users` DynamoDB table (new)
- PK: `pk = USER#<userId>`
- SK: `sk = USER#<userId>` (simple key schema)
- Attributes: `userId (S)`, `userName (S)`, `avatarUrl (S)`, `bio (S)`, `isActive (BOOL, default true)`, `isDeleted (BOOL, default false)`, `createdAt (S)`, `updatedAt (S)`.
- GSI (optional for list-by-updated):
  - Name: `UpdatedAtIndex`; partition key `gsi1pk = USERS`, sort key `gsi1sk = updatedAt`.

### Validation & observability
- Log audit events: `UserCreated`, `UserUpdated`, `UserDeleted`, including `requestId` and `actor` (UI-supplied `adminUserId` once available).
- Enforce avatar size/extension in API by inspecting `Content-Length` header and file suffix when provided (phase 1 trust client but validate URL suffix `.(png|jpg|jpeg)`).

## Terraform / CloudFormation changes
- **New DynamoDB table** `UsersTable`
  - PAY_PER_REQUEST billing; optional `UpdatedAtIndex` GSI.
  - Export name output for UI builds that want to seed data via scripts.
- **Lambda environment**
  - Add `USERS_TABLE` env var to function.
- **IAM policy**
  - Permit `dynamodb:PutItem`, `UpdateItem`, `DeleteItem`, `GetItem`, `Query`, `Scan` on the Users table and GSI.
- **API Gateway routes**
  - Add routes for `/api/users` (GET, POST), `/api/users/{userId}` (PUT, DELETE), and `/api/users/seed` (GET) pointing to existing Lambda integration.
- **Outputs**
  - Add `UsersTableName` output to infra template.

## Compatibility notes
- Existing personas loader (`personas/personas.json`) remains the seed source; the new Users table becomes the authoritative registry once populated.
- UI should read the published contract file (`personas/user-management-contract.json`) to stay in sync with the API surface until the backend is fully implemented.
