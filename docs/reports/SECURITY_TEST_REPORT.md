# تقرير فحوص الأمان — Panorama Backend

تاريخ التقرير: 2026-07-31  
النطاق: الشجرة المحلية في فرع backend/final-production-closure. لا يحتوي هذا التقرير أسرارًا أو بيانات تشغيل.

| الفحص | الأمر/الأسلوب | النتيجة | الدليل والحدود |
| --- | --- | --- | --- |
| قراءة فقط في الـViewSets | اختبار resolver وطلبات POST/PUT/PATCH/DELETE | PASS | tests_readonly_contract.py يغطي كل موروث من StandardReadOnlyModelViewSet ويثبت 405. |
| إدارة مستخدمي لوحة التحكم | API integration | PASS | رفض POST/PUT/DELETE، patch محدود، منع mass assignment، idempotency، audit، ومنع تعطيل النفس/آخر IT Support. |
| JWT وإبطال الجلسات | API integration | PASS | رمز access وrefresh سابقان يرفضان بعد تغيير كلمة المرور؛ migration إضافة فقط لـsession_version. |
| محاضرات وCelery dispatch | integration + transaction behavior | PASS محليًا | flag المعالجة يفحص قبل الحفظ؛ فشل نشر المهمة بعد commit يسجل حالة FAILED قابلة للمراجعة. |
| Ruff | ruff check app وruff format --check app | PASS | لا أخطاء تنسيق أو lint. |
| Bandit Medium/High | bandit على source | PASS | لا نتائج Medium/High. |
| pip-audit | pip-audit --disable-pip -r requirements.lock | PASS | أُعيد الفحص في نهاية العمل: No known vulnerabilities found. |
| Gitleaks | executable/CI action | BLOCKED محليًا | غير مثبت محليًا؛ workflow يستعمل gitleaks action. |
| DAST وIDOR الخارجي | OWASP ZAP على Staging مع حسابات اختبار | BLOCKED | لا توجد Staging معتمدة أو بيانات اختبار في هذه الجلسة. |
| Trivy/SBOM | image مبني وتشغيل Trivy/Syft | BLOCKED | Docker Linux daemon غير متاح محليًا؛ CI يوقف الإصدار عند فشل Trivy ويصدر SBOM/provenance. |

## ضوابط مثبتة في المصدر والاختبارات

- الموارد المسماة للقراءة فقط لا ترث create/update/destroy بعد الإصلاح.
- تنزيل الملفات الخاصة يمر من endpoints مصادق عليها وبفحص ملكية/صلاحية؛ لا يضاف static(MEDIA_URL) إلى الإنتاج.
- تجديد JWT يستخدم rotation/blacklist، وإصدار الجلسة يمنع الرموز التي ألغيت بعد تغيّر أمني.
- سجلات الإنتاج JSON مع SensitiveDataFilter؛ لا ينبغي تسجيل كلمات مرور أو OTP أو Authorization أو رموز refresh.
- عمليات التفعيل/التعطيل الإدارية تترك Audit event وتحتاج Idempotency-Key.

## مسائل لا يثبتها هذا التقرير

لا يعني نجاح الاختبارات المحلية عدم وجود ثغرات مستقبلية. لا توجد ثغرات حرجة أو عالية **معروفة وغير مقبولة ضمن الفحوص المنفذة**، لكن DAST وsecret scan وimage scan ما زالت بوابات محجوبة حتى تشغيل CI وStaging.
