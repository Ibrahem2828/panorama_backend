# دليل نشر Coolify

1. شغّل CI على commit محدد، واعتمد image digest الناتج فقط.
2. اضبط متغيرات runtime من .env.example في Coolify؛ لا تضع سرًا في Build Variables.
3. اربط Volume باسم panorama_media إلى /app/app/media لكل web/worker/conversion-worker المحتاج للملفات، ولا تنشر /media/ عبر proxy.
4. شغّل خدمة release مرة واحدة فقط: check --deploy ثم migrations ثم collectstatic ثم OpenAPI validation. لا تشغل migrations في replicas.
5. شغّل web وworker وconversion-worker وbeat singleton. لا تسمح بالمرور قبل HTTP 200 وcode=READY من /api/v1/health/ready/.
6. نفذ smoke مصادق عليه على Staging، ثم احتفظ بالـdigest والسجلات المنزوعة الأسرار.

إذا أعادت ready/startup 503، أوقف الترقية وافحص DB/Redis/migrations/media mount/configuration من سجلات Coolify فقط. لا تنسخ قيم البيئة إلى ticket أو chat.
