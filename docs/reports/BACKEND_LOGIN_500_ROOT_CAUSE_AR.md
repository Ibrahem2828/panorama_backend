# تحليل Login 500 — Panorama Backend

تاريخ التحديث: 2026-08-11  
Request ID المطلوب: `7bf354f2-e63f-49be-acbd-331f63b103d0`

## حالة الدليل

لا توجد سجلات Coolify أو حاوية إنتاج أو أثر لهذا الـRequest ID ضمن مساحة العمل؛ لذلك لا يجوز ادعاء نوع exception إنتاجي محدد أو أن السبب الجذري قد ثبت من السجل. يلزم البحث في سجل التطبيق المنزوع الأسرار بهذا الـRequest ID قبل اعتماد سبب إنتاجي نهائي.

## المسار والمخاطر المثبتة من الكود

1. `ProductLifecycleMiddleware` يقرأ `MaintenanceMode` من PostgreSQL قبل وصول الطلب إلى الـview.
2. `LoginView.post` يقرأ Feature Flag `otp_email_enabled` من PostgreSQL قبل التحقق من بيانات الاعتماد.
3. كان DRF يعيد رمي exceptions غير المعالجة عندما يعيد `custom_exception_handler` القيمة `None`، فيسمح لـDjango بإنتاج صفحة HTML 500.

وبالتالي فإن تعطل PostgreSQL/Redis، أو migration ناقص لجدول `product_*`، يمكنه أن يحول طلب login غير الصحيح إلى 500 قبل أن يصل إلى نتيجة بيانات الاعتماد. هذا يفسر المسار التقني الممكن، لكنه لا يثبت أن migration هو سبب الطلب الإنتاجي المحدد من دون السجل.

## الإصلاح المطبق

- تُحوَّل أعطال DB/Redis/timeout/configuration المعروفة في حدود DRF إلى `503 SERVICE_DEPENDENCY_UNAVAILABLE` أو `503 SERVICE_CONFIGURATION_INVALID`، لا إلى 401 أو 400 مضلل.
- أضيفت طبقة Django لحماية `/api/*` من صفحات HTML وtraceback، وتعيد envelope JSON آمنًا للـ500 غير المتوقع مع `request_id`.
- يسجل التطبيق event آمنًا مع `request_id` و`code` و`failure_class` من دون body أو identifiers أو credentials.
- لا يوجد `catch(Exception)` داخل login ولا تغير لسلوك بيانات الاعتماد غير الصحيحة؛ يبقى العقد الحالي `400` JSON.

## Regression evidence

- Redis/throttling unavailable أثناء login → `503` JSON آمن.
- Exception غير متوقع في LoginView → `500` JSON آمن، بلا تفاصيل exception أو HTML.
- invalid credentials regression القائم يظل ناجحًا.

الحالة: **سبب إنتاجي نهائي غير مثبت لغياب سجلات الطلب؛ حماية العقد وتنميط الأعطال مطبقان ومختبران محليًا.**
