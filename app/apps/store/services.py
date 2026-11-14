"""Business rules of the stationery store: cart, checkout, stock, order status, ratings and images.

Money rule (same as the counter's print orders): line totals keep their cents, the order TOTAL is rounded the way the
shop quotes it (SYP to a whole ten via ``round_syp``, USD keeps cents) and the difference is stored as
``rounding_adjustment``. Stock rule: checkout takes the units off the shelf at once with ``USAGE`` movements (that is
the reservation); cancelling puts them back with a signed ``ADJUSTMENT``; picking up changes nothing (already taken).
"""

from __future__ import annotations

from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Avg, Count, F
from django.utils import timezone
from rest_framework import status as http_status
from rest_framework.exceptions import APIException, ValidationError

from apps.audit.models import AuditAction
from apps.audit.services import AuditLogService
from apps.finance.models import InventoryCategory, InventoryItem, StockMovementType
from apps.finance.services import InventoryService, money
from apps.notifications.models import NotificationType
from apps.notifications.services import NotificationService
from apps.printing.services import round_price

from .models import (
    CURRENCIES,
    STORE_TRANSITIONS,
    StoreCart,
    StoreCartItem,
    StoreOrder,
    StoreOrderItem,
    StoreOrderStatus,
    StoreOrderStatusHistory,
    StoreProduct,
    StoreProductImage,
    StoreReview,
    StoreVariant,
)

MAX_CART_LINES = 30
DEFAULT_MAX_PER_LINE = 20
MAX_OPEN_ORDERS = 5
THUMB_MAX_PX = 480
MAX_REVIEW_EDITS = 1


class StoreError(APIException):
    """A refusal with a stable machine ``code`` (HTTP 400 unless said otherwise) and extra fields in ``errors``."""

    status_code = http_status.HTTP_400_BAD_REQUEST

    def __init__(self, code: str, message: str, status_code: int | None = None, **extra):
        super().__init__(detail={"detail": message, **extra}, code=code.lower())
        self.api_code = code
        self.api_message = message
        if status_code is not None:
            self.status_code = status_code


# ---- catalogue availability -------------------------------------------------------------------------------------------------------


def line_cap(variant: StoreVariant) -> int:
    return variant.max_per_order or DEFAULT_MAX_PER_LINE


def is_orderable(variant: StoreVariant) -> bool:
    product = variant.product
    return (
        variant.is_active
        and not variant.is_deleted
        and product.is_active
        and not product.is_deleted
        and (product.category.is_active and not product.category.is_deleted)
        and (product.brand is None or (product.brand.is_active and not product.brand.is_deleted))
    )


# ---- images -----------------------------------------------------------------------------------------------------------------------


def attach_thumbnail(image: StoreProductImage) -> None:
    """(Re)build the <=480 px WebP preview of the original; a file Pillow cannot decode was already rejected on upload."""

    from apps.chat.media import make_thumbnail

    if image.thumb:
        image.thumb.delete(save=False)
    image.image.open("rb")
    try:
        data, _, _ = make_thumbnail(image.image, THUMB_MAX_PX)
    finally:
        image.image.close()
    image.thumb.save(f"{image.pk}.webp", ContentFile(data), save=False)
    image.save(update_fields=["thumb", "updated_at"])


def set_primary_image(image: StoreProductImage) -> None:
    """Make ``image`` the product's only primary picture."""

    StoreProductImage.objects.filter(product_id=image.product_id, is_primary=True, is_deleted=False).exclude(
        pk=image.pk
    ).update(is_primary=False)
    if not image.is_primary:
        image.is_primary = True
        image.save(update_fields=["is_primary", "updated_at"])


# ---- stock ------------------------------------------------------------------------------------------------------------------------


def sync_inventory_item(variant: StoreVariant) -> None:
    """Keep the shelf row in line with the variant: the counter's quick-sale screen sells the same stock."""

    item = variant.inventory_item
    if item is None:
        return
    for currency in CURRENCIES:
        price = variant.price_for(currency)
        if price is not None:
            item.sale_price, item.currency = price, currency
            break
    item.is_sellable = variant.is_active and not variant.is_deleted
    item.save(update_fields=["sale_price", "currency", "is_sellable", "updated_at"])


@transaction.atomic
def ensure_inventory_item(variant: StoreVariant, *, initial_stock: int = 0, actor=None) -> InventoryItem:
    """Create the shelf row of a new variant (named after it) and optionally book the opening stock."""

    if variant.inventory_item is not None:
        return variant.inventory_item
    brand = variant.product.brand.name if variant.product.brand else ""
    name = " ".join(part for part in (brand, variant.product.name, variant.label) if part)[:150]
    taken = InventoryItem.objects.filter(category=InventoryCategory.PENS, name=name, is_deleted=False).exists()
    if taken:
        name = f"{name[:120]} [{variant.sku or variant.pk}]"[:150]
    item = InventoryItem.objects.create(
        name=name, category=InventoryCategory.PENS, unit="piece", sku=variant.sku, is_sellable=variant.is_active
    )
    variant.inventory_item = item
    variant.save(update_fields=["inventory_item", "updated_at"])
    sync_inventory_item(variant)
    if initial_stock > 0:
        InventoryService.record_movement(
            item, StockMovementType.PURCHASE, Decimal(initial_stock), actor=actor, note="رصيد افتتاحي لمتجر القرطاسية"
        )
    return item


# ---- cart -------------------------------------------------------------------------------------------------------------------------


def get_cart(user) -> StoreCart:
    cart, _ = StoreCart.objects.get_or_create(user=user, defaults={})
    return cart


def cart_items(cart: StoreCart) -> list[StoreCartItem]:
    return list(
        cart.items.select_related("variant__product__brand", "variant__product__category", "variant__inventory_item")
        .prefetch_related("variant__product__images")
        .order_by("created_at", "id")
    )


def _load_variant(variant_id: int) -> StoreVariant:
    variant = (
        StoreVariant.objects.select_related("product__brand", "product__category", "inventory_item")
        .filter(pk=variant_id, is_deleted=False)
        .first()
    )
    if variant is None or not is_orderable(variant):
        raise StoreError("VARIANT_UNAVAILABLE", "This product is not available.", http_status.HTTP_404_NOT_FOUND)
    return variant


def _check_quantity(variant: StoreVariant, quantity: int) -> None:
    cap = line_cap(variant)
    if quantity > cap:
        raise StoreError(
            "QUANTITY_LIMIT", f"At most {cap} of this item per order.", variant=variant.pk, max_per_order=cap
        )
    if quantity > variant.available:
        raise StoreError(
            "INSUFFICIENT_STOCK",
            "Not enough stock.",
            http_status.HTTP_409_CONFLICT,
            variant=variant.pk,
            available=variant.available,
        )


@transaction.atomic
def cart_add(user, variant_id: int, quantity: int) -> StoreCart:
    """Add ``quantity`` units of a variant (merged into its line). Refuses more than the cap or than the shelf holds."""

    cart = StoreCart.objects.select_for_update().get(pk=get_cart(user).pk)
    variant = _load_variant(variant_id)
    line = cart.items.filter(variant=variant).first()
    wanted = quantity + (line.quantity if line else 0)
    _check_quantity(variant, wanted)
    if line is None:
        if cart.items.count() >= MAX_CART_LINES:
            raise StoreError("CART_FULL", f"A cart holds at most {MAX_CART_LINES} different items.")
        StoreCartItem.objects.create(cart=cart, variant=variant, quantity=wanted)
    else:
        line.quantity = wanted
        line.save(update_fields=["quantity", "updated_at"])
    return cart


@transaction.atomic
def cart_set_quantity(user, item_id: int, quantity: int) -> StoreCart:
    cart = StoreCart.objects.select_for_update().get(pk=get_cart(user).pk)
    line = cart.items.select_related("variant__product__brand", "variant__product__category").filter(pk=item_id).first()
    if line is None:
        raise StoreError("CART_ITEM_NOT_FOUND", "This item is not in your cart.", http_status.HTTP_404_NOT_FOUND)
    _check_quantity(_load_variant(line.variant_id), quantity)
    line.quantity = quantity
    line.save(update_fields=["quantity", "updated_at"])
    return cart


@transaction.atomic
def cart_remove(user, item_id: int) -> StoreCart:
    cart = get_cart(user)
    deleted, _ = cart.items.filter(pk=item_id).delete()
    if not deleted:
        raise StoreError("CART_ITEM_NOT_FOUND", "This item is not in your cart.", http_status.HTTP_404_NOT_FOUND)
    return cart


def cart_clear(user) -> StoreCart:
    cart = get_cart(user)
    cart.items.all().delete()
    return cart


def price_lines(lines: list[tuple[StoreVariant, int]], currency: str) -> dict:
    """Unit price, line totals, subtotal, rounded total and rounding difference for ``currency`` (never guessed)."""

    subtotal = Decimal("0")
    priced = []
    for variant, quantity in lines:
        unit = variant.price_for(currency)
        if unit is None:
            raise StoreError(
                "PRICE_UNAVAILABLE", f"An item in the cart has no {currency} price.", variant=variant.pk
            )
        line_total = money(unit * quantity)
        subtotal += line_total
        priced.append((variant, quantity, unit, line_total))
    subtotal = money(subtotal)
    total = round_price(subtotal, currency)
    return {
        "lines": priced,
        "subtotal": subtotal,
        "total": total,
        "rounding_adjustment": total - subtotal,
    }


def cart_totals(items: list[StoreCartItem]) -> dict[str, dict | None]:
    """Per currency: the totals when EVERY line has a price in it, else None (the app greys that currency out)."""

    out: dict[str, dict | None] = {}
    for currency in CURRENCIES:
        try:
            quote = price_lines([(item.variant, item.quantity) for item in items], currency) if items else None
        except StoreError:
            quote = None
        out[currency] = (
            None
            if quote is None
            else {
                "subtotal": quote["subtotal"],
                "rounding_adjustment": quote["rounding_adjustment"],
                "total": quote["total"],
            }
        )
    return out


# ---- checkout ---------------------------------------------------------------------------------------------------------------------


def _note(order: StoreOrder, old: str, new: str, actor, note: str = "") -> None:
    StoreOrderStatusHistory.objects.create(order=order, old_status=old, new_status=new, changed_by=actor, note=note[:255])


@transaction.atomic
def checkout(user, currency: str, notes: str = "", request=None) -> StoreOrder:
    """Turn the cart into a pending in-store-pickup order, taking the stock off the shelf in the same transaction."""

    if currency not in CURRENCIES:
        raise ValidationError({"currency": f"Use one of {', '.join(CURRENCIES)}."})
    cart = StoreCart.objects.select_for_update().get(pk=get_cart(user).pk)  # one checkout per user at a time
    items = cart_items(cart)
    if not items:
        raise StoreError("CART_EMPTY", "Your cart is empty.")
    open_orders = StoreOrder.objects.filter(
        user=user, is_deleted=False, status__in=(StoreOrderStatus.PENDING, StoreOrderStatus.CONFIRMED)
    ).count()
    if open_orders >= MAX_OPEN_ORDERS:
        raise StoreError(
            "TOO_MANY_OPEN_ORDERS",
            f"You already have {MAX_OPEN_ORDERS} open orders. Collect or cancel one first.",
            http_status.HTTP_409_CONFLICT,
        )
    for item in items:
        if not is_orderable(item.variant):
            raise StoreError("VARIANT_UNAVAILABLE", "An item in your cart is no longer sold.", variant=item.variant_id)
        if item.quantity > line_cap(item.variant):
            raise StoreError("QUANTITY_LIMIT", "An item exceeds the per-order limit.", variant=item.variant_id)
    quote = price_lines([(item.variant, item.quantity) for item in items], currency)
    order = StoreOrder.objects.create(
        user=user,
        currency=currency,
        subtotal=quote["subtotal"],
        rounding_adjustment=quote["rounding_adjustment"],
        total=quote["total"],
        notes=notes.strip()[:500],
    )
    # Lock shelf rows in a fixed order so two checkouts that share items can never deadlock.
    for variant, quantity, unit, line_total in sorted(quote["lines"], key=lambda row: row[0].inventory_item_id or 0):
        product = variant.product
        StoreOrderItem.objects.create(
            order=order,
            product=product,
            variant=variant,
            name=product.name,
            brand_name=product.brand.name if product.brand else "",
            variant_label=variant.label,
            sku=variant.sku,
            unit_price=unit,
            quantity=quantity,
            line_total=line_total,
        )
        if variant.inventory_item is None:
            raise StoreError("INSUFFICIENT_STOCK", "Not enough stock.", http_status.HTTP_409_CONFLICT, variant=variant.pk)
        try:
            InventoryService.record_movement(
                variant.inventory_item,
                StockMovementType.USAGE,
                Decimal(quantity),
                actor=user,
                note=f"حجز طلب متجر {order.code}",
            )
        except ValidationError as exc:
            raise StoreError(
                "INSUFFICIENT_STOCK",
                "Not enough stock.",
                http_status.HTTP_409_CONFLICT,
                variant=variant.pk,
                available=StoreVariant.objects.get(pk=variant.pk).available,
            ) from exc
    cart.items.all().delete()
    _note(order, "", StoreOrderStatus.PENDING, user, "تم استلام الطلب")
    AuditLogService.log(
        actor=user,
        action=AuditAction.STORE_ORDER_CREATED,
        target=order,
        new_value={"total": str(order.total), "currency": currency, "lines": len(items)},
        request=request,
    )
    NotificationService.create_notification(
        user,
        "تم استلام طلبك من متجر القرطاسية",
        f"طلبك {order.code} بقيمة {order.total} {currency} قيد المراجعة. الدفع نقداً عند الاستلام من المحل.",
        type=NotificationType.PRINTING,
        data={"kind": "store_order_status", "store_order_id": order.id, "status": order.status},
    )

    def announce() -> None:
        from apps.notifications.staff import notify_staff

        notify_staff(
            "store_order",
            "طلب جديد من متجر القرطاسية",
            f"طلب {order.code} من {user.full_name} بقيمة {order.total} {currency}.",
            object_id=order.id,
            actor_id=user.pk,
        )

    transaction.on_commit(announce)
    return order


# ---- order status -----------------------------------------------------------------------------------------------------------------

_STUDENT_TEXT = {
    StoreOrderStatus.CONFIRMED: "تم تأكيد طلبك {code} وجاري تجهيزه.",
    StoreOrderStatus.READY: "طلبك {code} جاهز للاستلام من المحل. الدفع نقداً عند الاستلام.",
    StoreOrderStatus.PICKED_UP: "تم تسليم طلبك {code}. شكراً لك، يمكنك الآن تقييم المنتجات.",
    StoreOrderStatus.CANCELLED: "تم إلغاء طلبك {code}.",
}


def _release_stock(order: StoreOrder, actor) -> None:
    if order.stock_released_at is not None:
        return
    items = list(order.items.select_related("variant__inventory_item").order_by("variant__inventory_item_id"))
    for line in items:
        item = line.variant.inventory_item
        if item is not None:
            InventoryService.record_movement(
                item,
                StockMovementType.ADJUSTMENT,
                Decimal(line.quantity),
                actor=actor,
                note=f"إرجاع مخزون طلب متجر ملغى {order.code}",
            )
    order.stock_released_at = timezone.now()


@transaction.atomic
def change_status(order: StoreOrder, new_status: str, actor, *, reason: str = "", note: str = "", customer=False, request=None):
    """Move an order along ``STORE_TRANSITIONS``. Cancelling needs a reason (staff) and returns the stock."""

    locked = StoreOrder.objects.select_for_update().get(pk=order.pk, is_deleted=False)
    if customer:
        if locked.status != StoreOrderStatus.PENDING or new_status != StoreOrderStatus.CANCELLED:
            raise StoreError(
                "ORDER_NOT_CANCELLABLE",
                "Only an order that has not been confirmed yet can be cancelled.",
                http_status.HTTP_409_CONFLICT,
            )
    elif new_status not in STORE_TRANSITIONS.get(locked.status, set()):
        raise StoreError(
            "INVALID_TRANSITION",
            f"An order that is {locked.status} cannot become {new_status}.",
            http_status.HTTP_409_CONFLICT,
        )
    reason = reason.strip()
    if new_status == StoreOrderStatus.CANCELLED and not customer and not reason:
        raise ValidationError({"reason": "A reason is required to cancel an order."})
    old = locked.status
    now = timezone.now()
    locked.status = new_status
    if new_status == StoreOrderStatus.CONFIRMED:
        locked.confirmed_at = now
    elif new_status == StoreOrderStatus.READY:
        locked.ready_at = now
    elif new_status == StoreOrderStatus.PICKED_UP:
        locked.picked_up_at = now
        for line in locked.items.all():
            StoreProduct.objects.filter(pk=line.product_id).update(sold_count=F("sold_count") + line.quantity)
    elif new_status == StoreOrderStatus.CANCELLED:
        locked.cancelled_at = now
        locked.cancel_reason = reason[:255]
        locked.cancelled_by_customer = bool(customer)
        _release_stock(locked, actor)
    if note.strip() and not customer:
        locked.internal_notes = note.strip()[:500]
    locked.save()
    _note(locked, old, new_status, actor, reason or note)
    AuditLogService.log(
        actor=actor,
        action=AuditAction.STORE_ORDER_STATUS_CHANGED,
        target=locked,
        old_value={"status": old},
        new_value={"status": new_status, "reason": reason[:255]} if reason else {"status": new_status},
        request=request,
    )
    if not customer:  # the student who cancelled it knows already
        text = _STUDENT_TEXT[StoreOrderStatus(new_status)].format(code=locked.code)
        if new_status == StoreOrderStatus.CANCELLED and reason:
            text += f" السبب: {reason}"
        NotificationService.create_notification(
            locked.user,
            "تحديث على طلبك من متجر القرطاسية",
            text,
            type=NotificationType.PRINTING,
            data={"kind": "store_order_status", "store_order_id": locked.id, "status": new_status},
        )
    return locked


# ---- reviews ----------------------------------------------------------------------------------------------------------------------


def has_received(user, product_id: int) -> bool:
    return StoreOrderItem.objects.filter(
        product_id=product_id, order__user=user, order__status=StoreOrderStatus.PICKED_UP, order__is_deleted=False
    ).exists()


@transaction.atomic
def recompute_rating(product_id: int) -> None:
    """Average and count of the VISIBLE reviews, computed under a lock on the product row (no lost updates)."""

    product = StoreProduct.objects.select_for_update().get(pk=product_id)
    stats = StoreReview.objects.filter(product=product, is_deleted=False, is_hidden=False).aggregate(
        avg=Avg("rating"), count=Count("id")
    )
    product.rating_count = stats["count"] or 0
    product.rating_avg = Decimal(str(stats["avg"] or 0)).quantize(Decimal("0.01"))
    product.save(update_fields=["rating_avg", "rating_count", "updated_at"])


@transaction.atomic
def post_review(user, product: StoreProduct, rating: int, text: str) -> StoreReview:
    if not has_received(user, product.pk):
        raise StoreError(
            "REVIEW_NOT_ELIGIBLE",
            "You can rate a product after you have picked it up.",
            http_status.HTTP_403_FORBIDDEN,
        )
    if StoreReview.objects.filter(user=user, product=product, is_deleted=False).exists():
        raise StoreError("REVIEW_EXISTS", "You already reviewed this product; edit your review.", http_status.HTTP_409_CONFLICT)
    review = StoreReview.objects.create(user=user, product=product, rating=rating, text=text.strip())
    recompute_rating(product.pk)
    return review


@transaction.atomic
def edit_review(user, product: StoreProduct, rating: int | None, text: str | None) -> StoreReview:
    review = (
        StoreReview.objects.select_for_update().filter(user=user, product=product, is_deleted=False).first()
    )
    if review is None:
        raise StoreError("REVIEW_NOT_FOUND", "You have not reviewed this product.", http_status.HTTP_404_NOT_FOUND)
    if review.edit_count >= MAX_REVIEW_EDITS:
        raise StoreError("REVIEW_EDIT_LIMIT", "A review can be edited only once.", http_status.HTTP_409_CONFLICT)
    if rating is not None:
        review.rating = rating
    if text is not None:
        review.text = text.strip()
    review.edit_count += 1
    review.save()
    recompute_rating(product.pk)
    return review


@transaction.atomic
def moderate_review(review: StoreReview, actor, *, hide: bool, reason: str = "", request=None) -> StoreReview:
    review = StoreReview.objects.select_for_update().get(pk=review.pk, is_deleted=False)
    review.is_hidden = hide
    review.hidden_reason = reason.strip()[:255] if hide else ""
    review.moderated_by = actor
    review.moderated_at = timezone.now()
    review.save()
    recompute_rating(review.product_id)
    AuditLogService.log(
        actor=actor,
        action=AuditAction.STORE_REVIEW_MODERATED,
        target=review,
        new_value={"hidden": hide, "reason": review.hidden_reason, "product": review.product_id},
        request=request,
        critical=True,
    )
    return review

