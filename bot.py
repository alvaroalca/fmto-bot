import os
import asyncio
import io
import re
import requests
from playwright.async_api import async_playwright
from pypdf import PdfReader

FMTO_USER        = os.getenv("FMTO_USER")
FMTO_PASS        = os.getenv("FMTO_PASS")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
LAST_COMPETITION = os.getenv("LAST_COMPETITION", "")   # última tirada ya notificada
GITHUB_TOKEN     = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "") # p.ej. "alvaroalca/fmto-bot"

BASE_URL    = "https://www.fmto.net"
TARGET_NFED = "65226"


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    })
    print(f"[Telegram] status={r.status_code} respuesta={r.text[:300]}")


def send_telegram_photo(path, caption=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    with open(path, "rb") as f:
        r = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption}, files={"photo": f})
    print(f"[Telegram foto] status={r.status_code} respuesta={r.text[:200]}")


# ---------------------------------------------------------------------------
# Memoria: variable del repositorio en GitHub Actions
# ---------------------------------------------------------------------------
def save_last_competition(url):
    """Guarda la URL de la competición notificada como variable del repo."""
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        print("[Memoria] Sin GITHUB_TOKEN/REPOSITORY, no se puede guardar.")
        return
    api = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/variables/LAST_COMPETITION"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # Intentar actualizar (PATCH); si no existe, crear (POST)
    r = requests.patch(api, json={"name": "LAST_COMPETITION", "value": url}, headers=headers)
    if r.status_code == 404:
        r = requests.post(
            f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/variables",
            json={"name": "LAST_COMPETITION", "value": url},
            headers=headers,
        )
    print(f"[Memoria] Guardada competición: {url} (status={r.status_code})")


# ---------------------------------------------------------------------------
# PDF parsing
# ---------------------------------------------------------------------------
def parse_pdf(pdf_bytes):
    """Devuelve (puesto, tanda, linea_raw) para el tirador con TARGET_NFED.

    El PDF tiene el formato (sin espacios entre columnas):
      MODALIDAD + NFed + Nivel(1 dígito) + Categoría(texto) + Puesto + Tanda
    Ejemplo: 'PISTOLA AIRE 10 M652263SENIOR61'
      → NFed=65226, Nivel=3, Cat=SENIOR, Puesto=6, Tanda=1

    Estrategia: tomar el sufijo tras el NFed, coger el último bloque de dígitos
    (puesto+tanda concatenados) y separar: último dígito = tanda, el resto = puesto.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))

    for page in reader.pages:
        text = page.extract_text() or ""
        if TARGET_NFED not in text:
            continue

        lines = [l.strip() for l in text.split("\n") if l.strip()]
        print(f"[PDF] Página con {TARGET_NFED} encontrada ({len(lines)} líneas)")

        for line in lines:
            if TARGET_NFED not in line:
                continue

            print(f"[PDF] Línea: {line!r}")

            # Texto tras el N Fed: p.ej. '3SENIOR61'
            suffix = line[line.index(TARGET_NFED) + len(TARGET_NFED):]

            # Último bloque de dígitos al final del sufijo: p.ej. '61'
            m = re.search(r"(\d+)$", suffix)
            if not m or len(m.group(1)) < 2:
                continue

            combined = m.group(1)       # '61'
            tanda    = combined[-1]     # '1'
            puesto   = combined[:-1]    # '6'

            print(f"[PDF] Sufijo={suffix!r} → combined={combined!r} → Puesto={puesto}, Tanda={tanda}")
            return puesto, tanda, line

    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        try:
            # 1. Login  (selectores confirmados en el código original)
            print("Logueando en FMTO...")
            await page.goto(f"{BASE_URL}/acceso-federados", wait_until="networkidle")
            await page.fill('input[name="username"]', FMTO_USER)
            await page.fill('input[name="password"]', FMTO_PASS)

            # Esperar la navegación que ocurre tras el submit
            async with page.expect_navigation(wait_until="networkidle", timeout=15000):
                await page.click('button[type="submit"]')

            print(f"Sesión iniciada. URL actual: {page.url}")

            # 2. Ir a la lista de competiciones
            print("Navegando a la lista de preparatorias...")
            await page.goto(f"{BASE_URL}/competiciones/provpues", wait_until="networkidle")

            # 3. Encontrar el enlace de la PREPARATORIA PISTOLA AIRE 10M más reciente
            # (se asume que la lista está ordenada por fecha descendente)
            competition_url = None
            for link in await page.query_selector_all("a"):
                text = ((await link.inner_text()) or "").upper().strip()
                href = (await link.get_attribute("href")) or ""
                if "PREPARATORIA" in text and "PISTOLA" in text and \
                   ("AIRE" in text or "10M" in text or "pistola-aire" in href):
                    competition_url = href if href.startswith("http") else f"{BASE_URL}{href}"
                    print(f"Competición encontrada: {text!r} → {competition_url}")
                    break

            if not competition_url:
                raise Exception("No se encontró ninguna PREPARATORIA PISTOLA AIRE 10M en la lista.")

            # ¿Es una tirada nueva?
            if competition_url == LAST_COMPETITION:
                print(f"Sin cambios: esta tirada ya fue notificada ({competition_url}). Nada que hacer.")
                return

            print(f"¡Nueva tirada detectada! Procesando...")

            # 4. Entrar a la página de la competición
            await page.goto(competition_url, wait_until="networkidle")


            # 5. Descargar el PDF interceptando la descarga del navegador
            # Solo se consideran links del propio dominio fmto.net (no externos como phoca.cz)
            pdf_bytes = None

            def is_fmto_href(href):
                if not href:
                    return False
                if href.startswith("http"):
                    return "fmto.net" in href
                return True  # relativo → es del mismo dominio

            # Método A: enlace directo .pdf en fmto.net
            for lnk in await page.query_selector_all("a[href$='.pdf'], a[href*='.pdf?']"):
                href = (await lnk.get_attribute("href")) or ""
                if is_fmto_href(href):
                    full = href if href.startswith("http") else f"{BASE_URL}{href}"
                    print(f"[PDF] Método A: {full}")
                    cookies = await context.cookies()
                    s = requests.Session()
                    for c in cookies:
                        s.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
                    r = s.get(full, timeout=30)
                    r.raise_for_status()
                    pdf_bytes = r.content
                    print(f"[PDF] Descargado: {len(pdf_bytes)} bytes")
                    break

            # Método B: formulario Phoca Download — id="pdlicensesubmit", name="submit"
            if not pdf_bytes:
                submit_btn = await page.query_selector('input[id="pdlicensesubmit"]')
                if submit_btn:
                    print("[PDF] Método B: formulario Phoca Download (id=pdlicensesubmit)")
                    async with page.expect_download(timeout=20000) as dl_info:
                        await submit_btn.click()
                    download = await dl_info.value
                    tmp = "/tmp/fmto_result.pdf"
                    await download.save_as(tmp)
                    pdf_bytes = open(tmp, "rb").read()
                    print(f"[PDF] Descargado: {len(pdf_bytes)} bytes")

            # Método C: cualquier link de descarga en fmto.net → interceptar como descarga
            if not pdf_bytes:
                for lnk in await page.query_selector_all("a"):
                    href = (await lnk.get_attribute("href")) or ""
                    text = ((await lnk.inner_text()) or "").strip().lower()
                    if not is_fmto_href(href):
                        continue
                    if any(x in href for x in ["com_phocadownload", "download", "descargar"]) or \
                       any(x in text for x in ["descargar", "pdf", "resultado"]):
                        print(f"[PDF] Método C: {href!r} (texto: {text!r})")
                        try:
                            async with page.expect_download(timeout=15000) as dl_info:
                                await lnk.click()
                            download = await dl_info.value
                            tmp = "/tmp/fmto_result.pdf"
                            await download.save_as(tmp)
                            pdf_bytes = open(tmp, "rb").read()
                            print(f"[PDF] Descargado: {len(pdf_bytes)} bytes")
                        except Exception as e:
                            print(f"[PDF] Método C falló ({e}), probando siguiente...")
                            await page.goto(competition_url, wait_until="networkidle")
                            continue
                        break

            if not pdf_bytes:
                raise Exception("No se encontró ningún enlace de descarga de PDF en la página de la competición.")

            # 6. Parsear y notificar
            result = parse_pdf(pdf_bytes)
            _notify(result, competition_url)

        except Exception as e:
            print(f"ERROR: {e}")
            send_telegram(f"❌ *Error en el bot FMTO*\n`{e}`")

        finally:
            await browser.close()


def _notify(result, competition_url):
    if not result:
        raise Exception(f"El N Fed {TARGET_NFED} no apareció en el PDF o no se pudo extraer puesto/tanda.")

    puesto, tanda, _ = result
    comp_name = competition_url.split("/")[-1].replace("-", " ").title()
    msg = (
        f"🎯 *Preparatoria Pistola Aire 10m*\n"
        f"📋 {comp_name}\n\n"
        f"N Fed: `{TARGET_NFED}`\n"
        f"📍 Puesto: *{puesto}*\n"
        f"⏱ Tanda: *{tanda}*"
    )
    send_telegram(msg)
    print(f"Resultado enviado → Puesto: {puesto} | Tanda: {tanda}")
    save_last_competition(competition_url)


if __name__ == "__main__":
    asyncio.run(run())
