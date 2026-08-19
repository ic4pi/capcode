# Emergent Auth Testing Playbook (for CapCode)

## Auth-Gated App Testing Playbook

### Step 1: Create Test User & Session

```bash
mongosh --eval "
use('test_database');
var userId = 'test-user-' + Date.now();
var sessionToken = 'test_session_' + Date.now();
db.users.insertOne({
  user_id: userId,
  email: 'test.user.' + Date.now() + '@example.com',
  name: 'Test User',
  picture: 'https://via.placeholder.com/150',
  created_at: new Date()
});
db.user_sessions.insertOne({
  user_id: userId,
  session_token: sessionToken,
  expires_at: new Date(Date.now() + 7*24*60*60*1000),
  created_at: new Date()
});
print('Session token: ' + sessionToken);
print('User ID: ' + userId);
"
```

### Step 2: Test Backend API

```bash
# Test auth endpoint (via cookie or Bearer)
curl -X GET "https://your-app.com/api/auth/me" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"

# Or via cookie
curl -X GET "https://your-app.com/api/auth/me" \
  --cookie "session_token=YOUR_SESSION_TOKEN"
```

### Step 3: Browser Testing

```javascript
await page.context.add_cookies([{
    "name": "session_token",
    "value": "YOUR_SESSION_TOKEN",
    "domain": "your-app.com",
    "path": "/",
    "httpOnly": true,
    "secure": true,
    "sameSite": "None"
}]);
await page.goto("https://your-app.com");
```

### Debug

```bash
mongosh --eval "
use('test_database');
db.users.find().limit(2).pretty();
db.user_sessions.find().limit(2).pretty();
"

# Clean test data
mongosh --eval "
use('test_database');
db.users.deleteMany({email: /test\.user\./});
db.user_sessions.deleteMany({session_token: /test_session/});
"
```

### Checklist
- [ ] User doc has `user_id` field (custom UUID; Mongo's `_id` is separate/internal)
- [ ] Session `user_id` matches user's `user_id` exactly
- [ ] All queries use `{"_id": 0}` projection
- [ ] Backend queries use `user_id` (not `_id` or `id`)
- [ ] `/api/auth/me` returns user data (not 401/404)
- [ ] Browser loads dashboard (not login page)
- [ ] Callback detection uses `useLocation().hash`, not `window.location.hash`

### Success indicators
- ✅ `/api/auth/me` returns user data
- ✅ Dashboard loads without redirect
- ✅ CRUD operations work

### Failure indicators
- ❌ "User not found" errors
- ❌ 401 Unauthorized responses
- ❌ Redirect to login page

## CapCode-specific notes
- Auth is **optional** for now. Anonymous users still work (client-generated `session_id` in localStorage).
- Signed-in users get a persistent identity that can eventually own chains + BYOK keys across devices.
- No existing route is auth-gated in this MVP; auth just adds a `user_id` alongside the anonymous `session_id`.
