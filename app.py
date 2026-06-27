# app.py - Free Fire API Server with Enhanced Features
# Run locally: python app.py
# Run production: gunicorn app:app --workers 1 --threads 4 --timeout 120

import asyncio
import time
import httpx
import json
import os
import sys
import threading
import re
from flask import Flask, request, jsonify
from flask_cors import CORS
from cachetools import TTLCache
from google.protobuf import json_format
from Crypto.Cipher import AES
import base64
import pickle
from datetime import datetime

# ============= CREDITS =============
# DEVELOPED BY: BISHAL & SENKU
# Free Fire API Server v3.0
# Special Thanks to the FF Community
# ====================================

# ============= PATH FIX =============
current_dir = os.path.dirname(os.path.abspath(__file__))
proto_dir = os.path.join(current_dir, 'proto')
if proto_dir not in sys.path:
    sys.path.insert(0, proto_dir)

try:
    from proto import FreeFire_pb2, main_pb2, AccountPersonalShow_pb2
    print("Proto files imported successfully")
except ImportError:
    try:
        import FreeFire_pb2, main_pb2, AccountPersonalShow_pb2
        print("Proto files imported directly")
    except ImportError as e:
        print(f"Proto import error: {e}")
        sys.exit(1)

# === Settings ===
MAIN_KEY = base64.b64decode('WWcmdGMlREV1aDYlWmNeOA==')
MAIN_IV = base64.b64decode('Nm95WkRyMjJFM3ljaGpNJQ==')
RELEASEVERSION = "OB54"
USERAGENT = "UnityPlayer/2022.3.47f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)"
REGION_PRIORITY = ["ME", "BD", "IND", "SG", "ID", "TH", "VN", "PK", "BR", "US", "EU"]
SUPPORTED_REGIONS = set(REGION_PRIORITY)
# Use /tmp for cache files — works on Render, Railway, etc.
TOKEN_CACHE_FILE = '/tmp/token_cache.pkl'
REQUEST_CACHE_FILE = '/tmp/request_cache.pkl'
CACHE_TTL = 300  # 5 minutes

app = Flask(__name__)
CORS(app)
cache = TTLCache(maxsize=200, ttl=CACHE_TTL)
request_cache = {}

# ======================== REQUEST CACHE ==========================
def load_request_cache():
    global request_cache
    try:
        if os.path.exists(REQUEST_CACHE_FILE):
            with open(REQUEST_CACHE_FILE, 'rb') as f:
                request_cache = pickle.load(f)
                now = time.time()
                request_cache = {k: v for k, v in request_cache.items()
                                if v.get('expires_at', 0) > now}
                print(f"Loaded {len(request_cache)} cached requests")
    except Exception as e:
        print(f"Load request cache error: {e}")
        request_cache = {}

def save_request_cache():
    try:
        with open(REQUEST_CACHE_FILE, 'wb') as f:
            pickle.dump(request_cache, f)
    except Exception as e:
        print(f"Save request cache error: {e}")

def get_cached_response(uid):
    if uid in request_cache:
        cached = request_cache[uid]
        if cached.get('expires_at', 0) > time.time():
            print(f"Cache hit for UID: {uid}")
            return cached.get('data')
        else:
            del request_cache[uid]
            save_request_cache()
    return None

def cache_response(uid, data):
    request_cache[uid] = {
        'data': data,
        'expires_at': time.time() + CACHE_TTL,
        'cached_at': time.time()
    }
    save_request_cache()

# === Token Manager ===
class TokenManager:
    def __init__(self):
        self.tokens = {}
        self.lock = threading.Lock()
        self.region_stats = {}
        self.load_tokens()

    def load_tokens(self):
        try:
            if os.path.exists(TOKEN_CACHE_FILE):
                with open(TOKEN_CACHE_FILE, 'rb') as f:
                    saved = pickle.load(f)
                    now = time.time()
                    for r, info in saved.items():
                        if info.get('expires_at', 0) > now:
                            self.tokens[r] = info
                            print(f"Loaded cached token: {r}")
        except Exception as e:
            print(f"Load tokens error: {e}")

    def save_tokens(self):
        try:
            with open(TOKEN_CACHE_FILE, 'wb') as f:
                pickle.dump(dict(self.tokens), f)
        except Exception as e:
            print(f"Save tokens error: {e}")

    def get_token(self, region: str):
        """Thread-safe synchronous token getter using asyncio.run()."""
        with self.lock:
            info = self.tokens.get(region)
            if info and info.get('expires_at', 0) > time.time():
                return info

        # Generate fresh token (outside lock to avoid blocking other threads)
        new_token = asyncio.run(self._generate_token(region))
        if new_token:
            with self.lock:
                self.tokens[region] = new_token
                self.region_stats[region] = {'success': True, 'last_update': time.time()}
            self.save_tokens()
            return new_token
        else:
            with self.lock:
                self.region_stats[region] = {'success': False, 'last_update': time.time()}
            return None

    async def _generate_token(self, region: str):
        try:
            account = get_account_credentials(region)
            token_val, open_id = await get_access_token(account)
            if not token_val or not open_id:
                return None

            body = json.dumps({
                "open_id": open_id,
                "open_id_type": "4",
                "login_token": token_val,
                "orign_platform_type": "4"
            })

            proto_bytes = await json_to_proto(body, FreeFire_pb2.LoginReq())
            payload = aes_cbc_encrypt(MAIN_KEY, MAIN_IV, proto_bytes)

            url = "https://loginbp.ggpolarbear.com/MajorLogin"
            headers = {
                'User-Agent': USERAGENT,
                'Connection': 'Keep-Alive',
                'Accept-Encoding': 'deflate, gzip',
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': '*/*',
                'X-Unity-Version': '2022.3.47f1',
                'X-GA': 'v1 1',
                'ReleaseVersion': RELEASEVERSION
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, data=payload, headers=headers)
                if resp.status_code != 200:
                    print(f"MajorLogin {resp.status_code} for {region}")
                    return None

                login_res = FreeFire_pb2.LoginRes()
                login_res.ParseFromString(resp.content)
                msg = json.loads(json_format.MessageToJson(login_res))

                token_info = {
                    'token': f"Bearer {msg.get('token','0')}",
                    'region': msg.get('lockRegion', '0'),
                    'server_url': msg.get('serverUrl', '0'),
                    'expires_at': time.time() + 25200  # 7 hours
                }
                print(f"Token generated: {region}")
                return token_info

        except Exception as e:
            print(f"generate_token error [{region}]: {e}")
            return None

    def refresh_all_tokens(self):
        for region in REGION_PRIORITY:
            try:
                self.get_token(region)
            except Exception as e:
                print(f"Refresh error [{region}]: {e}")
        self.save_tokens()

    def get_best_region(self):
        if not self.tokens:
            return None
        valid_regions = []
        with self.lock:
            for region, info in self.tokens.items():
                if info.get('expires_at', 0) > time.time():
                    stats = self.region_stats.get(region, {})
                    if stats.get('success', True):
                        valid_regions.append(region)
        for r in REGION_PRIORITY:
            if r in valid_regions:
                return r
        return None


# === Helper Functions ===
def pad(text: bytes) -> bytes:
    n = AES.block_size - (len(text) % AES.block_size)
    return text + bytes([n] * n)

def aes_cbc_encrypt(key, iv, plaintext):
    return AES.new(key, AES.MODE_CBC, iv).encrypt(pad(plaintext))

async def json_to_proto(json_data, proto_message):
    json_format.ParseDict(json.loads(json_data), proto_message)
    return proto_message.SerializeToString()

def get_account_credentials(region: str) -> str:
    r = region.upper()
    if r == "ME":
        return "uid=4269012488&password=MG24_GAMER_U27YB_BY_SPIDEERIO_GAMING_0PNCN"
    elif r == "BD":
        return "uid=4270778393&password=MG24_GAMER_9NMYG_BY_SPIDEERIO_GAMING_FXK8R"
    elif r == "IND":
        return "uid=4269013803&password=MG24_GAMER_XSBOS_BY_SPIDEERIO_GAMING_TE5NG"
    elif r in {"BR", "US", "SAC"}:
        return "uid=4269012488&password=MG24_GAMER_U27YB_BY_SPIDEERIO_GAMING_0PNCN"
    else:
        return "uid=4269012488&password=MG24_GAMER_U27YB_BY_SPIDEERIO_GAMING_0PNCN"

async def get_access_token(account: str):
    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    payload = (account +
               "&response_type=token&client_type=2"
               "&client_secret=2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3"
               "&client_id=100067")
    headers = {'User-Agent': USERAGENT, 'Content-Type': "application/x-www-form-urlencoded"}
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, data=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("access_token"), data.get("open_id")
                else:
                    print(f"Token API attempt {attempt+1}: {resp.status_code}")
                    await asyncio.sleep(2)
        except Exception as e:
            print(f"Token API error attempt {attempt+1}: {e}")
            await asyncio.sleep(2)
    return None, None

async def _get_account_info_async(uid, region):
    try:
        with token_manager.lock:
            token_info = token_manager.tokens.get(region)

        if not token_info or token_info.get('expires_at', 0) <= time.time():
            return None

        token = token_info['token']
        server_url = token_info['server_url']
        payload = await json_to_proto(
            json.dumps({'a': uid, 'b': '7'}),
            main_pb2.GetPlayerPersonalShow()
        )
        data_enc = aes_cbc_encrypt(MAIN_KEY, MAIN_IV, payload)
        headers = {
            'User-Agent': USERAGENT,
            'Connection': "Keep-Alive",
            'Accept-Encoding': "gzip",
            'Content-Type': "application/octet-stream",
            'Expect': "100-continue",
            'Authorization': token,
            'X-Unity-Version': "2022.3.47f1",
            'X-GA': "v1 1",
            'ReleaseVersion': RELEASEVERSION
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                server_url + '/GetPlayerPersonalShow',
                data=data_enc,
                headers=headers
            )
            if resp.status_code != 200:
                return None

            account_info = AccountPersonalShow_pb2.AccountPersonalShowInfo()
            account_info.ParseFromString(resp.content)
            result = json.loads(json_format.MessageToJson(account_info))
            return result
    except Exception as e:
        print(f"GetAccountInformation error for {region}: {e}")
        return None

def get_player_level(data):
    if not data:
        return 0
    basic = data.get("basicInfo", {})
    return int(basic.get("level", 0))

def format_response(data, region_used=None):
    if not data:
        return {"error": "No data"}
    basic = data.get("basicInfo", {})
    clan = data.get("clanBasicInfo", {})
    profile = data.get("profileInfo", {})

    def fmt_time(ts):
        if ts and ts != "0":
            try:
                return datetime.fromtimestamp(int(ts)).strftime('%Y-%m-%d %H:%M:%S')
            except:
                return str(ts)
        return "0"

    return {
        "status": "success",
        "timestamp": datetime.now().isoformat(),
        "region_used": region_used,
        "credit": "Developed by BISHAL & SENKU",
        "AccountInfo": {
            "AccountAvatarId": str(basic.get("headPic", "0")),
            "AccountBPBadges": str(basic.get("badgeCnt", "0")),
            "AccountBPID": str(basic.get("badgeId", "0")),
            "AccountBannerId": str(basic.get("bannerId", "0")),
            "AccountCreateTime": fmt_time(basic.get("createAt", "0")),
            "AccountEXP": str(basic.get("exp", "0")),
            "AccountLastLogin": fmt_time(basic.get("lastLoginAt", "0")),
            "AccountLevel": str(basic.get("level", "0")),
            "AccountLikes": str(basic.get("liked", "0")),
            "AccountName": basic.get("nickname", "Unknown"),
            "AccountRegion": basic.get("region", "Unknown"),
            "AccountSeasonId": str(basic.get("seasonId", "0")),
            "AccountType": str(basic.get("accountType", "0")),
            "BrMaxRank": str(basic.get("maxRank", "0")),
            "BrRankPoint": str(basic.get("rankingPoints", "0")),
            "CsMaxRank": str(basic.get("csMaxRank", "0")),
            "CsRankPoint": str(basic.get("csRankingPoints", "0")),
            "EquippedWeapon": basic.get("weaponSkinShows", []),
            "ReleaseVersion": basic.get("releaseVersion", RELEASEVERSION),
            "ShowBrRank": str(basic.get("showBrRank", "0")),
            "ShowCsRank": str(basic.get("showCsRank", "0")),
            "Title": str(basic.get("title", "0")),
            "HasElitePass": str(basic.get("hasElitePass", "0")),
            "IsDeleted": str(basic.get("isDeleted", "0")),
            "PeriodicRank": str(basic.get("periodicRank", "0")),
            "PeriodicRankPoints": str(basic.get("periodicRankingPoints", "0")),
            "BrPeakRankPos": str(basic.get("peakRankPos", "0")),
            "CsPeakRankPos": str(basic.get("csPeakRankPos", "0")),
            "IsBanned": str(basic.get("isBanned", "0")),
            "BanReason": basic.get("banReason", ""),
        },
        "AccountProfileInfo": {
            "EquippedOutfit": profile.get("clothes", []),
        },
        "GuildInfo": {
            "GuildCapacity": str(clan.get("capacity", "0")),
            "GuildID": str(clan.get("clanId", "0")),
            "GuildLevel": str(clan.get("clanLevel", "0")),
            "GuildMember": str(clan.get("memberNum", "0")),
            "GuildName": clan.get("clanName", "No Guild"),
            "GuildOwner": str(clan.get("captainId", "0")),
            "HonorPoint": str(clan.get("honorPoint", "0")),
        },
        "socialinfo": {}
    }

# ======================== SMART REGION DETECTION ==========================
async def _check_all_regions_parallel(uid):
    """Check all regions in parallel and return the best result."""
    with token_manager.lock:
        regions_to_check = [
            r for r in REGION_PRIORITY
            if r in token_manager.tokens
            and token_manager.tokens[r].get('expires_at', 0) > time.time()
        ]

    if not regions_to_check:
        print("No tokens available yet")
        return None, None

    print(f"Checking {len(regions_to_check)} regions in parallel...")
    start_time = time.time()

    tasks = [_get_account_info_async(uid, region) for region in regions_to_check]
    results_raw = await asyncio.gather(*tasks, return_exceptions=True)

    results = []
    for region, data in zip(regions_to_check, results_raw):
        if isinstance(data, Exception) or not data:
            print(f"{region}: No data")
            continue
        level = get_player_level(data)
        results.append({'region': region, 'data': data, 'level': level})
        print(f"{region}: Level {level}")

    elapsed = time.time() - start_time
    print(f"All regions checked in {elapsed:.2f}s")

    if results:
        best = max(results, key=lambda x: x['level'])
        print(f"Best region: {best['region']} (Level {best['level']})")
        return best['region'], best['data']

    return None, None

# ======================== FLASK ROUTES ==========================
@app.route('/')
def home():
    return jsonify({
        "status": "online",
        "service": "Free Fire API",
        "version": "3.0",
        "release": RELEASEVERSION,
        "credit": "Developed by BISHAL & SENKU",
        "features": {
            "smart_region_detection": True,
            "parallel_requests": True,
            "response_caching": True,
            "token_auto_refresh": True
        },
        "endpoints": {
            "/get": {
                "method": "GET",
                "params": {
                    "uid": "required - Free Fire UID",
                    "region": "optional - Force specific region"
                },
                "example": "/get?uid=123456789"
            },
            "/status": "GET - Token and region status",
            "/refresh": "GET - Force refresh tokens",
            "/stats": "GET - API statistics",
            "/clear_cache": "GET - Clear response cache"
        }
    })

@app.route('/get')
def get_account_info():
    uid = request.args.get('uid')
    region_param = request.args.get('region', '').upper()

    if not uid:
        return jsonify({
            "error": "UID required",
            "message": "Please provide a Free Fire UID",
            "credit": "Developed by BISHAL & SENKU",
            "example": "/get?uid=123456789"
        }), 400

    if not re.match(r'^\d{5,15}$', uid):
        return jsonify({
            "error": "Invalid UID",
            "message": "UID must be 5-15 digits only",
            "credit": "Developed by BISHAL & SENKU"
        }), 400

    cached_data = get_cached_response(uid)
    if cached_data:
        cached_data['from_cache'] = True
        return jsonify(cached_data)

    print(f"Processing info for UID: {uid}")

    if region_param and region_param in SUPPORTED_REGIONS:
        print(f"User specified region: {region_param}")
        token_info = token_manager.get_token(region_param)
        if not token_info:
            return jsonify({
                "error": "Token unavailable for region",
                "credit": "Developed by BISHAL & SENKU"
            }), 503

        data = asyncio.run(_get_account_info_async(uid, region_param))
        if data:
            response = format_response(data, region_param)
            response['from_cache'] = False
            cache_response(uid, response)
            return jsonify(response)
        else:
            return jsonify({
                "error": "Player not found in specified region",
                "credit": "Developed by BISHAL & SENKU"
            }), 404

    # Smart detection — check all regions in parallel
    best_region, best_data = asyncio.run(_check_all_regions_parallel(uid))

    if best_data:
        response = format_response(best_data, best_region)
        response['from_cache'] = False
        cache_response(uid, response)
        return jsonify(response)

    return jsonify({
        "error": "Player not found in any region. Tokens may still be loading — try again in 30 seconds.",
        "credit": "Developed by BISHAL & SENKU"
    }), 404

@app.route('/status')
def token_status():
    with token_manager.lock:
        tokens_copy = dict(token_manager.tokens)

    status = {}
    for region, info in tokens_copy.items():
        expires_in = info['expires_at'] - time.time()
        url_preview = info.get('server_url', '')
        status[region] = {
            "has_token": True,
            "expires_in": f"{expires_in / 3600:.1f} hours",
            "is_valid": expires_in > 0,
            "server_url": (url_preview[:50] + "...") if len(url_preview) > 50 else url_preview
        }

    return jsonify({
        "credit": "Developed by BISHAL & SENKU",
        "region_priority": REGION_PRIORITY,
        "total_tokens": len(tokens_copy),
        "cached_requests": len(request_cache),
        "tokens": status
    })

@app.route('/refresh')
def refresh_tokens():
    threading.Thread(target=token_manager.refresh_all_tokens, daemon=True).start()
    return jsonify({
        "status": "Token refresh started in background",
        "credit": "Developed by BISHAL & SENKU"
    })

@app.route('/stats')
def api_stats():
    return jsonify({
        "credit": "Developed by BISHAL & SENKU",
        "timestamp": datetime.now().isoformat(),
        "stats": {
            "cached_responses": len(request_cache),
            "active_tokens": len(token_manager.tokens) if token_manager else 0,
            "supported_regions": len(SUPPORTED_REGIONS)
        },
        "regions": {
            "priority": REGION_PRIORITY,
            "available": list(token_manager.tokens.keys()) if token_manager else []
        }
    })

@app.route('/clear_cache')
def clear_cache():
    global request_cache
    request_cache = {}
    save_request_cache()
    return jsonify({
        "status": "Cache cleared",
        "credit": "Developed by BISHAL & SENKU"
    })

# ======================== STARTUP ==========================
def _background_startup():
    """Generate tokens in background — server starts instantly, tokens load in ~30s."""
    print("Background token generation started...")
    load_request_cache()
    token_manager.refresh_all_tokens()
    print(f"All tokens ready: {list(token_manager.tokens.keys())}")

    # Auto-refresh every 6 hours
    while True:
        time.sleep(6 * 60 * 60)
        print("Auto-refreshing all tokens...")
        token_manager.refresh_all_tokens()

# Global token manager — initialised before any request
token_manager = TokenManager()

# Start background token generation immediately
_bg_thread = threading.Thread(target=_background_startup, daemon=True)
_bg_thread.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print("=" * 55)
    print("Free Fire API Server v3.0")
    print("Developed by: BISHAL & SENKU")
    print(f"Starting on port {port}")
    print("Tokens loading in background — ready in ~30s")
    print("=" * 55)
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
