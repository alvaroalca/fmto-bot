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

            # 2. Buscar la próxima competición PREPARATORIA futura
            today = date.today()
            comp_url = await page.evaluate("""
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
                        if (rowTs < todayTs) continue;   // pasada, saltar
                        const oc = row.getAttribute('onclick') || '';
                        const m = oc.match(/href\\s*=\\s*'([^']+)'/);
                        return m ? {url: m[1], date: dm[0]} : null;
                    }
                    return null;
                }
            """, str(today))

            if not comp_url:
                print("No hay próxima PREPARATORIA futura en la lista. Nada que hacer.")
                return

            comp_date = comp_url["date"]
            comp_det  = "https://www.wirtexsports.com" + comp_url["url"]
            print(f"  Próxima competición: {comp_date} → {comp_det}")

            # 3. Navegar al detalle y obtener la URL de CLASIFICACIÓN
            await page.goto(comp_det, wait_until="networkidle")
            await page.wait_for_timeout(1500)

            clasif_url = await page.evaluate("""
                () => {
                    const btns = [...document.querySelectorAll('[onclick]')];
                    for (const btn of btns) {
                        const oc = btn.getAttribute('onclick') || '';
                        if (oc.toLowerCase().includes('clasificacion')) {
                            const m = oc.match(/href\\s*[=]\\s*'([^']+)'/);
                            return m ? m[1] : null;
                        }
                    }
                    return null;
                }
            """)

            if not clasif_url:
                print("No se encontró el botón CLASIFICACIÓN en el detalle. ¿Aún sin asignación?")
                return

            full_clasif = "https://www.wirtexsports.com" + clasif_url
            print(f"  URL clasificación: {full_clasif}")

            # 4. Navegar a la clasificación y buscar a TARGET_NAME paginando
            await page.goto(full_clasif, wait_until="networkidle")
            await page.wait_for_timeout(2000)

            found_text = ""
            for _ in range(15):   # máximo 15 páginas
                page_text = await page.inner_text("body")
                text_upper = page_text.upper()
                target_upper = TARGET_NAME.upper()
                # Buscar TODAS las ocurrencias del nombre; quedarse con la que
                # tenga "Tanda N" cerca (la fila de la tabla, no el menú/bienvenida)
                start = 0
                while True:
                    idx = text_upper.find(target_upper, start)
                    if idx == -1:
                        break
                    fragment = page_text[max(0, idx - 150): idx + 300]
                    if re.search(r'Tanda\s+\d+', fragment, re.IGNORECASE):
                        found_text = fragment
                        break
                    start = idx + 1
                if found_text:
                    break
                # Siguiente página
                next_btn = await page.query_selector(
                    'a:has-text("›"), a:has-text(">"), a[title*="iguiente"]')
                if not next_btn or not await next_btn.is_visible():
                    break
                await next_btn.click()
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(1000)

            if not found_text:
                print(f"No se encontró {TARGET_NAME} con Tanda asignada.")
                return

            print(f"[Clasificación] Fragmento:\n{found_text}")

            # 5. Extraer Tanda y Puesto del fragmento
            tanda_m = re.search(r'Tanda\s+(\d+)', found_text, re.IGNORECASE)
            tanda   = tanda_m.group(1) if tanda_m else "?"

            # El puesto es el número que aparece ANTES del nombre en la fila
            # Tomamos el fragmento anterior al nombre y cogemos el último número
            name_idx  = found_text.upper().index(TARGET_NAME.upper())
            before    = found_text[:name_idx]
            nums_before = re.findall(r'\b(\d+)\b', before)
            puesto = nums_before[-1] if nums_before else "?"

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
