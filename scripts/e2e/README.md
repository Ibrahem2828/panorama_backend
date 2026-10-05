# Full-stack end-to-end run (backend + student web + staff dashboard)

Runs everything locally with no external services: SQLite file, in-memory cache/channels, eager Celery, development OTPs
(`config.settings.e2e`; never used in production).

```bash
# 1. backend (from backend/app)
export DJANGO_SETTINGS_MODULE=config.settings.e2e
python manage.py migrate --noinput
python manage.py seed_initial_data
DEFAULT_PRINT_PRICE_BW_A4_ONE=100 DEFAULT_PRINT_PRICE_BW_A4_DOUBLE=150 DEFAULT_PRINT_PRICE_COLOR_A4_ONE=400 \
DEFAULT_PRINT_PRICE_COLOR_A4_DOUBLE=600 DEFAULT_PICKUP_LOCATION_NAME="E2E Print Shop" python manage.py seed_production_defaults
python manage.py shell -c "exec(open('../scripts/e2e/seed.py').read())"   # accounts: it/admin/print/student/normal @e2e.test
python -m daphne -b 127.0.0.1 -p 8000 config.asgi:application

# 2. student web app (from web/) and dashboard (from dashboard/) - build once, then run the standalone servers
BACKEND_API_BASE_URL=http://127.0.0.1:8000 ALLOW_INSECURE_BACKEND=true SECURE_COOKIES=false \
APP_ORIGIN=http://127.0.0.1:3000 PORT=3000 node .next/standalone/server.js
BACKEND_API_BASE_URL=http://127.0.0.1:8000 ALLOW_INSECURE_BACKEND=true SECURE_COOKIES=false PORT=3100 \
node .next/standalone/server.js

# 3. journeys
E2E_BASE_URL=http://127.0.0.1:3000 node web/tests/e2e/student-journey.mjs   # one student, every feature (20 steps)
node backend/scripts/e2e/cross-app.mjs                                       # student <-> staff across both apps (14 steps)
node backend/scripts/e2e/chat.mjs                                            # realtime chat: two students, tickets, WebSocket (8 steps)
```

All e2e accounts use the password `E2eStrongPass#2026`. In a browser, open the dashboard on `localhost` and the web app on
`127.0.0.1` so their (identically named) cookies do not collide; in production each app has its own host.
