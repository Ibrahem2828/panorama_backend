# سجل التحويل إلى Panorama Backend v2

## أمان ومصادقة
- Email OTP حقيقي مع Hash وCooldown وLockout.
- منع استخدام endpoint OTP كمرسل بريد عشوائي.
- منع تسجيل الدخول قبل توثيق البريد أو الهاتف.
- Refresh token rotation وblacklist وإبطال الجلسات بعد تغيير كلمة المرور.
- Rate Limits للمصادقة، الملفات، واتساب، الشات، الدعم، والتقييم.

## صلاحيات
- Capabilities مركزية.
- أدوار IT Support وAdmin وPrint Staff وSupport Staff وContent Manager.
- User-level allow/deny overrides مع Expiry وAudit.

## ملفات وخصوصية
- Protected tickets للملفات والبطاقات ومرفقات الطباعة والدعم والشات.
- لا روابط Storage خام في عقود الموبايل.
- File signature/MIME/size validation.
- Data retention command.

## واتساب
- تشفير الرابط في قاعدة البيانات.
- إظهار وجود القناة فقط.
- Redirect ticket مؤقت أحادي الاستخدام.

## الطباعة
- Pricing engine داخل الباك.
- قواعد أسعار ومواقع استلام وتجليد.
- Price snapshot وعدم قبول السعر من العميل.
- فصل الملاحظات العامة عن الداخلية.
- فصل Serializer الموبايل عن الداشبورد.

## التقييم والاقتراحات
- تقييم لكل رحلة/إجراء.
- سياسات Sample/Cooldown/Version.
- اقتراحات عامة بعد المراجعة وتصويت.
- Workflow وتحليلات وإشعار صاحب الملاحظة.
- منع تسرب بيانات المستخدم والأجهزة في قائمة الاقتراحات العامة.

## المحادثات والدعم
- WebSocket hardening.
- مرفقات شات محمية.
- منع البلاغات المكررة.
- فصل عقود الدعم العامة عن تفاصيل التعيين الداخلية.

## الإنتاج
- Docker multi-stage/non-root.
- Release job منفصل عن runtime.
- Celery worker.
- CI pipeline واختبارات عقود أمنية.

## تحسينات التسليم والتشغيل
- إضافة `.dockerignore` لمنع إدخال الأسرار والبيانات المؤقتة وملفات البيئة إلى صورة Docker.
- إضافة `SECURITY.md` لقناة الإبلاغ الأمني وسياسة الأسرار وبوابة قبول الإنتاج.
- تسجيل أخطاء حذف الملفات الحساسة في مهمة الاحتفاظ بالبيانات بدل تجاهلها بصمت.
