# مصفوفة OWASP ASVS Level 2 — حالة الإصدار

الحالة تعبر عن الدليل المتاح في هذا المستودع، لا عن ضمان مطلق.

| مجال ASVS | الضبط/الدليل | الحالة |
| --- | --- | --- |
| V1 Architecture | API v1، عقود OpenAPI/Postman، فصل live/ready/startup | PARTIAL — يلزم اعتماد Staging |
| V2 Authentication | hashed OTP، throttles، rotation/blacklist، session_version | PASS محليًا |
| V3 Session management | revocation بعد password/role/disable ورفض refresh القديم | PASS محليًا |
| V4 Access control | RBAC/capabilities، checks على الكائن، اختبار read-only router | PASS محليًا؛ IDOR الخارجي BLOCKED |
| V5 Validation | serializers، file validation، pagination/limits | PARTIAL — يحتاج DAST upload/payload |
| V6 Cryptography | Fernet key إلزامي في production، عدم تسجيل الأسرار | PARTIAL — إدارة مفاتيح الإنتاج تحتاج مراجعة تشغيلية |
| V7 Error handling/logging | envelope موحد، JSON logs، redaction | PASS محليًا |
| V8 Data protection | private media endpoints، no-store/nosniff | PASS محليًا؛ اختبار volume/edge BLOCKED |
| V9 Communications | HTTPS/HSTS/proxy settings، WSS design | PARTIAL — TLS/WSS Staging BLOCKED |
| V10 Malicious code | Bandit PASS، pip-audit baseline PASS، Gitleaks/Trivy pending | BLOCKED |
| V11 Business logic | idempotency للطلبات الحساسة، last-admin guard، transaction guards | PASS محليًا؛ concurrency Staging BLOCKED |
| V12 Files/resources | private storage، signature/size validation، protected FileResponse | PARTIAL — DAST polyglot/malware fixture BLOCKED |
| V13 API/Web service | resolver/OpenAPI/Postman validation، 405 read-only test | PASS محليًا |
| V14 Configuration | non-root/read-only compose/health/release definitions | PARTIAL — runtime container evidence BLOCKED |

لا توجد عناصر ASVS متبقية يمكن وسمها PASS تشغيليًا من دون Staging وCI artifacts.
