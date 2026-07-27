# نظام التقييم والاقتراحات — عقد التكامل

## الهدف
تحويل كل رحلة مهمة إلى مصدر قياس قابل لاتخاذ القرار دون إزعاج المستخدم أو جمع بيانات حساسة.

## Action Keys المرجعية

| السياق | Action Key | وقت الطلب |
|---|---|---|
| التسجيل | `registration.completed` | بعد إثبات الهوية وإنهاء التسجيل |
| التوثيق | `verification.submitted` | بعد إرسال الطلب بنجاح |
| التوثيق | `verification.reviewed` | بعد رؤية قرار الإدارة |
| المادة | `subject.opened` | بعد الوصول لمحتوى المادة |
| الغروب | `group.joined` | بعد قبول/اكتمال الانضمام |
| الشات | `chat.session.completed` | بعد جلسة مفيدة، لا بعد كل رسالة |
| الملفات | `file.viewed` | بعد قراءة فعلية/مدة مشاهدة مناسبة |
| التسعير | `printing.quote.completed` | بعد ظهور السعر |
| الطباعة | `printing.order.created` | بعد إنشاء الطلب |
| الطباعة | `printing.order.delivered` | بعد التسليم |
| الدعم | `support.ticket.resolved` | بعد الحل |
| واتساب | `group.whatsapp.opened` | بعد فتح القناة المساندة |
| البحث | `search.completed` | بعد ظهور النتائج/اختيار نتيجة |
| التطبيق | `app.general` | دوريًا وفق سياسة Cooldown |

## واجهات API

- `GET /api/v1/feedback/prompt/?context=printing&action_key=printing.order.delivered&app_version=2.0.0`
- `POST /api/v1/feedback/prompt-event/`
- `POST /api/v1/feedback/`
- `GET /api/v1/feedback/mine/`
- `GET /api/v1/feedback/suggestions/`
- `POST /api/v1/feedback/{id}/vote/`
- `GET /api/v1/dashboard/feedback/`
- `PATCH /api/v1/dashboard/feedback/{id}/workflow/`
- `GET /api/v1/dashboard/feedback-analytics/`
- `CRUD /api/v1/dashboard/feedback-prompt-policies/`

## Payload التقييم

```json
{
  "kind": "rating",
  "context": "printing",
  "action_key": "printing.order.delivered",
  "object_type": "print_order",
  "object_id": "123",
  "rating": 5,
  "comment": "الخدمة واضحة وسريعة",
  "app_version": "2.0.0",
  "build_number": "200",
  "platform": "android",
  "locale": "ar",
  "device_model": "generic",
  "metadata": {
    "duration_bucket": "1-3m"
  }
}
```

يحظر إرسال كلمات مرور، توكنات، OTP، روابط ملفات، صور بطاقات، أو بيانات سرية داخل `metadata`.

## دورة حياة الاقتراح

`new → reviewing → planned → in_progress → resolved`

ويمكن الانتقال إلى `rejected` أو `duplicate` مع رسالة قرار موجهة للمستخدم. التحديثات النهائية ترسل إشعارًا إلى صاحب الاقتراح.

## مؤشرات الداشبورد

- المتوسط العام ومتوسط كل سياق.
- توزيع النجوم 1–5.
- نسبة الرضا: التقييمات 4 و5.
- عدد الملاحظات المفتوحة والحرجة.
- أكثر الاقتراحات تصويتًا.
- المقارنة حسب إصدار التطبيق والمنصة.
- Trend أسبوعي/شهري يضاف في طبقة التقارير لاحقًا.
