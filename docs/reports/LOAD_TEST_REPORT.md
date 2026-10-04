# تقرير اختبار الحمل

الحالة: **BLOCKED**

لم ينفذ اختبار k6/Locust في هذه الجلسة لأن بيئة Staging المعزولة ومواردها وحدودها غير متاحة. لا يجوز استخدام الإنتاج لحقن حمل أو بيانات اختبار.

## خطة التنفيذ المطلوبة

1. انشر image digest نفسه على Staging مع PostgreSQL وRedis وCelery وChannels.
2. استخدم مستخدمين وملفات وطلبات طباعة اصطناعية فقط.
3. نفّذ سيناريوهات login/refresh، bootstrap، قوائم المواد، رسائل REST وWebSocket، viewer، quote/order idempotency، support، feedback.
4. سجل p50/p95/p99، RPS، 4xx/5xx، CPU/RAM، اتصالات PostgreSQL، Redis، عمق queue، وعدد وصلات WebSocket.
5. اعتمد SLOs وفق سعة الخادم قبل الاختبار؛ لا تُخترع أرقام قبول بعد التنفيذ.

لا تتحول هذه البوابة إلى PASS إلا بإرفاق نتائج التنفيذ ونسخة image digest والبيئة والتاريخ.
