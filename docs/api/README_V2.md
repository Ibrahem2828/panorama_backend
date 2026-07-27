# Panorama API v2 — مصدر الحقيقة

ملفا `mobile_api_collection.json` و`dashboard_api_collection.json` موروثان من MVP v1 وقد لا يحتويان كل مسارات v2 الجديدة. لا يُسمح باعتبارهما عقدًا نهائيًا.

المصدر المرجعي لعقود v2 هو مخطط OpenAPI الذي يولده الباك بعد تشغيل الاعتماديات:

```bash
python app/manage.py spectacular \
  --settings=config.settings.testing \
  --validate --fail-on-warn \
  --file docs/api/panorama-v2-openapi.yaml
```

بعد نجاح التحقق، تُولد منه Clients الموبايل والداشبورد وتُحدّث Collections. المسارات الجديدة تشمل التقييم والاقتراحات، تذاكر الملفات والبطاقات والمرفقات، فتح قناة واتساب، Print Quote، وإدارة الصلاحيات.
