import http from 'k6/http';
import ws from 'k6/ws';
import { check, group, sleep } from 'k6';

const baseUrl = __ENV.BASE_URL;
const email = __ENV.K6_EMAIL;
const password = __ENV.K6_PASSWORD;
const groupId = __ENV.K6_GROUP_ID;
const fileId = __ENV.K6_FILE_ID;
const printQuote = __ENV.K6_PRINT_QUOTE_JSON;

export const options = {
  scenarios: {
    api_journey: { executor: 'ramping-vus', startVUs: 1, stages: [{ duration: '2m', target: 10 }, { duration: '5m', target: 10 }, { duration: '1m', target: 0 }] },
  },
  thresholds: {
    http_req_failed: ['rate<0.005'],
    http_req_duration: ['p(95)<800'],
    ws_session_duration: ['p(95)<3000'],
  },
};

function requireEnv(value, name) {
  if (!value) throw new Error(`${name} is required; use a synthetic Staging account only`);
}

function login() {
  requireEnv(baseUrl, 'BASE_URL');
  requireEnv(email, 'K6_EMAIL');
  requireEnv(password, 'K6_PASSWORD');
  const response = http.post(`${baseUrl}/api/v1/auth/login/`, JSON.stringify({ email, password }), { headers: { 'Content-Type': 'application/json' }, tags: { name: 'login' } });
  check(response, { 'login succeeds': (r) => r.status === 200 });
  return response.json('data.access') || response.json('access');
}

function api(token, method, path, body, name) {
  const response = http.request(method, `${baseUrl}${path}`, body && JSON.stringify(body), { headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, tags: { name } });
  check(response, { [`${name} has no 5xx`]: (r) => r.status < 500 });
  return response;
}

export default function () {
  const token = login();
  group('home data', () => api(token, 'GET', '/api/v1/dashboard/stats/', null, 'home_data'));
  group('feedback submit and analytics', () => {
    api(token, 'POST', '/api/v1/feedback/', { kind: 'rating', metric_type: 'stars', metric_value: 5, rating: 5, context: 'app', title: 'Synthetic k6 feedback', comment: 'Synthetic load-test record' }, 'feedback_submit');
    api(token, 'GET', '/api/v1/dashboard/feedback-analytics/', null, 'feedback_analytics');
  });
  group('support', () => api(token, 'GET', '/api/v1/support/tickets/my/', null, 'support_list'));
  if (fileId) group('file ticket', () => api(token, 'POST', `/api/v1/files/${fileId}/access-ticket/`, {}, 'file_ticket'));
  if (printQuote) group('printing quote', () => api(token, 'POST', '/api/v1/printing/quote/', JSON.parse(printQuote), 'printing_quote'));
  if (groupId) {
    group('chat REST', () => api(token, 'GET', `/api/v1/groups/${groupId}/messages/`, null, 'chat_rest'));
    group('chat WebSocket', () => {
      const wsUrl = baseUrl.replace(/^http/, 'ws') + `/ws/v1/groups/${groupId}/chat/?token=${encodeURIComponent(token)}`;
      const result = ws.connect(wsUrl, { tags: { name: 'chat_websocket' } }, (socket) => {
        socket.on('open', () => socket.close());
        socket.setTimeout(() => socket.close(), 2000);
      });
      check(result, { 'websocket connected': (r) => r && r.status === 101 });
    });
  }
  sleep(1);
}
