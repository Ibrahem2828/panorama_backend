from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core import signing
from django.core.files.storage import default_storage
from django.http import FileResponse
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.accounts.choices import UserRole


PROTECTED_MEDIA_SALT = "panorama.protected_media.v1"


@dataclass(frozen=True)
class ProtectedMediaToken:
    user_id: int
    object_type: str
    object_id: int
    purpose: str
    expires_in: int
    extra: dict[str, Any]


class ProtectedMediaService:
    @staticmethod
    def create_token(
        *,
        user,
        object_type: str,
        object_id: int,
        purpose: str,
        expires_in: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> tuple[str, int]:
        ttl = int(expires_in or settings.PROTECTED_MEDIA_TOKEN_TTL_SECONDS)
        payload = {
            "user_id": user.id,
            "object_type": object_type,
            "object_id": int(object_id),
            "purpose": purpose,
            "expires_in": ttl,
            "extra": extra or {},
        }
        return signing.dumps(payload, salt=PROTECTED_MEDIA_SALT), ttl

    @staticmethod
    def load_token(token: str) -> ProtectedMediaToken:
        try:
            payload = signing.loads(
                token,
                salt=PROTECTED_MEDIA_SALT,
                max_age=settings.PROTECTED_MEDIA_TOKEN_TTL_SECONDS,
            )
        except signing.SignatureExpired as exc:
            raise PermissionDenied("Protected media token has expired.") from exc
        except signing.BadSignature as exc:
            raise PermissionDenied("Invalid protected media token.") from exc

        try:
            return ProtectedMediaToken(
                user_id=int(payload["user_id"]),
                object_type=str(payload["object_type"]),
                object_id=int(payload["object_id"]),
                purpose=str(payload["purpose"]),
                expires_in=int(payload.get("expires_in", settings.PROTECTED_MEDIA_TOKEN_TTL_SECONDS)),
                extra=dict(payload.get("extra") or {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PermissionDenied("Invalid protected media token.") from exc

    @staticmethod
    def build_url(request, token: str) -> str:
        return f"/api/v1/protected-media/{token}/"

    @staticmethod
    def _safe_file_response(file_field, *, as_attachment: bool = False) -> FileResponse:
        if not file_field:
            raise NotFound("File not found.")
        name = getattr(file_field, "name", "") or ""
        if not name or Path(name).is_absolute() or ".." in Path(name).parts:
            raise NotFound("File not found.")
        if not default_storage.exists(name):
            raise NotFound("File not found.")
        file_handle = default_storage.open(name, "rb")
        response = FileResponse(file_handle, as_attachment=as_attachment, filename=Path(name).name)
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response

    @staticmethod
    def _get_token_user(token_data: ProtectedMediaToken):
        from apps.accounts.models import User

        user = User.objects.filter(pk=token_data.user_id, is_active=True).first()
        if user is None:
            raise PermissionDenied("Invalid protected media token.")
        return user

    @staticmethod
    def _resolve_file_resource(token_data: ProtectedMediaToken, user):
        from apps.files.models import FileResource
        from apps.files.services import user_can_access_file

        file_resource = FileResource.objects.filter(pk=token_data.object_id, is_deleted=False).first()
        if file_resource is None:
            raise NotFound("File not found.")
        if token_data.purpose == "file_download":
            if not user_can_access_file(user, file_resource):
                raise PermissionDenied("You do not have access to this file.")
        elif token_data.purpose == "file_preview":
            if user.role not in {UserRole.ADMIN, UserRole.IT_SUPPORT}:
                raise PermissionDenied("You do not have access to this file.")
        else:
            raise PermissionDenied("Invalid protected media token.")
        return file_resource.file

    @staticmethod
    def _resolve_verification_card(token_data: ProtectedMediaToken, user):
        from apps.verification.models import VerificationRequest

        if token_data.purpose != "verification_card_preview" or user.role not in {UserRole.ADMIN, UserRole.IT_SUPPORT}:
            raise PermissionDenied("You do not have access to this file.")
        verification = VerificationRequest.objects.filter(pk=token_data.object_id, is_deleted=False).first()
        if verification is None:
            raise NotFound("File not found.")
        return verification.card_image

    @staticmethod
    def _resolve_student_account_request_card(token_data: ProtectedMediaToken, user):
        from apps.accounts.student_account_request_models import StudentAccountRequest

        if token_data.purpose != "student_account_card_preview" or user.role not in {
            UserRole.ADMIN,
            UserRole.IT_SUPPORT,
        }:
            raise PermissionDenied("You do not have access to this file.")
        request_obj = StudentAccountRequest.objects.filter(pk=token_data.object_id, is_deleted=False).first()
        if request_obj is None:
            raise NotFound("File not found.")
        return request_obj.uploaded_card

    @staticmethod
    def _resolve_print_order_file(token_data: ProtectedMediaToken, user):
        from apps.printing.models import PrintOrder, PrintOrderItem

        if token_data.purpose != "print_file_preview" or user.role not in {
            UserRole.PRINT_STAFF,
            UserRole.ADMIN,
            UserRole.IT_SUPPORT,
        }:
            raise PermissionDenied("You do not have access to this file.")
        order = PrintOrder.objects.filter(pk=token_data.object_id, is_deleted=False).first()
        if order is None:
            raise NotFound("File not found.")

        item_id = token_data.extra.get("item_id")
        items = PrintOrderItem.objects.filter(order=order, is_deleted=False).select_related("source_file")
        item = items.filter(pk=item_id).first() if item_id else items.first()
        if item is None:
            raise NotFound("File not found.")
        if item.uploaded_file:
            return item.uploaded_file
        if item.source_file and item.source_file.file:
            return item.source_file.file
        raise NotFound("File not found.")

    @staticmethod
    def serve(token: str) -> FileResponse:
        token_data = ProtectedMediaService.load_token(token)
        user = ProtectedMediaService._get_token_user(token_data)
        if token_data.object_type == "file_resource":
            file_field = ProtectedMediaService._resolve_file_resource(token_data, user)
        elif token_data.object_type == "verification_request":
            file_field = ProtectedMediaService._resolve_verification_card(token_data, user)
        elif token_data.object_type == "student_account_request":
            file_field = ProtectedMediaService._resolve_student_account_request_card(token_data, user)
        elif token_data.object_type == "print_order":
            file_field = ProtectedMediaService._resolve_print_order_file(token_data, user)
        else:
            raise ValidationError({"token": "Unsupported protected media object type."})
        return ProtectedMediaService._safe_file_response(file_field)
