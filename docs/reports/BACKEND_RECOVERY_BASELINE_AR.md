# خط أساس استعادة Panorama Backend

تاريخ الالتقاط: 2026-08-09

## حالة المستودع

- Git SHA: `e5bab799bbd827c8c713d34b87ee4afc048c36aa`
- الفرع الحالي: `backend/final-production-closure`
- كانت `git status --short` و `git diff --stat` فارغتين قبل إنشاء ملفات التقرير.
- حُفظت اللقطة في `PRE_BACKEND_RECOVERY_WORKTREE.patch` و`PRE_BACKEND_RECOVERY_UNTRACKED.txt` قبل أي تعديل للكود.
- لم يمكن إنشاء `fix/backend-production-recovery`: بيئة التنفيذ تمنع الكتابة إلى `.git/refs` (`cannot lock ref`). لا يعني ذلك أن اسم الفرع مأخوذ أو أن التبديل تم.

## بيئة وقت التشغيل وإعداداتها (من دون أسرار)

- Python: 3.12.2
- Django: 5.2.16
- Django REST Framework: 3.17.1
- PostgreSQL في الإنتاج: `DATABASE_URL` إلزامي، ومحرك PostgreSQL، و`DATABASE_SSL_REQUIRE=True` افتراضيًا.
- Redis في الإنتاج: `REDIS_URL` إلزامي؛ يستخدمه Django RedisCache وCelery broker/result backend وChannels layer. مهلات اتصال/cache الافتراضية ثلاث ثوانٍ.
- التخزين: local private filesystem (`PrivateFileSystemStorage`) على volume Coolify المسمى `panorama_media` عند `/app/app/media`؛ لا يوجد adapter S3 مفعل في هذا الإصدار.
- Celery: Redis broker/result backend؛ وتحويل المحاضرات على queue `conversion` في عامل منفصل.
- الإنتاج: `DEBUG=False` مفروض في compose، مع `ALLOWED_HOSTS` و`CSRF_TRUSTED_ORIGINS` و`CORS_ALLOWED_ORIGINS` إلزامية.

## حالة المخطط والـAPI والاختبارات

- عدد ملفات migrations المصدرية: 37.
- `makemigrations --check --dry-run --settings=config.settings.testing`: ناجح، بلا تغييرات.
- حالة migrations الفعلية في PostgreSQL الإنتاجية: غير متاحة من هذا السياق؛ لا توجد بيانات اعتماد أو console Coolify محلية، ولم يُشغّل أي migration.
- OpenAPI (إعدادات الاختبار، generated + validate + fail-on-warn): OpenAPI 3.0.3، 183 paths، 271 operations، 221 schemas.
- SHA-256 للـOpenAPI baseline: `CB3276D13BF1DC3178A06C9AD9C5002FC41B18E8995C50C6BBEAC3910DF33BAF`.
- عدد الاختبارات المكتشفة: 124.
- `pytest -q`: ناجح، 124/124 (32.6 ثانية).

## بوابات الجودة قبل الإصلاح

| البوابة | النتيجة |
| --- | --- |
| `manage.py check --settings=config.settings.testing` | ناجح |
| `ruff check app` | ناجح |
| `ruff format --check app` | ناجح (191 ملفًا) |
| `mypy app` | فاشل: 65 خطأ في 37 ملفًا |
| `bandit -r app ...` | 0 Medium، 0 High، و330 Low (يتضمن إشارات اختبار ورسائل نصية؛ يلزم ضبط نطاق التقرير لاحقًا) |
| `pip-audit -r requirements.lock` | فاشل: 5 ثغرات معروفة في حزمتين: `cryptography 48.0.1` (3) و`pypdf 6.14.2` (2) |

## دليل الإنتاج المتاح للقراءة فقط

في 2026-08-09، ومن دون أي طلبات Login أو mutations:

- `GET /api/v1/health/live/` أعاد 200 و`application/json`.
- `GET /api/v1/health/ready/` أعاد 503 و`application/json`.
- `GET /api/v1/health/startup/` أعاد 503 و`application/json`.
- `GET /api/v1/health/db/` أعاد 200 وصرح بأن قاعدة البيانات سليمة؛ ولذلك PostgreSQL ليست سبب 503 المرصود وقت القياس.

لا يحتوي workspace على request id `7bf354f2-e63f-49be-acbd-331f63b103d0`، ولا توجد حاوية Docker محلية قيد التشغيل أو وصلة Coolify للـlogs. لم يُنسب Login 500 إلى exception محدد بلا traceback، ولم يُنفذ invalid-login على الإنتاج لأنه يغيّر عدّاد rate limit/التدقيق ولا يوجد حساب smoke مخوّل في السياق.
