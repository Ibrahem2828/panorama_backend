# مصفوفة OWASP API Security Top 10

| الخطر | الضوابط الموجودة | الحالة |
| --- | --- | --- |
| API1 BOLA | queryset حسب المستخدم/عضوية/قدرات، اختبارات ملفات ومحاضرات | PARTIAL — DAST IDOR مطلوب |
| API2 Broken authentication | JWT rotation/blacklist/session version، OTP throttles | PASS محليًا |
| API3 Broken object property authorization | serializers ضيقة للـDashboard وتحقق mass assignment | PASS محليًا |
| API4 Unrestricted resource consumption | throttles، size limits، pagination | PARTIAL — load test مطلوب |
| API5 Broken function-level authorization | CanManage*، capabilities وactions صريحة | PASS محليًا |
| API6 Unrestricted access to sensitive business flows | idempotency، throttles، transaction locks | PARTIAL — concurrency Staging مطلوب |
| API7 SSRF | لا يوجد proxy عام للتكاملات ضمن المسار المثبت | REVIEW — DAST عند إضافة تكامل خارجي |
| API8 Security misconfiguration | secret env، security headers، read-only filesystem | PARTIAL — Trivy/Compose/runtime مطلوب |
| API9 Improper inventory management | OpenAPI وPostman مولدان ومتحققان | PASS محليًا |
| API10 Unsafe consumption of APIs | timeouts/retries موجودة في المهام؛ التحقق الخارجي مطلوب | PARTIAL |

لا تُغلق حالات PARTIAL أو REVIEW إلا بدليل اختبار محدد وartifact قابل للمراجعة.
