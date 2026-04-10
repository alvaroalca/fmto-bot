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

        # 1. Login
        print("Logueando en FMTO...")
        await page.goto("https://www.fmto.net/acceso-federados")
        await page.fill('input[name="username"]', os.getenv("FMTO_USER"))
        await page.fill('input[name="password"]', os.getenv("FMTO_PASS"))
        await page.click('button[type="submit"]')
        await page.wait_for_timeout(3000)

        # 2. Ir a la lista de puestos
        await page.goto("https://www.fmto.net/competiciones/provpues")
        
        # 3. Buscar el enlace de la preparatoria más reciente
        # Usamos una expresión regular para que encuentre el texto sin importar mayúsculas
        link = page.get_by_role("link", name=re.compile(r"PREPARATORIA PISTOLA AIRE 10M", re.I)).first
        
        if await link.count() > 0:
            pdf_url = await link.get_attribute("href")
            if not pdf_url.startswith("http"):
                pdf_url = "https://www.fmto.net" + pdf_url
            
            print(f"¡PDF encontrado!: {pdf_url}")

            # 4. Descargar el PDF en memoria
            response = requests.get(pdf_url)
            pdf_file = io.BytesIO(response.content)
            
            # 5. Leer el PDF buscando el N Fed 65226
            reader = PdfReader(pdf_file)
            found_text = ""
            target_fed = "65226" # Tu número de federado
            
            for page_pdf in reader.pages:
                text = page_pdf.extract_text()
                if target_fed in text:
                    # Buscamos la línea que contiene tu número
                    lines = text.split('\n')
                    for line in lines:
                        if target_fed in line:
                            found_text = line
                            break
            
            # 6. Enviar resultado a Telegram
            token = os.getenv("TELEGRAM_TOKEN")
            chat_id = os.getenv("TELEGRAM_CHAT_ID")
            
            if found_text:
                msg = f"🎯 ¡Encontrado en el listado!\n\nLínea detectada:\n`{found_text}`\n\nPDF: {pdf_url}"
            else:
                msg = f"✅ PDF revisado pero NO se encontró el N Fed {target_fed}.\nPDF: {pdf_url}"
                
            requests.get(f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={msg}&parse_mode=Markdown")
            print("Mensaje enviado a Telegram.")
        else:
            print("No se encontró ningún enlace de Pistola Aire 10m.")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
