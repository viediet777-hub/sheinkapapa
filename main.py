
import json, requests, sys, urllib.parse, re, time, threading, logging, html
from pathlib import Path
from datetime import datetime, timezone
from bs4 import BeautifulSoup
import telebot

# ---------------- CONFIG ----------------
TELEGRAM_BOT_TOKEN = "8155171919:AAFwJnrr7VomdrI-XuQ-q5Im9KGGVcaRs-0" 
ADMIN_CHAT_ID = "1364476174"
COOKIES_FILE = "cookies.json"
CONFIG_FILE = "config.json"
CHECK_INTERVAL_SECONDS = 3.0
MONITOR_LOOP_SLEEP = 1.5

PINCODE = "452016"
ADDRESS_ID = "auto"
USER_EMAIL = "vijaycore77@gmail.com"
USER_MOBILE = "9826621729"
USER_ID ="a5ca40ad-4185-46b8-9c85-010543cdbb3f"

# Endpoints
URL_MICROCART = "https://www.sheinindia.in/api/cart/microcart"
URL_DELETE = "https://www.sheinindia.in/api/cart/delete"
URL_CREATE = "https://www.sheinindia.in/api/cart/create"
URL_ADD_FMT = "https://www.sheinindia.in/api/cart/{cart_id}/product/{product_id}/add"
URL_APPLY_VOUCHER = "https://www.sheinindia.in/api/cart/apply-voucher"
URL_SERVICE_CHECK = "https://www.sheinindia.in/api/edd/checkDeliveryDetails"
URL_BANNER_INFO = "https://www.sheinindia.in/api/my-account/banner-info"
URL_PAY_STAGE2 = "https://payment.sheinindia.in/pay"
URL_PAY_NOW = "https://payment.sheinindia.in/payment-engine/api/v1/payment/pay-now"

URL_APP_ADDRESS = "https://www.sheinindia.in/checkout/address/getAddressList"
URL_ADDRESS_BOOK = "https://www.sheinindia.in/my-account/address-book"

COMMON_HEADERS = {
    "sec-ch-ua-platform": '"Android"',
    "user-agent": "Mozilla/5.0 (Linux; Android 10; RMX2030 Build/QKQ1.200209.002) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142 Mobile Safari/537.36",
    "x-tenant-id": "SHEIN",
    "accept-language": "en-US,en;q=0.9"
}
HEADERS_JSON = {
    **COMMON_HEADERS,
    "accept": "application/json",
    "content-type": "application/json",
    "referer": "https://www.sheinindia.in/cart?user=old"
}
HEADERS_HTML = {
    **COMMON_HEADERS,
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,/;q=0.8"
}

# ---------------- logging & bot ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("shein_autoaddr_bot")

try:
    ADMIN_CHAT_ID_INT = int(ADMIN_CHAT_ID)
except:
    ADMIN_CHAT_ID_INT = ADMIN_CHAT_ID

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode=None)

# Premium notification function
def tg_send(text):
    try:
        bot.send_message(ADMIN_CHAT_ID_INT, text)
    except:
        logger.exception("tg_send failed")

# ---------------- cookies ----------------
def load_cookies():
    p = Path(COOKIES_FILE)
    if not p.exists():
        raise FileNotFoundError("cookies.json missing")
    cookies = json.loads(p.read_text(encoding="utf-8"))
    return cookies

def save_cookies(cookies):
    Path(COOKIES_FILE).write_text(
        json.dumps(cookies, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8"
    )

def merge_set_cookie_headers(resp, cookies):
    set_cookie_all = []
    try:
        if hasattr(resp, "raw") and hasattr(resp.raw, "headers"):
            raw_headers = getattr(resp.raw, "headers", {})
            if hasattr(raw_headers, "get_all"):
                try:
                    set_cookie_all.extend(raw_headers.get_all("Set-Cookie"))
                except:
                    pass
        sc_single = resp.headers.get("Set-Cookie")
        if sc_single:
            set_cookie_all.append(sc_single)
    except:
        pass

    for sc in set_cookie_all:
        parts = sc.split(";")
        if not parts:
            continue
        kv = parts[0].strip()
        if "=" not in kv:
            continue
        name, val = kv.split("=", 1)
        cookies[name.strip()] = val.strip()
    return cookies

# ---------------- HTTP wrapper ----------------
def safe_json(r):
    try:
        return r.json()
    except:
        return None

def req(
    method,
    url,
    headers=None,
    cookies=None,
    body=None,
    params=None,
    allow_redirects=True,
    timeout=25,
    return_resp=False
):
    headers = headers or HEADERS_JSON
    try:
        if method == "GET":
            r = requests.get(
                url, headers=headers, cookies=cookies,
                params=params, allow_redirects=allow_redirects,
                timeout=timeout
            )
        else:
            r = requests.post(
                url, headers=headers, cookies=cookies,
                data=body, params=params,
                allow_redirects=allow_redirects,
                timeout=timeout
            )
    except Exception as e:
        logger.exception("HTTP error %s %s: %s", method, url, e)
        return None, None, False

    data = safe_json(r)
    ok = (200 <= r.status_code < 300)

    if return_resp:
        return r, data, ok
    return r, data, ok

# ---------------- utilities ----------------
def extract_product_id_from_url(url_or_id):
    s = str(url_or_id)
    if s.isdigit():
        return s
    try:
        parsed = urllib.parse.urlparse(s)
        parts = parsed.path.strip("/").split("/")
        for seg in reversed(parts):
            if seg.isdigit():
                return seg
        m = re.search(r"(\d{6,})", s)
        if m:
            return m.group(1)
    except:
        pass
    return None

def normalize_address_for_payload(addr):
    if not isinstance(addr, dict):
        return {}

    def pick(*keys):
        for k in keys:
            if k in addr and addr[k] not in (None, ""):
                return addr[k]
        return ""

    normalized = {
        "addressId": str(pick("addressId", "id", "address_id", "addressID")),
        "consignee": pick("addressPoc", "consignee", "name", "fullName", "receiver"),
        "mobile": pick("phone", "mobile", "phoneNumber", "telephone"),
        "postalCode": pick("postalCode", "zip", "pincode"),
        "country": pick("country", "countryName", "countryCode", "country.isocode"),
        "province": pick("state", "province", "region"),
        "city": pick("district", "city"),
        "region": pick("region", "district"),
        "address": pick("line1", "line2", "address", "address1", "addressDetail"),
        "isDefault": bool(
            addr.get("defaultAddress")
            or addr.get("default")
            or addr.get("isDefault")
        )
    }
    return normalized
    # ---------------- ADDRESS DETECTION ----------------

def get_app_default_address(cookies):
    try:
        r = requests.get(
            URL_APP_ADDRESS,
            headers=HEADERS_JSON,
            cookies=cookies,
            timeout=12
        )
    except Exception:
        return None

    try:
        Path(f"addr_app_debug_{int(time.time())}.json").write_text(
            json.dumps(
                {
                    "status": getattr(r, "status_code", None),
                    "data": safe_json(r) or r.text
                },
                default=str,
                ensure_ascii=False
            ),
            encoding="utf-8"
        )
    except:
        pass

    if r.status_code >= 400:
        return None

    data = safe_json(r)
    if not data:
        return None

    addr_list = None

    if isinstance(data, dict):
        for key in ("data", "addressList", "addresses", "result", "list", "records"):
            if key in data:
                candidate = data[key]
                if isinstance(candidate, list):
                    addr_list = candidate
                    break
                if isinstance(candidate, dict) and "addressList" in candidate:
                    if isinstance(candidate["addressList"], list):
                        addr_list = candidate["addressList"]
                        break

    if not addr_list:
        def deep_find_list(obj):
            if isinstance(obj, list):
                return obj
            if isinstance(obj, dict):
                for v in obj.values():
                    found = deep_find_list(v)
                    if found:
                        return found
            return None

        addr_list = deep_find_list(data)

    if not addr_list or not isinstance(addr_list, list):
        return None

    if len(addr_list) == 0:
        return None

    first = None
    for a in addr_list:
        if not isinstance(a, dict):
            continue
        if first is None:
            first = a
        for flag in ("isDefault", "default", "defaultAddress", "defaultFlag", "selectedAddressType"):
            if flag in a:
                val = str(a[flag]).lower()
                if val in ("true", "1"):
                    return a

    return first


def extract_json_object_around(text, idx):
    left = text.rfind("{", 0, idx)
    if left == -1:
        left = 0

    depth = 0
    right = None

    for i in range(left, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                right = i
                break

    if right is None:
        right = min(left + 2000, len(text) - 1)

    return text[left:right+1]


def parse_address_from_address_book_html(html_text):
    try:
        Path(f"addr_html_debug_{int(time.time())}.txt").write_text(
            html_text[:20000],
            encoding="utf-8"
        )
    except:
        pass

    m = re.search(r'"addressPoc"\s*:\s*"[^"]+"', html_text)
    if not m:
        m = re.search(r'"postalCode"\s*:\s*"\d{5,6}"', html_text)
    if not m:
        m = re.search(r'"id"\s*:\s*"\d{6,}"', html_text)
    if not m:
        m = re.search(r'addressPoc\s*[:=]\s*["\']?[^",\}\]]+', html_text)
    if not m:
        return None

    obj_text = extract_json_object_around(html_text, m.start())
    obj_text = obj_text.replace("&quot;", '"').replace("&#39;", "'")

    try:
        return json.loads(obj_text)
    except:
        attempt = obj_text
        attempt = re.sub(r"(['\"])?([a-zA-Z0-9_]+)\1\s*:", r'"\2":', attempt)
        attempt = attempt.replace("'", '"')
        attempt = re.sub(r",(\s*[}\]])", r"\1", attempt)

        try:
            return json.loads(attempt)
        except:
            kv = {}
            mid = re.search(r'"?id"?\s*[:=]\s*"?(\d{5,})"?', obj_text)
            if mid:
                kv["id"] = mid.group(1)

            mname = re.search(r'"?addressPoc"?\s*[:=]\s*"?([^",\}\]]+)"?', obj_text)
            if mname:
                kv["addressPoc"] = mname.group(1).strip()

            mphone = re.search(r'"?phone"?\s*[:=]\s*"?([6-9]\d{9})"?', obj_text)
            if mphone:
                kv["phone"] = mphone.group(1)

            mpin = re.search(r'"?postalCode"?\s*[:=]\s*"?(\d{5,6})"?', obj_text)
            if mpin:
                kv["postalCode"] = mpin.group(1)

            mline1 = re.search(r'"?line1"?\s*[:=]\s*"?([^",\}]+)"?', obj_text)
            if mline1:
                kv["line1"] = mline1.group(1).strip()

            mline2 = re.search(r'"?line2"?\s*[:=]\s*"?([^",\}]+)"?', obj_text)
            if mline2:
                kv["line2"] = mline2.group(1).strip()

            if kv:
                return kv

    return None


def get_address_from_address_book_page(cookies):
    try:
        r = requests.get(
            URL_ADDRESS_BOOK,
            headers=HEADERS_HTML,
            cookies=cookies,
            timeout=12
        )
    except:
        return None

    try:
        Path(f"addr_book_debug_{int(time.time())}.html").write_text(
            r.text[:20000],
            encoding="utf-8"
        )
    except:
        pass

    if r.status_code >= 400:
        return None

    parsed = parse_address_from_address_book_html(r.text)
    if parsed:
        addr = {}
        addr["id"] = (
            parsed.get("id")
            or parsed.get("addressId")
            or parsed.get("address_id")
            or parsed.get("addressID")
        )
        addr["addressPoc"] = (
            parsed.get("addressPoc")
            or parsed.get("consignee")
            or parsed.get("name")
        )
        addr["phone"] = (
            parsed.get("phone")
            or parsed.get("mobile")
            or parsed.get("phoneNumber")
        )
        addr["line1"] = (
            parsed.get("line1")
            or parsed.get("address")
            or parsed.get("address1")
        )
        addr["line2"] = (
            parsed.get("line2")
            or parsed.get("landmark")
            or ""
        )
        addr["postalCode"] = (
            parsed.get("postalCode")
            or parsed.get("postal_code")
            or parsed.get("zip")
        )
        addr["state"] = (
            parsed.get("state")
            or parsed.get("province")
        )
        addr["district"] = (
            parsed.get("district")
            or parsed.get("city")
        )
        addr["country.isocode"] = (
            parsed.get("country.isocode")
            or parsed.get("country")
        )

        for k in list(addr.keys()):
            if addr[k] is None:
                addr[k] = ""

        return addr

    return None


def get_best_address(cookies):
    app_addr = get_app_default_address(cookies)
    if app_addr:
        return dict(app_addr)

    ab = get_address_from_address_book_page(cookies)
    if ab:
        return ab

    json_endpoints = [
        "https://www.sheinindia.in/api/my-account/address/list",
        "https://www.sheinindia.in/api/shipping/address/list",
        "https://www.sheinindia.in/api/customer/addressList"
    ]

    for url in json_endpoints:
        try:
            r, data, ok = req(
                "GET",
                url,
                HEADERS_JSON,
                cookies,
                return_resp=True
            )
            Path(f"addr_json_debug_{int(time.time())}.json").write_text(
                json.dumps(
                    {
                        "url": url,
                        "status": getattr(r, "status_code", None),
                        "data": data
                    },
                    default=str,
                    ensure_ascii=False
                ),
                encoding="utf-8"
            )

            if ok and data:
                if isinstance(data, dict):
                    for key in ("data", "addressList", "addresses", "result"):
                        if key in data and isinstance(data[key], list):
                            if len(data[key]) > 0:
                                return data[key][0]

                if isinstance(data, list) and len(data) > 0:
                    return data[0]

        except:
            continue

    return None
    # ---------------- CART + ORDER functions ----------------

def create_cart(cookies):
    payload = {
        "user": urllib.parse.quote(USER_EMAIL).replace("%40", "%40"),
        "accessToken": ""
    }

    r, data, ok = req(
        "POST",
        URL_CREATE,
        HEADERS_JSON,
        cookies,
        body=json.dumps(payload),
        return_resp=True
    )

    if not ok or data is None:
        logger.error(
            "create_cart failed: %s %s",
            getattr(r, "status_code", None),
            getattr(r, "text", None)
        )
        return None, cookies, "[ERR createCart]"

    cookies = merge_set_cookie_headers(r, cookies)
    save_cookies(cookies)

    fallback = {
        "code": data.get("code"),
        "cartCount": data.get("totalItems"),
        "totalItems": data.get("totalItems"),
        "totalPrice": data.get("totalPrice"),
        "netPrice": data.get("netPrice"),
        "entries": []
    }

    return fallback, cookies, None


def ensure_cart_exists(cookies):
    r, data, ok = req(
        "GET",
        URL_MICROCART,
        HEADERS_JSON,
        cookies,
        return_resp=True
    )
    if ok and data and data.get("code"):
        return data, cookies, None

    return create_cart(cookies)


def fetch_cart(cookies):
    r, data, ok = req(
        "GET",
        URL_MICROCART,
        HEADERS_JSON,
        cookies,
        return_resp=True
    )

    if not ok or data is None:
        return None, "[ERR cartRefresh]"

    return data, None


def clear_cart_if_needed(cart_data, cookies):
    cart_id = cart_data.get("code")
    cart_count = cart_data.get("cartCount")
    total_items = cart_data.get("totalItems")

    has_items = False
    if isinstance(cart_count, int) and cart_count > 0:
        has_items = True
    if isinstance(total_items, int) and total_items > 0:
        has_items = True

    if not cart_id:
        return cart_data, "[ERR noCartId]"

    if not has_items:
        return cart_data, None

    body = {"entryNumber": 0}

    r, data, ok = req(
        "POST",
        URL_DELETE,
        HEADERS_JSON,
        cookies,
        body=json.dumps(body),
        return_resp=True
    )

    if not ok or data is None:
        return None, "[ERR deleteCart]"

    return data, None


def check_serviceability(product_id, cookies):
    params = {
        "productCode": product_id,
        "postalCode": PINCODE,
        "quantity": "1",
        "IsExchange": "false"
    }

    r, data, ok = req(
        "GET",
        URL_SERVICE_CHECK,
        HEADERS_JSON,
        cookies,
        params=params,
        return_resp=True
    )

    if not ok or data is None:
        return False, "[ERR serviceCheck]"

    svc = data.get("servicability")
    cod = data.get("codEligible")

    details = data.get("productDetails") or [{}]
    detail = details[0]

    svc_prod = detail.get("servicability")
    cod_prod = detail.get("codEligible")

    return bool(svc and cod and svc_prod and cod_prod), None


def add_item(cart_id, product_id_or_sku, cookies):
    url = URL_ADD_FMT.format(
        cart_id=cart_id,
        product_id=product_id_or_sku
    )

    body = {"quantity": 1}

    r, data, ok = req(
        "POST",
        url,
        HEADERS_JSON,
        cookies,
        body=json.dumps(body),
        return_resp=True
    )

    if not ok or data is None:
        return False, f"[ERR addItem] {getattr(r,'status_code',None)} {getattr(r,'text',None)}"

    if isinstance(data, dict) and (data.get("statusCode") == "success" or data.get("status") == "success"):
        return True, None

    if isinstance(data, dict) and "errorMessage" in data:
        errs = data["errorMessage"].get("errors", [])
        if errs:
            return False, errs[0].get("message")

    return False, "Add failed"


def apply_voucher(voucher_code, cookies):
    payload = {
        "voucherId": voucher_code,
        "device": {"client_type": "MSITE"}
    }

    r, data, ok = req(
        "POST",
        URL_APPLY_VOUCHER,
        HEADERS_JSON,
        cookies,
        body=json.dumps(payload),
        return_resp=True
    )

    if not ok or data is None:
        return None, "[ERR voucherApply]"

    return data, None


def build_banner_info_payload(cart_id, address_obj=None):
    addr_norm = normalize_address_for_payload(address_obj) if address_obj else {}
    addr_id = addr_norm.get("addressId", "") if addr_norm else ""

    user_info = {
        "email": USER_EMAIL,
        "phoneNumber": USER_MOBILE,
        "profileName": "",
        "userId": USER_ID
    }

    if addr_norm:
        user_info["address"] = {
            "addressId": addr_id,
            "consignee": addr_norm.get("consignee", ""),
            "mobile": addr_norm.get("mobile", ""),
            "postalCode": addr_norm.get("postalCode", ""),
            "country": addr_norm.get("country", "") or "IN",
            "province": addr_norm.get("province", ""),
            "city": addr_norm.get("city", ""),
            "region": addr_norm.get("region", ""),
            "address": (
                addr_norm.get("address", "")
                or (addr_norm.get("line1", "") + " " + addr_norm.get("line2", ""))
            )
        }

    return {
        "item": {
            "baseRequest": {
                "consumerType": "STOREFRONT",
                "pageType": "string",
                "tenantId": "SHEIN",
                "cartId": cart_id,
                "channelInfo": {
                    "appType": "OTHER",
                    "appVersion": "string",
                    "channelType": "MSITE",
                    "deviceId": "string",
                    "nthOrderOnChannel": 0,
                    "osType": "WINDOW",
                    "osVersion": "string",
                    "sessionId": "string"
                },
                "userInfo": user_info
            }
        },
        "extraParam": {
            "addressId": addr_id,
            "address": user_info.get("address", {})
        },
        "showConvenienceFeeFlow": True
    }


def encode_stage2_body(stage1_json):
    parts = []

    for key, val in stage1_json.items():
        enc_key = urllib.parse.quote(f'"{key}"', safe="")

        if isinstance(val, (dict, list)):
            enc_val = urllib.parse.quote(json.dumps(val, separators=(",", ":")), safe="")
        elif isinstance(val, bool):
            enc_val = urllib.parse.quote(str(val).lower(), safe="")
        else:
            enc_val = urllib.parse.quote(str(val), safe="")

        if isinstance(val, str) and not val.startswith("{") and not val.startswith("["):
            enc_val = urllib.parse.quote(f'"{val}"', safe="")

        parts.append(enc_key + "=" + enc_val)

    return "&".join(parts)


def stage2_pay(cart_id, stage1_json, cookies):
    body = encode_stage2_body(stage1_json)

    r, data, ok = req(
        "POST",
        URL_PAY_STAGE2,
        {
            **COMMON_HEADERS,
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://www.sheinindia.in"
        },
        cookies,
        body=body,
        allow_redirects=False,
        return_resp=True
    )

    try:
        Path(f"stage2_{cart_id}.html").write_text(
            getattr(r, "text", ""),
            encoding="utf-8"
        )
    except:
        pass

    if not (200 <= getattr(r, "status_code", 0) < 400):
        return None, "[ERR stage2/pay]"

    return r, None


def build_pay_now_form(stage1_json):
    cust = stage1_json.get("customer", {}) or {}
    order = stage1_json.get("order", {}) or {}
    tenant = stage1_json.get("tenant", {}) or {}
    pci = stage1_json.get("paymentChannelInformation", {}) or {}

    form_pairs = {
        "paymentInstrument": "COD",
        "notes[eligibleToEarnLoyalty]": "true",
        "paymentChannelInformation.paymentChannel": pci.get("paymentChannel", "MSITE"),
        "paymentChannelInformation.appType": pci.get("appType", "OTHER"),
        "tenant.code": tenant.get("code", "SHEIN"),
        "tenant.callbackUrl": tenant.get("callbackUrl", "https://www.sheinindia.in/payment-redirect"),
        "tenantTransactionId": stage1_json.get("tenantTransactionId", ""),
        "customer.uuid": cust.get("uuid", ""),
        "customer.email": USER_EMAIL,
        "customer.otp": "",
        "customer.mobile": USER_MOBILE,
        "order.orderId": order.get("orderId", ""),
        "order.amount": str(order.get("amount", "")),
        "order.netPayableAmount": str(order.get("netAmount", "")),
        "order.totalPrice1p": str(order.get("amount", "")),
        "order.totalPrice3p": "0",
        "accessToken": stage1_json.get("accessToken", ""),
        "requestChecksum": stage1_json.get("requestChecksum", ""),
        "deviceId": stage1_json.get("deviceId", ""),
        "deviceChecksum": stage1_json.get("deviceChecksum", ""),
        "cartCheckSum": stage1_json.get("cartCheckSum", ""),
        "transactionToken": stage1_json.get("transactionToken", "NA"),
    }

    return urllib.parse.urlencode(form_pairs)


def stage3_pay_now(cart_id, stage1_json, cookies):
    form_data = build_pay_now_form(stage1_json)

    r, data, ok = req(
        "POST",
        URL_PAY_NOW,
        {
            **COMMON_HEADERS,
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://payment.sheinindia.in"
        },
        cookies,
        body=form_data,
        allow_redirects=False,
        return_resp=True
    )

    html_text = getattr(r, "text", "")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    try:
        Path(f"pay_now_{cart_id}_{ts}.html").write_text(
            html_text,
            encoding="utf-8"
        )
    except:
        pass

    if not (200 <= getattr(r, "status_code", 0) < 400):
        return None, None, "[ERR pay-now]"

    return r, html_text, None


def parse_payment_success(html_text):
    if not html_text:
        return None

    m = re.search(
        r'name="paymentEngineCallbackData"\s+value="([^"]+)"',
        html_text
    )
    if not m:
        return None

    json_text = m.group(1)

    try:
        json_text = json_text.replace('\\"', '"')
        data = json.loads(json_text)
    except:
        try:
            data = json.loads(json_text.replace("'", '"'))
        except:
            return None

    tx_info = data.get("transactionInformation", {}) or {}
    order_info = data.get("order", {}) or {}

    status = tx_info.get("transactionStatus")
    order_id = order_info.get("orderId")
    payable = order_info.get("netPayableAmount", order_info.get("amount"))
    instrument = (tx_info.get("paymentInformation", {}) or {}).get("paymentInstrument")

    return {
        "status": status,
        "order_id": order_id,
        "amount": payable,
        "instrument": instrument
    }
    # ---------------- WATCHLIST + MONITOR ----------------

WATCHLIST = []
WATCH_LOCK = threading.Lock()
MONITOR_RUNNING = threading.Event()


def add_to_watch(product_ref, voucher=""):
    pid = extract_product_id_from_url(product_ref) or product_ref
    with WATCH_LOCK:
        for p in WATCHLIST:
            if p["product_id"] == pid:
                p["voucher"] = voucher
                p["active"] = True
                p["last_status"] = "updated"
                return False
        WATCHLIST.append({
            "product_id": pid,
            "ref": product_ref,
            "voucher": voucher,
            "active": True,
            "last_status": "added"
        })
    return True


def remove_from_watch(product_ref):
    pid = extract_product_id_from_url(product_ref) or product_ref
    with WATCH_LOCK:
        for p in list(WATCHLIST):
            if p["product_id"] == pid:
                WATCHLIST.remove(p)
                return True
    return False


def list_watch():
    with WATCH_LOCK:
        return [p.copy() for p in WATCHLIST]


def set_active(product_id, active):
    with WATCH_LOCK:
        for p in WATCHLIST:
            if p["product_id"] == product_id:
                p["active"] = active
                return True
    return False


def monitor_loop():
    try:
        cookies = load_cookies()
    except Exception:
        tg_send("🛑 cookies.json missing — monitor aborting.")
        return

    MONITOR_RUNNING.set()
    tg_send("▶ Monitor started (auto-order; address auto-detect).")

    while MONITOR_RUNNING.is_set():
        with WATCH_LOCK:
            items = [p.copy() for p in WATCHLIST if p.get("active", True)]

        if not items:
            time.sleep(MONITOR_LOOP_SLEEP)
            continue

        for item in items:
            pid = item["product_id"]

            try:
                # Ensure cart
                cart_data, cookies, err = ensure_cart_exists(cookies)
                if err:
                    tg_send(f"⚠ cart error: {err}")
                    continue

                save_cookies(cookies)

                # Clear existing items
                cart_data, err = clear_cart_if_needed(cart_data, cookies)
                if err:
                    tg_send(f"⚠ clear cart error: {err}")
                    continue

                # Check COD + serviceability
                ok, svc_err = check_serviceability(pid, cookies)
                if not ok:
                    item["last_status"] = "serviceability fail"
                    continue

                # Fetch cart fresh
                cart_live, err = fetch_cart(cookies)
                if err:
                    tg_send(f"⚠ fetch_cart err: {err}")
                    continue

                cart_id = cart_live.get("code")

                # Add item
                added, add_err = add_item(cart_id, pid, cookies)
                if not added:
                    item["last_status"] = f"add failed: {add_err}"
                    continue

                # Voucher
                voucher = item.get("voucher", "")
                if voucher:
                    _, v_err = apply_voucher(voucher, cookies)
                    if v_err:
                        tg_send(f"⚠ Voucher apply failed for {pid}: {v_err}")

                # Address auto-detect
                address_obj = None
                if ADDRESS_ID == "auto":
                    address_obj = get_best_address(cookies)

                # Stage1
                stage1_json, s1_err = stage1_banner_info(
                    cart_id,
                    cookies,
                    address_obj=address_obj
                )
                if s1_err:
                    tg_send(f"⚠ stage1 failed for {pid}: {s1_err}")
                    continue

                # Stage2
                _, s2_err = stage2_pay(cart_id, stage1_json, cookies)
                if s2_err:
                    tg_send(f"⚠ stage2 failed for {pid}: {s2_err}")
                    continue

                # Stage3
                r3, html_text, s3_err = stage3_pay_now(
                    cart_id,
                    stage1_json,
                    cookies
                )
                if s3_err:
                    tg_send(f"⚠ pay-now failed for {pid}: {s3_err}")
                    continue

                # Parse success
                parsed = parse_payment_success(html_text)
                if parsed and parsed.get("status") == "SUCCESS":
                    tg_send(f"✅ ORDER SUCCESS for {pid} order_id={parsed.get('order_id')}")
                    set_active(pid, False)

                    with WATCH_LOCK:
                        for w in WATCHLIST:
                            if w["product_id"] == pid:
                                w["last_status"] = f"Ordered {parsed.get('order_id')}"
                else:
                    tg_send(f"⚠ Order placed but couldn't parse SUCCESS for {pid}. Check saved pay_now HTML.")
                    with WATCH_LOCK:
                        for w in WATCHLIST:
                            if w["product_id"] == pid:
                                w["last_status"] = "order parse failed"

            except Exception as e:
                logger.exception("Monitor exception: %s", e)
                tg_send(f"🔥 Monitor exception for {pid}: {e}")

            time.sleep(CHECK_INTERVAL_SECONDS)

    tg_send("⏹ Monitor stopped.")


# ---------------- IMMEDIATE BUY ----------------

def attempt_buy_once(product_ref, voucher=""):
    pid = extract_product_id_from_url(product_ref) or product_ref

    try:
        cookies = load_cookies()
    except Exception:
        tg_send("🛑 cookies.json missing — buy aborted.")
        return

    try:
        # Ensure cart
        cart_data, cookies, err = ensure_cart_exists(cookies)
        if err:
            tg_send(f"⚠ cart error: {err}")
            return

        save_cookies(cookies)

        # Clear old cart
        cart_data, err = clear_cart_if_needed(cart_data, cookies)
        if err:
            tg_send(f"⚠ clear cart err: {err}")
            return

        # Check serviceability
        ok, svc_err = check_serviceability(pid, cookies)
        if not ok:
            tg_send(f"⚠ Product not serviceable/COD not allowed: {pid}")
            return

        # Fetch fresh cart
        cart_live, err = fetch_cart(cookies)
        if err:
            tg_send(f"⚠ fetch_cart err: {err}")
            return

        cart_id = cart_live.get("code")

        # Add to cart
        added, add_err = add_item(cart_id, pid, cookies)
        if not added:
            tg_send(f"❌ Add failed: {add_err}")
            return

        # Voucher
        if voucher:
            _, v_err = apply_voucher(voucher, cookies)
            if v_err:
                tg_send(f"⚠ Voucher apply failed: {v_err}")

        # Address auto-detect
        address_obj = None
        if ADDRESS_ID == "auto":
            address_obj = get_best_address(load_cookies())

        # Stage1
        stage1_json, s1_err = stage1_banner_info(
            cart_id,
            load_cookies(),
            address_obj=address_obj
        )
        if s1_err:
            tg_send(f"⚠ stage1 failed: {s1_err}")
            return

        # Stage2
        _, s2_err = stage2_pay(
            cart_id,
            stage1_json,
            load_cookies()
        )
        if s2_err:
            tg_send(f"⚠ stage2 failed: {s2_err}")
            return

        # Stage3
        r3, html_text, s3_err = stage3_pay_now(
            cart_id,
            stage1_json,
            load_cookies()
        )
        if s3_err:
            tg_send(f"⚠ pay-now failed: {s3_err}")
            return

        parsed = parse_payment_success(html_text)
        if parsed and parsed.get("status") == "SUCCESS":
            tg_send(
                f"✅ Immediate order success for {pid} "
                f"order={parsed.get('order_id')}"
            )
        else:
            tg_send("⚠ Immediate buy finished but could not verify success. Inspect saved pay_now HTML.")

    except Exception as e:
        logger.exception("Immediate buy exception: %s", e)
        tg_send(f"🔥 Immediate buy exception: {e}")


# ---------------- stage1_banner_info ----------------

def stage1_banner_info(cart_id, cookies, address_obj=None):
    if address_obj:
        try:
            payload = build_banner_info_payload(
                cart_id,
                address_obj=address_obj
            )

            r, data, ok = req(
                "POST",
                URL_BANNER_INFO,
                HEADERS_JSON,
                cookies,
                body=json.dumps(payload),
                return_resp=True
            )

            Path(f"stage1_{cart_id}.json").write_text(
                json.dumps(
                    {
                        "req": payload,
                        "resp": data,
                        "status": getattr(r, "status_code", None)
                    },
                    ensure_ascii=False,
                    separators=(",", ":")
                ),
                encoding="utf-8"
            )

            if not ok or data is None:
                return None, "[ERR banner-info]"

            return data, None

        except Exception as e:
            logger.exception("stage1 (address_obj) exception: %s", e)
            return None, "[ERR stage1 exception]"

    return None, "[ERR no address provided]"
    # ---------------- TELEGRAM COMMANDS (PREMIUM UI) ----------------

@bot.message_handler(commands=['start'])
def cmd_start(m):
    bot.reply_to(
        m,
        "✨ **Shein AutoBuyer — Premium Edition** ✨\n\n"
        "👑 Welcome to the fastest automated Shein auto-order bot.\n"
        "All operations are fully automatic:\n"
        "• 🔍 Address auto-detection\n"
        "• 🛒 Auto cart handling\n"
        "• 🎟 Voucher support\n"
        "• 🚚 COD & serviceability checking\n\n"
        "📘 **Available Commands:**\n"
        "➡️ /add `<url_or_id> [voucher]`\n"
        "➡️ /remove `<url_or_id>`\n"
        "➡️ /watchlist\n"
        "➡️ /monitor start | stop\n"
        "➡️ /buy `<url_or_id> [voucher]`\n"
        "➡️ /status\n"
        "➡️ /address\n\n"
        "💡 *Use /status anytime to check your bot health.*"
    )


@bot.message_handler(commands=['status'])
def cmd_status(m):
    try:
        cookies = load_cookies()
        cookie_ok = True
    except:
        cookies = None
        cookie_ok = False

    if cookie_ok:
        cart_data, err = fetch_cart(cookies)
    else:
        cart_data = None

    running = MONITOR_RUNNING.is_set()

    # Address
    addr_detected = ""
    if cookie_ok:
        try:
            addr = get_best_address(cookies)
            if addr:
                addr_detected = (
                    addr.get("id")
                    or addr.get("addressId")
                    or addr.get("address_id")
                    or ""
                )
        except:
            addr_detected = ""

    text = (
        "📦 **SYSTEM STATUS**\n\n"
        f"🟢 Monitor: **{'Running' if running else 'Stopped'}**\n"
        f"📁 Cookies: **{'Present' if cookie_ok else 'Missing'}**\n"
        f"🏠 Address Detected: **{addr_detected or 'none'}**\n"
    )

    if cart_data:
        text += (
            f"\n🛒 Cart ID: `{cart_data.get('code')}`\n"
            f"📦 Items in Cart: `{cart_data.get('totalItems') or cart_data.get('cartCount')}`\n"
        )

    text += f"\n📧 Email connected: `{USER_EMAIL}`"

    bot.reply_to(m, text)


@bot.message_handler(commands=['address'])
def cmd_address(m):
    try:
        cookies = load_cookies()
    except Exception:
        bot.reply_to(m, "❌ cookies.json missing")
        return

    addr = get_best_address(cookies)
    if not addr:
        bot.reply_to(
            m,
            "❌ Could not detect your address.\n"
            "Please ensure cookies are correct and address is added in Shein app."
        )
        return

    # Masking sensitive info
    safe = dict(addr)
    if "phone" in safe and isinstance(safe["phone"], str):
        if len(safe["phone"]) >= 6:
            safe["phone"] = safe["phone"][:3] + "XXXXX" + safe["phone"][-2:]

    if "line1" in safe and isinstance(safe["line1"], str):
        if len(safe["line1"]) > 30:
            safe["line1"] = safe["line1"][:30] + "..."

    bot.reply_to(
        m,
        "🏠 **Detected Address (Safe View):**\n\n"
        + json.dumps(safe, ensure_ascii=False, indent=2)
    )


@bot.message_handler(commands=['add'])
def cmd_add(m):
    parts = m.text.split()
    if len(parts) < 2:
        bot.reply_to(
            m,
            "❗ **Usage:** `/add <product_url_or_id> [voucher]`"
        )
        return

    ref = parts[1].strip()
    voucher = parts[2].strip() if len(parts) >= 3 else ""

    added = add_to_watch(ref, voucher)

    bot.reply_to(
        m,
        "📌 **Added to Watchlist**"
        if added else
        "✏️ **Updated Watchlist Entry**"
    )

    tg_send(f"🔔 Watchlist updated: {ref} (voucher={voucher})")


@bot.message_handler(commands=['remove'])
def cmd_remove(m):
    parts = m.text.split()
    if len(parts) < 2:
        bot.reply_to(
            m,
            "❗ **Usage:** `/remove <product_url_or_id>`"
        )
        return

    ref = parts[1].strip()
    ok = remove_from_watch(ref)

    bot.reply_to(
        m,
        "🗑 **Removed from watchlist**"
        if ok else
        "❌ Not found in watchlist"
    )


@bot.message_handler(commands=['watchlist'])
def cmd_watchlist(m):
    wl = list_watch()

    if not wl:
        bot.reply_to(m, "📭 **Watchlist is empty.**")
        return

    msg = "📋 **CURRENT WATCHLIST**\n\n"

    for p in wl:
        msg += (
            f"• 🆔 `{p['product_id']}`\n"
            f"  🔗 Ref: {p['ref']}\n"
            f"  🎟 Voucher: `{p.get('voucher', '')}`\n"
            f"  ⚙ Active: `{p.get('active')}`\n"
            f"  📝 Last status: {p.get('last_status')}\n\n"
        )

    bot.reply_to(m, msg)


@bot.message_handler(commands=['monitor'])
def cmd_monitor(m):
    parts = m.text.split()
    if len(parts) < 2:
        bot.reply_to(
            m,
            "❗ **Usage:** `/monitor start | stop`"
        )
        return

    sub = parts[1].lower()

    if sub == "start":
        if MONITOR_RUNNING.is_set():
            bot.reply_to(m, "⚠️ **Monitor is already running**")
            return

        threading.Thread(target=monitor_loop, daemon=True).start()
        bot.reply_to(m, "🚀 **Monitor started**")

    elif sub == "stop":
        if not MONITOR_RUNNING.is_set():
            bot.reply_to(m, "⚠️ **Monitor is not running**")
            return

        MONITOR_RUNNING.clear()
        bot.reply_to(m, "🛑 **Monitor stopping...**")

    else:
        bot.reply_to(m, "❌ Invalid argument. Use `start` or `stop`.")


@bot.message_handler(commands=['buy'])
def cmd_buy(m):
    parts = m.text.split()
    if len(parts) < 2:
        bot.reply_to(
            m,
            "❗ **Usage:** `/buy <product_url_or_id> [voucher]`"
        )
        return

    ref = parts[1].strip()
    voucher = parts[2].strip() if len(parts) >= 3 else ""

    bot.reply_to(
        m,
        f"⚡ **Attempting Immediate Buy** → `{ref}`"
    )

    threading.Thread(
        target=attempt_buy_once,
        args=(ref, voucher),
        daemon=True
    ).start()
 

def parse_payment_success(html_text):
    if not html_text:
        return None

    # 1) Find the paymentEngineCallbackData input value
    m = re.search(r'name=["\']paymentEngineCallbackData["\']\s+value=["\']([^"\']+)["\']', html_text, flags=re.I)
    if m:
        raw_val = m.group(1)

   
        try:
            candidate = html.unescape(raw_val)
        except Exception:
            candidate = raw_val

        # 3) Try a few JSON decode attempts
        data = None
        attempts = [
            lambda s: json.loads(s),
            lambda s: json.loads(s.replace('\\"', '"')),
            lambda s: json.loads(s.replace("'", '"')),
        ]
        for fn in attempts:
            try:
                data = fn(candidate)
                break
            except Exception:
                continue

        # Additional attempt: URL-unquote then json.loads
        if data is None:
            try:
                import urllib.parse as _up
                dec = _up.unquote(candidate)
                data = json.loads(dec)
            except Exception:
                data = None

        if data:
            tx_info = (data.get("transactionInformation") or {})
            order_info = (data.get("order") or {})
            status = tx_info.get("transactionStatus") or order_info.get("status") or data.get("status")
            order_id = order_info.get("orderId") or data.get("orderId")
            payable = order_info.get("netPayableAmount", order_info.get("amount") or data.get("amount"))
            instrument = (tx_info.get("paymentInformation") or {}).get("paymentInstrument") or (tx_info.get("paymentInformation") or {}).get("paymentInstrumentInstanceCode")
            return {"status": status, "order_id": order_id, "amount": payable, "instrument": instrument}

    # 4) Fallback: search for JSON blocks containing orderId / transactionStatus
    for m in re.finditer(r'\{[^}{]{20,5000}\}', html_text, flags=re.S):
        block = m.group(0)
        if "orderId" in block or "transactionStatus" in block:
            try:
                j = json.loads(block)
                tx_info = (j.get("transactionInformation") or {})
                order_info = (j.get("order") or {})
                status = tx_info.get("transactionStatus") or order_info.get("status") or j.get("status")
                order_id = order_info.get("orderId") or j.get("orderId")
                payable = order_info.get("netPayableAmount", order_info.get("amount") or j.get("amount"))
                instrument = (tx_info.get("paymentInformation") or {}).get("paymentInstrument")
                return {"status": status, "order_id": order_id, "amount": payable, "instrument": instrument}
            except Exception:
                continue

    # 5) Last resort: detect plain SUCCESS and nearby order number
    if re.search(r'\bSUCCESS\b', html_text, flags=re.I):
        mo = re.search(r'order[_\s\-]?id[:=\s]*([A-Z0-9\-]{4,40})', html_text, flags=re.I)
        order_id = mo.group(1) if mo else None
        return {"status": "SUCCESS", "order_id": order_id, "amount": None, "instrument": None}

    return None
    

def run_bot():
    logger.info("Starting Telebot polling...")
    try:
        bot.infinity_polling()
    except Exception as e:
      
        logger.exception("Telebot crashed: %s", e)
        try:
            tg_send(f"⚠ Telebot crashed: {e}")
        except:
            pass
       
        raise

if __name__ == "__main__":
    run_bot()