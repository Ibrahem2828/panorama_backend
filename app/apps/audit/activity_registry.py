"""Single place that decides WHAT is recorded in ``SystemActivity`` and HOW it is described.

* ``SKIP_ROUTES``: high-volume noise that is never recorded (explicit, one list).
* ``ROUTE_LABELS``: ``(METHOD, route pattern) -> short Arabic label`` for action endpoints.
* ``RESOURCE_NOUNS``: Arabic nouns for plain CRUD collections (``dashboard/<resource>/`` and ``.../{pk}/``).
* ``label_for(method, route)``: registry lookup with a generic fallback ``طلب {method} {route}``.

Routes are URL *patterns* as produced by ``normalise_route`` (``dashboard/users/{user_id}/``), never raw paths.
"""

from __future__ import annotations

#: Route patterns (without the ``api/v1/`` prefix) that are never recorded: polling, receipts, heartbeats, token
#: refresh and health probes would drown the trail. ``auth/login/`` and ``auth/logout/`` are skipped here because the
#: dedicated login/logout events describe them better than a generic request row.
SKIP_ROUTES: frozenset[str] = frozenset(
    {
        "notifications/read-all/",
        "notifications/{pk}/read/",
        "notifications/summary/",
        "groups/{group_id}/chat/receipts/",
        "chat/unread/",
        "chat/presence/",
        "chat/heartbeat/",
        "groups/{group_id}/chat/presence/",
        "groups/{group_id}/chat/heartbeat/",
        "auth/token/refresh/",
        "dashboard/quick-access/",
        "printing/agent/heartbeat/",
        "feedback/prompt-event/",
        "auth/login/",
        "auth/logout/",
    }
)
#: Any route whose first segment is one of these is skipped too (``health/``, ``health/ready/`` ...).
SKIP_PREFIXES: tuple[str, ...] = ("health/", "schema/", "docs/")


def is_skipped(route: str) -> bool:
    return route in SKIP_ROUTES or route.startswith(SKIP_PREFIXES)


#: Arabic nouns for CRUD resources, keyed by the collection path (``dashboard/groups`` -> ``مجموعة``).
RESOURCE_NOUNS: dict[str, str] = {
    "dashboard/academic-years": "سنة دراسية",
    "dashboard/announcements": "إعلان",
    "dashboard/faculties": "كلية",
    "dashboard/feature-flags": "مفتاح ميزة",
    "dashboard/feedback-prompt-policies": "سياسة طلب الملاحظات",
    "dashboard/file-visibility-templates": "قالب ظهور الملفات",
    "dashboard/files": "ملف",
    "dashboard/finance/exchange-rates": "سعر صرف",
    "dashboard/finance/expenses": "مصروف",
    "dashboard/finance/inventory-items": "صنف مخزون",
    "dashboard/finance/purchase-reminders": "تذكير شراء",
    "dashboard/groups": "مجموعة",
    "dashboard/lectures": "محاضرة",
    "dashboard/maintenance-modes": "وضع صيانة",
    "dashboard/majors": "اختصاص",
    "dashboard/mobile-release-policies": "سياسة إصدار التطبيق",
    "dashboard/permission-templates": "قالب صلاحيات",
    "dashboard/printing/binding-prices": "سعر تجليد",
    "dashboard/printing/covers": "غلاف",
    "dashboard/printing/pickup-locations": "موقع استلام",
    "dashboard/printing/pricing-rules": "قاعدة تسعير",
    "dashboard/printing/printers": "طابعة",
    "dashboard/printing/products": "منتج طباعة",
    "dashboard/printing/wire-specs": "مواصفة سلك",
    "dashboard/privacy-policy-versions": "نسخة سياسة الخصوصية",
    "dashboard/semesters": "فصل دراسي",
    "dashboard/subjects": "مادة",
    "dashboard/terms-versions": "نسخة الشروط",
    "dashboard/universities": "جامعة",
    "notifications/device-tokens": "رمز إشعارات الجهاز",
}

_VERB_BY_METHOD = {"POST": "إضافة", "PUT": "تعديل", "PATCH": "تعديل", "DELETE": "حذف"}

#: Exact ``(METHOD, route)`` labels for action endpoints and the staff/student flows.
ROUTE_LABELS: dict[tuple[str, str], str] = {
    # --- account / auth ---
    ("POST", "account/deletion/request/"): "طلب حذف الحساب",
    ("POST", "account/deletion/cancel/"): "إلغاء طلب حذف الحساب",
    ("POST", "auth/change-password/"): "تغيير كلمة المرور",
    ("POST", "auth/confirm-password-reset/"): "تأكيد استعادة كلمة المرور",
    ("POST", "auth/request-password-reset/"): "طلب استعادة كلمة المرور",
    ("POST", "auth/invitations/accept/"): "قبول دعوة وتعيين كلمة المرور",
    ("POST", "auth/otp/send/"): "طلب رمز تحقق",
    ("POST", "auth/otp/verify/"): "إدخال رمز التحقق",
    ("POST", "auth/register/normal/"): "تسجيل حساب جديد",
    ("POST", "auth/register/student/"): "تسجيل طالب جديد",
    ("PATCH", "auth/me/"): "تعديل الملف الشخصي",
    ("PATCH", "students/me/profile/"): "تعديل الملف الدراسي",
    ("POST", "students/me/term-confirmation/"): "تأكيد الفصل الدراسي",
    ("POST", "policies/accept/"): "قبول الشروط والسياسات",
    # --- student actions ---
    ("POST", "enrollment/requests/"): "تقديم طلب تسجيل",
    ("POST", "enrollment/requests/{pk}/cancel/"): "إلغاء طلب تسجيل",
    ("POST", "verification/submit/"): "إرسال طلب توثيق",
    ("POST", "verification/resubmit/"): "إعادة إرسال طلب التوثيق",
    ("POST", "feedback/"): "إرسال ملاحظة",
    ("POST", "feedback/{pk}/vote/"): "تصويت على ملاحظة",
    ("POST", "feedback/{pk}/privacy-request/"): "طلب خصوصية لملاحظة",
    ("POST", "support/tickets/"): "فتح تذكرة دعم",
    ("POST", "support/tickets/{pk}/messages/"): "رد على تذكرة دعم",
    ("POST", "support/messages/{pk}/attachment-ticket/"): "طلب فتح مرفق دعم",
    ("POST", "printing/orders/"): "إنشاء طلب طباعة",
    ("POST", "printing/orders/{pk}/cancel/"): "إلغاء طلب طباعة",
    ("POST", "printing/quote/"): "طلب تسعيرة طباعة",
    ("POST", "printing/convert/"): "تحويل ملف للطباعة",
    ("POST", "printing/items/{pk}/access-ticket/"): "طلب فتح ملف طباعة",
    ("POST", "files/{pk}/access-ticket/"): "طلب فتح ملف",
    ("POST", "files/{pk}/viewer/session/"): "فتح ملف في العارض",
    ("POST", "lectures/{pk}/viewer/session/"): "فتح محاضرة في العارض",
    ("POST", "lectures/{pk}/notes/"): "إضافة ملاحظة على محاضرة",
    ("PATCH", "lectures/{lecture_id}/notes/{note_id}/"): "تعديل ملاحظة على محاضرة",
    ("DELETE", "lectures/{lecture_id}/notes/{note_id}/"): "حذف ملاحظة على محاضرة",
    ("POST", "annotations/{kind}/{pk}/sync/"): "مزامنة تعليقات الملف",
    ("POST", "groups/{pk}/join/"): "طلب الانضمام لمجموعة",
    ("POST", "groups/{pk}/leave/"): "مغادرة مجموعة",
    ("POST", "groups/{pk}/whatsapp-launch/"): "فتح مجموعة واتساب",
    ("POST", "groups/{pk}/whatsapp-ticket/"): "طلب رابط مجموعة واتساب",
    ("POST", "groups/{group_id}/chat-ticket/"): "فتح محادثة المجموعة",
    ("PUT", "groups/{group_id}/chat/preferences/"): "تعديل إعدادات محادثة المجموعة",
    ("POST", "groups/{group_id}/messages/"): "إرسال رسالة في المحادثة",
    ("DELETE", "groups/{group_id}/messages/{pk}/"): "حذف رسالة",
    ("DELETE", "groups/{group_id}/messages/{message_id}/delete/"): "حذف رسالة",
    ("PUT", "groups/{group_id}/messages/{message_id}/reaction/"): "تفاعل مع رسالة",
    ("DELETE", "groups/{group_id}/messages/{message_id}/reaction/"): "إزالة تفاعل عن رسالة",
    ("POST", "groups/{group_id}/messages/{message_id}/report/"): "الإبلاغ عن رسالة",
    ("POST", "groups/{group_id}/messages/{message_id}/attachment-ticket/"): "طلب فتح مرفق رسالة",
    ("POST", "mobile/devices/register/"): "تسجيل جهاز",
    ("PATCH", "mobile/devices/{installation_id}/"): "تعديل بيانات جهاز",
    ("POST", "mobile/devices/{installation_id}/revoke/"): "إلغاء جهاز",
    ("PATCH", "notifications/preferences/"): "تعديل تفضيلات الإشعارات",
    ("POST", "notifications/web-push/subscriptions/"): "تفعيل إشعارات المتصفح",
    ("DELETE", "notifications/web-push/subscriptions/"): "إيقاف إشعارات المتصفح",
    ("POST", "notifications/device-tokens/"): "تسجيل رمز إشعارات الجهاز",
    # --- dashboard: academic ---
    ("PUT", "dashboard/academic/current-term/"): "تغيير الفصل الحالي",
    ("POST", "dashboard/academic/end-term/"): "إنهاء الفصل الدراسي",
    ("POST", "dashboard/academic/end-term/preview/"): "معاينة إنهاء الفصل",
    ("POST", "dashboard/academic/end-term/{rollover_id}/undo/"): "التراجع عن إنهاء الفصل",
    # --- dashboard: users ---
    ("POST", "dashboard/users/invite/"): "دعوة مستخدم جديد",
    ("PATCH", "dashboard/users/{pk}/"): "تعديل بيانات مستخدم",
    ("POST", "dashboard/users/{pk}/activate/"): "تفعيل حساب مستخدم",
    ("POST", "dashboard/users/{pk}/deactivate/"): "تعطيل حساب مستخدم",
    ("POST", "dashboard/users/{pk}/unlock-login/"): "فك قفل تسجيل الدخول",
    ("POST", "dashboard/users/{user_id}/end-sessions/"): "إنهاء جلسات مستخدم",
    ("POST", "dashboard/users/{user_id}/resend-invitation/"): "إعادة إرسال دعوة",
    ("POST", "dashboard/users/{user_id}/revoke-invitation/"): "إلغاء دعوة",
    ("PUT", "dashboard/users/{user_id}/permission-overrides/"): "تعديل صلاحيات مستخدم",
    ("DELETE", "dashboard/users/{user_id}/permission-overrides/"): "حذف صلاحيات مستخدم المخصصة",
    ("DELETE", "dashboard/users/{user_id}/permission-overrides/{permission_code}/"): "حذف صلاحية مخصصة",
    # --- dashboard: moderation / requests ---
    ("POST", "dashboard/account-deletions/{pk}/cancel/"): "إلغاء طلب حذف حساب",
    ("POST", "dashboard/account-deletions/{pk}/execute/"): "تنفيذ حذف حساب",
    ("POST", "dashboard/chat/reports/{pk}/resolve/"): "معالجة بلاغ محادثة",
    ("POST", "dashboard/devices/{pk}/revoke/"): "إلغاء جهاز مستخدم",
    ("POST", "dashboard/enrollment/requests/bulk-decision/"): "قرار جماعي على طلبات التسجيل",
    ("POST", "dashboard/enrollment/requests/{pk}/decision/"): "قرار على طلب تسجيل",
    ("POST", "dashboard/enrollment/requests/{pk}/screenshot-ticket/"): "فتح صورة طلب تسجيل",
    ("PATCH", "dashboard/feedback/{pk}/workflow/"): "تحديث حالة ملاحظة",
    ("POST", "dashboard/files/reprocess-bulk/"): "إعادة معالجة ملفات",
    ("POST", "dashboard/files/{pk}/reprocess/"): "إعادة معالجة ملف",
    ("POST", "dashboard/group-memberships/{pk}/approve/"): "قبول عضوية مجموعة",
    ("POST", "dashboard/group-memberships/{pk}/block/"): "حظر عضوية مجموعة",
    ("POST", "dashboard/group-memberships/{pk}/reject/"): "رفض عضوية مجموعة",
    ("PATCH", "dashboard/group-memberships/{pk}/role/"): "تغيير دور عضو مجموعة",
    ("PUT", "dashboard/groups/{pk}/whatsapp-channel/"): "تعديل قناة واتساب المجموعة",
    ("POST", "dashboard/lectures/{pk}/retry/"): "إعادة معالجة محاضرة",
    ("POST", "dashboard/notifications/campaign/"): "إرسال حملة إشعارات",
    ("POST", "dashboard/verifications/{pk}/approve/"): "قبول طلب توثيق",
    ("POST", "dashboard/verifications/{pk}/reject/"): "رفض طلب توثيق",
    ("POST", "dashboard/verifications/{pk}/needs-update/"): "طلب تعديل على التوثيق",
    ("POST", "dashboard/verifications/{pk}/card-ticket/"): "فتح صورة بطاقة التوثيق",
    # --- dashboard: finance / printing / shop ---
    ("POST", "dashboard/finance/payments/{pk}/void/"): "إلغاء دفعة",
    ("POST", "dashboard/printing/counter-orders/"): "إنشاء طلب طباعة من المحل",
    ("POST", "dashboard/printing/counter-orders/quote/"): "تسعير طلب من المحل",
    ("PATCH", "dashboard/printing/orders/{pk}/assign/"): "إسناد طلب طباعة",
    ("POST", "dashboard/printing/orders/{pk}/note/"): "ملاحظة على طلب طباعة",
    ("POST", "dashboard/printing/orders/{pk}/print-jobs/"): "إنشاء مهمة طباعة",
    ("PATCH", "dashboard/printing/orders/{pk}/status/"): "تغيير حالة طلب طباعة",
    ("POST", "dashboard/printing/payments/"): "تسجيل دفعة طباعة",
    ("POST", "dashboard/printing/print-jobs/{pk}/cancel/"): "إلغاء مهمة طباعة",
    ("POST", "dashboard/printing/print-jobs/{pk}/retry/"): "إعادة مهمة طباعة",
    ("PATCH", "dashboard/printing/print-jobs/{pk}/status/"): "تغيير حالة مهمة طباعة",
    ("POST", "dashboard/printing/stock/{pk}/movements/"): "حركة مخزون",
    ("POST", "dashboard/shop/sales/create/"): "تسجيل بيع قرطاسية",
    # --- dashboard: support ---
    ("POST", "dashboard/support/tickets/{pk}/assign/"): "إسناد تذكرة دعم",
    ("POST", "dashboard/support/tickets/{pk}/messages/"): "رد على تذكرة دعم",
    ("PATCH", "dashboard/support/tickets/{pk}/priority/"): "تغيير أولوية تذكرة",
    ("PATCH", "dashboard/support/tickets/{pk}/status/"): "تغيير حالة تذكرة",
}


def _crud_label(method: str, route: str) -> str | None:
    verb = _VERB_BY_METHOD.get(method)
    if verb is None:
        return None
    collection = route.removesuffix("/")
    if collection.endswith("/{pk}"):
        collection = collection[: -len("/{pk}")]
    noun = RESOURCE_NOUNS.get(collection)
    if noun is None:
        return None
    if method == "POST" and route.removesuffix("/") != collection:
        return None  # POST on an item route is an action, not a create
    if method != "POST" and route.removesuffix("/") == collection:
        return None  # PATCH/PUT/DELETE need the item route
    return f"{verb} {noun}"


def label_for(method: str, route: str) -> str:
    method = method.upper()
    label = ROUTE_LABELS.get((method, route)) or _crud_label(method, route)
    return label or f"طلب {method} {route}"
