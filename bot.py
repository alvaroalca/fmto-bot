import os
import asyncio
from playwright.async_api import async_playwright
import requests
import io
import re

async def run():
    async with async_playwright() as p:
        # Abrimos el navegador
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 720})
        page = await context.new_page()

        # 1. Login
        print("Logueando en FMTO...")
        await page.goto("https://www.fmto.net/acceso-federados", wait_until="networkidle")
        await page.fill('input[name="username"]', os.getenv("FMTO_USER"))
        await page.fill('input[name="password"]', os.getenv("FMTO_PASS"))
        await page.click('button[type="submit"]')
        
        # Esperamos a que el login procese
        await page.wait_for_timeout(5000)

        # 2. Ir a la lista de puestos
        print("Navegando a Puestos...")
        await page.goto("https://www.fmto.net/competiciones/provpues", wait_until="networkidle")
        
        # ESPERA CRÍTICA: Esperamos a que aparezca al menos un enlace en la tabla
        try:
            await page.wait_for_selector("a", timeout=10000)
        except:
            print("La página tardó demasiado en cargar los enlaces.")

        # 3. Buscador ultra-flexible
        all_links = await page.query_selector_all("a")
        pdf_url = None
        target_fed = "65226"
        
        print(f"He encontrado {len(all_links)} enlaces en total. Buscando el correcto...")

        for link in all_links:
            text = (await link.inner_text()).upper()
            href = await link.get_attribute("href")
            
            # Buscamos combinaciones de palabras clave
            if href and ("PISTOLA" in text and "AIRE" in text):
                pdf_url = href
                print(f"¡Cazado!: {text}")
                break
        
        token = os.getenv("TELEGRAM_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")

        if pdf_url:
            full_url = pdf_url if pdf_url.startswith("http") else f"https://www.fmto.net{pdf_url}"
            print(f"Analizando PDF: {full_url}")
            
            # Intentamos leer el PDF
            try:
                from pypdf import PdfReader
                res = requests.get(full_url)
                reader = PdfReader(io.BytesIO(res.content))
                found_line = None
                
                for p_pdf in reader.pages:
                    lines = p_pdf.extract_text().split('\n')
                    for line in lines:
                        if target_fed in line:
                            found_line = line
                            break
                
                if found_line:
                    msg = f"🎯 *¡FICHA ENCONTRADA!*\n\n`{found_line}`\n\n[Ver PDF]({full_url})"
                else:
                    msg = f"✅ PDF detectado, pero no veo el federado {target_fed}.\n[Abrir PDF]({full_url})"
            except Exception as e:
                msg = f"⚠️ He encontrado el enlace pero no he podido leer el PDF. Revísalo aquí: {full_url}"
            
            requests.get(f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={msg}&parse_mode=Markdown")
        else:
            print("Seguimos sin ver el link. Probablemente la web usa una estructura de carga lenta (iframe o JS).")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
