import os
import asyncio
from playwright.async_api import async_playwright
import requests
import io
import re

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = await context.new_page()

        # 1. Login
        print("Logueando en FMTO...")
        await page.goto("https://www.fmto.net/acceso-federados", wait_until="networkidle")
        await page.fill('input[name="username"]', os.getenv("FMTO_USER"))
        await page.fill('input[name="password"]', os.getenv("FMTO_PASS"))
        await page.click('button[type="submit"]')
        await page.wait_for_timeout(5000)

        # 2. Ir a Puestos
        print("Navegando a Puestos...")
        await page.goto("https://www.fmto.net/competiciones/provpues", wait_until="networkidle")
        await page.wait_for_selector("a", timeout=10000)

        # 3. BUSCADOR INTELIGENTE POR FILAS
        # Buscamos el contenedor que tiene el texto de Pistola Aire
        target_fed = "65226"
        pdf_url = None
        
        # Buscamos todos los elementos que contengan el texto
        # El selector busca un texto que contenga PISTOLA y AIRE (da igual mayúsculas)
        rows = await page.query_selector_all("tr, div.row, div.item, li") 
        print(f"Analizando {len(rows)} bloques de contenido...")

        for row in rows:
            content = (await row.inner_text()).upper()
            if "PISTOLA" in content and "AIRE" in content:
                # Si este bloque tiene el texto, buscamos el enlace de descarga dentro de él
                link = await row.query_selector("a[href*='.pdf'], a:has-text('Descarga'), a:has-text('pdf')")
                if not link:
                    # Si no hay botón específico, pillamos el primer enlace que salga en esa fila
                    link = await row.query_selector("a")
                
                if link:
                    pdf_url = await link.get_attribute("href")
                    print(f"¡Encontrado enlace en fila de Pistola Aire!: {pdf_url}")
                    break

        token = os.getenv("TELEGRAM_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")

        if pdf_url:
            full_url = pdf_url if pdf_url.startswith("http") else f"https://www.fmto.net{pdf_url}"
            print(f"Descargando PDF: {full_url}")
            
            try:
                from pypdf import PdfReader
                res = requests.get(full_url)
                reader = PdfReader(io.BytesIO(res.content))
                found_line = None
                
                for p_pdf in reader.pages:
                    text_content = p_pdf.extract_text()
                    if target_fed in text_content:
                        lines = text_content.split('\n')
                        for line in lines:
                            if target_fed in line:
                                found_line = line
                                break
                
                if found_line:
                    msg = f"🎯 *¡TU PUESTO ENCONTRADO!*\n\n`{found_line}`\n\n[Abrir PDF oficial]({full_url})"
                else:
                    msg = f"✅ Listado de Pistola Aire detectado, pero no veo el federado {target_fed} en las tablas.\n[Revisar manualmente aquí]({full_url})"
            except:
                msg = f"⚠️ He encontrado el enlace de Pistola Aire pero no puedo leer el interior. Míralo tú mismo: {full_url}"
            
            requests.get(f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={msg}&parse_mode=Markdown")
        else:
            print("No he podido localizar el enlace de descarga en las filas de Pistola.")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
