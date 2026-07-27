# سجل تحويل Panorama Backend v2

> مرشح إنتاج مرجعي — لا يعد اعتمادًا نهائيًا للإنتاج قبل تطبيق migrations والاختبارات والتدقيق الأمني في بيئة Staging.

- وقت إنشاء السجل: 2026-07-27T10:18:34.667125+00:00
- ملفات النسخة الأصلية المقارنة: 173
- ملفات النسخة الجديدة المقارنة: 220
- ملفات مضافة: 48
- ملفات معدلة: 70
- ملفات محذوفة: 1

## أبرز التحولات

- نظام RBAC بصلاحيات قابلة للتوسعة واستثناءات Allow/Deny مؤقتة لكل مستخدم.
- OTP بريدي آمن ومشفّر، وتدوير JWT، وإلغاء الجلسات بعد تغيير كلمة المرور.
- نظام تقييم واقتراحات مرتبط بالإجراءات والإصدارات والمنصات، مع Workflow إداري وتحليلات.
- إخفاء رابط واتساب من الاستجابات العامة، وتشفيره في قاعدة البيانات، وفتحه بتذكرة قصيرة العمر.
- حماية بطاقات الطلاب والملفات ومرفقات الشات والدعم والطباعة بتذاكر وصول مؤقتة.
- Pricing Engine للطباعة يحسب السعر داخل الباك ويحفظ Snapshot ولا يثق بسعر العميل.
- تقوية إعدادات الإنتاج، Docker غير Root، Release Job، CI، احتفاظ بالبيانات، ومراقبة أفضل.

## الملفات المضافة

- `.dockerignore`
- `.github/workflows/backend-ci.yml`
- `CHANGELOG_V2_AR.md`
- `SECURITY.md`
- `app/apps/accounts/dashboard_serializers.py`
- `app/apps/accounts/dashboard_urls.py`
- `app/apps/accounts/dashboard_views.py`
- `app/apps/accounts/migrations/0004_security_rbac_and_email_otp.py`
- `app/apps/chat/migrations/0002_protected_attachments_and_report_constraint.py`
- `app/apps/common/crypto.py`
- `app/apps/common/file_validation.py`
- `app/apps/common/management/commands/purge_expired_sensitive_data.py`
- `app/apps/common/management/commands/seed_production_defaults.py`
- `app/apps/common/middleware.py`
- `app/apps/common/request_utils.py`
- `app/apps/common/throttles.py`
- `app/apps/feedback/__init__.py`
- `app/apps/feedback/admin.py`
- `app/apps/feedback/apps.py`
- `app/apps/feedback/migrations/0001_initial.py`
- `app/apps/feedback/migrations/0002_extended_feedback_contexts.py`
- `app/apps/feedback/migrations/__init__.py`
- `app/apps/feedback/models.py`
- `app/apps/feedback/serializers.py`
- `app/apps/feedback/services.py`
- `app/apps/feedback/urls.py`
- `app/apps/feedback/views.py`
- `app/apps/files/document_inspection.py`
- `app/apps/files/migrations/0002_protected_access_and_metadata.py`
- `app/apps/groups/migrations/0003_encrypted_external_channels.py`
- `app/apps/notifications/migrations/0003_expand_notification_types.py`
- `app/apps/notifications/tasks.py`
- `app/apps/printing/migrations/0002_pricing_engine_and_protected_items.py`
- `app/apps/printing/migrations/0003_split_public_internal_status_notes.py`
- `app/apps/support/migrations/0002_protected_attachments_and_response_time.py`
- `app/apps/verification/migrations/0002_protected_cards_and_pending_constraint.py`
- `app/config/celery.py`
- `app/tests/__init__.py`
- `app/tests/test_feedback.py`
- `app/tests/test_otp_and_login.py`
- `app/tests/test_printing_contract.py`
- `app/tests/test_security_contracts.py`
- `docker/release.sh`
- `docs/api/README_V2.md`
- `docs/architecture/BACKEND_V2_PRODUCTION_TRANSFORMATION_AR.md`
- `docs/architecture/FEEDBACK_AND_RATING_INTEGRATION_AR.md`
- `docs/operations/SECURITY_AND_PRODUCTION_OPERATIONS_AR.md`
- `pyproject.toml`

## الملفات المعدلة

- `.env.example`
- `Dockerfile`
- `README.md`
- `app/apps/accounts/admin.py`
- `app/apps/accounts/choices.py`
- `app/apps/accounts/models.py`
- `app/apps/accounts/permissions.py`
- `app/apps/accounts/serializers.py`
- `app/apps/accounts/services.py`
- `app/apps/accounts/views.py`
- `app/apps/announcements/views.py`
- `app/apps/audit/models.py`
- `app/apps/audit/services.py`
- `app/apps/audit/views.py`
- `app/apps/chat/admin.py`
- `app/apps/chat/consumers.py`
- `app/apps/chat/middleware.py`
- `app/apps/chat/models.py`
- `app/apps/chat/serializers.py`
- `app/apps/chat/services.py`
- `app/apps/chat/urls.py`
- `app/apps/chat/views.py`
- `app/apps/common/dashboard_views.py`
- `app/apps/common/exceptions.py`
- `app/apps/common/pagination.py`
- `app/apps/common/responses.py`
- `app/apps/common/viewsets.py`
- `app/apps/files/admin.py`
- `app/apps/files/models.py`
- `app/apps/files/serializers.py`
- `app/apps/files/services.py`
- `app/apps/files/urls.py`
- `app/apps/files/views.py`
- `app/apps/groups/admin.py`
- `app/apps/groups/models.py`
- `app/apps/groups/serializers.py`
- `app/apps/groups/services.py`
- `app/apps/groups/urls.py`
- `app/apps/groups/views.py`
- `app/apps/notifications/models.py`
- `app/apps/notifications/serializers.py`
- `app/apps/notifications/services.py`
- `app/apps/printing/admin.py`
- `app/apps/printing/models.py`
- `app/apps/printing/serializers.py`
- `app/apps/printing/services.py`
- `app/apps/printing/urls.py`
- `app/apps/printing/views.py`
- `app/apps/support/admin.py`
- `app/apps/support/models.py`
- `app/apps/support/serializers.py`
- `app/apps/support/services.py`
- `app/apps/support/urls.py`
- `app/apps/support/views.py`
- `app/apps/universities/views.py`
- `app/apps/verification/admin.py`
- `app/apps/verification/models.py`
- `app/apps/verification/serializers.py`
- `app/apps/verification/services.py`
- `app/apps/verification/urls.py`
- `app/apps/verification/views.py`
- `app/config/__init__.py`
- `app/config/settings/base.py`
- `app/config/settings/production.py`
- `app/config/settings/testing.py`
- `app/config/urls.py`
- `docker-compose.yml`
- `docker/entrypoint.sh`
- `requirements/base.txt`
- `requirements/local.txt`

## الملفات المحذوفة

- `.env`

## حدود التحقق الحالي

- نجح فحص بناء Python النحوي وتحليل AST وملفات JSON وShell.
- لم تُشغّل Django checks أو migrations أو pytest في هذه الجلسة لأن اعتماديات Django وقاعدة البيانات والخدمات الخارجية غير متاحة دون اتصال.
- يجب أن تنفذ CI وStaging الاختبارات والمخطط والمهاجرات قبل أي نشر إنتاجي.
