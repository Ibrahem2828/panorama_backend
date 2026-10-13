# دليل المشغّل: استيراد البيانات المرجعية للإنتاج

الأمر `import_reference_data` يُدخل البيانات المرجعية (الجامعات، الكليات، الاختصاصات، المواد، السنوات والفصول الدراسية، أسعار الطباعة، أسعار التجليد، نقاط الاستلام، نسخ الشروط والخصوصية) من ملف JSON واحد. هو **مُدقَّق ومتكرر الأمان (idempotent) ويعمل داخل معاملة واحدة**: أي خطأ يُلغي كل شيء ولا يُكتب شيء. لا يحذف أي سجل أبدًا ولا ينشئ أي مستخدم.

> تحذير مهم: **لا تشغّل `seed_initial_data` في الإنتاج أبدًا.** هذا الأمر مخصص للتجربة المحلية فقط وينشئ حسابات وبيانات تجريبية (مستخدمون، طلبات، تذاكر، غروبات). استخدم `import_reference_data` فقط.

## 1) تجهيز الملف

1. انسخ `docs/reference-data.example.json` إلى ملف جديد (مثلاً `reference-data.prod.json`) واستبدل **كل** القيم التي تبدأ بـ `EXAMPLE` أو `FAKE` بالقيم الحقيقية. القيم في المثال وهمية عمدًا (أسعار `0.00`، تواريخ سنة 2099).
2. الملف بترميز UTF-8، والأقسام كلها اختيارية لكن يجب أن يحتوي قسمًا واحدًا على الأقل، ويجب وجود `"schema_version": 1`. أي حقل غير معروف يُرفض (حماية من الأخطاء الإملائية).

| القسم | المفتاح الطبيعي (للتحديث) | ملاحظات |
|---|---|---|
| `academic_years` | `name` | `order` فريد (1..). هذه مستويات الدراسة (السنة الأولى...)، لا سنوات تقويمية، ولا يوجد مفهوم "سنة حالية" في النماذج |
| `semesters` | `name` | `order` فريد |
| `universities` | `code` (يُحوَّل لأحرف كبيرة) | `name` فريد |
| `universities[].faculties` | `code` ضمن الجامعة | **الرمز يجب أن يطابق أول رقم من الرقم الجامعي (1 إلى 7)**: 1 طب بشري، 2 طب أسنان، 3 صيدلة، 4 هندسة معلوماتية، 5 هندسة بترول، 6 إدارة أعمال، 7 هندسة تكنولوجيا البناء والتشييد. هذا ما يربط الطالب بكليته تلقائيًا |
| `...faculties[].majors` | `code` أو `name` إن لم يوجد رمز | |
| `...majors[].subjects` | `(major, academic_year, semester, name)` | `academic_year` و`semester` يُكتبان **بالاسم** ويجب أن يكونا في الملف أو موجودين مسبقًا في القاعدة |
| `print_pricing_rules` | `name` | `price_per_sheet` و`setup_fee` ≥ 0 بخانتين عشريتين كحد أقصى؛ `effective_from` إلزامي و`effective_to` بعده؛ لا يُسمح بقاعدتين فعّالتين متداخلتين لنفس (color_mode, paper_size, sides) |
| `binding_prices` | `binding` (`none/staple/spiral/thermal`) | `price_per_copy` ≥ 0 |
| `pickup_locations` | `name` | |
| `terms_versions` / `privacy_versions` | `version` | حقول: `content_url` (رابط https إلزامي)، `published_at`، `is_active`، `requires_reacceptance` (true يُجبر المستخدمين على إعادة القبول). النموذج يحفظ رابط المحتوى فقط ولا يحفظ نصًا. "الحالية" = أحدث `published_at` بين النسخ الفعّالة |

التواريخ بصيغة ISO 8601 مثل `2026-09-01T00:00:00Z` أو `2026-09-01`.

## 2) التجربة الجافة (Dry-run) ثم الاستيراد الفعلي

على الخادم، انسخ الملف إلى الحاوية (يجب أن تكون الحاوية هي نفس صورة الإنتاج):

```sh
docker cp reference-data.prod.json panorama-web-1:/tmp/reference-data.prod.json

# 1) تجربة جافة: تتحقق من الملف وتطبع ما سيُنشأ/يُحدَّث/لا يتغير ثم تتراجع عن كل شيء
docker exec -i panorama-web-1 python manage.py import_reference_data /tmp/reference-data.prod.json --dry-run

# 2) الاستيراد الفعلي بعد مراجعة النتيجة
docker exec -i panorama-web-1 python manage.py import_reference_data /tmp/reference-data.prod.json

# 3) تنظيف
docker exec -i panorama-web-1 rm /tmp/reference-data.prod.json
```

الناتج جدول بعدد `created` و`updated` و`unchanged` لكل نوع. إن وُجدت أخطاء تُطبع بمسار JSON (مثل `universities[0].faculties[2].code: ...` أو `print_pricing_rules[1].price_per_sheet: must be >= 0`) ويخرج الأمر بكود خطأ **دون أي تغيير في القاعدة**. صحّح الملف وأعد المحاولة. أي سطر `WARNING` تنبيه لا يمنع الاستيراد (مثلاً استخدام رمز كلية نفسه في جامعتين).

## 3) التحقق بعد الاستيراد (نقاط عامة)

```sh
curl -s https://<api-host>/api/v1/universities/
curl -s https://<api-host>/api/v1/academic-years/
curl -s https://<api-host>/api/v1/semesters/
curl -s https://<api-host>/api/v1/policies/current/
```

تحقق أن الجامعات والكليات (عبر `/universities/<id>/faculties/`) والسنوات والفصول ونسخ الشروط والخصوصية الحالية تظهر كما هو متوقع. أسعار الطباعة ونقاط الاستلام يراها موظف الطباعة من لوحة التحكم.

## 4) التصحيح والتراجع

الأمر **لا يحذف أبدًا**؛ فلا يوجد "تراجع" بحذف السجلات. لتصحيح خطأ: عدّل الملف وأعد تشغيل الاستيراد، فيُحدَّث ما تغيّر فقط (المفتاح الطبيعي ثابت). لإخفاء عنصر اجعل `"is_active": false` وأعد الاستيراد. ملاحظات:

- العناصر غير المذكورة في الملف تبقى كما هي.
- تغيير **المفتاح الطبيعي** (اسم السنة/الفصل، رمز الجامعة أو الكلية، اسم قاعدة السعر...) يُنشئ سجلاً جديدًا ولا يعيد تسمية القديم؛ أعد التسمية من لوحة التحكم أو أوقف القديم بـ `is_active: false`.
- إعادة استيراد الملف نفسه لا تغيّر شيئًا (تُطبع `No changes`).
- لا يُنشئ الأمر أي مستخدم؛ حسابات الموظفين تُنشأ عبر `setup_admin_accounts` / `create-admin-accounts.sh`.
