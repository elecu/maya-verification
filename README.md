# Maya Verification — Admin Manual

**IMPORTANT:**  
In this document we use the placeholder:

```
YOUR_ADMIN_KEY_HERE
```

Replace it with your real admin key stored in Render as:

```
ADMIN_API_KEY
```

---

# 1. Service Overview

Render web service base URL:

```
https://maya-verification.onrender.com
```

### Main endpoints

```
GET  /health         → health check
POST /issue          → create a new 1-year license
POST /renew          → renew a license (extend 1 year + reset devices)
POST /reset_devices  → clear all registered devices for a license
POST /check          → called automatically by the māyā launcher
POST /update_email   → update the email associated with a license
```

All admin endpoints require a valid `admin_key` that must match the `ADMIN_API_KEY` configured in Render.

---

# 2. Quick Health Check

Verify the service is reachable:

```bash
curl https://maya-verification.onrender.com/health
```

Expected response:

```json
{"ok": true, "ts": 1234567890}
```

If this works, FastAPI is running correctly.

---

# 3. License Management (Admin API)

## 3.1 Create a new license (1 year)

```bash
curl -X POST "https://maya-verification.onrender.com/issue" \
  -H "Content-Type: application/json" \
  -d '{
        "admin_key": "YOUR_ADMIN_KEY_HERE",
        "email": "user@example.com"
      }'
```

Response example:

```json
{
  "success": true,
  "code": "ABCD-EFGH-IJKL",
  "expires": "2026-02-10 14:32:00"
}
```

---

## 3.2 Renew an existing license

Extends expiration by 1 year **and resets registered devices**.

```bash
curl -X POST "https://maya-verification.onrender.com/renew" \
  -H "Content-Type: application/json" \
  -d '{
        "admin_key": "YOUR_ADMIN_KEY_HERE",
        "code": "ABCD-EFGH-IJKL"
      }'
```

---

## 3.3 Reset devices (clear machine IDs)

Use this when a user changes computer or reinstalls their OS.

```bash
curl -X POST "https://maya-verification.onrender.com/reset_devices" \
  -H "Content-Type: application/json" \
  -d '{
        "admin_key": "YOUR_ADMIN_KEY_HERE",
        "code": "ABCD-EFGH-IJKL"
      }'
```

Response:

```json
{"success": true, "cleared": 2}
```

---

## 3.4 Update the email linked to a license

```bash
curl -X POST "https://maya-verification.onrender.com/update_email" \
  -H "Content-Type: application/json" \
  -d '{
        "admin_key": "YOUR_ADMIN_KEY_HERE",
        "code": "ABCD-EFGH-IJKL",
        "new_email": "newuser@example.com"
      }'
```

---

# 4. View License Status (Database)

## 4.1 Connect to PostgreSQL from Linux terminal

Use the **External Database URL** from Render → Database → *Connection Info*.

Example:

```bash
psql "postgres://USERNAME:PASSWORD@HOST:PORT/DATABASE"
```

You will now be inside the Postgres prompt:

```
maya-licenses-db=#
```

---

## 4.2 Show all licenses

```sql
SELECT * FROM licenses;
```

---

## 4.3 Check one specific license

```sql
SELECT * FROM licenses WHERE code = 'ABCD-EFGH-IJKL';
```

---

## 4.4 Show registered machine IDs for a license

```sql
SELECT code, devices FROM licenses WHERE code = 'ABCD-EFGH-IJKL';
```

---

## 4.5 Clear devices manually (SQL method)

Equivalent to `/reset_devices` but done directly in DB:

```sql
UPDATE licenses
SET devices = '[]'
WHERE code = 'ABCD-EFGH-IJKL';
```

---

## 4.6 Change email manually (SQL method)

Equivalent to `/update_email`.

```sql
UPDATE licenses
SET email = 'new_email@example.com'
WHERE code = 'ABCD-EFGH-IJKL';
```

---

# 5. Backups & Safety

### Recommended backup commands from psql:

Export entire table:

```bash
pg_dump "postgres://USERNAME:PASSWORD@HOST:PORT/DATABASE" > maya_backup.sql
```

Restore backup:

```bash
psql "postgres://USERNAME:PASSWORD@HOST:PORT/DATABASE" < maya_backup.sql
```

---

# 6. Notes on Security

- Keep `ADMIN_API_KEY` **secret**.  
- Never publish it in repos, screenshots, or scripts.  
- All admin actions require this key.  
- Users only interact through the māyā launcher (`/check` endpoint).  
- Licenses automatically expire after exactly 1 year.  
- A warning is shown inside the launcher if the license expires in < 7 days.

---

# 7. Device Handling Rules

- Each license supports **up to 2 devices**.  
- First two devices are auto-registered.  
- A third device will cause the launcher to show:

```
No permission: DEVICE_LIMIT_REACHED
```

- To move the license to new hardware:
  - Admin runs `/reset_devices` OR
  - Admin uses SQL to clear devices.

Renewing a license **automatically resets devices** so the user can start fresh.

---

# 8. Admin Checklist

### To issue a new license
```
POST /issue
```

### To renew a license
```
POST /renew
```

### To clear devices
```
POST /reset_devices
```

### To change the registered email
```
POST /update_email
```

### To inspect or modify the database
```
psql "postgres://..."
```

---

# End of README.md
