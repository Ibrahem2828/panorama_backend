# دليل النسخ والاستعادة

- RPO/RTO الفعليان غير مثبتين حتى تنفيذ restore على Staging؛ مالك العملية: Platform Operations.
- أوقف الكتابة أو التقط snapshot متناسقًا، ثم خذ PostgreSQL وvolume media في نقطة زمنية واحدة.
- scripts/backup_postgres.sh يتطلب DATABASE_URL وBACKUP_DIR وAGE_RECIPIENT ويخرج archive مشفرًا مع checksum.
- scripts/backup_local_media.sh <volume> <output-dir> [--dry-run] لا يفترض volume ثابتًا ولا يحذف المصدر.
- تحقق بـscripts/verify_local_media_backup.sh، وانسخ archive المشفر إلى موقع منفصل محدود الوصول مع retention موثق.
- الاستعادة محصورة في Staging بواسطة scripts/restore_postgres_staging.sh وتتطلب تأكيدًا صريحًا.
- بعد الاستعادة تحقق من migration state، عدادات الصفوف، checksums media، تنزيل خاص مصرح، authentication، وsmoke.

يمنع استخدام docker system prune --volumes أو حذف volume أثناء نشر.
