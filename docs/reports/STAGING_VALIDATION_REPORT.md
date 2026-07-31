# تقرير اعتماد Staging

الحالة: **BLOCKED**

لا توجد في هذه الجلسة صلاحية أو عنوان Staging معتمد ولا بيانات اختبار مناسبة. المطلوب قبل الاعتماد:

1. نشر immutable image digest الناتج من CI، وليس tag متحركًا.
2. تنفيذ release job مرة واحدة على قاعدة فارغة وعلى نسخة ترقية معزولة.
3. التحقق من live/ready/startup وDaphne/Channels وworker وconversion-worker وbeat.
4. اختبار volume media بعد restart/redeploy، والملفات الخاصة، وTLS/WSS وforwarded headers.
5. تنفيذ smoke وDAST وload وbackup/restore وrollback وحفظ artifacts.

أي نجاح محلي أو صحة compose لا يثبت هذه البنود.
