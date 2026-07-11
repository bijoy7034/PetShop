# Pet Shop Inventory Management

Multi-role inventory system for a pet shop.

## Roles (Release 1)

| Role | What they can do |
|---|---|
| **admin** | Manage users & roles, full inventory access (later), all reports |
| **manager** | Full inventory CRUD (later), stock adjustments, reports; no user mgmt |
| **cashier** | View inventory, record sales (later); no edits |

## Layout

```
pet-shop/
└── backend/            FastAPI service (auth + RBAC + user management)
```

Frontend and inventory modules land in later releases.

## Run

```bash
cd backend
cp .env.example .env    # set JWT_SECRET, ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_NAME
uv sync
uv run uvicorn app:app --reload --port 8000
```

The lifespan hook seeds the first admin from `ADMIN_EMAIL`/`ADMIN_PASSWORD`
if that email does not exist. The account is created with
`must_change_password=true` so it forces a rotation on first login.
