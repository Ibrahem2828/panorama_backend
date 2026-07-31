# الأمن والتشغيل الإنتاجي

## الأسرار

- يمنع رفع `.env` أو App Password أو Fernet Key إلى Git.
- `FIELD_ENCRYPTION_KEY` يجب أن يكون Fernet key ثابتًا مع Backup آمن؛ تغييره دون خطة تدوير يمنع فك روابط القنوات القديمة.
- SMTP App Password وJWT secret تحفظ في Secrets Manager الخاص بالمنصة. التخزين
  المحلي الحالي لا يحتاج مفاتيح لمزود تخزين خارجي.

## خدمات الإنتاج

1. Web/ASGI: Daphne.
2. PostgreSQL مع TLS ونسخ احتياطية وPITR إن توفر.
3. Redis منفصل للقنوات والكاش وCelery.
4. Celery Worker للإشعارات والمهام الخلفية.
5. Named Volume خاص للملفات المحلية، مع نسخ احتياطي خارج الخادم.
6. Reverse proxy مع HTTPS وWebSocket upgrade.
7. Error monitoring وMetrics وCentralized logs.

## أوامر النشر

Release job واحد فقط:

```bash
/app/docker/release.sh
```

Runtime:

```bash
daphne -b 0.0.0.0 -p 8000 config.asgi:application
```

Worker:

```bash
celery -A config worker --loglevel=INFO --concurrency=2
```

Retention job يومي:

```bash
python manage.py purge_expired_sensitive_data
```

اختبار قبل الحذف:

```bash
python manage.py purge_expired_sensitive_data --dry-run
```

## حماية الملفات

- Volume خاص لا ينشر عبر HTTP أو Reverse Proxy.
- لا تستخدم `/media/` في الإنتاج.
- التحقق من النوع والحجم والتوقيع قبل الحفظ.
- روابط عرض مؤقتة مع `no-store` و`nosniff` وCSP sandbox.
- تشغيل Malware scanner في بيئة Staging/Production قبل نشر المرفق للمستخدمين.
- يوصى باستخدام ClamAV أو خدمة فحص ملفات خارجية داخل Queue مع حالة `quarantined` قبل الإتاحة.

## WebSocket

- المصادقة عبر Authorization header، مع query token كمسار توافق مؤقت فقط.
- منع Binary payloads.
- حد 16KB للحدث و4000 حرف للرسالة.
- Rate Limit للرسائل وTyping.
- لا ترسل Stack traces أو نصوص Exception إلى العميل.
- مرفقات الشات تحصل على Protected Ticket.

## نسخ احتياطية واستعادة

- Backup مشفر يومي لقاعدة البيانات.
- manifest وchecksums وretention للنسخ الاحتياطية الخاصة بالـVolume.
- تجربة Restore شهرية موثقة.
- RPO وRTO معتمدان قبل الإطلاق.
- تخزين نسخة من Fernet key وSecrets ضمن Disaster Recovery vault.

## بوابة الإطلاق

- لا ثغرات P0/P1 مفتوحة.
- لا endpoints غير موثقة في OpenAPI.
- لا وصول Cross-user في اختبارات IDOR.
- لا سعر يقبل من العميل.
- لا رابط ملف/بطاقة/واتساب خام في الاستجابات.
- لا توكنات أو OTP أو كلمات مرور داخل logs.
- استعادة Backup ناجحة.
