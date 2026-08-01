import os
import re
import time
import uuid
import json
import logging
import sqlite3
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import requests
from datetime import datetime

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("buzz-api")

# ==================== CONSTANTS ====================
BASE_URL = "https://profile.swiggy.com/api/v3/app"
SMS_OTP_URL = f"{BASE_URL}/sms_otp"
LOGIN_VERIFY_URL = f"{BASE_URL}/login/verify"
SPNS_BASE = "https://spns.swiggy.com/api/v1/campaign"
REWARDS_URL = f"{SPNS_BASE}/rewards"
ACTION_URL = f"{SPNS_BASE}/action"

TARGET_USERS = [
    "9905454846", "8302374884", "9569907686", "6019557067",
    "8103200020", "9793231470", "6075716540", "6057085260",
    "6529467214", "4742565540", "2159541308", "5711812412",
    "4805096977", "6306972524", "5810763039", "5767374231",
    "5255411320", "9263536039", "9656243680", "7028403798",
    "2022592103", "4339594714", "9315838951", "5021810039",
    "4179533661", "3969482763", "5378219742", "4622672366",
    "6529938009", "8841032307", "7847081991", "8476578440",
    "4958316534", "6089374148", "3974751011", "9076113237",
    "2405719218", "8557791891", "5237191585", "7504061044",
    "7239858845", "5773101973", "9292974443", "8481419410",
    "4219735233", "9104704566", "3923205642", "1106827431",
    "9066285442", "8745675335"
]

# ==================== DATABASE ====================
DATABASE_URL = os.environ.get("DATABASE_URL")
USE_SQLITE = not DATABASE_URL

if USE_SQLITE:
    DB_PATH = os.path.join(os.path.dirname(__file__), "swiggy_buzz.db")
    log.warning("⚠️ Using SQLite (data may not persist)")

def get_db():
    if USE_SQLITE:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    else:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db()
    with conn:
        if USE_SQLITE:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone TEXT UNIQUE,
                    token TEXT,
                    tid TEXT,
                    sid TEXT,
                    device_id TEXT,
                    secrettoken TEXT,
                    customer_id TEXT,
                    total_earned REAL DEFAULT 0,
                    last_collection_date TEXT,
                    streak_days INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    phone TEXT UNIQUE,
                    token TEXT,
                    tid TEXT,
                    sid TEXT,
                    device_id TEXT,
                    secrettoken TEXT,
                    customer_id TEXT,
                    total_earned REAL DEFAULT 0,
                    last_collection_date DATE,
                    streak_days INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
    conn.close()

init_db()

# ==================== HELPERS ====================
def generate_device_id():
    return uuid.uuid4().hex[:16]

def swiggy_headers(tid="", sid="", token="", device_id=""):
    headers = {
        "pl-version": "138",
        "version-code": "1795",
        "app-version": "4.113.0",
        "os-version": "11",
        "latitude": "22.7421633",
        "longitude": "75.907875",
        "current-latitude": "22.7421633",
        "current-longitude": "75.907875",
        "accessibility_enabled": "false",
        "x-network-quality": "GOOD",
        "faw-flags": "1354",
        "accept": "application/json; charset=utf-8",
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        "user-agent": "Swiggy-Android/4.113.0 (Android 11; Pixel 4)",
        "deviceid": device_id or generate_device_id(),
        "swuid": device_id or generate_device_id(),
    }
    if tid: headers["tid"] = tid
    if sid: headers["sid"] = sid
    if token: headers["token"] = token
    return headers

def spns_headers(secrettoken="", tid="", sid=""):
    headers = {
        "client-id": "portal",
        "user-agent": "Mozilla/5.0 (Linux; Android 11; Pixel 4) AppleWebKit/537.36",
        "content-type": "application/json",
        "accept": "*/*",
        "origin": "https://webviews.swiggy.com",
        "x-requested-with": "in.swiggy.android",
        "referer": "https://webviews.swiggy.com/moments-iw/buzz-your-friend/",
    }
    if secrettoken: headers["token"] = secrettoken
    if tid: headers["tid"] = tid
    if sid: headers["sid"] = sid
    return headers

def extract_earned(data):
    try:
        for resp in data.get("data", {}).get("campaignRewardResponses", []):
            for reward in resp.get("rewards", []):
                rolling = reward.get("rollingFreecash", {})
                earned = rolling.get("totalEarned", {})
                return float(earned.get("units", 0))
    except:
        pass
    return 0.0

# ==================== SWIGGY API ====================
def send_otp(phone):
    device_id = generate_device_id()
    url = f"{SMS_OTP_URL}?mobile={phone}"
    resp = requests.get(url, headers=swiggy_headers(device_id=device_id), timeout=30)
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}"}
    data = resp.json()
    if data.get("statusCode") == 0:
        return {"tid": data.get("tid",""), "sid": data.get("sid",""), "deviceId": data.get("deviceId", device_id), "status": "ok"}
    return {"error": data.get("statusMessage", "OTP failed")}

def verify_otp(phone, otp, tid, sid, device_id):
    url = f"{LOGIN_VERIFY_URL}?otp_source=Sms-manual"
    body = {"cloningSignalsData": {"appFilesDirPathInvalid":0, "developerModeEnabled":1, "deviceModelVmos":0, "emulatorStatus":0, "packageName":"in.swiggy.android", "workProfileEnabled":0}, "otp": otp}
    headers = swiggy_headers(tid=tid, sid=sid, device_id=device_id)
    headers["manufacturer"] = "GOOGLE"
    headers["model-name"] = "PIXEL 4"
    resp = requests.post(url, headers=headers, json=body, timeout=30)
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}"}
    data = resp.json()
    if data.get("statusCode") == 0:
        inner = data.get("data", {})
        token = inner.get("token", "")
        customer_id = str(inner.get("customer_id", ""))
        return {
            "status": "ok",
            "token": token,
            "tid": data.get("tid", tid),
            "sid": data.get("sid", sid),
            "device_id": data.get("deviceId", device_id),
            "customer_id": customer_id,
            "name": inner.get("name", ""),
            "secrettoken": token,
        }
    return {"error": data.get("statusMessage", "Verification failed")}

def check_buzz(secrettoken, tid, sid):
    url = REWARDS_URL
    body = {
        "generalContext": {"requestContext": {"clientId": "portal_banner"}},
        "campaignRewardRequests": [{
            "campaignType": "CAMPAIGN_TYPE_BUZZ_MONEY_STREAKS",
            "campaignId": "ougwl",
            "rollingFreecashParams": {"forceRefresh": True, "requestParams": {"dataRequested": "wallet,connections,transactions", "source": "banner"}}
        }]
    }
    resp = requests.post(url, headers=spns_headers(secrettoken, tid, sid), json=body, timeout=30)
    return resp.json() if resp.content else {}

def send_connect(secrettoken, target_id, customer_id, tid, sid):
    url = ACTION_URL
    body = {
        "generalContext": {"requestContext": {"clientId": "portal_banner"}},
        "consumerContext": {"consumerId": customer_id},
        "campaignUserActionRequest": {
            "campaignId": "ougwl",
            "campaignType": "CAMPAIGN_TYPE_BUZZ_MONEY_STREAKS",
            "action": {"actionType": "ACTION_TYPE_CONNECT", "targetEntityId": target_id}
        }
    }
    resp = requests.post(url, headers=spns_headers(secrettoken, tid, sid), json=body, timeout=30)
    return resp.json() if resp.content else {}

def accept_connection(secrettoken, target_id, customer_id, tid, sid):
    url = ACTION_URL
    body = {
        "generalContext": {"requestContext": {"clientId": "portal_banner"}},
        "consumerContext": {"consumerId": customer_id},
        "campaignUserActionRequest": {
            "campaignId": "ougwl",
            "campaignType": "CAMPAIGN_TYPE_BUZZ_MONEY_STREAKS",
            "action": {"actionType": "ACTION_TYPE_ACCEPT", "targetEntityId": target_id}
        }
    }
    resp = requests.post(url, headers=spns_headers(secrettoken, tid, sid), json=body, timeout=30)
    return resp.json() if resp.content else {}

def get_pending_incoming(secrettoken, tid, sid):
    status = check_buzz(secrettoken, tid, sid)
    connections = status.get("data", {}).get("campaignRewardResponses", [{}])[0].get("connections", {}).get("connections", [])
    pending = []
    for conn in connections:
        progress = conn.get("progress", {})
        if progress.get("status") == "PROGRESS_STATUS_IN_PROGRESS_ACCEPT_INVITE_PENDING":
            pending.append(conn.get("connectedUserId"))
    return pending

# ==================== FLASK ENDPOINTS ====================
@app.route('/send-otp', methods=['POST', 'OPTIONS'])
def send_otp_endpoint():
    if request.method == 'OPTIONS': return '', 200
    data = request.get_json()
    phone = data.get('phone', '').strip()
    if not re.match(r'^[6-9]\d{9}$', phone):
        return jsonify({"status": "error", "message": "Invalid phone number"}), 400
    res = send_otp(phone)
    if 'error' in res:
        return jsonify({"status": "error", "message": res['error']}), 400
    return jsonify({"status": "ok", "tid": res['tid'], "sid": res['sid'], "deviceId": res['deviceId']})

@app.route('/json-login', methods=['POST', 'OPTIONS'])
def json_login():
    if request.method == 'OPTIONS': return '', 200
    data = request.get_json()
    secrettoken = data.get('secrettoken', '').strip()
    if not secrettoken:
        return jsonify({"status": "error", "message": "Secrettoken required"}), 400
    
    conn = get_db()
    cur = conn.cursor()
    if USE_SQLITE:
        cur.execute("SELECT phone, tid, sid, customer_id, total_earned, streak_days, last_collection_date FROM users WHERE secrettoken = ?", (secrettoken,))
    else:
        cur.execute("SELECT phone, tid, sid, customer_id, total_earned, streak_days, last_collection_date FROM users WHERE secrettoken = %s", (secrettoken,))
    row = cur.fetchone()
    conn.close()
    
    if not row:
        return jsonify({"status": "error", "message": "Invalid token"}), 404
    
    return jsonify({
        "status": "ok",
        "phone": row['phone'],
        "secrettoken": secrettoken,
        "totalEarned": row['total_earned'] or 0,
        "streak": row['streak_days'] or 0,
        "lastDate": row['last_collection_date'] or "Never"
    })

@app.route('/verify-otp', methods=['POST', 'OPTIONS'])
def verify_otp_endpoint():
    if request.method == 'OPTIONS': return '', 200
    data = request.get_json()
    phone = data.get('phone')
    otp = data.get('otp')
    tid = data.get('tid')
    sid = data.get('sid')
    device_id = data.get('deviceId')
    res = verify_otp(phone, otp, tid, sid, device_id)
    if 'error' in res:
        return jsonify({"status": "error", "message": res['error']}), 400
    
    conn = get_db()
    with conn:
        if USE_SQLITE:
            conn.execute("""
                INSERT OR REPLACE INTO users (phone, token, tid, sid, device_id, secrettoken, customer_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (phone, res['token'], res['tid'], res['sid'], res['device_id'], res['secrettoken'], res['customer_id']))
        else:
            conn.execute("""
                INSERT INTO users (phone, token, tid, sid, device_id, secrettoken, customer_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (phone) DO UPDATE SET
                    token = EXCLUDED.token,
                    tid = EXCLUDED.tid,
                    sid = EXCLUDED.sid,
                    device_id = EXCLUDED.device_id,
                    secrettoken = EXCLUDED.secrettoken,
                    customer_id = EXCLUDED.customer_id
            """, (phone, res['token'], res['tid'], res['sid'], res['device_id'], res['secrettoken'], res['customer_id']))
    conn.close()
    
    return jsonify({
        "status": "ok",
        "name": res['name'],
        "user_id": res['customer_id'],
        "secrettoken": res['secrettoken']
    })

@app.route('/check-balance', methods=['POST', 'OPTIONS'])
def check_balance():
    if request.method == 'OPTIONS': return '', 200
    data = request.get_json()
    secrettoken = data.get('secrettoken')
    
    conn = get_db()
    cur = conn.cursor()
    if USE_SQLITE:
        cur.execute("SELECT tid, sid, total_earned, streak_days, last_collection_date FROM users WHERE secrettoken = ?", (secrettoken,))
    else:
        cur.execute("SELECT tid, sid, total_earned, streak_days, last_collection_date FROM users WHERE secrettoken = %s", (secrettoken,))
    row = cur.fetchone()
    conn.close()
    
    if not row:
        return jsonify({"status": "error", "message": "User not found"}), 404
    
    # Fetch fresh balance from Swiggy
    resp = check_buzz(secrettoken, row['tid'], row['sid'])
    earned = extract_earned(resp)
    
    # Update DB with fresh balance
    conn = get_db()
    with conn:
        if USE_SQLITE:
            conn.execute("UPDATE users SET total_earned = ? WHERE secrettoken = ?", (earned, secrettoken))
        else:
            conn.execute("UPDATE users SET total_earned = %s WHERE secrettoken = %s", (earned, secrettoken))
    conn.close()
    
    return jsonify({
        "status": "ok",
        "totalEarned": earned,
        "streak": row['streak_days'] or 0,
        "lastDate": row['last_collection_date'] or "Never"
    })

@app.route('/collect', methods=['POST', 'OPTIONS'])
def collect():
    if request.method == 'OPTIONS': return '', 200
    data = request.get_json()
    secrettoken = data.get('secrettoken')
    
    conn = get_db()
    cur = conn.cursor()
    if USE_SQLITE:
        cur.execute("SELECT id, tid, sid, customer_id FROM users WHERE secrettoken = ?", (secrettoken,))
    else:
        cur.execute("SELECT id, tid, sid, customer_id FROM users WHERE secrettoken = %s", (secrettoken,))
    row = cur.fetchone()
    conn.close()
    
    if not row:
        return jsonify({"status": "error", "message": "User not found"}), 404
    
    user_id, tid, sid, customer_id = row['id'], row['tid'], row['sid'], row['customer_id']
    
    today = datetime.now().date().isoformat()
    conn = get_db()
    cur = conn.cursor()
    if USE_SQLITE:
        cur.execute("SELECT last_collection_date FROM users WHERE id = ?", (user_id,))
    else:
        cur.execute("SELECT last_collection_date FROM users WHERE id = %s", (user_id,))
    last = cur.fetchone()
    conn.close()
    if last and last['last_collection_date'] == today:
        return jsonify({"status": "error", "message": "Already collected today!"}), 400
    
    initial_data = check_buzz(secrettoken, tid, sid)
    initial_earned = extract_earned(initial_data)
    
    connect_success, connect_fail = 0, 0
    for target in TARGET_USERS:
        try:
            resp = send_connect(secrettoken, target, customer_id, tid, sid)
            if resp.get('statusCode') == 0:
                connect_success += 1
            else:
                connect_fail += 1
            time.sleep(0.5)
        except:
            connect_fail += 1
    
    pending = get_pending_incoming(secrettoken, tid, sid)
    accept_success, accept_fail = 0, 0
    for target_id in pending:
        try:
            resp = accept_connection(secrettoken, target_id, customer_id, tid, sid)
            if resp.get('statusCode') == 0:
                accept_success += 1
            else:
                accept_fail += 1
            time.sleep(0.5)
        except:
            accept_fail += 1
    
    final_data = check_buzz(secrettoken, tid, sid)
    final_earned = extract_earned(final_data)
    earned_amount = final_earned - initial_earned
    
    conn = get_db()
    with conn:
        if USE_SQLITE:
            conn.execute("""
                UPDATE users SET total_earned = total_earned + ?,
                    last_collection_date = ?,
                    streak_days = streak_days + 1
                WHERE id = ?
            """, (earned_amount, today, user_id))
        else:
            conn.execute("""
                UPDATE users SET total_earned = total_earned + %s,
                    last_collection_date = %s,
                    streak_days = streak_days + 1
                WHERE id = %s
            """, (earned_amount, today, user_id))
    conn.close()
    
    return jsonify({
        "status": "ok",
        "earned": earned_amount,
        "successful": connect_success + accept_success,
        "failed": connect_fail + accept_fail,
        "totalEarned": final_earned,
        "connects": connect_success,
        "accepts": accept_success
    })

# ==================== NEW SSE ENDPOINT ====================
@app.route('/collect-stream', methods=['GET', 'OPTIONS'])
def collect_stream():
    if request.method == 'OPTIONS':
        return '', 200
    secrettoken = request.args.get('secrettoken')
    if not secrettoken:
        return jsonify({"status": "error", "message": "Missing secrettoken"}), 400

    def generate():
        conn = get_db()
        cur = conn.cursor()
        if USE_SQLITE:
            cur.execute("SELECT id, tid, sid, customer_id FROM users WHERE secrettoken = ?", (secrettoken,))
        else:
            cur.execute("SELECT id, tid, sid, customer_id FROM users WHERE secrettoken = %s", (secrettoken,))
        row = cur.fetchone()
        conn.close()
        if not row:
            yield f"data: {json.dumps({'error': 'User not found'})}\n\n"
            return

        user_id, tid, sid, customer_id = row['id'], row['tid'], row['sid'], row['customer_id']

        today = datetime.now().date().isoformat()
        conn = get_db()
        cur = conn.cursor()
        if USE_SQLITE:
            cur.execute("SELECT last_collection_date FROM users WHERE id = ?", (user_id,))
        else:
            cur.execute("SELECT last_collection_date FROM users WHERE id = %s", (user_id,))
        last = cur.fetchone()
        conn.close()
        if last and last['last_collection_date'] == today:
            yield f"data: {json.dumps({'error': 'Already collected today!'})}\n\n"
            return

        initial_data = check_buzz(secrettoken, tid, sid)
        initial_earned = extract_earned(initial_data)

        # Connect phase
        for idx, target in enumerate(TARGET_USERS, 1):
            try:
                resp = send_connect(secrettoken, target, customer_id, tid, sid)
                status = resp.get('statusCode') == 0
                yield f"data: {json.dumps({'type': 'connect', 'index': idx, 'total': len(TARGET_USERS), 'target': target, 'success': status})}\n\n"
                time.sleep(0.5)
            except Exception as e:
                yield f"data: {json.dumps({'type': 'connect', 'index': idx, 'total': len(TARGET_USERS), 'target': target, 'success': False, 'error': str(e)})}\n\n"

        # Accept phase
        pending = get_pending_incoming(secrettoken, tid, sid)
        for idx, target_id in enumerate(pending, 1):
            try:
                resp = accept_connection(secrettoken, target_id, customer_id, tid, sid)
                status = resp.get('statusCode') == 0
                yield f"data: {json.dumps({'type': 'accept', 'index': idx, 'total': len(pending), 'target': target_id, 'success': status})}\n\n"
                time.sleep(0.5)
            except Exception as e:
                yield f"data: {json.dumps({'type': 'accept', 'index': idx, 'total': len(pending), 'target': target_id, 'success': False, 'error': str(e)})}\n\n"

        final_data = check_buzz(secrettoken, tid, sid)
        final_earned = extract_earned(final_data)
        earned_amount = final_earned - initial_earned

        conn = get_db()
        with conn:
            if USE_SQLITE:
                conn.execute("""
                    UPDATE users SET total_earned = total_earned + ?,
                        last_collection_date = ?,
                        streak_days = streak_days + 1
                    WHERE id = ?
                """, (earned_amount, today, user_id))
            else:
                conn.execute("""
                    UPDATE users SET total_earned = total_earned + %s,
                        last_collection_date = %s,
                        streak_days = streak_days + 1
                    WHERE id = %s
                """, (earned_amount, today, user_id))
        conn.close()

        yield f"data: {json.dumps({'type': 'summary', 'earned': earned_amount, 'totalEarned': final_earned})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
