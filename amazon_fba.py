import asyncio
from playwright.async_api import async_playwright
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import logging
import time
from tenacity import retry, stop_after_attempt, wait_fixed
import os

# Logger setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
COOKIE_FILE = 'sellercentral.amazon.co.uk_json.json'
URL = "https://sellercentral.amazon.co.uk/hz/fba/profitabilitycalculator/index?lang=en_GB"
SHEET_URL = "https://docs.google.com/spreadsheets/d/12q4pt53suMHixlJ3Zd_J5frLDp-yBdLU3U1pZmLGlu8"
CREDENTIALS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")


def fix_cookies(cookies):
    valid_same_site = {"Strict", "Lax", "None"}
    for cookie in cookies:
        if "sameSite" in cookie and cookie["sameSite"] not in valid_same_site:
            cookie["sameSite"] = "Lax"
    return cookies

async def load_cookies(context, cookie_file, domain):
    try:
        with open(cookie_file, 'r') as f:
            cookies = json.load(f)
    except FileNotFoundError:
        logger.error("Cookie file %s not found. Please provide a valid cookie JSON file.", cookie_file)
        raise
    except json.JSONDecodeError:
        logger.error("Invalid JSON format in %s. Check the cookie file.", cookie_file)
        raise

    for cookie in cookies:
        if cookie.get('domain', '').startswith('.'):
            cookie['domain'] = cookie['domain'][1:]

    cookies = fix_cookies(cookies)
    await context.add_cookies(cookies)

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def get_asin_input_handle(page):
    try:
        await page.wait_for_timeout(5000)
        asin_input_handle = await page.evaluate_handle("""
            () => {
                const shadowHost = document.querySelector('kat-input[data-testid="product-search-input"]');
                if (!shadowHost) return null;
                const shadowRoot = shadowHost.shadowRoot;
                if (!shadowRoot) return null;
                return shadowRoot.querySelector('input');
            }
        """)
        return asin_input_handle
    except Exception as e:
        logger.error("Failed to get ASIN input handle: %s", e)
        return None

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def click_continue_button(page):
    try:
        continue_button = await page.wait_for_selector('[data-testid="continue-btn"]', timeout=15000)
        await continue_button.click()
        logger.info("Clicked 'Continue as Guest'.")
        await page.wait_for_timeout(5000)
        return True
    except Exception as e:
        logger.error("Failed to click 'Continue as Guest': %s", e)
        return False

async def click_search_button(page):
    try:
        search_button_handle = await page.evaluate_handle("""
            () => {
                const katButton = document.querySelector('kat-button[data-testid="product-search-button"]');
                if (!katButton) return null;
                const shadowRoot = katButton.shadowRoot;
                if (!shadowRoot) return null;
                return shadowRoot.querySelector('button.button[type="submit"]');
            }
        """)
        if search_button_handle:
            await search_button_handle.as_element().click()
            logger.info("✅ Clicked 'Search' button inside shadow DOM.")
            return True
        else:
            logger.error("❌ Search button not found inside shadow DOM.")
            return False
    except Exception as e:
        logger.error("Failed to click 'Search' button: %s", e)
        return False

async def check_failed_match_and_retry(page):
    try:
        failed_match_message = await page.evaluate(""" 
            () => {
                const alertElement = document.querySelector('kat-alert[data-testid="alert-component"]');
                if (!alertElement) return null;
                const descriptionElement = alertElement.getAttribute('description');
                return descriptionElement ? descriptionElement.trim() : '';
            }
        """)
        if "Failed to get product match" in failed_match_message:
            logger.warning("❌ Product match failed for ASIN, retrying search...")
            return True
        return False
    except Exception as e:
        logger.error("Error checking 'Failed to get product match' message: %s", e)
        return False

async def calculate_profit_margin(page, asin, row, asins_ws):
    try:
        await page.wait_for_timeout(5000)
        asin_input_handle = await get_asin_input_handle(page)
        if not asin_input_handle:
            logger.error("❌ Could not locate ASIN input field inside shadow DOM for ASIN: %s", asin)
            return None

        await asin_input_handle.as_element().fill("")
        await asin_input_handle.as_element().fill(asin)
        logger.info("✅ Entered ASIN: %s", asin)

        await page.wait_for_timeout(1000)

        if not await click_search_button(page):
            logger.error("❌ Failed to click search button after retry attempts.")
            return None

        await page.wait_for_timeout(7000)

        # Retry logic for failed product match
        retry_attempts = 3
        for _ in range(retry_attempts):
            if await check_failed_match_and_retry(page):
                if not await click_search_button(page):
                    logger.error("❌ Failed to click search button again after retry attempts.")
                    return None
                await page.wait_for_timeout(7000)
            else:
                break

        try:
            await page.wait_for_selector('kat-label', timeout=15000)
            margin_values = await page.evaluate("""
                () => {
                    const results = [];
                    const katLabels = document.querySelectorAll('kat-label');
                    for (const label of katLabels) {
                        const shadowRoot = label.shadowRoot;
                        if (shadowRoot) {
                            const span = shadowRoot.querySelector('span[part="label-text"]');
                            if (span && span.textContent.trim().includes('%')) {
                                results.push(span.textContent.trim());
                            }
                        }
                    }
                    return results;
                }
            """)
            logger.info("All margin values found for ASIN %s: %s", asin, margin_values)

            if margin_values:
                valid_margins = [mv for mv in margin_values if mv.replace('%', '').replace('.', '').isdigit()]
                if valid_margins:
                    first_margin = valid_margins[0]
                    logger.info("✅ Net Profit Margin for ASIN %s: %s", asin, first_margin)

                    # Write the first margin to column B of the corresponding row
                    try:
                        asins_ws.update_cell(row, 3, first_margin)
                        logger.info("✅ Wrote Net Profit Margin %s for ASIN %s at row %d, column C", first_margin, asin, row)
                    except Exception as e:
                        logger.error("❌ Failed to write margin to Google Sheet for ASIN %s at row %d: %s", asin, row, e)
                else:
                    logger.warning("❌ No valid numeric margin found among extracted values for ASIN %s: %s", asin, margin_values)
            else:
                logger.warning("No margin values found for ASIN %s", asin)
                warning_icon_exists = await page.evaluate("""
                    () => {
                        const warningIcon = document.querySelector('[data-testid="alert-component"]');
                        return warningIcon ? true : false;
                    }
                """)
                if warning_icon_exists:
                    logger.warning("❌ Warning icon found. Clicking 'Search another product'...")
                    search_another_button = await page.query_selector('[data-testid="search-another-product-btn"]')
                    if search_another_button:
                        await search_another_button.click()
                        logger.info("✅ Clicked 'Search another product' button.")
                        await page.wait_for_timeout(5000)

                        # After clicking "Search another product", click "Select" button
                        while True:
                            select_button = await page.query_selector('[data-testid="select-product-btn"]')
                            if select_button:
                                await select_button.click()
                                logger.info("✅ Clicked 'Select' button.")
                                await page.wait_for_timeout(5000)

                                # Check if warning icon exists
                                warning_icon_exists = await page.evaluate("""
                                    () => {
                                        const warningIcon = document.querySelector('[data-testid="alert-component"]');
                                        return warningIcon ? true : false;
                                    }
                                """)
                                if warning_icon_exists:
                                    logger.warning("❌ Warning icon found after selecting. Clicking 'Search another product' again...")
                                    search_another_button = await page.query_selector('[data-testid="search-another-product-btn"]')
                                    if search_another_button:
                                        await search_another_button.click()
                                        logger.info("✅ Clicked 'Search another product' button again.")
                                        await page.wait_for_timeout(5000)
                                    # Loop continues to try select again
                                else:
                                    # No warning icon, try to extract margin values
                                    try:
                                        await page.wait_for_selector('kat-label', timeout=15000)
                                        margin_values = await page.evaluate("""
                                            () => {
                                                const results = [];
                                                const katLabels = document.querySelectorAll('kat-label');
                                                for (const label of katLabels) {
                                                    const shadowRoot = label.shadowRoot;
                                                    if (shadowRoot) {
                                                        const span = shadowRoot.querySelector('span[part="label-text"]');
                                                        if (span && span.textContent.trim().includes('%')) {
                                                            results.push(span.textContent.trim());
                                                        }
                                                    }
                                                }
                                                return results;
                                            }
                                        """)
                                        if margin_values:
                                            logger.info("✅ Found margin values after select: %s", margin_values)
                                            # Filter for the first valid numeric margin
                                            valid_margins = [mv for mv in margin_values if mv.replace('%', '').replace('.', '').isdigit()]
                                            if valid_margins:
                                                first_margin = valid_margins[0]
                                                asins_ws.update_cell(row, 3, first_margin)
                                                logger.info("✅ Wrote Net Profit Margin %s for ASIN %s at row %d, column C", first_margin, asin, row)
                                            else:
                                                logger.warning("❌ No valid numeric margin found among extracted values for ASIN %s: %s", asin, margin_values)
                                            return margin_values
                                        else:
                                            logger.warning("❌ No margin values found after select, but no warning icon present.")
                                            return None
                                    except Exception as e:
                                        logger.error("❌ Error extracting Net Profit Margin after select for ASIN %s: %s", asin, e)
                                        return None
                            else:
                                logger.error("❌ 'Select' button not found after clicking 'Search another product'.")
                                return None
                return None
        except Exception as e:
            logger.error("❌ Error extracting Net Profit Margin for ASIN %s: %s", asin, e)
            return None

    except Exception as e:
        logger.error("❌ Error interacting with ASIN input or Search for ASIN %s: %s", asin, e)
        return None
    finally:
        await page.wait_for_timeout(1000)
        
                    
async def process_asins():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_PATH, scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_url(SHEET_URL)
        asins_ws = sheet.worksheet("ASINs")
        asins_data = asins_ws.col_values(1)
    except PermissionError as e:
        logger.error("PermissionError: Ensure the service account email in %s is shared with 'Editor' access to the sheet at %s", CREDENTIALS_PATH, SHEET_URL)
        raise
    except FileNotFoundError as e:
        logger.error("File not found: %s. Verify the path to the service account JSON.", CREDENTIALS_PATH)
        raise
    except Exception as e:
        logger.error("Error loading Google Sheet: %s. Check %s and %s", e, CREDENTIALS_PATH, SHEET_URL)
        raise

    if asins_data[0].strip().lower() == "asin":
        asins = asins_data[1:]
        asins_with_index = [(i + 2, asin) for i, asin in enumerate(asins)]
    else:
        asins = asins_data
        asins_with_index = [(i + 1, asin) for i, asin in enumerate(asins)]

    logger.info(f"Processing {len(asins)} ASINs from Google Sheet... (Updated: 06:01 PM +0530, Sat, Jun 28, 2025)")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()

        try:
            await load_cookies(context, COOKIE_FILE, 'https://sellercentral.amazon.co.uk')
            page = await context.new_page()
            await page.goto(URL)
            logger.info("Navigated to FBA Calculator page.")
            if not await click_continue_button(page):
                logger.error("Initial 'Continue as Guest' click failed. Aborting.")
                await browser.close()
                raise
        except Exception as e:
            logger.error("Error loading cookies or navigating to page: %s. Check %s", e, COOKIE_FILE)
            await browser.close()
            raise

        for row, asin in asins_with_index:
            if asin.strip():
                logger.info("Processing ASIN: %s at row %d", asin, row)
                margin_values = await calculate_profit_margin(page, asin.strip(), row, asins_ws)
                if margin_values:
                    valid_margins = [mv for mv in margin_values if mv.replace('%', '').replace('.', '').isdigit()]
                    if valid_margins:
                        first_margin = valid_margins[0]
                        logger.info("✅ Net Profit Margin for ASIN %s: %s", asin, first_margin)
                    else:
                        logger.warning("❌ No valid numeric margin found among extracted values for ASIN %s: %s", asin, margin_values)
                else:
                    logger.warning("No margin values found for ASIN %s at row %d", asin, row)

                # Always navigate back to FBA Calculator page after processing each ASIN
                await page.goto(URL)
                logger.info("Navigated back to FBA Calculator page for next ASIN.")
                if not await click_continue_button(page):
                    logger.error("Failed to click 'Continue as Guest' for next ASIN. Aborting loop.")
                    break

        await browser.close()

if __name__ == "__main__":
    asyncio.run(process_asins())


