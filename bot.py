import os
import asyncio
from playwright.async_api import async_playwright
import requests
import io
import re
try:
    from pypdf import PdfReader
except ImportError:
    os.system('pip install pypdf')
    from pypdf import PdfReader

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        print("Logueando en FMTO...")
        await page.goto("https://www.fmto.net/acceso-federados")
        await page.fill('input[name="username"]', os.getenv("FMTO_USER"))
        await page.fill('input[name="password"]', os.getenv("FMTO_PASS"))
        await page.click('button[type="submit"]')
        await page.wait_for_timeout(3000)

        await page.goto("https://www.fmto.net/competiciones/provpues")
        await page.wait_for_load_state("networkidle")
        
        # Buscamos el enlace
        all_links = await page.query_selector_all("a")
        pdf_url = None
        for l in all_links:
            t = await l.inner_text()
            h = await l.get_attribute("href")
            if h and "PISTOLA" in t.upper() and "AIRE" in t.upper():
                pdf_url = h
                print(f"Encontrado: {t}")
                break
        
        token = os.getenv("TELEGRAM_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")

        if pdf_url:
            full_url = pdf_url if pdf_url.startswith("http") else f"https://www.fmto.net{pdf_url}"
            print(f"Procesando PDF: {full_url}")
            
            res = requests.get(full_url)
            reader = PdfReader(io.BytesIO(res.content))
            target = "65226"
            found_line = None
            
            for p_pdf in reader.pages:
                lines = p_pdf.extract_text().split('\n')
                for line in lines:
                    if target in line:
                        found_line = line
                        break
            
            if found_line:
                msg = f"🎯 *¡FICHA ENCONTRADA!*\n\n`{found_line}`\n\n[Ver PDF]({full_url})"
            else:
                msg = f"✅ PDF de Pistola Aire detectado, pero no veo tu federado {target} dentro.\n[Abrir PDF para revisar]({full_url})"
            
            requests.get(f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={msg}&parse_mode=Markdown")
        else:
            print("No se encontró ningún link con 'PISTOLA' y 'AIRE'.")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
