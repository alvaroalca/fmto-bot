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
    if r.status_code in (404, 403):
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
    """Extrae lista de {score, shots} de cada serie.
    Formato mobile: 'Serie N\\t93 Ptos.\\n1\\n10\\n 2\\nX\\n...'
    Los tokens alternan: posición (1-10), valor del disparo.
    """
    series = []
    blocks = re.split(r'Serie\s+\d+', text, flags=re.IGNORECASE)
    for block in blocks[1:]:
        score_m = (re.search(r'(\d+)\s*Ptos\.', block) or
                   re.search(r'Puntuaci[oó]n de la serie[:\s]+(\d+)', block, re.IGNORECASE))
        if not score_m:
            continue
        score = int(score_m.group(1))
        after = block[score_m.end():]
        # Tokens alternan posición/valor; tomar los de índice impar (valores)
        tokens = re.findall(r'\b(X|10|[0-9]|/)\b', after)
        shots = tokens[1::2]   # val1, val2, ... (posiciones en índices pares)
        if len(shots) < 5:    # fallback si el patrón no encaja
            shots = [t for t in tokens
                     if not (t.isdigit() and 1 <= int(t) <= 9)][:10]
        series.append({"score": score, "shots": shots[:10]})
    return series


# ---------------------------------------------------------------------------
# Construcción del mensaje
# ---------------------------------------------------------------------------
def build_message(fecha, clasificaciones, clasif_general, total, xs, series):
    lines = [
        f"🎯 *Puntuaciones - Preparatoria Pistola Aire 10m*",
        f"📅 {fecha}",
        "",
    ]
    if clasificaciones:
        lines.append(clasificaciones)
        lines.append("")
    lines.append(f"🏅 Clasif: *{clasif_general}º* | 💯 Total: *{total}* | ✖️ X's: *{xs}*")
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

            # Extraer la URL de detalle del onclick del <tr> para la competición más reciente pasada
            det_url = await page.evaluate("""
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
                        if (rowTs > todayTs) continue;
                        // Extraer URL del onclick: onclick="document.location.href = '/Mobile/...'"
                        const oc = row.getAttribute('onclick') || '';
                        const m = oc.match(/href\\s*=\\s*'([^']+)'/);
                        return m ? {url: m[1], date: dm[0]} : null;
                    }
                    return null;
                }
            """, str(today))

            if not det_url:
                print("No se encontró competición pasada con resultados.")
                return

            comp_date = det_url["date"]
            comp_url  = "https://www.wirtexsports.com" + det_url["url"]
            print(f"  Competición: {comp_date} → {comp_url}")

            # 5. Comprobar si ya fue notificada
            if comp_date == LAST_SCORES:
                print(f"Sin cambios: {comp_date} ya notificado.")
                return

            # 6. Navegar al detalle de la competición y buscar el enlace de resultados
            await page.goto(comp_url, wait_until="networkidle")
            await page.wait_for_timeout(2000)
            print(f"Detalle competición cargado. URL: {page.url}")

            det_text = await page.inner_text("body")
            print(f"[Debug detalle] Primeros 1000:\n{det_text[:1000]}")

            # Listar todos los links del detalle para encontrar el de resultados/puntuaciones
            det_links = await page.evaluate("""
                () => [...document.querySelectorAll('a[href], [onclick]')].map(el => ({
                    tag:     el.tagName,
                    href:    el.getAttribute('href') || '',
                    onclick: el.getAttribute('onclick') || '',
                    text:    (el.innerText || '').trim().substring(0, 60),
                }))
            """)
            for lnk in det_links:
                print(f"  [det link] {lnk}")

            # Navegar al enlace de puntuaciones/resultados
            scores_url = None
            for lnk in det_links:
                href = lnk["href"].lower()
                oc   = lnk["onclick"].lower()
                if any(k in href or k in oc for k in
                       ["puntuacion", "resultado", "score", "clasif"]):
                    raw = lnk["href"] or re.search(r"href\s*=\s*'([^']+)'", lnk["onclick"], re.I)
                    if isinstance(raw, str) and raw:
                        scores_url = ("https://www.wirtexsports.com" + raw
                                      if raw.startswith("/") else raw)
                        break
                    if hasattr(raw, "group"):
                        scores_url = "https://www.wirtexsports.com" + raw.group(1)
                        break

            if not scores_url:
                print("No se encontró enlace de puntuaciones en el detalle.")
                return

            print(f"  URL puntuaciones: {scores_url}")
            await page.goto(scores_url, wait_until="networkidle")
            await page.wait_for_timeout(2000)
            print(f"Página resultados cargada.")

            # 7. Extraer datos de la página de Puntuación Individual
            #    Hacer scroll hasta el fondo para forzar carga de las 6 series
            for _ in range(4):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(800)

            page_text = await page.inner_text("body")
            print(f"[Puntuación] Texto completo ({len(page_text)} chars):\n{page_text}")

            # Total (aparece como "Ptos.\n542" — label en línea propia)
            total_m = re.search(r'\bPtos\.\s*\n\s*(\d+)', page_text)
            total   = total_m.group(1) if total_m else "?"

            # X's / 10 interior (aparece como "10i\n5")
            xs_m = re.search(r'\b10i\s*\n\s*(\d+)', page_text)
            xs   = xs_m.group(1) if xs_m else "?"

            # Clasificación general (aparece como "Clas.\n12")
            clas_m        = re.search(r'\bClas\.\s*\n\s*(\d+)', page_text)
            clasif_general = clas_m.group(1) if clas_m else "?"

            # Clasificaciones por categoría
            cat_m    = re.search(r'Clasf\. Cat\.\s*\n\s*([^\n\t]+)', page_text)
            niv_m    = re.search(r'Clasf\. Niv\.\s*\n\s*([^\n\t]+)', page_text)
            catniv_m = re.search(r'Clasf\. Cat/Niv\.\s*\n\s*([^\n\t]+)', page_text)
            clasif_parts = []
            if cat_m:    clasif_parts.append(f"Cat: {cat_m.group(1).strip()}")
            if niv_m:    clasif_parts.append(f"Niv: {niv_m.group(1).strip()}")
            if catniv_m: clasif_parts.append(f"Cat/Niv: {catniv_m.group(1).strip()}")
            clasif_str = "  |  ".join(clasif_parts)

            print(f"  Total={total} X's={xs} Clasif={clasif_general}")
            print(f"  Clasificaciones: {clasif_str}")

            fecha_comp = comp_date or "?"

            # 8. Parsear series de tiros
            series = parse_series(page_text)
            print(f"  Series extraídas: {len(series)}")

            # 9. Construir y enviar mensaje
            score_key = f"{fecha_comp}_{total}"
            if score_key == LAST_SCORES:
                print(f"Sin cambios: {score_key} ya notificado.")
                return

            msg = build_message(fecha_comp, clasif_str, clasif_general, total, xs, series)
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
