import sys
import asyncio
import subprocess
import streamlit as st
from amazon_fba import process_asins  # your scraping function

# On Windows, use ProactorEventLoop for subprocess support
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

st.set_page_config(page_title="Amazon Margin Calculator", layout="centered")

st.title("📦 Amazon FBA Profit Margin Calculator")
st.markdown("Click the button below to fetch profit margins for ASINs from your Google Sheet.")

def install_playwright_browsers():
    try:
        with st.spinner("Installing Playwright browsers..."):
            subprocess.run(["playwright", "install"], check=True)
        st.success("Playwright browsers installed successfully.")
    except Exception as e:
        st.error(f"Failed to install Playwright browsers: {e}")
        raise e

# Run Playwright install once on app startup
install_playwright_browsers()

if st.button("🚀 Get Profit Margin"):
    st.info("Running script... this may take several minutes depending on ASIN count.")
    placeholder = st.empty()
    try:
        with st.spinner("Launching browser and scraping margin data..."):
            # Run your async function synchronously using asyncio.run
            asyncio.run(process_asins())
        placeholder.success("✅ Completed successfully! Margins updated in the Google Sheet.")
    except Exception as e:
        placeholder.error(f"❌ Error occurred: {e}")
