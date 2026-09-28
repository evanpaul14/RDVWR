"""Render the RDVWR promo (index.html) to rdvwr_ad.mp4, frame by frame.

    python promo/audio.py    # writes promo/ad_audio.wav
    python promo/render.py   # writes promo/rdvwr_ad.mp4

Needs: playwright, imageio-ffmpeg (numpy + scipy for audio.py).
Each frame calls window.render(t) in the page, so output is deterministic.
"""
import asyncio, functools, http.server, os, subprocess, threading, time
import imageio_ffmpeg
from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
FPS, DUR = 60, 15.5
CHROME = os.environ.get('CHROME', '/opt/pw-browsers/chromium-1194/chrome-linux/chrome')


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve():
    handler = functools.partial(_Quiet, directory=HERE)
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def main():
    srv = serve()
    ff = subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-loglevel', 'error',
        '-f', 'image2pipe', '-framerate', str(FPS), '-c:v', 'mjpeg', '-i', '-',
        '-i', os.path.join(HERE, 'ad_audio.wav'),
        '-c:v', 'libx264', '-preset', 'slow', '-crf', '16', '-pix_fmt', 'yuv420p', '-profile:v', 'high',
        '-movflags', '+faststart', '-c:a', 'aac', '-b:a', '192k', '-shortest',
        os.path.join(HERE, 'rdvwr_ad.mp4')], stdin=subprocess.PIPE)
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=CHROME if os.path.exists(CHROME) else None)
        pg = await b.new_page(viewport={'width': 1920, 'height': 1080})
        await pg.goto(f'http://127.0.0.1:{srv.server_address[1]}/index.html')
        await pg.evaluate('window.ready')
        t0 = time.time()
        for f in range(int(FPS * DUR)):
            await pg.evaluate(f'render({f / FPS})')
            ff.stdin.write(await pg.screenshot(type='jpeg', quality=95))
            if f % 150 == 0:
                print(f, round(time.time() - t0, 1), flush=True)
        await b.close()
    ff.stdin.close(); ff.wait(); srv.shutdown()
    print('done', ff.returncode)


asyncio.run(main())
