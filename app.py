import io
import os
import asyncio
import httpx
import base64
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageDraw, ImageFont
from concurrent.futures import ThreadPoolExecutor

# ================= ADJUSTMENT SETTINGS =================
AVATAR_ZOOM = 1.26
AVATAR_SHIFT_Y = 0  
AVATAR_SHIFT_X = 0  
BANNER_START_X = 0.25
BANNER_START_Y = 0.29
BANNER_END_X = 0.81
BANNER_END_Y = 0.65

# ================= Lifespan =================
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await client.aclose()
    process_pool.shutdown()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

INFO_API_URL = "https://infofull.vercel.app/get"
BASE64 = "aHR0cHM6Ly9jZG4uanNkZWxpdnIubmV0L2doL1NoYWhHQ3JlYXRvci9pY29uQG1haW4vUE5H"
info_URL = base64.b64decode(BASE64).decode('utf-8')

FONT_FILE = "NotoSansCJKjp-Bold.otf"
FONT_CHEROKEE = "NotoSansCJKjp-Bold.otf"

client = httpx.AsyncClient(
    headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    },
    timeout=20.0,
    follow_redirects=True
)

process_pool = ThreadPoolExecutor(max_workers=4)

def load_unicode_font(size, font_file=FONT_FILE):
    try:
        font_path = os.path.join(os.path.dirname(__file__), font_file)
        if os.path.exists(font_path):
            return ImageFont.truetype(font_path, size)
    except:
        pass
    return ImageFont.load_default()

async def fetch_image_bytes(item_id):
    if not item_id or str(item_id) in ["0", "None", "null"]:
        print(f"DEBUG: Invalid ID {item_id}")
        return None
    url = f"{info_URL}/{item_id}.png"
    try:
        resp = await client.get(url)
        print(f"DEBUG: Fetching {url} - Status: {resp.status_code}")
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        print(f"DEBUG: Error fetching {item_id}: {e}")
    return None

def bytes_to_image(img_bytes):
    if img_bytes:
        try:
            return Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        except:
            pass
    
    return Image.new("RGBA", (400, 400), (200, 200, 200, 255))
    
# ================= IMAGE PROCESS =================
def process_banner_image(data, avatar_bytes, banner_bytes):
    avatar_img = bytes_to_image(avatar_bytes)
    banner_img = bytes_to_image(banner_bytes)

    level = str(data.get("AccountLevel") or "0")
    name = data.get("AccountName") or "Unknown"
    guild = data.get("GuildName") or ""   # FIX: None-safe, handles null from API

    TARGET_HEIGHT = 400

    # Avatar Crop
    zoom_size = int(TARGET_HEIGHT * AVATAR_ZOOM)
    avatar_img = avatar_img.resize((zoom_size, zoom_size), Image.LANCZOS)
    left = (zoom_size - TARGET_HEIGHT) // 2 - AVATAR_SHIFT_X
    top = (zoom_size - TARGET_HEIGHT) // 2 - AVATAR_SHIFT_Y
    avatar_img = avatar_img.crop((left, top, left + TARGET_HEIGHT, top + TARGET_HEIGHT))
    av_w, av_h = avatar_img.size

    # Banner Crop Logic
    b_w, b_h = banner_img.size
    if b_w > 100 and b_h > 100:
        banner_img = banner_img.rotate(3, expand=True)
        bw_rot, bh_rot = banner_img.size
        crop_left = bw_rot * BANNER_START_X
        crop_top = bh_rot * BANNER_START_Y
        crop_right = bw_rot * BANNER_END_X
        crop_bottom = bh_rot * BANNER_END_Y
        banner_img = banner_img.crop((crop_left, crop_top, crop_right, crop_bottom))

    # Resize Banner
    b_w, b_h = banner_img.size
    aspect = (b_w / b_h) if b_h > 0 else 2.0
    new_banner_w = int(TARGET_HEIGHT * aspect * 2)
    banner_img = banner_img.resize((new_banner_w, TARGET_HEIGHT), Image.LANCZOS)

    final_w = av_w + new_banner_w
    combined = Image.new("RGBA", (final_w, TARGET_HEIGHT), (0, 0, 0, 255))
    
    combined.paste(avatar_img, (0, 0))
    combined.paste(banner_img, (av_w, 0))

    draw = ImageDraw.Draw(combined)
    font_large = load_unicode_font(125)
    font_large_cherokee = load_unicode_font(125, FONT_CHEROKEE)
    font_small = load_unicode_font(95)
    font_small_cherokee = load_unicode_font(95, FONT_CHEROKEE)
    font_level = load_unicode_font(50)

    def is_cherokee(c):
        return 0x13A0 <= ord(c) <= 0x13FF or 0xAB70 <= ord(c) <= 0xABBF

    def draw_text(x, y, text, f_main, f_alt, stroke):
        cx = x
        for ch in text:
            f = f_alt if is_cherokee(ch) else f_main
            for dx in range(-stroke, stroke + 1):
                for dy in range(-stroke, stroke + 1):
                    draw.text((cx + dx, y + dy), ch, font=f, fill="black")
            draw.text((cx, y), ch, font=f, fill="white")
            cx += f.getlength(ch)

    draw_text(av_w + 65, 40, name, font_large, font_large_cherokee, 4)
    draw_text(av_w + 65, 220, guild, font_small, font_small_cherokee, 3)

    lvl_text = f"Lvl.{level}"
    bbox = draw.textbbox((0, 0), lvl_text, font=font_level)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.rectangle([final_w - w - 60, TARGET_HEIGHT - h - 50, final_w, TARGET_HEIGHT], fill="black")
    draw.text((final_w - w - 30, TARGET_HEIGHT - h - 40), lvl_text, font=font_level, fill="white")

    img_io = io.BytesIO()
    combined.save(img_io, "PNG")
    img_io.seek(0)
    return img_io

@app.get("/profile")
async def get_banner(uid: str):
    if not uid:
        raise HTTPException(status_code=400, detail="UID required")

    resp = await client.get(f"{INFO_API_URL}?uid={uid}")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Info API Error")

    data = resp.json()
    
    account = data.get("AccountInfo", {})
    captain = data.get("captainBasicInfo", {})
    guild_info = data.get("GuildInfo", {})

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    avatar_id = account.get("AccountAvatarId") or captain.get("headPic")
    banner_id = account.get("AccountBannerId") or captain.get("bannerId")

    print(f"DEBUG: Found IDs -> Avatar: {avatar_id}, Banner: {banner_id}")

    avatar_task = fetch_image_bytes(avatar_id)
    banner_task = fetch_image_bytes(banner_id)

    avatar, banner = await asyncio.gather(avatar_task, banner_task)
    
    banner_data = {
        "AccountLevel": account.get("AccountLevel") or "0",
        "AccountName": account.get("AccountName") or "Unknown",
        "GuildName": guild_info.get("GuildName") or ""   # FIX: None-safe
    }

    loop = asyncio.get_event_loop()
    img_io = await loop.run_in_executor(process_pool, process_banner_image, banner_data, avatar, banner)

    return Response(content=img_io.getvalue(), media_type="image/png", headers={"Cache-Control": "public, max-age=300"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5000)
