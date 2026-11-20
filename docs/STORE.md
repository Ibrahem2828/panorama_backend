# Stationery store

An e-shop section for students and other signed-in users: browse brands and products (picture, name, rating), open a
product (colours, sizes, details, quantity), fill a cart and place an order that the student collects at the print
shop. **There is no online payment**: the student pays in cash at pickup (`payment: "cash_on_pickup"` in every cart
and order payload). Prices exist in the shop's two currencies, SYP (new pound) and USD.

Code: `app/apps/store/`. Tests: `tests_*.py` in the same folder.

## Design in one paragraph

The store EXTENDS what the shop already has instead of duplicating it. Stock is the existing `finance.InventoryItem`
(every variant points at one; movements go through `InventoryService.record_movement`, so the low-stock alerts,
stock report and the counter's quick-sale screen see the same shelf). Notifications, audit rows, idempotency,
throttling, pagination, soft delete, SYP rounding (`printing.services.round_price`) and the upload validators are the
existing ones. New tables exist only for what had no home: brands, categories, products, variants, images, reviews,
the cart and the orders. `printing.ReadyProduct` (ready-printed notebooks ordered through a print order) is a
different feature and is untouched.

## Models

| Model | Purpose | Key rules |
| --- | --- | --- |
| `StoreBrand` | brand name + logo | live name unique; shown to students only while it has a visible product |
| `StoreCategory` | أقلام، دفاتر، ... | live slug unique |
| `StoreProduct` | name, description, `specs` (list of `{label, value}`), base `price_syp` / `price_usd` (nullable = not sold in that currency), `rating_avg`, `rating_count`, `sold_count` | rating fields maintained by `services.recompute_rating` under a row lock |
| `StoreVariant` | colour name + `#RRGGBB`, size label, SKU, optional price overrides, `max_per_order`, link to an `InventoryItem` | live SKU unique; `(product, colour, size)` unique; price = own price, else the product's |
| `StoreProductImage` | several per product, one primary (DB constraint), original + <=480 px WebP thumbnail | validated upload, pixel cap |
| `StoreReview` | rating 1-5 + text | one per (user, product); edited once; hidden by staff |
| `StoreCart` / `StoreCartItem` | server-side cart | one line per variant; quantity >= 1 |
| `StoreOrder` / `StoreOrderItem` / `StoreOrderStatusHistory` | the order, price/name snapshots, status trail | totals in the chosen currency |

Soft delete (`is_deleted`) everywhere, like the rest of the backend. Brands/categories with live products cannot be
deleted (`409 IN_USE`).

## Money

* Prices are per currency; there is no automatic conversion. A cart line without a price in the chosen currency
  makes checkout fail with `PRICE_UNAVAILABLE` (the cart payload shows `totals.<currency> = null` for such a currency).
* Line totals keep cents. The order **total** is `round_price(subtotal, currency)`: SYP is rounded to a whole ten
  (last digit 1-2 down, 3-9 up, a positive price never becomes 0), USD keeps cents. The difference is stored as
  `rounding_adjustment` (same rule as counter print orders).
* The order keeps the unit price, product name, brand, variant label and SKU as they were at checkout.

## Stock rules

1. **Reserve at checkout**: checkout takes the units off the shelf with `USAGE` movements (note "حجز طلب متجر ST-…")
   in the same transaction as the order. Rows are locked in a fixed order (`select_for_update` inside
   `InventoryService.record_movement`) so two students can never take the last unit twice and no deadlock is possible.
2. **Release on cancel**: a cancelled order (by the student while pending, or by staff) puts the units back with a
   signed `ADJUSTMENT` movement. `stock_released_at` makes it impossible to return stock twice.
3. **Pick-up changes nothing** (already taken) but raises the product `sold_count` (popularity).
4. Print orders deduct stock when delivered instead; the store reserves earlier because stationery is sold one-off.
5. Staff book purchases / waste / corrections with `POST /dashboard/store/variants/{id}/stock/`.
6. Creating a variant through the dashboard creates its inventory item automatically (category "pens", sellable,
   sale price = the variant's first available price) and books the optional `initial_stock`.

## Order status flow

```
pending --confirm--> confirmed --ready--> ready_for_pickup --picked up--> picked_up
   |                     |                      |
   +---------------------+----------------------+--> cancelled (reason required for staff)
```

* Student: may cancel only while `pending` (`409 ORDER_NOT_CANCELLABLE` otherwise).
* Staff: `PATCH /dashboard/store/orders/{id}/status/`; anything not in the diagram is `409 INVALID_TRANSITION`.
  `picked_up` and `cancelled` are final.
* A student may have at most 5 open (pending/confirmed) orders (`409 TOO_MANY_OPEN_ORDERS`).
* Notifications: the student is told at checkout and on every staff status change (notification type `printing`,
  `data.kind = "store_order_status"`, `data.store_order_id`, `data.status`). Staff holding `store.manage` get a
  `store_order` inbox notification for each new order (`/dashboard/store/orders?highlight=<id>`). Quick-access
  counters include `store_orders_new`.

## Reviews

Only a user with a `picked_up` order containing the product may review it (`403 REVIEW_NOT_ELIGIBLE`). One review per
user and product (`409 REVIEW_EXISTS`), editable once (`409 REVIEW_EDIT_LIMIT`). The public list shows the first name
only and never hidden reviews. Staff hide (reason required) / unhide; both are audited and the product's average and
count are recomputed from visible reviews only.

## Image delivery

Production does not publish `/media/`, so every picture goes through an authenticated API view built on the shared
helper `apps/common/image_delivery.py` (also used by the print option help images):

* `GET /api/v1/store/images/{id}/` original, `?variant=thumb` the <=480 px WebP made at upload (falls back to the
  original when missing);
* `GET /api/v1/store/brands/{id}/logo/`.

Content type comes from the file's magic bytes (a non-image is a 404), `ETag` + `If-None-Match` give `304`,
`Cache-Control: private, max-age=3600`, `X-Content-Type-Options: nosniff`. URLs in payloads carry `?v=<updated_at>` so
clients re-fetch after a change. Students only reach pictures of visible products; `store.manage` holders also reach
hidden ones (dashboard preview). Uploads accept PNG/JPEG/WebP, up to the platform image limit and pixel cap.

## Capability

`store.manage` (new): granted to `admin`, `print_staff` and (wildcard) `it_support`, exactly like `printing.manage`.
Overridable per user like every capability. Nobody else (support staff, content managers, students) can use
`/dashboard/store/*`. Existing staff *permission templates* stored in the database (for example "print_officer")
are not rewritten by code; grant `store.manage` in the dashboard if a custom template needs it.

## Endpoints

Every route needs a login (`401` otherwise). Responses use the standard envelope
`{success, code, message, data, request_id}`; lists are paginated with
`data = {count, page, page_size, total_pages, next, previous, results}` (`?page`, `?page_size` <= 100) except the
small lists marked "plain".

### Student (`/api/v1/store/`)

| Method + path | Notes |
| --- | --- |
| `GET brands/` (plain) | `[{id, name, logo_url}]`, only brands with a visible product |
| `GET categories/` (plain) | `[{id, name, slug}]` |
| `GET products/` | filters `brand`, `category`, `search` (name, brand, category, description, SKU), `in_stock=true`, `currency=SYP\|USD`, `ordering=popular\|newest\|rating\|price_asc\|price_desc`. Card: `{id, name, brand{id,name}, category, image_url, thumb_url, rating_avg, rating_count, price_from{SYP,USD}, in_stock}` |
| `GET products/{id}/` | card + `description`, `specs[]`, `prices{SYP,USD}`, `variants[{id, sku, colour_name, colour_hex, size_label, prices{SYP,USD}, available, in_stock, max_per_order, image_id}]`, `images[{id, url, thumb_url, alt_text, is_primary, variant}]`, `can_review` |
| `GET products/{id}/reviews/` | paginated `{id, rating, text, author, edited, created_at, updated_at}` |
| `POST products/{id}/reviews/` | `{rating 1-5, text?}` -> 201 |
| `GET` / `PATCH products/{id}/reviews/mine/` | your review; PATCH `{rating?, text?}` once |
| `GET cart/` | `{items[{id, variant{...}, product{id,name,brand}, quantity, prices, orderable, available, thumb_url}], items_count, max_lines, totals{SYP,USD -> {subtotal, rounding_adjustment, total} or null}, payment}` |
| `POST cart/items/` | `{variant, quantity}`; merges into the existing line; 201 + the cart |
| `PATCH cart/items/{id}/` | `{quantity}` sets the quantity |
| `DELETE cart/items/{id}/`, `DELETE cart/` | remove a line / clear |
| `POST checkout/` | `{currency: "SYP"\|"USD", notes?}` + optional `Idempotency-Key`; 201 + the order |
| `GET orders/` (`?status=`), `GET orders/{id}/` | `{id, code "ST-000123", status, currency, subtotal, rounding_adjustment, total, notes, cancel_reason, cancelled_by_customer, confirmed_at, ready_at, picked_up_at, cancelled_at, can_cancel, payment, items[{id, product, variant, name, brand_name, variant_label, sku, unit_price, quantity, line_total, thumb_url}], history[{old_status, new_status, note, created_at}]}` |
| `POST orders/{id}/cancel/` | `{reason?}`; only while `pending` |
| `GET images/{id}/`, `GET brands/{id}/logo/` | see "Image delivery" |

Error codes (`code` in the envelope; extra numbers are in `errors`): `INSUFFICIENT_STOCK` (409, `variant`, `available`),
`QUANTITY_LIMIT` (400, `variant`, `max_per_order`), `VARIANT_UNAVAILABLE` (404 in the cart, 400 at checkout),
`CART_FULL` (400, max 30 lines), `CART_EMPTY`, `CART_ITEM_NOT_FOUND` (404), `PRICE_UNAVAILABLE` (400, `variant`),
`TOO_MANY_OPEN_ORDERS` (409), `ORDER_NOT_CANCELLABLE` (409), `REVIEW_NOT_ELIGIBLE` (403), `REVIEW_EXISTS` (409),
`REVIEW_EDIT_LIMIT` (409), `REVIEW_NOT_FOUND` (404), `IDEMPOTENCY_KEY_REUSED` / `IDEMPOTENCY_IN_PROGRESS` (409),
`RATE_LIMITED` (429), `VALIDATION_ERROR` (400).

Limits: 30 cart lines, 20 units per line (variant `max_per_order` overrides), 5 open orders, review text 1000,
order notes 500. Throttles (per user): cart/cancel 120/min, checkout 10/hour, review writes 20/hour, images 600/min
(env `THROTTLE_STORE_CART`, `THROTTLE_STORE_CHECKOUT`, `THROTTLE_STORE_REVIEW`, `THROTTLE_STORE_IMAGE`).

### Dashboard (`/api/v1/dashboard/store/`, capability `store.manage`)

| Resource | Methods | Notes |
| --- | --- | --- |
| `brands/` | GET list (`is_active`, `search`) POST; `{id}/` GET PATCH PUT DELETE | multipart with `logo`; DELETE is `409 IN_USE` while it has products |
| `categories/` | same (not paginated) | `{name, slug, sort_order, is_active}` |
| `products/` | same (`brand`, `category`, `is_active`, `search`, ordering) | `{name, description, brand, category, specs[], price_syp, price_usd, is_active, sort_order}`; read-only `rating_avg`, `rating_count`, `sold_count`, `variants_count` |
| `variants/` | same (`product`, `is_active`, `search`) | `{product, sku, colour_name, colour_hex, size_label, price_syp, price_usd, max_per_order, is_active, sort_order, initial_stock (create only), reorder_level}`; read-only `stock`, `inventory_item` |
| `variants/{id}/stock/` | POST | `{movement_type: purchase\|adjustment\|waste, quantity, note?}` (adjustment is signed) -> `{variant, stock}`; recorded in the inventory movement log |
| `images/` | GET list (`product`, `variant`, `is_primary`) POST (multipart `product`, `image`, `variant?`, `alt_text`, `sort_order`, `is_primary`); `{id}/` PATCH DELETE | first image of a product becomes primary; setting `is_primary` demotes the others; replacing `image` rebuilds the thumbnail |
| `orders/` | GET list (`status`, `q` order number/name/phone, `from`, `to`); `{id}/` GET | customer name and phone included |
| `orders/{id}/status/` | PATCH | `{status: confirmed\|ready_for_pickup\|picked_up\|cancelled, reason (required to cancel), note}` |
| `reviews/` | GET list (`product`, `rating`, `is_hidden`) | |
| `reviews/{id}/hide/` / `unhide/` | POST | hide needs `{reason}` |
| `summary/` | GET | `{orders_by_status, orders_today, products_active, reviews_hidden, low_stock[{variant, product, label, available, reorder_level}]}` |

Audit actions (new): `store_catalog_changed`, `store_price_changed` (any create/update touching a price column is
logged as a price change with old/new values), `store_order_created`, `store_order_status_changed`,
`store_review_moderated`; stock movements reuse `stock_movement_recorded`. Catalogue writes are audited in the same
transaction and roll back if the audit row cannot be written.

## Rollout

1. Deploy the backend (migrations `store.0001_initial`, `audit.0021_alter_auditlog_action`; additive, no data
   migration, no downtime).
2. `python manage.py seed_store_defaults` (idempotent): six categories and eight example brand NAMES. No products,
   prices, stock or pictures are created, so students see an empty store until the owner enters real data.
3. In the dashboard: create products, add variants (with opening stock and reorder level), upload pictures.
4. Confirm that print staff and admins can open the store screens (`store.manage`); grant it to custom templates.
5. Then ship the mobile/dashboard clients (the API is additive; mobile 2.12/2.13 are unaffected).

## Not covered yet

* Revenue of a picked-up store order is not posted to the finance reports (counter sales are, via
  `shop.StationerySale`). Cash is collected at the counter; recording it needs a decision on double-counting stock.
* No coupons, delivery, partial pick-up or order edits after checkout.
* Concurrency is guaranteed with `select_for_update` on PostgreSQL (production); the test suite runs on SQLite, where
  row locks are no-ops, so the oversell test is sequential.
