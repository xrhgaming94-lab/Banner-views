import io
import os
import asyncio
import base64
import httpx
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageDraw, ImageFont

# ================= ADJUSTMENT SETTINGS =================
AVATAR_ZOOM = 1.26
AVATAR_SHIFT_Y = 0
AVATAR_SHIFT_X = 0

BANNER_START_X = 0.25
BANNER_START_Y = 0.29
BANNER_END_X = 0.81
BANNER_END_Y = 0.65
# =======================================================

# Your Info API
INFO_API_URL = "https://infoapi.up.railway.app/player-info"

BASE64 = "aHR0cHM6Ly9jZG4uanNkZWxpdnIubmV0L2doL1NoYWhHQ3JlYXRvci9pY29uQG1haW4vUE5H"
info_URL = base64.b64decode(BASE64).decode("utf-8")

# ✅ Vercel-safe: same folder as app.py
BASE_DIR = os.path.dirname(__file__)

# ✅ Your existing fonts (root)
FONT_BOLD_PATH = os.path.join(BASE_DIR, "arial_unicode_bold.otf")
FONT_REGULAR_PATH = os.path.join(BASE_DIR, "NotoSansCherokee.ttf")  # regular fallback

timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)

client = httpx.AsyncClient(
    headers={"User-Agent": "Mozilla/5.0"},
    timeout=timeout,
    follow_redirects=True,
    limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
)

process_pool = ThreadPoolExecutor(max_workers=4)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    try:
        await client.aclose()
    except Exception:
        pass
    try:
        process_pool.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================= FONT LOADER (VERCEL SAFE) =================
def load_font(size: int, bold: bool = False):
    """
    Vercel pe Termux dirs/subprocess/fc-match work nahi karte.
    Isliye font files ko project me hi rakho (root).
    """
    path = FONT_BOLD_PATH if bold else FONT_REGULAR_PATH

    # Try requested font
    try:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    except Exception:
        pass

    # Fallback: try the other one
    try:
        other = FONT_REGULAR_PATH if bold else FONT_BOLD_PATH
        if os.path.exists(other):
            return ImageFont.truetype(other, size)
    except Exception:
        pass

    # Last fallback
    return ImageFont.load_default()


# ================= INFO FETCH =================
async def fetch_info(uid: str, retries: int = 3, delay: float = 0.6):
    url = f"{INFO_API_URL}?uid={uid}"
    last_err = None

    for attempt in range(1, retries + 1):
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
            raise HTTPException(status_code=502, detail=f"Info API Error: {resp.status_code}")
        except httpx.TimeoutException as e:
            last_err = e
            if attempt < retries:
                await asyncio.sleep(delay * attempt)
                continue
            raise HTTPException(status_code=504, detail="Info API timeout")
        except httpx.RequestError as e:
            last_err = e
            if attempt < retries:
                await asyncio.sleep(delay * attempt)
                continue
            raise HTTPException(status_code=502, detail="Info API request failed")

    raise HTTPException(status_code=502, detail=f"Info API failed: {last_err}")


async def fetch_image_bytes(item_id):
    if not item_id or str(item_id) == "0":
        return None

    url = f"{info_URL}/{item_id}.png"
    try:
        resp = await client.get(url)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception:
        pass
    return None


def bytes_to_image(img_bytes):
    if img_bytes:
        return Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    return Image.new("RGBA", (100, 100), (0, 0, 0, 0))


# ================= IMAGE PROCESS =================
def process_banner_image(data, avatar_bytes, banner_bytes, pin_bytes):
    avatar_img = bytes_to_image(avatar_bytes)
    banner_img = bytes_to_image(banner_bytes)
    pin_img = bytes_to_image(pin_bytes)

    level = str(data.get("AccountLevel", "0"))
    name = str(data.get("AccountName", "Unknown"))
    guild = str(data.get("GuildName", ""))

    TARGET_HEIGHT = 400

    # Avatar crop
    zoom_size = int(TARGET_HEIGHT * AVATAR_ZOOM)
    avatar_img = avatar_img.resize((zoom_size, zoom_size), Image.LANCZOS)

    center_x = zoom_size // 2
    center_y = zoom_size // 2
    half_target = TARGET_HEIGHT // 2

    left = center_x - half_target - AVATAR_SHIFT_X
    top = center_y - half_target - AVATAR_SHIFT_Y
    right = left + TARGET_HEIGHT
    bottom = top + TARGET_HEIGHT

    avatar_img = avatar_img.crop((left, top, right, bottom))
    av_w, av_h = avatar_img.size

    # Banner crop
    b_w, b_h = banner_img.size
    if b_w > 50 and b_h > 50:
        banner_img = banner_img.rotate(3, expand=True)
        b_w, b_h = banner_img.size

        crop_left = b_w * BANNER_START_X
        crop_top = b_h * BANNER_START_Y
        crop_right = b_w * BANNER_END_X
        crop_bottom = b_h * BANNER_END_Y
        banner_img = banner_img.crop((crop_left, crop_top, crop_right, crop_bottom))

    b_w, b_h = banner_img.size
    new_banner_w = int(TARGET_HEIGHT * (b_w / b_h) * 2) if b_h else 800
    banner_img = banner_img.resize((new_banner_w, TARGET_HEIGHT), Image.LANCZOS)

    final_w = av_w + new_banner_w
    combined = Image.new("RGBA", (final_w, TARGET_HEIGHT))

    combined.paste(avatar_img, (0, 0))
    combined.paste(banner_img, (av_w, 0))

    draw = ImageDraw.Draw(combined)

    # Fonts
    font_large = load_font(125, bold=True)
    font_small = load_font(95, bold=True)
    font_level = load_font(50, bold=True)

    def safe_text(draw_obj, x, y, text, font, stroke=3):
        for dx in range(-stroke, stroke + 1):
            for dy in range(-stroke, stroke + 1):
                draw_obj.text((x + dx, y + dy), text, font=font, fill="black")
        draw_obj.text((x, y), text, font=font, fill="white")

    safe_text(draw, av_w + 65, 40, name, font_large, stroke=4)
    safe_text(draw, av_w + 65, 220, guild, font_small, stroke=3)

    # Pin
    if pin_img.size != (100, 100):
        pin_img = pin_img.resize((130, 130), Image.LANCZOS)
        combined.paste(pin_img, (0, TARGET_HEIGHT - 130), pin_img)

    lvl_text = f"Lvl.{level}"
    bbox = draw.textbbox((0, 0), lvl_text, font=font_level)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]

    draw.rectangle(
        [final_w - w - 60, TARGET_HEIGHT - h - 50, final_w, TARGET_HEIGHT],
        fill="black",
    )
    draw.text(
        (final_w - w - 30, TARGET_HEIGHT - h - 40),
        lvl_text,
        font=font_level,
        fill="white",
    )

    img_io = io.BytesIO()
    combined.save(img_io, "PNG")
    img_io.seek(0)
    return img_io


@app.get("/")
async def home():
    return {"status": "Banner API Running", "endpoint": "/profile?uid=UID"}


@app.get("/profile")
async def get_banner(uid: str):
    if not uid:
        raise HTTPException(status_code=400, detail="UID required")

    data = await fetch_info(uid)

    # Some APIs wrap inside "data"
    payload = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data

    basic = (payload.get("basicInfo") or {}) if isinstance(payload, dict) else {}
    clan = (payload.get("clanBasicInfo") or {}) if isinstance(payload, dict) else {}
    captain = (payload.get("captainBasicInfo") or {}) if isinstance(payload, dict) else {}

    if not basic:
        raise HTTPException(status_code=404, detail="Account not found")

    name = basic.get("nickname", "Unknown")
    level = basic.get("level", "0")
    guild_name = clan.get("clanName", "")

    avatar_id = basic.get("headPic") or basic.get("avatarId") or basic.get("iconId") or 0
    banner_id = basic.get("bannerId") or captain.get("bannerId") or basic.get("banner") or 0
    pin_id = basic.get("pinId") or captain.get("pinId") or 0

    avatar_task = fetch_image_bytes(avatar_id)
    banner_task = fetch_image_bytes(banner_id)
    pin_task = fetch_image_bytes(pin_id)

    avatar, banner, pin = await asyncio.gather(avatar_task, banner_task, pin_task)

    banner_data = {
        "AccountLevel": level,
        "AccountName": name,
        "GuildName": guild_name,
    }

    loop = asyncio.get_event_loop()
    img_io = await loop.run_in_executor(
        process_pool,
        process_banner_image,
        banner_data,
        avatar,
        banner,
        pin,
    )

    return Response(
        content=img_io.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )