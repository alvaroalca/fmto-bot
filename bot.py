import os
import asyncio
import re
import json
import requests
from datetime import date
from playwright.async_api import async_playwright

WIRTEX_USER       = os.getenv("WIRTEX_USER")
WIRTEX_PASS       = os.getenv("WIRTEX_PASS")
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")
GITHUB_TOKEN      = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "")

WIRTEX_URL  = "https://www.wirtexsports.com"
TARGET_NAME = "ALVARO ALCARAZ"
MEMORY_FILE = ".github/last_competition.txt"


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    })
    print(f"[Telegram] status={r.status_code}")


# ---------------------------------------------------------------------------
# Memoria (fichero en el repo via GitHub Contents API)
# ---------------------------------------------------------------------------
def _gh_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def load_last_competition():
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        return ""
    api = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{MEMORY_FILE}"
    r = requests.get(api, headers=_gh_headers())
    if r.status_code == 200:
        import base64
        return base64.b64decode(r.json()["content"]).decode().strip()
    return ""

def save_last_competition(key):
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        print("[Memoria] Sin token/repo.")
        return
    import base64
    api = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{MEMORY_FILE}"
    r = requests.get(api, headers=_gh_headers())
    sha = r.json().get("sha") if r.status_code == 200 else None
    data = {
        "message": f"chore: last_competition={key}",
        "content": base64.b64encode(key.encode()).decode(),
    }
    if sha:
        data["sha"] = sha
    r = requests.put(api, json=data, headers=_gh_headers())
    print(f"[Memoria] last_competition={key!r} → {'OK' if r.status_code in (200,201) else f'ERROR {r.status_code}'}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def run():
    last_competition = load_last_competition()
    print(f"[Memoria] Última competición: {last_competition!r}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        try:
            # 1. Login en Wirtex (interfaz móvil)
            print("Logueando en Wirtex...")
            await page.goto(WIRTEX_URL, wait_until="networkidle")

            spain = await page.query_selector('a:has-text("Spain")')
            if spain and await spain.is_visible():
                await spain.click()
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(2000)

            await page.evaluate(
                """([user, pwd]) => {
                    const inputs = [...document.querySelectorAll('input:not([type="hidden"])')];
                    const userInput = inputs.find(i =>
                        i.type === 'email' ||
                        (i.name || '').toLowerCase().includes('user') ||
                        (i.name || '').toLowerCase().includes('mail')
                    ) || inputs[0];
                    const passInput = inputs.find(i => i.type === 'password');
                    function setVal(el, val) {
                        const nativeSet = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value').set;
                        nativeSet.call(el, val);
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                    if (userInput) setVal(userInput, user);
                    if (passInput)  setVal(passInput, pwd);
                }""",
                [WIRTEX_USER, WIRTEX_PASS]
            )
            await page.evaluate("""
                const btn = document.querySelector('button[type="submit"], input[type="submit"]');
                if (btn) btn.click();
                else { const f = document.querySelector('form'); if (f) f.submit(); }
            """)
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)

            body = await page.inner_text("body")
            if "INVITADO" in body.upper() or "GUEST" in body.upper():
                raise Exception("Login fallido en Wirtex.")
            print(f"Sesión iniciada. URL: {page.url}")

            # 2. Buscar la próxima competición PREPARATORIA futura
            today = date.today()
            comp_info = await page.evaluate("""
                (todayStr) => {
                    const [ty, tm, td] = todayStr.split('-').map(Number);
                    const todayTs = new Date(ty, tm-1, td).getTime();
                    const rows = [...document.querySelectorAll('tr[onclick]')];
                    let best = null, bestTs = Infinity;
                    for (const row of rows) {
                        const text = row.innerText.toUpperCase();
                        if (!text.includes('PISTOLA AIRE') || !text.includes('PREPARATORIA')) continue;
                        const dm = row.innerText.match(/(\\d{2})\\/(\\d{2})\\/(\\d{4})/);
                        if (!dm) continue;
                        const rowTs = new Date(+dm[3], +dm[2]-1, +dm[1]).getTime();
                        if (rowTs < todayTs) continue;          // pasada, saltar
                        if (rowTs >= bestTs) continue;          // no es la más próxima
                        const oc = row.getAttribute('onclick') || '';
                        const mId = oc.match(/Competicion_Det_Ver\\/(\\d+)/);
                        if (mId) { best = {id: mId[1], date: dm[0]}; bestTs = rowTs; }
                    }
                    return best;
                }
            """, str(today))

            if not comp_info:
                print("No hay próxima PREPARATORIA futura. Nada que hacer.")
                return

            comp_date = comp_info["date"]
            comp_id   = comp_info["id"]
            print(f"  Próxima competición: {comp_date} (ID={comp_id})")

            # 3. Obtener pCodInscripcion desde el detalle móvil
            mobile_det = (f"{WIRTEX_URL}/Mobile/GLB/Competicion/"
                          f"Competicion_Det_Ver/{comp_id}?pCodInscripcion=0")
            await page.goto(mobile_det, wait_until="networkidle")
            await page.wait_for_timeout(1500)

            inscripcion_code = await page.evaluate("""
                () => {
                    const btns = [...document.querySelectorAll('[onclick]')];
                    for (const btn of btns) {
                        const oc = btn.getAttribute('onclick') || '';
                        const m = oc.match(/pCodInscripcion=(\\d+)/);
                        if (m && m[1] !== '0') return m[1];
                    }
                    return null;
                }
            """)

            if not inscripcion_code:
                print("Sin inscripción en esta competición. Nada que hacer.")
                return

            print(f"  pCodInscripcion: {inscripcion_code}")

            # 4. Desde la ficha de inscripción extraer la URL de PuntuacionesIndex
            inscripcion_url = (f"{WIRTEX_URL}/Publica/GLB/Competicion/"
                               f"co_prec_InscripcionVer/{inscripcion_code}")
            await page.goto(inscripcion_url, wait_until="networkidle")
            await page.wait_for_timeout(2000)

            puntuaciones_path = await page.evaluate("""
                () => {
                    const els = [...document.querySelectorAll('[onclick], a[href]')];
                    for (const el of els) {
                        const src = (el.getAttribute('onclick') || '') +
                                    (el.getAttribute('href') || '');
                        const m = src.match(/\\/Publica\\/GLB\\/Competicion\\/co_prec_PuntuacionesIndex[^'"\\s]*/);
                        if (m) return m[0];
                    }
                    return null;
                }
            """)

            if not puntuaciones_path:
                html_src = await page.content()
                m = re.search(
                    r'/Publica/GLB/Competicion/co_prec_PuntuacionesIndex[^\'"\\s]+', html_src)
                puntuaciones_path = m.group(0) if m else None

            if not puntuaciones_path:
                print("Sin enlace a Puntuaciones. Tanda/Puesto aún no asignados.")
                return

            puntuaciones_url = WIRTEX_URL + puntuaciones_path
            print(f"  URL puntuaciones: {puntuaciones_url}")

            # 5. Navegar a PuntuacionesIndex e interceptar la respuesta JSON del jqGrid
            grid_data = []

            async def capture_grid(response):
                if "PuntuacionesGrid" in response.url or "PuntuacionesObtener" in response.url:
                    try:
                        grid_data.append(await response.text())
                    except Exception:
                        pass

            page.on("response", capture_grid)
            await page.goto(puntuaciones_url, wait_until="networkidle")
            await page.wait_for_timeout(4000)

            # 6. Buscar TARGET_NAME en el JSON del grid (puede haber varias páginas)
            tanda, puesto = None, None
            target_upper  = TARGET_NAME.upper()

            for body_text in grid_data:
                try:
                    data = json.loads(body_text)
                except Exception:
                    continue
                for row in data.get("rows", []):
                    cells = row.get("cell", [])
                    if target_upper not in " ".join(str(c) for c in cells).upper():
                        continue
                    full = " ".join(str(c) for c in cells)
                    tm = re.search(r'Tanda\s+(\d+)', full, re.IGNORECASE)
                    tanda = tm.group(1) if tm else "?"
                    # El puesto es la primera celda numérica entre 1-200
                    # que no sea el número de tanda
                    nums = [str(c) for c in cells
                            if str(c).isdigit() and 1 <= int(c) <= 200
                            and str(c) != tanda]
                    puesto = nums[0] if nums else "?"
                    break
                if tanda is not None:
                    break

            if tanda is None:
                print("Tanda/Puesto aún no asignados por la organización. Se reintentará.")
                return

            print(f"  Tanda={tanda}  Puesto={puesto}")

            # 7. Comprobar memoria
            comp_key = f"{comp_date}_P{puesto}_T{tanda}"
            if comp_key == last_competition:
                print(f"Sin cambios: {comp_key} ya notificado.")
                return

            # 8. Enviar mensaje
            msg = (
                f"🎯 *Preparatoria Pistola Aire 10m*\n"
                f"📅 {comp_date}\n\n"
                f"📍 Puesto: *{puesto}* | ⏱ Tanda: *{tanda}*"
            )
            send_telegram(msg)
            save_last_competition(comp_key)
            print(f"Notificación enviada: {comp_key}")

        except Exception as e:
            print(f"ERROR (sin notificar): {e}")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
