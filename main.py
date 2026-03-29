import os
import secrets
import string
import httpx
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "supersecret123")

KEYS_FILE = "keys.json"
LAST_POST_FILE = "last_post.json"

def load_keys():
    if not os.path.exists(KEYS_FILE):
        return {}
    with open(KEYS_FILE) as f:
        return json.load(f)

def save_keys(keys):
    with open(KEYS_FILE, "w") as f:
        json.dump(keys, f, indent=2)

def load_last_post():
    if not os.path.exists(LAST_POST_FILE):
        return {}
    with open(LAST_POST_FILE) as f:
        return json.load(f)

def save_last_post(data):
    with open(LAST_POST_FILE, "w") as f:
        json.dump(data, f)

def gen_key():
    chars = string.ascii_uppercase + string.digits
    parts = [''.join(secrets.choice(chars) for _ in range(4)) for _ in range(3)]
    return '-'.join(parts)

def check_key(key: str):
    keys = load_keys()
    k = key.upper().strip()
    if k not in keys:
        return None, None
    entry = keys[k]
    expires = datetime.fromisoformat(entry["expires_at"])
    if datetime.now() > expires:
        return None, None
    return k, entry

# ─── МОДЕЛИ ──────────────────────────────────────────────────
class ActivateRequest(BaseModel):
    key: str
    device_id: str = ""

class AdminRequest(BaseModel):
    password: str
    count: int = 1
    days: int = 7

class RevokeRequest(BaseModel):
    password: str
    key: str

class PostsRequest(BaseModel):
    key: str
    device_id: str = ""
    last_id: int = 0

# ─── ЭНДПОИНТЫ ───────────────────────────────────────────────

@app.post("/activate")
async def activate(req: ActivateRequest):
    keys = load_keys()
    k = req.key.upper().strip()

    if k not in keys:
        return {"valid": False, "message": "Ключ не найден"}

    entry = keys[k]
    expires = datetime.fromisoformat(entry["expires_at"])

    if datetime.now() > expires:
        return {"valid": False, "message": "Срок действия ключа истёк"}

    # Привязка к устройству
    if entry.get("device_id") and req.device_id:
        if entry["device_id"] != req.device_id:
            return {"valid": False, "message": "Ключ уже используется на другом устройстве"}

    # Привязываем устройство при первой активации
    if req.device_id and not entry.get("device_id"):
        keys[k]["device_id"] = req.device_id
        keys[k]["activated_at"] = datetime.now().isoformat()
        save_keys(keys)

    return {"valid": True, "expires_at": entry["expires_at"]}


@app.get("/posts")
async def get_posts(key: str, device_id: str = "", last_id: int = 0):
    k, entry = check_key(key)
    if not k:
        return {"valid": False}

    # Проверка устройства
    if entry.get("device_id") and device_id and entry["device_id"] != device_id:
        return {"valid": False}

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={"limit": 100, "allowed_updates": ["channel_post"]},
            timeout=15
        )
        data = r.json()

    posts = []
    new_last_id = last_id

    if data.get("ok"):
        for update in reversed(data.get("result", [])):
            post = update.get("channel_post", {})
            if not post:
                continue

            chat = post.get("chat", {})
            chat_id = str(chat.get("id", ""))
            chat_username = str(chat.get("username", ""))
            channel = CHANNEL_ID.lstrip("@")

            if chat_username != channel and chat_id != CHANNEL_ID:
                continue

            msg_id = post.get("message_id", 0)
            if msg_id <= last_id:
                continue

            new_last_id = max(new_last_id, msg_id)

            item = {
                "id": msg_id,
                "date": post.get("date"),
                "text": post.get("text") or post.get("caption") or ""
            }

            if "photo" in post:
                file_id = post["photo"][-1]["file_id"]
                async with httpx.AsyncClient() as c:
                    fr = await c.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile", params={"file_id": file_id})
                    fd = fr.json()
                    if fd.get("ok"):
                        item["photo"] = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fd['result']['file_path']}"

            if "video" in post:
                file_id = post["video"]["file_id"]
                async with httpx.AsyncClient() as c:
                    fr = await c.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile", params={"file_id": file_id})
                    fd = fr.json()
                    if fd.get("ok"):
                        item["video"] = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fd['result']['file_path']}"

            if item.get("text") or item.get("photo") or item.get("video"):
                posts.append(item)

    return {"valid": True, "posts": posts[:20], "last_id": new_last_id}


@app.post("/admin/generate")
async def generate_keys(req: AdminRequest):
    if req.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Неверный пароль")
    keys = load_keys()
    new_keys = []
    days = max(1, min(req.days, 3650))
    for _ in range(min(req.count, 50)):
        k = gen_key()
        while k in keys:
            k = gen_key()
        expires = (datetime.now() + timedelta(days=days)).isoformat()
        keys[k] = {"expires_at": expires, "created_at": datetime.now().isoformat(), "device_id": "", "activated_at": ""}
        new_keys.append({"key": k, "expires_at": expires})
    save_keys(keys)
    return {"generated": new_keys}


@app.post("/admin/list")
async def list_keys(req: AdminRequest):
    if req.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Неверный пароль")
    keys = load_keys()
    now = datetime.now()
    result = []
    for k, v in keys.items():
        expires = datetime.fromisoformat(v["expires_at"])
        result.append({
            "key": k,
            "expires_at": v["expires_at"],
            "active": now < expires,
            "days_left": max(0, (expires - now).days),
            "activated": bool(v.get("device_id")),
            "activated_at": v.get("activated_at", "")
        })
    result.sort(key=lambda x: x["expires_at"], reverse=True)
    return {"keys": result}


@app.post("/admin/revoke")
async def revoke_key(req: RevokeRequest):
    if req.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Неверный пароль")
    keys = load_keys()
    k = req.key.upper().strip()
    if k not in keys:
        raise HTTPException(status_code=404, detail="Ключ не найден")
    del keys[k]
    save_keys(keys)
    return {"revoked": k}


@app.get("/")
async def root():
    return {"status": "ok", "service": "Channel Access API"}
