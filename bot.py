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
# PDF parsing
# ---------------------------------------------------------------------------
def parse_pdf(pdf_bytes):
    """Devuelve (puesto, tanda, linea_raw) para el tirador con TARGET_NFED."""
    reader = PdfReader(io.BytesIO(pdf_bytes))

    for page in reader.pages:
        text = page.extract_text() or ""
        if TARGET_NFED not in text:
            continue

        lines = [l.strip() for l in text.split("\n") if l.strip()]
        print(f"[PDF] {len(lines)} líneas en esta página, buscando N Fed {TARGET_NFED}...")

        # --- Estrategia A: "TANDA X" como cabecera de sección ---
        current_tanda = None
        for line in lines:
            m = re.match(r"TANDA\s+(\d+)", line, re.IGNORECASE)
            if m:
                current_tanda = m.group(1)
                continue
            if TARGET_NFED in line:
                nums = re.findall(r"\d+", line)
                nfed_idx = next((i for i, n in enumerate(nums) if n == TARGET_NFED), -1)
                puesto = nums[nfed_idx - 1] if nfed_idx > 0 else None
                if puesto and current_tanda:
                    print(f"[PDF] Estrategia A → Puesto: {puesto}, Tanda: {current_tanda}")
                    return puesto, current_tanda, line

        # --- Estrategia B: tabla plana, tanda como columna ---
        for line in lines:
            if TARGET_NFED not in line:
                continue
            nums = re.findall(r"\d+", line)
            nfed_idx = next((i for i, n in enumerate(nums) if n == TARGET_NFED), -1)
            if nfed_idx < 0:
                continue
            puesto = nums[nfed_idx - 1] if nfed_idx > 0 else (nums[0] if nums else None)
            # La tanda suele ser el último número pequeño (≤ 20)
            tanda = next((n for n in reversed(nums) if int(n) <= 20), None)
            if puesto and tanda:
                print(f"[PDF] Estrategia B → Puesto: {puesto}, Tanda: {tanda}")
                return puesto, tanda, line

        # --- Fallback: devolver lo que haya ---
        for line in lines:
            if TARGET_NFED in line:
                nums = re.findall(r"\d+", line)
                nfed_idx = next((i for i, n in enumerate(nums) if n == TARGET_NFED), -1)
                puesto = nums[nfed_idx - 1] if nfed_idx > 0 else "?"
                tanda  = nums[-1] if nums else "?"
                print(f"[PDF] Fallback → Puesto: {puesto}, Tanda: {tanda}")
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

            # 4. Entrar a la página de la competición
            await page.goto(competition_url, wait_until="networkidle")

            # 5. Localizar el PDF (varios métodos)
            pdf_url = None

            # Método A: enlace directo con extensión .pdf
            el = await page.query_selector("a[href$='.pdf'], a[href*='.pdf?']")
            if el:
                href = await el.get_attribute("href")
                pdf_url = href if href.startswith("http") else f"{BASE_URL}{href}"
                print(f"[PDF] Método A (enlace directo): {pdf_url}")

            # Método B: Phoca Download u otro enlace de descarga genérico
            if not pdf_url:
                for sel in [
                    "a[href*='phocadownload']",
                    "a[href*='download']",
                    "a:has-text('Descargar')",
                    "a:has-text('PDF')",
                    "a:has-text('Resultados')",
                ]:
                    el = await page.query_selector(sel)
                    if el:
                        href = (await el.get_attribute("href")) or ""
                        if href:
                            pdf_url = href if href.startswith("http") else f"{BASE_URL}{href}"
                            print(f"[PDF] Método B ({sel}): {pdf_url}")
                            break

            # Método C: formulario Phoca Download (checkbox + submit) → interceptar descarga
            if not pdf_url:
                submit_btn = await page.query_selector('input[name="pdlicensesubmit"]')
                if submit_btn:
                    checkbox = await page.query_selector('input[type="checkbox"]')
                    if checkbox and not await checkbox.is_checked():
                        await checkbox.check()
                        await page.wait_for_timeout(500)
                    async with page.expect_download(timeout=20000) as dl_info:
                        await submit_btn.click(force=True)
                    download = await dl_info.value
                    tmp = "/tmp/fmto_result.pdf"
                    await download.save_as(tmp)
                    pdf_bytes = open(tmp, "rb").read()
                    print(f"[PDF] Método C (formulario): {len(pdf_bytes)} bytes")
                    result = parse_pdf(pdf_bytes)
                    _notify(result, competition_url)
                    return

            if not pdf_url:
                raise Exception("No se encontró ningún enlace de descarga de PDF en la página de la competición.")

            # 6. Descargar PDF reutilizando las cookies de sesión de Playwright
            cookies = await context.cookies()
            session = requests.Session()
            for c in cookies:
                session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))

            print(f"[PDF] Descargando con sesión autenticada: {pdf_url}")
            res = session.get(pdf_url, timeout=30)
            res.raise_for_status()
            print(f"[PDF] Descargado: {len(res.content)} bytes")

            # 7. Parsear y notificar
            result = parse_pdf(res.content)
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
        f"🏅 Puesto: *{puesto}*\n"
        f"⏱ Tanda: *{tanda}*\n\n"
        f"[Ver competición]({competition_url})"
    )
    send_telegram(msg)
    print(f"Resultado enviado → Puesto: {puesto} | Tanda: {tanda}")


if __name__ == "__main__":
    asyncio.run(run())
