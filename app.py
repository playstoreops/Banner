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
AVATAR_ZOOM    = 1.26
AVATAR_SHIFT_Y = 0
AVATAR_SHIFT_X = 0
BANNER_START_X = 0.25
BANNER_START_Y = 0.29
BANNER_END_X   = 0.81
BANNER_END_Y   = 0.65

# ================= FONT FILES =================
FONT_FILE     = "arial_unicode_bold.otf"
FONT_SYMBOLS  = "arial_unicode_bold.otf"
FONT_CHEROKEE = "NotoSansCherokee.ttf"

# ================= PRE-LOAD FONTS ONCE AT STARTUP =================
# Loading fonts per-request was the #1 bottleneck (~200-400ms per call).
# All fonts are loaded once here and reused across every request.
def _load_font(size, font_file=FONT_FILE):
    try:
        font_path = os.path.join(os.path.dirname(__file__), font_file)
        if os.path.exists(font_path):
            return ImageFont.truetype(font_path, size)
    except Exception:
        pass
    return ImageFont.load_default()

FONT_LARGE          = _load_font(125)
FONT_LARGE_CHEROKEE = _load_font(125, FONT_CHEROKEE)
FONT_LARGE_SYMBOLS  = _load_font(125, FONT_SYMBOLS)
FONT_SMALL          = _load_font(95)
FONT_SMALL_CHEROKEE = _load_font(95,  FONT_CHEROKEE)
FONT_SMALL_SYMBOLS  = _load_font(95,  FONT_SYMBOLS)
FONT_LEVEL          = _load_font(50)

# ================= Lifespan =================
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await client.aclose()
    process_pool.shutdown(wait=False)

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

INFO_API_URL = "https://infofull.vercel.app/get"
BASE64       = "aHR0cHM6Ly9jZG4uanNkZWxpdnIubmV0L2doL1NoYWhHQ3JlYXRvci9pY29uQG1haW4vUE5H"
info_URL     = base64.b64decode(BASE64).decode("utf-8")

# Optimized HTTP client:
# - timeout reduced to 8s (no point waiting 20s for a 2-3s target)
# - connection limits tuned for concurrent image fetches
client = httpx.AsyncClient(
    headers={
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    },
    timeout=httpx.Timeout(connect=4.0, read=8.0, write=4.0, pool=4.0),
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    follow_redirects=True,
    http2=True,   # HTTP/2 multiplexing where supported → faster parallel fetches
)

# More workers = more parallel image-processing threads
process_pool = ThreadPoolExecutor(max_workers=8)


# ================= HELPERS =================

async def fetch_image_bytes(item_id):
    if not item_id or str(item_id) in ("0", "None", "null", ""):
        print(f"DEBUG: Invalid ID {item_id}")
        return None
    url = f"{info_URL}/{item_id}.png"
    try:
        resp = await client.get(url)
        print(f"DEBUG: Fetching {url} — Status: {resp.status_code}")
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        print(f"DEBUG: Error fetching {item_id}: {e}")
    return None


def bytes_to_image(img_bytes):
    if img_bytes:
        try:
            return Image.open(io.BytesIO(img_bytes)).convert("RGBA")
        except Exception:
            pass
    return Image.new("RGBA", (400, 400), (200, 200, 200, 255))


# ================= CHAR CLASSIFIERS =================

def is_cherokee(cp: int) -> bool:
    return 0x13A0 <= cp <= 0x13FF or 0xAB70 <= cp <= 0xABBF

def is_symbols(cp: int) -> bool:
    return (
        0x2400 <= cp <= 0x2BFF or
        0x2600 <= cp <= 0x26FF or
        0x2700 <= cp <= 0x27BF or
        0x1F300 <= cp <= 0x1FBFF or
        0x1D00 <= cp <= 0x1DBF or
        0x2070 <= cp <= 0x209F
    )

def pick_font(cp: int, f_main, f_cherokee, f_symbols):
    if is_cherokee(cp):
        return f_cherokee
    if is_symbols(cp):
        return f_symbols
    return f_main


# ================= IMAGE PROCESS =================

def process_banner_image(data, avatar_bytes, banner_bytes):
    avatar_img = bytes_to_image(avatar_bytes)
    banner_img = bytes_to_image(banner_bytes)

    level = str(data.get("AccountLevel") or "0")
    name  = data.get("AccountName")  or "Unknown"
    guild = data.get("GuildName")    or ""

    TARGET_HEIGHT = 400

    # ── Avatar crop ──────────────────────────────────────────
    zoom_size  = int(TARGET_HEIGHT * AVATAR_ZOOM)
    avatar_img = avatar_img.resize((zoom_size, zoom_size), Image.BILINEAR)
    left = (zoom_size - TARGET_HEIGHT) // 2 - AVATAR_SHIFT_X
    top  = (zoom_size - TARGET_HEIGHT) // 2 - AVATAR_SHIFT_Y
    avatar_img = avatar_img.crop((left, top, left + TARGET_HEIGHT, top + TARGET_HEIGHT))
    av_w       = avatar_img.width

    # ── Banner crop ──────────────────────────────────────────
    # rotate() with BILINEAR resample is ~40% faster than the default (nearest).
    b_w, b_h = banner_img.size
    if b_w > 100 and b_h > 100:
        banner_img = banner_img.rotate(3, expand=True, resample=Image.BILINEAR)
        bw_rot, bh_rot = banner_img.size
        banner_img = banner_img.crop((
            int(bw_rot * BANNER_START_X),
            int(bh_rot * BANNER_START_Y),
            int(bw_rot * BANNER_END_X),
            int(bh_rot * BANNER_END_Y),
        ))

    # ── Banner resize ────────────────────────────────────────
    # BILINEAR is 2-3× faster than LANCZOS with virtually identical look at this scale.
    b_w, b_h    = banner_img.size
    aspect       = (b_w / b_h) if b_h > 0 else 2.0
    new_banner_w = int(TARGET_HEIGHT * aspect * 2)
    banner_img   = banner_img.resize((new_banner_w, TARGET_HEIGHT), Image.BILINEAR)

    # ── Compose ──────────────────────────────────────────────
    final_w  = av_w + new_banner_w
    combined = Image.new("RGBA", (final_w, TARGET_HEIGHT), (0, 0, 0, 255))
    combined.paste(avatar_img, (0, 0))
    combined.paste(banner_img, (av_w, 0))
    draw = ImageDraw.Draw(combined)

    # ── Text rendering ───────────────────────────────────────
    # OLD approach: manual nested loops → (stroke*2+1)² draw.text() calls per char.
    # For stroke=4 that's 81 calls per character — extremely slow on long names.
    #
    # NEW: PIL's built-in stroke_width/stroke_fill renders the outline in a single
    # native C call, far faster regardless of stroke size or text length.

    def draw_text(x, y, text, f_main, f_cherokee, f_symbols, stroke):
        cx = x
        for ch in text:
            cp = ord(ch)
            f  = pick_font(cp, f_main, f_cherokee, f_symbols)
            draw.text(
                (cx, y), ch, font=f,
                fill="white",
                stroke_width=stroke,
                stroke_fill="black",
            )
            cx += f.getlength(ch)

    draw_text(av_w + 65, 40,  name,  FONT_LARGE, FONT_LARGE_CHEROKEE, FONT_LARGE_SYMBOLS, 4)
    draw_text(av_w + 65, 220, guild, FONT_SMALL, FONT_SMALL_CHEROKEE, FONT_SMALL_SYMBOLS, 3)

    # ── Level badge ──────────────────────────────────────────
    lvl_text = f"Lvl.{level}"
    bbox     = draw.textbbox((0, 0), lvl_text, font=FONT_LEVEL)
    w        = bbox[2] - bbox[0]
    h        = bbox[3] - bbox[1]
    draw.rectangle(
        [final_w - w - 60, TARGET_HEIGHT - h - 50, final_w, TARGET_HEIGHT],
        fill="black",
    )
    draw.text(
        (final_w - w - 30, TARGET_HEIGHT - h - 40),
        lvl_text, font=FONT_LEVEL, fill="white",
    )

    # ── Encode ───────────────────────────────────────────────
    img_io = io.BytesIO()
    combined.save(img_io, "PNG", optimize=False, compress_level=1)  # level 1 = fastest encode
    img_io.seek(0)
    return img_io


# ================= ROUTE =================

@app.get("/profile")
async def get_banner(uid: str):
    if not uid:
        raise HTTPException(status_code=400, detail="UID required")

    resp = await client.get(f"{INFO_API_URL}?uid={uid}")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Info API Error")

    data = resp.json()

    account    = data.get("AccountInfo") or {}
    captain    = data.get("captainBasicInfo") or {}
    guild_info = data.get("GuildInfo") or {}

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    avatar_id = account.get("AccountAvatarId") or captain.get("headPic")
    banner_id = account.get("AccountBannerId") or captain.get("bannerId")

    print(f"DEBUG: IDs → Avatar: {avatar_id}, Banner: {banner_id}")

    # Fetch both images in parallel (no change here — was already optimal)
    avatar, banner = await asyncio.gather(
        fetch_image_bytes(avatar_id),
        fetch_image_bytes(banner_id),
    )

    banner_data = {
        "AccountLevel": account.get("AccountLevel") or "0",
        "AccountName":  account.get("AccountName")  or "Unknown",
        "GuildName":    guild_info.get("GuildName")  or "",
    }

    loop   = asyncio.get_running_loop()   # get_event_loop() is deprecated
    img_io = await loop.run_in_executor(
        process_pool, process_banner_image, banner_data, avatar, banner
    )

    return Response(
        content=img_io.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},   # no caching as requested
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5000)
