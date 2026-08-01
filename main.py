import os
import re
import time
import uuid
import logging
import sqlite3
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from datetime import datetime

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("buzz-api")

# ------------------- DATABASE (SQLite fallback) -------------------
DATABASE_URL = os.environ.get("DATABASE_URL")
USE_SQLITE = not DATABASE_URL

if USE_SQLITE:
    DB_PATH = os.path.join(os.path.dirname(__file__), "swiggy_buzz.db")
    log.warning("⚠️ DATABASE_URL not set – using SQLite (data will not persist across restarts)")

def get_db_connection():
    if USE_SQLITE:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    else:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db_connection()
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    action TEXT,
                    amount REAL,
                    status TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(id)
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    action TEXT,
                    amount REAL,
                    status TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
    conn.close()

init_db()

# ------------------- SWIGGY CLIENT -------------------
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

def send_otp(phone):
    device_id = generate_device_id()
    url = f"{SMS_OTP_URL}?mobile={phone}"
    resp = requests.get(url, headers=swiggy_headers(device_id=device_id), timeout=30)
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}"}
    data = resp.json()
    if data.get("statusCode") == 0:
        return {
            "tid": data.get("tid", ""),
            "sid": data.get("sid", ""),
            "deviceId": data.get("deviceId", device_id),
            "status": "ok"
        }
    return {"error": data.get("statusMessage", "OTP send failed")}

def verify_otp(phone, otp, tid, sid, device_id):
    url = f"{LOGIN_VERIFY_URL}?otp_source=Sms-manual"
    body = {
        "cloningSignalsData": {
            "appFilesDirPathInvalid": 0,
            "developerModeEnabled": 1,
            "deviceModelVmos": 0,
            "emulatorStatus": 0,
            "packageName": "in.swiggy.android",
            "workProfileEnabled": 0,
        },
        "otp": otp,
    }
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
            "secrettoken": token,  # same as token for SPNS
        }
    return {"error": data.get("statusMessage", "Verification failed")}

def check_buzz(secrettoken, tid, sid):
    url = REWARDS_URL
    body = {
        "generalContext": {"requestContext": {"clientId": "portal_banner"}},
        "campaignRewardRequests": [{
            "campaignType": "CAMPAIGN_TYPE_BUZZ_MONEY_STREAKS",
            "campaignId": "ougwl",
            "rollingFreecashParams": {
                "forceRefresh": True,
                "requestParams": {"dataRequested": "wallet,connections,transactions", "source": "banner"}
            }
        }]
    }
    resp = requests.post(url, headers=spns_headers(secrettoken, tid, sid), json=body, timeout=30)
    return resp.json() if resp.content else {}

def initiate_buzz(secrettoken, customer_id, tid, sid):
    url = ACTION_URL
    body = {
        "generalContext": {"requestContext": {"clientId": "portal_banner"}},
        "consumerContext": {"consumerId": customer_id},
        "campaignUserActionRequest": {
            "campaignId": "ougwl",
            "campaignType": "CAMPAIGN_TYPE_BUZZ_MONEY_STREAKS",
            "action": {"actionType": "ACTION_TYPE_CONNECT", "targetEntityId": TARGET_USERS[0]}  # placeholder
        }
    }
    resp = requests.post(url, headers=spns_headers(secrettoken, tid, sid), json=body, timeout=30)
    return resp.json() if resp.content else {}

def complete_buzz(secrettoken, target_entity_id, customer_id, tid, sid):
    url = ACTION_URL
    body = {
        "generalContext": {"requestContext": {"clientId": "portal_banner"}},
        "consumerContext": {"consumerId": customer_id},
        "campaignUserActionRequest": {
            "campaignId": "ougwl",
            "campaignType": "CAMPAIGN_TYPE_BUZZ_MONEY_STREAKS",
            "action": {"actionType": "ACTION_TYPE_ACCEPT", "targetEntityId": target_entity_id}
        }
    }
    resp = requests.post(url, headers=spns_headers(secrettoken, tid, sid), json=body, timeout=30)
    return resp.json() if resp.content else {}

def extract_earned(data):
    try:
        responses = data.get("data", {}).get("campaignRewardResponses", [])
        for resp in responses:
            for reward in resp.get("rewards", []):
                rolling = reward.get("rollingFreecash", {})
                earned = rolling.get("totalEarned", {})
                return float(earned.get("units", 0))
    except:
        pass
    return 0.0

def run_collection(secrettoken, customer_id, tid, sid):
    results = {"successful": 0, "failed": 0, "total_earned": 0}
    # check initial balance
    initial_data = check_buzz(secrettoken, tid, sid)
    initial_earned = extract_earned(initial_data)
    
    for user_id in TARGET_USERS:
        # initiate (with target user)
        init_resp = initiate_buzz(secrettoken, customer_id, tid, sid)
        # but initiate uses a fixed target, we need to use the actual target from list
        # We'll modify the initiate function to accept target
        # For simplicity, let's use the complete_buzz with the user_id directly (since initiate already sent request)
        # Actually the flow: initiate buzz without target? The API may return a target id. Better: we use the provided order.
        # We'll use a different approach: for each user_id, we send connect request (initiate) and then accept (complete) 
        # but we need to send the connect request to that specific user_id.
        # Let's create a function send_connect and send_accept.
        pass
    # We'll reimplement inside the Flask endpoint
    return results

# ------------------- FLASK ENDPOINTS -------------------
@app.route('/send-otp', methods=['POST'])
def send_otp_endpoint():
    data = request.get_json()
    phone = data.get('phone', '').strip()
    if not re.match(r'^[6-9]\d{9}$', phone):
        return jsonify({"status": "error", "message": "Invalid phone number"}), 400
    res = send_otp(phone)
    if 'error' in res:
        return jsonify({"status": "error", "message": res['error']}), 400
    return jsonify({"status": "ok", "tid": res['tid'], "sid": res['sid'], "deviceId": res['deviceId']})

@app.route('/verify-otp', methods=['POST'])
def verify_otp_endpoint():
    data = request.get_json()
    phone = data.get('phone')
    otp = data.get('otp')
    tid = data.get('tid')
    sid = data.get('sid')
    device_id = data.get('deviceId')
    res = verify_otp(phone, otp, tid, sid, device_id)
    if 'error' in res:
        return jsonify({"status": "error", "message": res['error']}), 400
    # save user in DB
    conn = get_db_connection()
    with conn:
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
    return jsonify({
        "status": "ok",
        "name": res['name'],
        "user_id": res['customer_id'],
        "secrettoken": res['secrettoken']
    })

@app.route('/check-balance', methods=['POST'])
def check_balance():
    data = request.get_json()
    secrettoken = data.get('secrettoken')
    # we need tid, sid from DB or request
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT tid, sid FROM users WHERE secrettoken = %s", (secrettoken,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({"status": "error", "message": "User not found"}), 404
    tid, sid = row['tid'], row['sid']
    resp = check_buzz(secrettoken, tid, sid)
    earned = extract_earned(resp)
    return jsonify({"status": "ok", "totalEarned": earned})

@app.route('/collect', methods=['POST'])
def collect():
    data = request.get_json()
    secrettoken = data.get('secrettoken')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, tid, sid, customer_id FROM users WHERE secrettoken = %s", (secrettoken,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"status": "error", "message": "User not found"}), 404
    user_id, tid, sid, customer_id = row['id'], row['tid'], row['sid'], row['customer_id']
    conn.close()
    
    # Perform 50 buzz loop
    successful = 0
    failed = 0
    initial_data = check_buzz(secrettoken, tid, sid)
    initial_earned = extract_earned(initial_data)
    
    for target in TARGET_USERS:
        # initiate
        init_resp = initiate_buzz(secrettoken, customer_id, tid, sid)
        # But this uses a placeholder target; we need to send connect to the specific target.
        # Correct way: use ACTION_TYPE_CONNECT with targetEntityId = target
        # Let's implement a dedicated function:
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
            return requests.post(url, headers=spns_headers(secrettoken, tid, sid), json=body, timeout=30).json()
        # accept
        def send_accept(secrettoken, target_id, customer_id, tid, sid):
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
            return requests.post(url, headers=spns_headers(secrettoken, tid, sid), json=body, timeout=30).json()
        
        try:
            connect_resp = send_connect(secrettoken, target, customer_id, tid, sid)
            if connect_resp.get('statusCode') != 0:
                failed += 1
                continue
            time.sleep(0.5)
            accept_resp = send_accept(secrettoken, target, customer_id, tid, sid)
            if accept_resp.get('statusCode') == 0:
                successful += 1
            else:
                failed += 1
            time.sleep(0.5)
        except Exception as e:
            log.error(f"Error with target {target}: {e}")
            failed += 1
    
    final_data = check_buzz(secrettoken, tid, sid)
    final_earned = extract_earned(final_data)
    earned_amount = final_earned - initial_earned
    
    # update user's total_earned in DB
    conn = get_db_connection()
    with conn:
        conn.execute("UPDATE users SET total_earned = total_earned + %s, last_collection_date = %s WHERE id = %s",
                     (earned_amount, datetime.now().date(), user_id))
    conn.close()
    
    return jsonify({
        "status": "ok",
        "earned": earned_amount,
        "successful": successful,
        "failed": failed
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
