"""Graba una sesión de la bandeja como video horizontal 1920x1080 (16:9) listo para LinkedIn.

    uv run --extra record python scripts/record.py                      # última partida, 2x
    uv run --extra record python scripts/record.py --replay <archivo> --speed 4

Requiere la bandeja corriendo (./scripts/start.sh).
Si hay ffmpeg instalado (brew install ffmpeg) convierte a MP4 H.264; si no, deja el .webm.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
REPLAYS = ROOT / "replays"
VIDEOS = ROOT / "videos"


async def record(base_url: str, replay: str, speed: float, hold_s: float, W: int, H: int) -> Path:
    VIDEOS.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(
            viewport={"width": W, "height": H},
            record_video_dir=str(VIDEOS / "_tmp"),
            record_video_size={"width": W, "height": H},
        )
        page = await ctx.new_page()
        await page.goto(f"{base_url}/?replay={replay}&speed={speed}&clean=1")
        await page.wait_for_function("document.body.dataset.done === '1'", timeout=60 * 60 * 1000)
        await page.wait_for_timeout(int(hold_s * 1000))  # se queda en el cartel final
        video = page.video
        await ctx.close()
        await browser.close()
        raw = Path(await video.path())

    out = VIDEOS / f"{Path(replay).stem}-{speed:g}x.webm"
    raw.replace(out)
    shutil.rmtree(VIDEOS / "_tmp", ignore_errors=True)
    return out


def to_mp4(webm: Path) -> Path | None:
    if not shutil.which("ffmpeg"):
        return None
    mp4 = webm.with_suffix(".mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-r", "30", "-movflags", "+faststart", str(mp4)],
        check=True,
    )
    return mp4


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", help="archivo en replays/ (por defecto, el último)")
    ap.add_argument("--speed", type=float, default=2.0, help="velocidad de reproducción (los ms mostrados siguen siendo los reales)")
    ap.add_argument("--hold", type=float, default=3.0, help="segundos en el cartel final")
    ap.add_argument("--url", default="http://127.0.0.1:8002")
    ap.add_argument("--size", default="1920x1080", help="ancho x alto del video")
    args = ap.parse_args()

    replay = args.replay
    if not replay:
        files = sorted(REPLAYS.glob("*.json"))
        if not files:
            raise SystemExit("No hay repeticiones en replays/. Jugá una partida primero.")
        replay = files[-1].name

    w, h = (int(v) for v in args.size.lower().split("x"))
    webm = asyncio.run(record(args.url, replay, args.speed, args.hold, w, h))
    mp4 = to_mp4(webm)
    print(f"Video: {mp4 or webm}")
    if not mp4:
        print("(sin ffmpeg: quedó en .webm. Para MP4: brew install ffmpeg y volvé a correr)")


if __name__ == "__main__":
    main()
