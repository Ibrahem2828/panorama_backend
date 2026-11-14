"""Shared fixtures and builders for the store tests."""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.choices import UserRole
from apps.accounts.models import User

from .models import StoreBrand, StoreCategory, StoreProduct, StoreVariant
from .services import ensure_inventory_item


def make_user(role: str, n: int) -> User:
    return User.objects.create_user(
        full_name=f"Store {role} {n}",
        email=f"store-{role}-{n}@example.test",
        phone_number=f"+96395550{n:04d}",
        password="StrongPass123!",
        role=role,
    )


def client_for(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user)
    return client


def png_bytes(size=(40, 30), colour="red", fmt="PNG") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, colour).save(buffer, fmt)
    return buffer.getvalue()


def png_upload(name="p.png", size=(40, 30)) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, png_bytes(size), content_type="image/png")


def make_variant(product: StoreProduct, stock: int = 10, **kwargs) -> StoreVariant:
    kwargs.setdefault("colour_name", "أحمر")
    variant = StoreVariant.objects.create(product=product, **kwargs)
    ensure_inventory_item(variant, initial_stock=stock)
    variant.refresh_from_db()
    return variant


@pytest.fixture
def student(db):
    return make_user(UserRole.STUDENT, 1)


@pytest.fixture
def other_student(db):
    return make_user(UserRole.STUDENT, 2)


@pytest.fixture
def staff(db):
    return make_user(UserRole.PRINT_STAFF, 3)


@pytest.fixture
def admin(db):
    return make_user(UserRole.ADMIN, 4)


@pytest.fixture
def support_staff(db):
    return make_user(UserRole.SUPPORT_STAFF, 5)


@pytest.fixture
def category(db):
    return StoreCategory.objects.create(name="أقلام", slug="pens")


@pytest.fixture
def brand(db):
    return StoreBrand.objects.create(name="Pilot")


@pytest.fixture
def product(db, category, brand):
    return StoreProduct.objects.create(
        name="قلم جاف",
        description="قلم ناعم",
        category=category,
        brand=brand,
        price_syp=Decimal("1003.00"),
        price_usd=Decimal("2.50"),
        specs=[{"label": "الحبر", "value": "أزرق"}],
    )


@pytest.fixture
def variant(product):
    return make_variant(product, stock=10, sku="PEN-RED")
