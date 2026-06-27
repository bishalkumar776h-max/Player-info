# app.py - Free Fire API (Fast Parallel Region Check)
# ⚡ Developed by: BISHAL & SENKU

import time
import httpx
import json
import os
import sys
import re
import concurrent.futures
from flask import Flask, request, jsonify
from flask_cors import CORS
from google.protobuf import json_format
from Crypto.Cipher import AES
import base64
import pickle
from datetime import datetime

# ============= CREDITS =============
# ⚡ DEVELOPED BY: BISHAL & SENKU
# 🔥 Free Fire API Server v3.0
# ====================================

# ============= PATH FIX =============
current_dir = os.path.dirname(os.path.abspath(__file__))
proto_dir = os.path.join(current_dir, 'proto')
if proto_dir not in sys.path:
    sys.path.insert(0, proto_dir)

try:
    from proto import FreeFire_pb2, main_pb2, AccountPersonalShow_pb2
    print("✅ Proto files imported successfully")
except ImportError:
    try:
        import FreeFire_pb2, main_pb2, AccountPersonalShow_pb2
        print("✅ Proto files imported directly")
    except ImportError as e:
        print(f"❌ Proto import error: {e}")
        FreeFire_pb2 = None
        main_pb2 = None
        AccountPersonalShow_pb2 = None

# === Settings ===
MAIN_KEY = base64.b64decode('WWcmdGMlREV1aDYlWmNeOA==')
MAIN_IV = base64.b64decode('Nm95WkRyMjJFM3ljaGpNJQ==')
RELEASEVERSION = "OB54"
USERAGENT = "UnityPlayer/2022.3.47f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)"
REGION_PRIORITY = ["ME", "BD", "IND", "SG", "ID", "TH", "VN", "PK", "BR", "US", "EU"]
SUPPORTED_REGIONS = set(REGION_PRIORITY)
TOKEN_CACHE_FILE = 'token_cache.pkl'
REQUEST_CACHE_FILE = 'request_cache.pkl'
CACHE_TTL = 300

# ======================== CREATE FLASK APP ==========================
app = Flask(__name__)
CORS(app)
token_cache = {}
request_cache = {}

# ======================== LOAD TOKENS ==========================
def load_cached_tokens():
    try:
        if os.path.exists(TOKEN_CACHE_FILE):
            with open(TOKEN_CACHE_FILE, 'rb') as f:
                saved = pickle.load(f)
                now = time.time()
                for r, info in saved.items():
                    if info.get('expires_at', 0) > now:
                        token_cache[r] = info
                        print(f"✅ Loaded cached token: {r}")
    except Exception as e:
        print(f"❌ Load tokens error: {e}")

def save_cached_tokens():
    try:
        with open(TOKEN_CACHE_FILE, 'wb') as f:
            pickle.dump(dict(token_cache), f)
    except Exception as e:
        print(f"❌ Save tokens error: {e}")

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
                print(f"✅ Loaded {len(request_cache)} cached requests")
    except Exception as e:
        print(f"❌ Load request cache error: {e}")
        request_cache = {}

def save_request_cache():
    try:
        with open(REQUEST_CACHE_FILE, 'wb') as f:
            pickle.dump(request_cache, f)
    except Exception as e:
        print(f"❌ Save request cache error: {e}")

def get_cached_response(uid):
    if uid in request_cache:
        cached = request_cache[uid]
        if cached.get('expires_at', 0) > time.time():
            print(f"📦 Cache hit for UID: {uid}")
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

# === Helper Functions (SYNC) ===
def pad(text: bytes) -> bytes:
    n = AES.block_size - (len(text) % AES.block_size)
    return text + bytes([n] * n)

def aes_cbc_encrypt(key, iv, plaintext):
    return AES.new(key, AES.MODE_CBC, iv).encrypt(pad(plaintext))

def json_to_proto_sync(json_data, proto_message):
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

def get_access_token_sync(account: str):
    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    payload = account + "&response_type=token&client_type=2&client_secret=2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3&client_id=100067"
    headers = {'User-Agent': USERAGENT, 'Content-Type': "application/x-www-form-urlencoded"}
    for attempt in range(3):
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(url, data=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("access_token"), data.get("open_id")
                else:
                    print(f"⚠️ Token API attempt {attempt+1}: {resp.status_code}")
                    time.sleep(2)
        except Exception as e:
            print(f"⚠️ Token API error attempt {attempt+1}: {e}")
            time.sleep(2)
    return None, None

def generate_token_sync(region: str):
    try:
        account = get_account_credentials(region)
        token_val, open_id = get_access_token_sync(account)
        if not token_val or not open_id:
            return None
        
        body = json.dumps({
            "open_id": open_id,
            "open_id_type": "4",
            "login_token": token_val,
            "orign_platform_type": "4"
        })
        
        proto_bytes = json_to_proto_sync(body, FreeFire_pb2.LoginReq())
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
        
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, data=payload, headers=headers)
            
            if resp.status_code != 200:
                print(f"❌ MajorLogin {resp.status_code} for {region}")
                return None
            
            login_res = FreeFire_pb2.LoginRes()
            login_res.ParseFromString(resp.content)
            msg = json.loads(json_format.MessageToJson(login_res))
            
            token_info = {
                'token': f"Bearer {msg.get('token','0')}",
                'region': msg.get('lockRegion','0'),
                'server_url': msg.get('serverUrl','0'),
                'expires_at': time.time() + 25200
            }
            print(f"✅ Token generated: {region}")
            return token_info
            
    except Exception as e:
        print(f"❌ generate_token error [{region}]: {e}")
        return None

def get_token(region: str):
    if region in token_cache:
        info = token_cache[region]
        if info.get('expires_at', 0) > time.time():
            return info
    
    token = generate_token_sync(region)
    if token:
        token_cache[region] = token
        save_cached_tokens()
        return token
    return None

def GetAccountInformationSync(uid, region):
    try:
        token_info = get_token(region)
        if not token_info:
            return None
        
        token = token_info['token']
        server_url = token_info['server_url']
        payload = json_to_proto_sync(json.dumps({'a': uid, 'b': '7'}), main_pb2.GetPlayerPersonalShow())
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
        
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(server_url + '/GetPlayerPersonalShow', data=data_enc, headers=headers)
            if resp.status_code != 200:
                return None
            
            account_info = AccountPersonalShow_pb2.AccountPersonalShowInfo()
            account_info.ParseFromString(resp.content)
            result = json.loads(json_format.MessageToJson(account_info))
            return result
    except Exception as e:
        print(f"❌ GetAccountInformation error for {region}: {e}")
        return None

def get_player_level(data):
    if not data:
        return 0
    basic = data.get("basicInfo", {})
    return int(basic.get("level", 0))

# ======================== PARALLEL REGION CHECK ==========================
def check_region_parallel(uid, region):
    """Check single region - for parallel execution"""
    try:
        data = GetAccountInformationSync(uid, region)
        if data:
            level = get_player_level(data)
            return {
                'region': region,
                'data': data,
                'level': level,
                'success': True
            }
        else:
            return {
                'region': region,
                'data': None,
                'level': 0,
                'success': False
            }
    except Exception as e:
        print(f"❌ {region} error: {e}")
        return {
            'region': region,
            'data': None,
            'level': 0,
            'success': False
        }

def check_all_regions_parallel(uid):
    """Check ALL regions in PARALLEL and return highest level"""
    print(f"\n🚀 Checking ALL {len(REGION_PRIORITY)} regions in PARALLEL...")
    start_time = time.time()
    
    results = []
    
    # Use ThreadPoolExecutor for parallel execution
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        # Submit all region checks
        future_to_region = {
            executor.submit(check_region_parallel, uid, region): region 
            for region in REGION_PRIORITY
        }
        
        # Collect results
        for future in concurrent.futures.as_completed(future_to_region):
            region = future_to_region[future]
            try:
                result = future.result()
                results.append(result)
                if result['success']:
                    print(f"✅ {region}: Level {result['level']}")
                else:
                    print(f"❌ {region}: No data")
            except Exception as e:
                print(f"❌ {region}: Error - {e}")
                results.append({
                    'region': region,
                    'data': None,
                    'level': 0,
                    'success': False
                })
    
    elapsed = time.time() - start_time
    print(f"⏱️ All regions checked in {elapsed:.2f} seconds")
    
    # Filter successful results
    successful = [r for r in results if r['success']]
    
    if successful:
        # Sort by level (highest first)
        successful.sort(key=lambda x: x['level'], reverse=True)
        best = successful[0]
        print(f"\n🏆 BEST REGION: {best['region']} (Level {best['level']})")
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
            "parallel_region_check": True,
            "highest_level_selection": True,
            "all_regions_checked": True,
            "response_caching": True
        },
        "endpoints": {
            "/get": {
                "method": "GET",
                "params": {
                    "uid": "required - Free Fire UID"
                },
                "example": "/get?uid=1576195175",
                "description": "Checks ALL regions in parallel and returns highest level"
            },
            "/status": "GET - Token status",
            "/refresh": "GET - Force refresh tokens",
            "/stats": "GET - API statistics"
        }
    })

@app.route('/get')
def get_account_info():
    uid = request.args.get('uid')
    
    if not uid:
        return jsonify({
            "error": "UID required",
            "credit": "Developed by BISHAL & SENKU",
            "example": "/get?uid=1576195175"
        }), 400
    
    if not re.match(r'^\d{5,15}$', uid):
        return jsonify({
            "error": "Invalid UID",
            "message": "UID must be 5-15 digits only",
            "credit": "Developed by BISHAL & SENKU"
        }), 400
    
    # Check cache
    cached_data = get_cached_response(uid)
    if cached_data:
        return jsonify(cached_data)
    
    print(f"\n🔍 Processing UID: {uid}")
    
    # Check ALL regions in PARALLEL
    best_region, best_data = check_all_regions_parallel(uid)
    
    if best_data:
        basic = best_data.get("basicInfo", {})
        clan = best_data.get("clanBasicInfo", {})
        profile = best_data.get("profileInfo", {})
        
        response = {
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            "region_used": best_region,
            "credit": "Developed by BISHAL & SENKU",
            "all_regions_checked": True,
            "AccountInfo": {
                "AccountName": basic.get("nickname", "Unknown"),
                "AccountLevel": str(basic.get("level", "0")),
                "AccountRegion": basic.get("region", "Unknown"),
                "AccountLikes": str(basic.get("liked", "0")),
                "AccountEXP": str(basic.get("exp", "0")),
                "BrRankPoint": str(basic.get("rankingPoints", "0")),
                "CsRankPoint": str(basic.get("csRankingPoints", "0")),
                "GuildName": clan.get("clanName", "No Guild"),
                "EquippedWeapon": basic.get("weaponSkinShows", []),
                "EquippedOutfit": profile.get("clothes", [])
            }
        }
        
        cache_response(uid, response)
        return jsonify(response)
    
    return jsonify({
        "error": "Player not found in any region",
        "credit": "Developed by BISHAL & SENKU"
    }), 404

@app.route('/status')
def token_status():
    status = {}
    for region, info in token_cache.items():
        expires_in = info['expires_at'] - time.time()
        status[region] = {
            "has_token": True,
            "expires_in": f"{expires_in/3600:.1f} hours",
            "is_valid": expires_in > 0
        }
    
    return jsonify({
        "credit": "Developed by BISHAL & SENKU",
        "total_tokens": len(token_cache),
        "cached_requests": len(request_cache),
        "tokens": status
    })

@app.route('/refresh')
def refresh_tokens():
    for region in REGION_PRIORITY:
        token_cache.pop(region, None)
        token = generate_token_sync(region)
        if token:
            token_cache[region] = token
    save_cached_tokens()
    return jsonify({
        "status": "refreshed",
        "count": len(token_cache),
        "credit": "Developed by BISHAL & SENKU"
    })

@app.route('/stats')
def api_stats():
    return jsonify({
        "credit": "Developed by BISHAL & SENKU",
        "timestamp": datetime.now().isoformat(),
        "stats": {
            "cached_responses": len(request_cache),
            "active_tokens": len(token_cache),
            "supported_regions": len(SUPPORTED_REGIONS)
        },
        "regions": {
            "priority": REGION_PRIORITY,
            "available": list(token_cache.keys())
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

# ======================== LOAD CACHE ==========================
load_cached_tokens()
load_request_cache()

print("🎯 Generating tokens...")
for region in REGION_PRIORITY:
    if region not in token_cache:
        token = generate_token_sync(region)
        if token:
            token_cache[region] = token
            save_cached_tokens()
            print(f"✅ Token generated for {region}")

print(f"✅ Total tokens: {len(token_cache)}")

# ======================== FOR VERCEL ==========================
app = app

if __name__ == '__main__':
    print("=" * 55)
    print("🚀 Free Fire API - PARALLEL VERSION")
    print("⚡ Developed by: BISHAL & SENKU")
    print("🔥 Checks ALL regions in PARALLEL")
    print("🏆 Returns highest level region")
    print("=" * 55)
    app.run(host='0.0.0.0', port=5000, debug=False)