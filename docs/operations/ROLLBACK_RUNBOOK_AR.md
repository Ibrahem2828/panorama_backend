# دليل التراجع

1. حدّد digest الإصدار السابق وسبب التراجع وسجّل وقت القرار.
2. لا تعكس migration data/destructive أثناء الحادث. سياسة المشروع expand/migrate/contract.
3. انشر image digest السابق للـweb وworker وconversion-worker، ثم شغّل release فقط إن كانت migrations backward-compatible.
4. افحص live/ready/startup، login/refresh، الملفات الخاصة، Celery، WebSocket، وأهم الرحلات.
5. إذا كانت قاعدة البيانات غير متوافقة، استعد نسخة Staging/isolated موثقة؛ لا تعدل production يدويًا.

اختبار rollback الحقيقي ما زال مطلوبًا على Staging قبل اعتماد production.
