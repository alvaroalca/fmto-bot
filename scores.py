import os
import asyncio
import re
import requests
from datetime import date, datetime
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
TARGET_NAME   = "ALVARO ALCARAZ"


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

            # Rellenar y enviar el formulario de login via JavaScript
            # (evita problemas de overlays que bloquean clicks)
            await page.evaluate(
                """([user, pwd]) => {
                    const inputs = [...document.querySelectorAll('input:not([type="hidden"])')];
                    const userInput = inputs.find(i =>
                        i.type === 'email' ||
                        (i.name || '').toLowerCase().includes('user') ||
                        (i.name || '').toLowerCase().includes('mail') ||
                        (i.name || '').toLowerCase().includes('login')
                    ) || inputs[0];
                    const passInput = inputs.find(i => i.type === 'password');

                    function setVal(el, val) {
                        const nativeSet = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        nativeSet.call(el, val);
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                    if (userInput) setVal(userInput, user);
                    if (passInput) setVal(passInput, pwd);
                }""",
                [WIRTEX_USER, WIRTEX_PASS]
            )
            print("  Campos rellenados via JS")

            # Enviar el formulario via JS (bypassa el overlay)
            await page.evaluate("""
                const btn = document.querySelector('button[type="submit"], input[type="submit"]');
                if (btn) btn.click();
                else { const f = document.querySelector('form'); if (f) f.submit(); }
            """)
            print("  Submit via JS")

            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)

            # Verificar login
            body = await page.inner_text("body")
            if "INVITADO" in body.upper() or "GUEST" in body.upper():
                raise Exception("Login fallido en Wirtex: sigue mostrando usuario invitado.")
            print(f"Sesión iniciada. URL: {page.url}")

            # 2. Ya estamos en /Mobile/GLB/Competicion/CompeticionesGrid tras el login
            #    Inspeccionamos el HTML de las filas via JS para encontrar los botones de acción
            print("Inspeccionando filas de competición...")
            row_infos = await page.evaluate("""
                () => {
                    const rows = [...document.querySelectorAll('tr')];
                    return rows.map(row => ({
                        text: row.innerText.trim().substring(0, 120),
                        html: row.outerHTML.substring(0, 1500),
                        clickables: [...row.querySelectorAll('a,button,img,[onclick]')].map(el => ({
                            tag:     el.tagName,
                            href:    el.getAttribute('href') || '',
                            onclick: el.getAttribute('onclick') || '',
                            cls:     el.className || '',
                            src:     el.getAttribute('src') || '',
                            text:    (el.innerText || '').trim().substring(0, 30),
                        }))
                    }));
                }
            """)
            for ri in row_infos:
                if "PREPARATORIA" in ri["text"].upper():
                    print(f"\n[ROW] {ri['text']!r}")
                    print(f"  HTML: {ri['html'][:600]}")
                    for c in ri["clickables"]:
                        print(f"  clickable: {c}")

            # 3. Buscar la competición más reciente YA PASADA y hacer clic en resultados via JS
            today     = date.today()
            comp_date = None

            clicked = await page.evaluate("""
                (todayStr) => {
                    const [ty, tm, td] = todayStr.split('-').map(Number);
                    const todayTs = new Date(ty, tm-1, td).getTime();

                    const rows = [...document.querySelectorAll('tr')];
                    for (const row of rows) {
                        const text = row.innerText.toUpperCase();
                        if (!text.includes('PISTOLA AIRE') || !text.includes('PREPARATORIA')) continue;

                        const dm = row.innerText.match(/(\d{2})\/(\d{2})\/(\d{4})/);
                        if (!dm) continue;
                        const rowTs = new Date(+dm[3], +dm[2]-1, +dm[1]).getTime();
                        if (rowTs > todayTs) continue;   // futura, saltar

                        // Buscar todos los elementos clicables de la fila
                        const clickables = [...row.querySelectorAll('a,button,img,[onclick]')];
                        // El botón de resultados es el segundo (el primero es inscripción)
                        if (clickables.length >= 2) {
                            clickables[1].click();
                            return dm[0];   // devuelve la fecha "DD/MM/YYYY"
                        } else if (clickables.length === 1) {
                            return 'SOLO_UN_BOTON:' + dm[0];
                        } else {
                            return 'SIN_BOTONES:' + dm[0];
                        }
                    }
                    return null;
                }
            """, str(today))

            print(f"  JS click resultado: {clicked!r}")

            if not clicked:
                print("No se encontró competición pasada con resultados.")
                return
            if clicked.startswith("SOLO_UN_BOTON:") or clicked.startswith("SIN_BOTONES:"):
                print(f"Competición encontrada pero sin botón de resultados: {clicked}")
                return

            comp_date = clicked

            # 5. Comprobar si ya fue notificada
            if comp_date == LAST_SCORES:
                print(f"Sin cambios: {comp_date} ya notificado.")
                return

            # 6. Abrir página de resultados
            await results_btn.click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)
            print(f"Página resultados cargada.")

            # 7. Buscar TARGET_NAME paginando si es necesario
            found = False
            for _ in range(10):   # máximo 10 páginas
                page_text = await page.inner_text("body")
                if TARGET_NAME.upper() in page_text.upper():
                    found = True
                    break
                # Ir a siguiente página
                next_btn = None
                for sel in ['a:has-text("›")', 'a:has-text(">")',
                            'a[title="siguiente"]', 'a.next']:
                    nb = await page.query_selector(sel)
                    if nb and await nb.is_visible():
                        next_btn = nb
                        break
                if not next_btn:
                    break
                await next_btn.click()
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(1000)

            if not found:
                print(f"No se encontró {TARGET_NAME} en los resultados.")
                return

            # 8. Extraer clasificaciones, puesto, total, tanda de la zona del tirador
            # El texto del área de ALCARAZ tiene el formato:
            # "ALVARO ALCARAZ PEREZ (Clasif. SENIOR: N) ... Prueba - Tanda N fecha fecha puesto total clasif"
            idx = page_text.upper().index(TARGET_NAME.upper())
            snippet = page_text[max(0, idx-50): idx+600]
            print(f"[Snippet tirador]\n{snippet}")

            clasif_parts = re.findall(
                r'Clasif\.\s+([A-ZÁ-Ú /ª0-9]+?)\s*[:]\s*(\d+)',
                snippet, re.IGNORECASE
            )
            clasif_str = "  |  ".join(
                [f"{k.strip()}: {v}º" for k, v in clasif_parts]
            ) if clasif_parts else ""

            # Fila de datos: "Prueba - Tanda N DD/MM/YYYY ... puesto total clasif"
            prueba_m = re.search(
                r'Prueba\s*-\s*Tanda\s*(\d+)[^\d]*(\d{2}/\d{2}/\d{4})[^\d]+(\d+)\s+(\d{3,})\s+(\d+)',
                snippet, re.IGNORECASE
            )
            tanda = puesto = total = xs = "?"
            fecha_comp = comp_date or "?"
            if prueba_m:
                tanda      = prueba_m.group(1)
                fecha_comp = prueba_m.group(2)
                puesto     = prueba_m.group(3)
                total      = prueba_m.group(4)
                xs         = prueba_m.group(5)
                print(f"  Tanda={tanda} Fecha={fecha_comp} Puesto={puesto} "
                      f"Total={total} Clasif={xs}")

            # 9. Clic en botón expandir (+) de la fila del tirador para ver tiros
            # El botón + está en la fila del nombre del tirador
            archer_row = None
            all_rows = await page.query_selector_all("tr")
            for row in all_rows:
                rt = await row.inner_text()
                if TARGET_NAME.upper() in rt.upper():
                    archer_row = row
                    break

            series = []
            if archer_row:
                expand_btn = await archer_row.query_selector("a, button")
                if expand_btn:
                    await expand_btn.click()
                    await page.wait_for_load_state("networkidle")
                    await page.wait_for_timeout(2000)
                    detail_text = await page.inner_text("body")
                    print(f"[Debug detalles] Primeros 800:\n{detail_text[:800]}")

                    # Extraer Nº de 10 interior de la cabecera
                    xs_m = re.search(r'N[oº]\s*(?:de\s*)?10\s*interior[:\s]+(\d+)',
                                     detail_text, re.IGNORECASE)
                    if xs_m:
                        xs = xs_m.group(1)

                    series = parse_series(detail_text)
                    print(f"  Series extraídas: {len(series)}")
                else:
                    print("  No se encontró botón + en la fila del tirador")
            else:
                print("  No se encontró fila del tirador")

            # 10. Construir y enviar mensaje
            score_key = f"{fecha_comp}_{total}"
            if score_key == LAST_SCORES:
                print(f"Sin cambios: {score_key} ya notificado.")
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
