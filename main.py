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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── НАСТРОЙКИ ───────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВАШ_BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@ВАШ_КАНАЛ")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "supersecret123")
SUBSCRIPTION_DAYS = 7
# ─────────────────────────────────────────────────────────────

KEYS_FILE = "keys.json"

def load_keys():
    if not os.path.exists(KEYS_FILE):
        return {}
    with open(KEYS_FILE) as f:
        return json.load(f)

def save_keys(keys):
    with open(KEYS_FILE, "w") as f:
        json.dump(keys, f, indent=2)

def gen_key():
    chars = string.ascii_uppercase + string.digits
    parts = [''.join(secrets.choice(chars) for _ in range(4)) for _ in range(3)]
    return '-'.join(parts)

# ─── МОДЕЛИ ──────────────────────────────────────────────────
class ActivateRequest(BaseModel):
    key: str

class AdminRequest(BaseModel):
    password: str
    count: int = 1

class RevokeRequest(BaseModel):
    password: str
    key: str

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
    return {"valid": True, "expires_at": entry["expires_at"]}


@app.get("/posts")
async def get_posts(key: str):
    keys = load_keys()
    k = key.upper().strip()
    if k not in keys:
        return {"valid": False}
    expires = datetime.fromisoformat(keys[k]["expires_at"])
    if datetime.now() > expires:
        return {"valid": False}

    # Получаем последние 20 постов из канала
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={"limit": 50, "allowed_updates": ["channel_post"]},
            timeout=10
        )
        data = r.json()

    posts = []
    if data.get("ok"):
        for update in reversed(data.get("result", [])):
            post = update.get("channel_post", {})
            if not post:
                continue

            chat = post.get("chat", {})
            if str(chat.get("username", "")) != CHANNEL_ID.lstrip("@") and \
               str(chat.get("id", "")) != CHANNEL_ID:
                continue

            item = {
                "id": post.get("message_id"),
                "date": post.get("date"),
                "text": post.get("text") or post.get("caption") or ""
            }

            # Фото — берём наибольшее
            if "photo" in post:
                photo = post["photo"][-1]
                file_id = photo["file_id"]
                async with httpx.AsyncClient() as c:
                    fr = await c.get(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                        params={"file_id": file_id}
                    )
                    fd = fr.json()
                    if fd.get("ok"):
                        path = fd["result"]["file_path"]
                        item["photo"] = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"

            # Видео
            if "video" in post:
                video = post["video"]
                file_id = video["file_id"]
                async with httpx.AsyncClient() as c:
                    fr = await c.get(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                        params={"file_id": file_id}
                    )
                    fd = fr.json()
                    if fd.get("ok"):
                        path = fd["result"]["file_path"]
                        item["video"] = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"

            if item.get("text") or item.get("photo") or item.get("video"):
                posts.append(item)

    return {"valid": True, "posts": posts[:20]}


@app.post("/admin/generate")
async def generate_keys(req: AdminRequest):
    if req.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Неверный пароль")
    keys = load_keys()
    new_keys = []
    for _ in range(min(req.count, 50)):
        k = gen_key()
        while k in keys:
            k = gen_key()
        expires = (datetime.now() + timedelta(days=SUBSCRIPTION_DAYS)).isoformat()
        keys[k] = {"expires_at": expires, "created_at": datetime.now().isoformat()}
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
            "days_left": max(0, (expires - now).days)
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
