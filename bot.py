import os
import asyncio
import re
import requests
from datetime import date
from playwright.async_api import async_playwright

WIRTEX_USER       = os.getenv("WIRTEX_USER")
WIRTEX_PASS       = os.getenv("WIRTEX_PASS")
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")
GITHUB_TOKEN      = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "")

WIRTEX_URL   = "https://www.wirtexsports.com"
COMP_KEYWORD = "PISTOLA AIRE 10 METROS"
TARGET_NAME  = "ALVARO ALCARAZ"
MEMORY_FILE  = ".github/last_competition.txt"


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
            # 1. Login en Wirtex
            print("Logueando en Wirtex...")
            await page.goto(WIRTEX_URL, wait_until="networkidle")

            spain = await page.query_selector('a:has-text("Spain")')
            if spain and await spain.is_visible():
                await spain.click()
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(2000)

            print(f"  URL login: {page.url}")

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

            # 2. Buscar la próxima competición PREPARATORIA futura (interfaz móvil)
            today = date.today()
            comp_info = await page.evaluate("""
                (todayStr) => {
                    const [ty, tm, td] = todayStr.split('-').map(Number);
                    const todayTs = new Date(ty, tm-1, td).getTime();
                    const rows = [...document.querySelectorAll('tr[onclick]')];
                    for (const row of rows) {
                        const text = row.innerText.toUpperCase();
                        if (!text.includes('PISTOLA AIRE') || !text.includes('PREPARATORIA')) continue;
                        const dm = row.innerText.match(/(\\d{2})\\/(\\d{2})\\/(\\d{4})/);
                        if (!dm) continue;
                        const rowTs = new Date(+dm[3], +dm[2]-1, +dm[1]).getTime();
                        if (rowTs < todayTs) continue;
                        const oc = row.getAttribute('onclick') || '';
                        const mId = oc.match(/Competicion_Det_Ver\\/(\\d+)/);
                        return mId ? {id: mId[1], date: dm[0]} : null;
                    }
                    return null;
                }
            """, str(today))

            if not comp_info:
                print("No hay próxima PREPARATORIA futura. Nada que hacer.")
                return

            comp_date = comp_info["date"]
            comp_id   = comp_info["id"]
            print(f"  Próxima competición: {comp_date} (ID={comp_id})")

            # 3. Navegar al detalle móvil para obtener pCodInscripcion
            mobile_det = f"https://www.wirtexsports.com/Mobile/GLB/Competicion/Competicion_Det_Ver/{comp_id}?pCodInscripcion=0"
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
                print("No se encontró pCodInscripcion. ¿Sin inscripción en esta competición?")
                return

            print(f"  pCodInscripcion: {inscripcion_code}")

            # 4. Navegar directamente a la ficha de inscripción (clásica)
            #    Este endpoint devuelve los datos del inscrito: Tanda, Puesto, etc.
            inscripcion_url = (
                f"https://www.wirtexsports.com/Publica/GLB/Competicion/"
                f"co_prec_InscripcionVer/{inscripcion_code}"
            )
            await page.goto(inscripcion_url, wait_until="networkidle")
            await page.wait_for_timeout(2000)

            page_text = await page.inner_text("body")

            # 5. Extraer Tanda y Puesto — campos: "Nº Tanda inicial\n<valor>"
            tanda_m  = re.search(r'Nº Tanda inicial\s*\n\s*(\d+)', page_text, re.IGNORECASE)
            puesto_m = re.search(r'Nº Puesto inicial\s*\n\s*(\d+)', page_text, re.IGNORECASE)
            tanda  = tanda_m.group(1)  if tanda_m  else None
            puesto = puesto_m.group(1) if puesto_m else None

            if tanda is None or puesto is None:
                print("Tanda/Puesto aún no asignados por la organización. Se reintentará.")
                return

            print(f"  Tanda={tanda}  Puesto={puesto}")

            # 6. Comprobar memoria
            comp_key = f"{comp_date}_P{puesto}_T{tanda}"
            if comp_key == last_competition:
                print(f"Sin cambios: {comp_key} ya notificado.")
                return

            # 7. Enviar mensaje
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
