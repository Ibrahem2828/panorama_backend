# تخزين ملفات Panorama المحلي الدائم في Coolify

## القرار الحالي

يستخدم هذا الإصدار `STORAGE_BACKEND=local` فقط. الملفات الخاصة تبقى على Volume
دائم ولا تُنشر على مسار HTTP عام. هذا مناسب لخادم واحد وحمل محدود، وليس بديلاً
عن النسخ الاحتياطي أو عن التخزين الكائني عند التوسع الأفقي.

## الإعداد الدقيق في Coolify

من صفحة التطبيق اتبع:

```text
Application → Storages → Add Persistent Storage → Volume
```

القيم المطلوبة:

```text
Name: panorama_media
Destination Path: /app/app/media
```

أضف متغيرات **Runtime only** التالية، ولا تضعها ضمن Build Variables:

```dotenv
STORAGE_BACKEND=local
MEDIA_ROOT=/app/app/media
MEDIA_URL=/media/
```

لا يلزم في هذا الوضع أي مفتاح لمزود تخزين كائني أو Bucket أو S3 endpoint. لا
تنشئ متغيرات وهمية لإرضاء التطبيق.

## الصلاحيات والتحقق

صورة التطبيق تعمل بالمستخدم `panorama` ذي UID/GID `10001`. بعد تركيب الـVolume
افتح Terminal التطبيق ونفّذ:

```sh
id
ls -ld /app/app/media
touch /app/app/media/.write-test
rm /app/app/media/.write-test
python manage.py storage_status
python manage.py storage_status --write-test
```

يجب أن ينجح `touch` للمستخدم `panorama` وأن ينتهي `storage_status --write-test`
بـexit code صفر من دون ترك ملف ضمن `healthchecks/`. إذا فشل ذلك، أوقف قبول
الترافيك، وصحّح ملكية الـVolume أو مسار تركيبه؛ لا تشغّل الحاوية كمستخدم root.

## حماية الوصول

- لا تضف Route أو proxy rule للمسار `/media/`.
- لا تستخدم `django.conf.urls.static.static()` في إعدادات الإنتاج.
- تمر الملفات الخاصة فقط عبر API محمي: مصادقة المستخدم، ملكية التذكرة، ثم RBAC
  أو صلاحية المورد. الاستجابة تضيف `Cache-Control: private, no-store` و
  `X-Content-Type-Options: nosniff`.
- لا تنسخ Volume إلى Docker image، ولا تحذف الـVolume في إعادة النشر.

## الخدمات التي تحتاج الـVolume

يربط `docker-compose.coolify.yml` الـVolume نفسه مع `web` و`worker` و`beat`؛
وبالتالي تستطيع المهام الخلفية قراءة الملفات الخاصة عند الحاجة. يبقى filesystem
الأساسي للحاويات read-only، والمسار القابل للكتابة محصورًا في الـVolume و`/tmp`
المؤقت.
