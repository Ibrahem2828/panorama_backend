# بدائل تقنية التخزين لـPanorama

تاريخ التحقق: **2026-07-31**. الأسعار أدناه مرجعية فقط وتتغير بحسب المنطقة
والضرائب وحجم الطلبات والخروج؛ لا تعتمد قرار شراء قبل مراجعة الرابط الرسمي
المذكور لكل مزود. لا يربط تصميم Panorama الحالي نفسه بأي مزود.

## معيار المقارنة

تستمر واجهات API الحالية في تنزيل الملفات من endpoints محمية، لا من روابط عامة.
عند الانتقال إلى تخزين كائني، يمكن استخدام روابط موقعة داخلية قصيرة العمر أو
الاستمرار في streaming عبر Django بعد التحقق من الصلاحية. لا يجوز جعل Bucket
عامًا بسبب اختيار أي تقنية في الجدول.

| الخيار | النوع والإدارة | S3 / Django | تشغيله مع Coolify | الخصوصية والنسخ | الكلفة المرجعية | الملاءمة |
| --- | --- | --- | --- | --- | --- | --- |
| Local filesystem + Named Volume | filesystem على الخادم؛ الفريق يديره | لا يحتاج S3؛ `FileSystemStorage` الحالي | الأبسط؛ Volume واحد مشترك للخدمات | private عبر API، تشفير القرص وسياسة backup مسؤولية المشغل؛ versioning يدوي بالنسخ | لا توجد فاتورة تخزين مستقلة؛ قرص الخادم والنسخ الاحتياطي فقط | **الخيار الحالي** لخادم واحد وحمل محدود وميزانية محدودة |
| MinIO | Object Storage مستضاف ذاتيًا | S3-compatible؛ يعمل مع adapter S3 عام | ممكن كخدمة منفصلة، لكن يحتاج disks/TLS/KMS/monitoring | IAM، presigned URLs، versioning، lifecycle وSSE متاحة؛ النسخ مسؤولية الفريق | برنامج مفتوح المصدر؛ تكلفة الخادم والأقراص والنسخ تحتاج تسعير المضيف | جيد لاحقًا عند الحاجة إلى API S3 مع قبول عبء التشغيل |
| AWS S3 | Managed object storage | المرجع الأساسي لـS3؛ adapter S3 عام مناسب | سهل بالـcredentials والـendpoint، خارج Coolify | IAM، presigned URLs، encryption، versioning وlifecycle؛ vendor lock-in متوسط | usage-based حسب المنطقة: storage + requests + retrieval/egress؛ لا يوجد رقم عالمي صادق | قوي جدًا لكن قد يزيد تعقيد وفاتورة فريق طلابي صغير |
| Backblaze B2 | Managed object storage | يقدم S3-compatible API؛ adapter S3 عام مناسب | سهل بعد إعداد private bucket وcredentials محدودة | private buckets، encryption، Object Lock/versioning وخيارات signed access | usage-based؛ صفحة B2 الرسمية توضح آلية egress المجاني المحدود ثم المحاسبة؛ راجع السعر وقت الشراء | مرشح اقتصادي لاحقًا إذا كان الاعتماد الخارجي مقبولًا |
| Wasabi | Managed object storage | S3/IAM bit-compatible بحسب الوثائق | سهل تقنيًا؛ endpoint وcredentials فقط | private buckets وTLS وversioning/immutability بحسب الخطة؛ vendor lock-in متوسط | السعر المنشور $7.99/TB/شهر من 2026-07-01، مع سياسة egress/API منشورة؛ راجع الحد الأدنى وretention | مرشح جيد لميزانية متوقعة، بعد مراجعة شروط الاحتفاظ |
| DigitalOcean Spaces | Managed object storage | S3-compatible | سهل مع endpoint وcredentials | keys، private objects، presigned URLs وCDN اختياري؛ versioning يحتاج تحققًا حسب المنطقة/الخطة | الاشتراك الأساسي المنشور $5/شهر؛ bandwidth له تسعير منفصل | بسيط لفرق صغيرة لكن يعتمد على مزود واحد |
| Google Cloud Storage | Managed object storage | ليس S3 الأصلي؛ يستخدم adapter GCS أو interoperability محدودة بعد اختبار | ممكن لكن يتطلب حساب خدمة وadapter مناسب | IAM، signed URLs، encryption، versioning/lifecycle | usage-based حسب region/class/operations/egress؛ يلزم calculator رسمي | جيد عند وجود مشروع GCP، لكنه ليس أقصر انتقال S3 |
| Azure Blob Storage | Managed object storage | ليس S3 الأصلي؛ يستخدم adapter Azure وSAS URLs | ممكن لكن يتطلب Azure identity/adapter | RBAC، SAS، encryption، versioning/lifecycle | usage-based حسب region/tier/operations/retrieval؛ راجع calculator الرسمي | مناسب لمن يملك Azure، لكن يزيد اختلاف API عن S3 |
| Ceph RGW | Object Storage مستضاف ذاتيًا | يقدم S3-compatible API | ممكن تقنيًا، لكن لا يوصى به كتطبيق منفرد صغير | سياسات، TLS، versioning حسب الضبط؛ النسخ والإصلاح والمراقبة مسؤولية الفريق | لا رسوم برنامج لازمة عادةً؛ البنية والخبرة التشغيلية هما التكلفة | مناسب لفريق بنية تحتية ناضج، وليس للمرحلة الحالية |

## الخصوصية والتشفير وقابلية التوسع

- كل الخيارات المدارة المذكورة تدعم التشفير أثناء النقل عبر TLS؛ التشفير في
  السكون والإصدارات والاحتفاظ يجب تفعيله ومراجعته لكل Bucket، ولا يكفي الافتراض.
- MinIO وCeph يمنحان تحكمًا أعلى وvendor lock-in أقل، لكنهما ينقلان مسؤولية
  patching، capacity، disaster recovery، KMS والتنبيهات إلى الفريق.
- Local Volume هو الأقل تعقيدًا الآن لكنه لا يدعم scale-out بين عدة عقد؛ أي
  توسع أفقي حقيقي يجب أن يسبقه انتقال مخطط إلى generic S3-compatible storage.
- `django-storages` ليس مثبتًا في صورة الإصدار المحلي كي لا يحمل runtime
  dependencies غير مستخدمة. يعاد إدخاله أو اختيار adapter بديل فقط في release
  منفصل مع اختبارات القراءة والكتابة والترحيل.

## المصادر الرسمية

- [MinIO: S3 compatibility](https://min.io/product/s3-compatibility)،
  [versioning](https://min.io/docs/minio/kubernetes/upstream/administration/object-management/object-versioning.html)،
  و[server-side encryption](https://min.io/docs/minio/linux/administration/server-side-encryption.html).
- [AWS S3 pricing](https://aws.amazon.com/s3/pricing/) و
  [presigned URLs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html).
- [Backblaze B2 S3 integration](https://www.backblaze.com/docs/en/cloud-storage-get-started-with-a-backblaze-integration)
  و[transaction/egress pricing](https://www.backblaze.com/cloud-storage/transaction-pricing).
- [Wasabi S3 API](https://docs.wasabi.com/apidocs/wasabi-api)،
  [Signature V4](https://docs.wasabi.com/apidocs/rest-api-introduction)، و
  [pricing](https://wasabi.com/pricing).
- [DigitalOcean Spaces pricing](https://docs.digitalocean.com/products/spaces/details/pricing/).
- [Google Cloud Storage pricing](https://cloud.google.com/storage/pricing) و
  [signed URLs](https://cloud.google.com/storage/docs/access-control/signed-urls).
- [Azure Blob pricing](https://azure.microsoft.com/pricing/details/storage/blobs/) و
  [SAS](https://learn.microsoft.com/azure/storage/common/storage-sas-overview).
- [Ceph RGW S3 API](https://docs.ceph.com/en/latest/radosgw/s3/).

## التوصية

التوصية الحالية هي **Local filesystem + Coolify Named Volume**: التطبيق يعمل
على خادم واحد، والحمل والميزانية محدودان، والتنفيذ والنسخ واضحان. يجب إدخال
الـVolume ضمن النسخ الاحتياطي مع PostgreSQL، ولا يجب نشره للعامة.

التوصية المستقبلية هي **مزود S3-compatible عام** بعد اختبار adapter وترحيل
متسق ومراجعة تكلفة المنطقة والـegress؛ الاختيار لا يُحجز لاسم مزود بعينه.
