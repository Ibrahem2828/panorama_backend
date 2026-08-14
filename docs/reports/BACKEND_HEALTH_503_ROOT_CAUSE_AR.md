# تحليل READY/STARTUP 503 — Panorama Backend

تاريخ التحديث: 2026-08-11

## الدليل المتاح

- القياس السابق للبيئة المنشورة: `live=200` و`ready=503` و`startup=503`.
- `GET /api/v1/health/db/` أعاد `200` وقت القياس؛ لذا لم يكن اتصال PostgreSQL وحده سبب الفشل حينها.
- لا توجد سجلات Coolify أو console أو بيانات اعتماد تشغيلية في هذه الجلسة؛ لذلك dependency الفاشلة بالضبط غير قابلة للإسناد بأمان.

## مصفوفة الاعتماديات

| الاعتمادية | Live | Ready | Startup | الحالة الإنتاجية المثبتة | السلوك |
| --- | --- | --- | --- | --- | --- |
| PostgreSQL | لا | نعم | نعم | probe DB كان سليمًا | 503 عند الفشل |
| Redis/cache | لا | نعم | نعم | غير مثبت | 503 عند الفشل |
| migrations | لا | نعم | نعم | غير مثبت | 503 عند وجود خطة pending |
| media storage/config | لا | نعم | نعم | غير مثبت | 503 عند غياب volume أو صلاحياته |
| Celery/email/SMS/push/conversion | لا | لا | لا | غير مطلوب لطلبات الويب الأساسية | لا يمنع readiness |

## السبب الجذري

**غير مثبت من دون سجل الإنتاج.** بعد استبعاد probe قاعدة البيانات في لحظة القياس، الاحتمالات القابلة للإثبات من التنفيذ هي Redis، migrations غير مطبقة، أو mount/sلاحيات `MEDIA_ROOT`. لا يصح اختيار أحدها بالحدس.

## الإصلاح التشخيصي والسلوكي

- لم تُخفَّف readiness إلى 200 وهمية.
- فصلت checks إلى DB وRedis وmigrations وconfiguration/storage، وسجلت `dependency` و`failure_class` و`pending_migration_count` و`request_id` آمنةً في السجل.
- استجابة العميل تظل `503` عامة بلا أسماء hosts أو أسرار أو تفاصيل migration.
- liveness لا يفحص DB أو Redis أو storage ويظل `200` حين تفشل dependency خارجية.

## المطلوب على Coolify قبل النشر

نفّذ release job مرة واحدة، ثم افحص `showmigrations` و`storage_status --write-test` وRedis ping من runtime، وابحث عن الـrequest IDs في logs المنزوعة الأسرار. لا تُشغَّل migrations أو probes الكتابية على الإنتاج من هذه الجلسة.
