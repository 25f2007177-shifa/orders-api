
import time
import threading
from collections import defaultdict, deque
from itertools import count

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

TOTAL_ORDERS = 47
RATE_LIMIT = 15
WINDOW_SECONDS = 10

lock = threading.Lock()
idempotency_store = {}
next_order_id = count(48)
client_requests = defaultdict(deque)

catalog = [
    {"id": i, "item": f"Item {i}", "status": "created"}
    for i in range(1, TOTAL_ORDERS + 1)
]


class OrderRequest(BaseModel):
    item: str = "New order"


def check_rate_limit(client_id: str | None):
    client_id = client_id or "anonymous"
    now = time.monotonic()

    with lock:
        timestamps = client_requests[client_id]

        while timestamps and now - timestamps[0] >= WINDOW_SECONDS:
            timestamps.popleft()

        if len(timestamps) >= RATE_LIMIT:
            retry_after = max(
                1, int(WINDOW_SECONDS - (now - timestamps[0]) + 0.999)
            )
            return retry_after

        timestamps.append(now)
        return None


@app.middleware("http")
async def apply_rate_limit(request, call_next):
    if request.method != "OPTIONS":
        retry_after = check_rate_limit(
            request.headers.get("X-Client-Id")
        )

        if retry_after is not None:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )

    return await call_next(request)


@app.post("/orders", status_code=201)
def create_order(
    body: OrderRequest,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
):
    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header is required",
        )

    with lock:
        if idempotency_key in idempotency_store:
            return idempotency_store[idempotency_key]

        order = {
            "id": f"order-{next(next_order_id)}",
            "item": body.item,
        }
        idempotency_store[idempotency_key] = order
        return order


@app.get("/orders")
def list_orders(
    limit: int = Query(10, ge=1, le=47),
    cursor: str | None = None,
):
    try:
        start_id = int(cursor) + 1 if cursor is not None else 1
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid cursor")

    if start_id < 1 or start_id > TOTAL_ORDERS + 1:
        raise HTTPException(status_code=400, detail="Invalid cursor")

    items = catalog[start_id - 1 : start_id - 1 + limit]
    last_id = items[-1]["id"] if items else None

    next_cursor = (
        str(last_id)
        if last_id is not None and last_id < TOTAL_ORDERS
        else None
    )

    return {"items": items, "next_cursor": next_cursor}

