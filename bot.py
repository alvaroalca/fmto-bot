import os
import asyncio
from playwright.async_api import async_playwright
import requests

async def run():
    async with async_playwright() as p:
        # Abrimos el navegador (Chrome real)
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

        # 2. Ir a competiciones
        await page.goto("https://www.fmto.net/competiciones/provpues")
        
        # 3. Buscar el enlace de "Preparatoria Pistola Aire"
        # Buscamos el link que tenga el texto deseado
        link_element = await page.query_selector('a:has-text("PREPARATORIA PISTOLA AIRE")')
        
        if link_element:
            pdf_url = await link_element.get_attribute("href")
            if not pdf_url.startswith("http"):
                pdf_url = "https://www.fmto.net" + pdf_url
            
            print(f"¡Encontrado! Descargando: {pdf_url}")
            
            # 4. Enviar a Telegram
            token = os.getenv("TELEGRAM_TOKEN")
            chat_id = os.getenv("TELEGRAM_CHAT_ID")
            msg = f"✅ Nueva competición encontrada: {pdf_url}"
            requests.get(f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={msg}")
        else:
            print("No se encontró ninguna competición nueva hoy.")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
