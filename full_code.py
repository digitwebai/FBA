import streamlit as st
import asyncio
from playwright.async_api import async_playwright
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import logging
import time
from tenacity import retry, stop_after_attempt, wait_fixed
import tempfile
import os

# ------------------------------
# UI SETUP
st.set_page_config(page_title="ASIN Profit Margin Calculator", layout="wide")
st.title("📦 Amazon FBA ASIN Profit Margin Calculator")
st.write("Upload your required files and click 'Run Profit Calculator'.")

# Upload cookie file
cookie_file = st.file_uploader("Upload Amazon Cookie File (JSON)", type=["json"])
# Upload credentials file
cred_file = st.file_uploader("Upload Google Service Credentials (JSON)", type=["json"])
# Sheet URL input
sheet_url = st.text_input("Paste Google Sheet URL (with ASINs in column A)", "")

run_button = st.button("🚀 Run Profit Calculator")

log_area = st.empty()

# ------------------------------
# Helper functions
def fix_cookies(cookies):
    valid_same_site = {"Strict", "Lax", "None"}
    for cookie in cookies:
        if "sameSite" in cookie and cookie["sameSite"] not in valid_same_site:
            cookie["sameSite"] = "Lax"
    return cookies

async def load_cookies(context, file, domain):
    cookies = json.load(file)
    for cookie in cookies:
        if cookie.get('domain', '').startswith('.'):
            cookie['domain'] = cookie['domain'][1:]
    cookies = fix_cookies(cookies)
    await context.add_cookies(cookies)

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def click_continue_button(page):
    try:
        continue_button = await page.wait_for_selector('[data-testid="continue-btn"]', timeout=15000)
        await continue_button.click()
        await page.wait_for_timeout(2000)
        return True
    except:
        return False

# Use previous `calculate_profit_margin` from your code here...
# (TO SAVE SPACE: Assume the same function is reused directly.)

# --- MAIN ASYNC TASK
async def process_asins_streamlit(sheet_url, cookie_file, cred_file, log_func):
    log = log_func
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']

    creds = ServiceAccountCredentials.from_json_keyfile_name(cred_file, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_url(sheet_url)
    asins_ws = sheet.worksheet("ASINs")
    asins_data = asins_ws.col_values(1)

    if asins_data[0].strip().lower() == "asin":
        asins = asins_data[1:]
        asins_with_index = [(i + 2, asin) for i, asin in enumerate(asins)]
    else:
        asins = asins_data
        asins_with_index = [(i + 1, asin) for i, asin in enumerate(asins)]

    log(f"Processing {len(asins)} ASINs...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()

        await load_cookies(context, cookie_file, "https://sellercentral.amazon.co.uk")
        page = await context.new_page()

        URL = "https://sellercentral.amazon.co.uk/hz/fba/profitabilitycalculator/index?lang=en_GB"
        await page.goto(URL)
        if not await click_continue_button(page):
            log("❌ Could not continue as guest.")
            return

        for row, asin in asins_with_index:
            asin = asin.strip()
            if not asin:
                continue

            log(f"🔎 Processing ASIN {asin} at row {row}...")
            try:
                await page.goto(URL)
                await click_continue_button(page)
                await page.wait_for_timeout(2000)

                asin_input_handle = await page.evaluate_handle("""
                    () => {
                        const shadowHost = document.querySelector('kat-input[data-testid="product-search-input"]');
                        const shadowRoot = shadowHost?.shadowRoot;
                        return shadowRoot?.querySelector('input#katal-id-4');
                    }
                """)
                await asin_input_handle.as_element().fill("")
                await asin_input_handle.as_element().fill(asin)

                search_btn = await page.evaluate_handle("""
                    () => {
                        const btnHost = document.querySelector('kat-button[data-testid="product-search-button"]');
                        const shadowRoot = btnHost?.shadowRoot;
                        return shadowRoot?.querySelector('button.button[type="submit"]');
                    }
                """)
                await search_btn.as_element().click()
                await page.wait_for_timeout(5000)

                margin_values = await page.evaluate("""
                    () => {
                        const results = [];
                        const katLabels = document.querySelectorAll('kat-label');
                        for (const label of katLabels) {
                            const shadow = label.shadowRoot;
                            const span = shadow?.querySelector('span[part="label-text"]');
                            if (span && span.textContent.includes('%')) {
                                results.push(span.textContent.trim());
                            }
                        }
                        return results;
                    }
                """)

                valid_margins = [mv for mv in margin_values if mv.replace('%', '').replace('.', '').isdigit()]
                if valid_margins:
                    margin = valid_margins[0]
                    asins_ws.update_cell(row, 2, margin)
                    log(f"✅ ASIN {asin}: {margin}")
                else:
                    log(f"⚠️ No valid margin found for ASIN {asin}")
            except Exception as e:
                log(f"❌ Error processing ASIN {asin}: {e}")
        await browser.close()
        log("✅ Done!")

# ------------------------------
# Run if user clicks the button
if run_button:
    if not cookie_file or not cred_file or not sheet_url:
        st.warning("Please upload all files and provide a Google Sheet URL.")
    else:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp_cookie:
            tmp_cookie.write(cookie_file.read())
            cookie_path = tmp_cookie.name

        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp_cred:
            tmp_cred.write(cred_file.read())
            cred_path = tmp_cred.name

        def log_func(msg):
            current = log_area.text_area("📋 Logs", value="", height=400)
            log_area.text_area("📋 Logs", value=current + msg + "\n", height=400)

        asyncio.run(process_asins_streamlit(sheet_url, open(cookie_path, 'r'), cred_path, log_func))

        # Cleanup
        os.remove(cookie_path)
        os.remove(cred_path)
 
 