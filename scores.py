import os
import asyncio
import re
import requests
from playwright.async_api import async_playwright

WIRTEX_USER      = os.getenv("WIRTEX_USER")
WIRTEX_PASS      = os.getenv("WIRTEX_PASS")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
LAST_SCORES      = os.getenv("LAST_SCORES", "")
GITHUB_TOKEN     = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "")

WIRTEX_URL    = "https://www.wirtexsports.com"
COMP_KEYWORD  = "PISTOLA AIRE 10 METROS"
TARGET_NAME   = "ALCARAZ"   # busca "ALVARO ALCARAZ" en resultados


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
    print(f"[Telegram] status={r.status_code} respuesta={r.text[:200]}")


# ---------------------------------------------------------------------------
# Memoria
# ---------------------------------------------------------------------------
def save_last_scores(key):
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        print("[Memoria] Sin token/repo, no se puede guardar.")
        return
    api = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/variables/LAST_SCORES"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    r = requests.patch(api, json={"name": "LAST_SCORES", "value": key}, headers=headers)
    if r.status_code == 404:
        r = requests.post(
            f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/variables",
            json={"name": "LAST_SCORES", "value": key},
            headers=headers,
        )
    print(f"[Memoria] LAST_SCORES={key!r} (status={r.status_code})")


# ---------------------------------------------------------------------------
# Parseo de series
# ---------------------------------------------------------------------------
def parse_series(text):
    """Extrae lista de {score, shots} de cada serie del texto de Detalles."""
    series = []
    # Buscar bloques "Serie N ... Puntuación de la serie: X ... (disparos)"
    blocks = re.split(r'Serie\s+\d+', text, flags=re.IGNORECASE)
    for block in blocks[1:]:  # skip first (before Serie 1)
        score_m = re.search(r'Puntuaci[oó]n de la serie[:\s]+(\d+)', block, re.IGNORECASE)
        if not score_m:
            continue
        score = int(score_m.group(1))
        # Disparos: X, 10, dígito simple, / — aparecen después del número de posición (1..10)
        shots = re.findall(r'(?:^|\s)(10|X|/|[0-9])(?:\s|$)', block)
        # Alternativa más permisiva si no encuentra 10 disparos
        if len(shots) < 10:
            shots = re.findall(r'\b(10|X|[0-9])\b', block)
            # Filtrar números de posición (1-10 que aparecen como índice)
            shots = [s for s in shots if s not in [str(i) for i in range(1, 11)] or s == "10"][:10]
        series.append({"score": score, "shots": shots[:10]})
    return series


# ---------------------------------------------------------------------------
# Construcción del mensaje
# ---------------------------------------------------------------------------
def build_message(fecha, clasificaciones, puesto, total, xs, series):
    lines = [
        f"🎯 *Puntuaciones - Preparatoria Pistola Aire 10m*",
        f"📅 {fecha}",
        "",
    ]
    if clasificaciones:
        lines.append(clasificaciones)
    lines += [
        f"📍 Puesto: *{puesto}* | 💯 Total: *{total}* | ✖️ X's: *{xs}*",
    ]
    if series:
        lines.append("")
        for i, s in enumerate(series, 1):
            shots_str = "  ".join(s["shots"]) if s["shots"] else "—"
            lines.append(f"Serie {i}: *{s['score']}* → {shots_str}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        try:
            # 1. Login
            print("Logueando en Wirtex...")
            await page.goto(WIRTEX_URL, wait_until="networkidle")

            # Si aparece selector de país, elegir España
            spain = await page.query_selector('a:has-text("Spain")')
            if spain and await spain.is_visible():
                await spain.click()
                print("  Seleccionado país: Spain")
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(2000)

            print(f"  URL tras Spain: {page.url}")
            body_dbg = await page.inner_text("body")
            print(f"  [Debug página tras Spain] Primeros 600 chars:\n{body_dbg[:600]}")

            # Abrir el dropdown de login (botón "Acceder" / "Access" arriba a la derecha)
            acceder = None
            for sel in ['a:has-text("Acceder")', 'button:has-text("Acceder")',
                        'a:has-text("Access")', 'button:has-text("Access")',
                        'a:has-text("Login")', 'button:has-text("Login")',
                        'a:has-text("Log in")', 'button:has-text("Log in")',
                        'a[href*="login"]', 'a[href*="acceso"]', 'a[href*="access"]']:
                acceder = await page.query_selector(sel)
                if acceder and await acceder.is_visible():
                    print(f"  Botón login con: {sel}")
                    break
                acceder = None
            if not acceder:
                print(f"  [Debug botones] Lista de todos los links/botones:")
                for el in await page.query_selector_all("a, button"):
                    txt = ((await el.inner_text()) or "").strip()
                    if txt:
                        print(f"    {txt[:80]!r}")
                raise Exception("No se encontró el botón de login en la página.")
            await acceder.click()
            print("  Clic en 'Acceder'")
            await page.wait_for_timeout(1500)

            # Rellenar el formulario del dropdown (Correo electrónico / Contraseña)
            await page.wait_for_selector('input[type="email"], input[placeholder*="Correo"], input[placeholder*="correo"], input[name*="mail"], input[name*="user" i]', state="visible", timeout=8000)

            # Email / usuario
            for sel in ['input[type="email"]', 'input[placeholder*="Correo" i]',
                        'input[name*="mail" i]', 'input[name*="user" i]']:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.fill(WIRTEX_USER)
                    print(f"  Email con: {sel}")
                    break

            # Contraseña
            pw_el = await page.query_selector('input[type="password"]')
            if pw_el:
                await pw_el.fill(WIRTEX_PASS)
                print("  Contraseña rellenada")

            # Submit — botón "Iniciar sesión"
            for sel in ['button:has-text("Iniciar sesión")', 'button:has-text("Iniciar")',
                        'input[type="submit"]', 'button[type="submit"]']:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    print(f"  Submit con: {sel}")
                    break

            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)

            # Verificar login
            body = await page.inner_text("body")
            if "INVITADO" in body.upper() or "GUEST" in body.upper():
                raise Exception("Login fallido en Wirtex: sigue mostrando usuario invitado.")
            print(f"Sesión iniciada. URL: {page.url}")

            # 2. Ir a Competiciones → Mis competiciones
            # La página puede estar en inglés (Competitions / My competitions)
            # o en español (Competiciones / Mis competiciones)
            print("Navegando a Mis competiciones...")

            # Debug: ver todos los links del menú
            for lnk in await page.query_selector_all("nav a, .nav a, .menu a, header a"):
                print(f"  [nav] {((await lnk.inner_text()) or '').strip()!r} → {await lnk.get_attribute('href')!r}")

            # Clic en menú Competiciones / Competitions
            for sel in ['a:has-text("Competiciones")', 'button:has-text("Competiciones")',
                        'a:has-text("Competitions")', 'button:has-text("Competitions")',
                        'span:has-text("Competiciones")', 'span:has-text("Competitions")']:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    print(f"  Menú principal con: {sel}")
                    await page.wait_for_timeout(1000)
                    break

            # Clic en Mis competiciones / My competitions
            for sel in ['a:has-text("Mis competiciones")', 'a:has-text("My competitions")',
                        'button:has-text("Mis competiciones")', 'button:has-text("My competitions")',
                        'a:has-text("My Competitions")']:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    print(f"  Mis competiciones con: {sel}")
                    break

            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)
            print(f"URL tras navegación: {page.url}")

            # Debug: imprimir texto de la página para ver la tabla
            body_text = await page.inner_text("body")
            print(f"[Debug] Primeros 1500 chars:\n{body_text[:1500]}")

            # 3. Buscar la competición PISTOLA AIRE 10M más reciente
            rows = await page.query_selector_all("tr, .competition-row, .list-item, li")
            target_row = None
            comp_date  = None

            for row in rows:
                text = (await row.inner_text()).upper()
                if COMP_KEYWORD in text and "PREPARATORIA" in text:
                    target_row = row
                    date_m = re.search(r'\d{2}/\d{2}/\d{4}', await row.inner_text())
                    if date_m:
                        comp_date = date_m.group(0)
                    print(f"  Competición encontrada: {(await row.inner_text())[:150]!r}")
                    break

            if not target_row:
                print("No se encontró la competición. ¿Aún no publicada?")
                return

            # 4. Comprobar si ya fue notificada
            if comp_date and comp_date == LAST_SCORES:
                print(f"Sin cambios: puntuaciones del {comp_date} ya notificadas.")
                return

            # 5. Buscar el botón/enlace de resultados en la fila
            results_el = None
            for sel in ['a:has-text("Resultados")', 'button:has-text("Resultados")',
                        'a:has-text("Ver resultados")', 'a[href*="result"]']:
                results_el = await target_row.query_selector(sel)
                if results_el:
                    print(f"  Enlace resultados con: {sel}")
                    break

            if not results_el:
                print("No hay enlace de resultados en la fila (¿no publicados aún?)")
                return

            await results_el.click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)
            print(f"En resultados. URL: {page.url}")

            # Debug
            page_text = await page.inner_text("body")
            print(f"[Debug resultados] Primeros 1500 chars:\n{page_text[:1500]}")

            if TARGET_NAME.upper() not in page_text.upper():
                print(f"No se encontró {TARGET_NAME} en resultados (¿no publicados?)")
                return

            # 6. Extraer clasificaciones
            clasif_parts = re.findall(r'Clasif\.\s+([^:()]+)[:\s]+(\d+)', page_text, re.IGNORECASE)
            clasif_str = "  ".join([f"{k.strip()}: {v}º" for k, v in clasif_parts]) if clasif_parts else ""
            print(f"  Clasificaciones: {clasif_str}")

            # 7. Extraer puesto, total, X's de la fila del tirador
            # Formato: "Prueba - Tanda N | fecha | fecha | puesto | total | xs"
            puesto = total = xs = fecha_comp = "?"
            prueba_m = re.search(
                r'Prueba[^\n]*Tanda\s*\d+[^\n]*?(\d{2}/\d{2}/\d{4})[^\n]*?(\d+)\s+(\d{3})\s+(\d+)',
                page_text, re.IGNORECASE
            )
            if prueba_m:
                fecha_comp = prueba_m.group(1)
                puesto     = prueba_m.group(2)
                total      = prueba_m.group(3)
                xs         = prueba_m.group(4)
                print(f"  Fecha={fecha_comp} Puesto={puesto} Total={total} X's={xs}")
            else:
                fecha_comp = comp_date or "?"
                print("  No se pudo parsear fila de puntuación, buscando números...")
                nums = re.findall(r'\b(\d{3})\b', page_text)
                if nums:
                    total = nums[0]

            # 8. Clic en Detalles para el desglose de series
            detalles_el = None
            for sel in ['button:has-text("Detalles")', 'a:has-text("Detalles")',
                        'button:has-text("Ver detalles")', 'span:has-text("Detalles")']:
                detalles_el = await page.query_selector(sel)
                if detalles_el:
                    print(f"  Botón detalles con: {sel}")
                    break

            series = []
            if detalles_el:
                await detalles_el.click()
                await page.wait_for_timeout(2000)
                detail_text = await page.inner_text("body")
                print(f"[Debug detalles] Primeros 1000 chars:\n{detail_text[:1000]}")
                series = parse_series(detail_text)
                print(f"  Series extraídas: {len(series)}")
            else:
                print("  Botón Detalles no encontrado")

            # 9. Construir y enviar mensaje
            score_key = f"{fecha_comp}_{total}"
            if score_key == LAST_SCORES:
                print(f"Sin cambios: puntuaciones {score_key} ya notificadas.")
                return

            msg = build_message(fecha_comp, clasif_str, puesto, total, xs, series)
            send_telegram(msg)
            save_last_scores(score_key)
            print(f"Puntuaciones enviadas. Clave: {score_key}")

        except Exception as e:
            print(f"ERROR: {e}")
            send_telegram(f"❌ *Error en bot Wirtex*\n`{e}`")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
