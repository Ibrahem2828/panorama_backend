# تقرير smoke قراءة فقط للإنتاج

تاريخ الفحص: 2026-07-31  
الهدف: https://api.xn--mgbaab0cxheq.tech  
طريقة الفحص: GET عام فقط، بلا حساب وبلا كتابة.

| المسار | HTTP | code | النتيجة |
| --- | ---: | --- | --- |
| / | 404 | — | PASS: لا stack trace ظاهر. |
| /api/v1/health/ | 200 | OK | PASS |
| /api/v1/health/live/ | 200 | LIVE | PASS |
| /api/v1/health/ready/ | 503 | SERVICE_NOT_READY | **FAIL** |
| /api/v1/health/startup/ | 503 | STARTUP_NOT_READY | **FAIL** |

رُصدت رؤوس HTTPS وX-Request-ID وContent-Security-Policy وX-Content-Type-Options دون كشف stack trace في الاستجابات المقروءة. لم ينفذ smoke مصادق عليه لأن رمز اختبار إنتاج مخصص لم يوفّر، ولا يجوز استخدام حسابات أو بيانات حقيقية.

**القرار:** لا تحول أي ترافيك جديد إلى هذا النشر قبل تشخيص سبب readiness/startup 503 في Coolify من دون طباعة أسرار، ثم إعادة الاختبار.
