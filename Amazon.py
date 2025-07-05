import requests
from bs4 import BeautifulSoup
import logging
import random
import time
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from urllib.parse import quote_plus

# Logger setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Random User-Agents list
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/85.0.4183.83 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0"
]

# Function to scrape Amazon UK
def scrape_amazon_uk(search_query):
    try:
        encoded_query = quote_plus(search_query)
        amazon_url = f"https://www.amazon.co.uk/s?k={encoded_query}&ref=nb_sb_noss"
        
        logger.info(f"Scraping Amazon UK with search term: {search_query}")

        headers = {
            'User-Agent': random.choice(USER_AGENTS),
            'Accept-Language': 'en-GB,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp',
            'Connection': 'keep-alive',
            'DNT': '1',  
        }

        # Delay to mimic human behavior
        time.sleep(random.uniform(2, 5))

        response = requests.get(amazon_url, headers=headers)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        items = soup.find_all('div', {'data-asin': True})

        logger.info(f"Found {len(items)} items.")

        product_data = []

        for item in items:
            title_tag = item.find('span', {'class': 'a-text-normal'})
            title = title_tag.text.strip() if title_tag else "Title not found"

            # Price logic update
            price_tag = item.find('span', {'class': 'a-price-whole'})
            if not price_tag:
                price_tag = item.find('span', {'class': 'a-offscreen'})
            price = price_tag.text.strip() if price_tag else "No price found"
            
            # Getting the URL (This includes the full path)
            url_tag = item.find('a', class_='a-link-normal')
            url = 'https://www.amazon.co.uk' + url_tag['href'] if url_tag else "No URL found"

            # Rating (could be empty)
            rating_tag = item.find('span', class_='a-icon-alt')
            rating = rating_tag.text.strip() if rating_tag else "No rating"

            # Prime membership indicator
            prime_tag = item.find('i', class_='a-icon-prime')
            is_prime = True if prime_tag else False

            # Reviews count (could be empty)
            reviews_tag = item.find('span', class_='a-size-base')
            reviews_count = reviews_tag.text.strip() if reviews_tag else "No reviews"

            # Validate that the reviews count is a number before attempting to convert it
            try:
                reviews_count = int(reviews_count.replace(",", ""))
            except ValueError:
                reviews_count = 0  # Set to 0 if conversion fails (i.e., non-numeric value)

            # Sponsored status
            sponsored_tag = item.find('span', class_='s-sponsored-label')
            is_sponsored = True if sponsored_tag else False

            # Image URL
            image_tag = item.find('img', class_='s-image')
            url_image = image_tag['src'] if image_tag else "No image"

            # Strikethrough price (if available)
            strike_through_tag = item.find('span', class_='a-price-strikethrough')
            strike_through_price = strike_through_tag.text.strip() if strike_through_tag else "No strike through price"

            # Shipping information
            shipping_info_tag = item.find('span', {'class': 'a-size-base', 'aria-label': 'shipping'})
            shipping_info = shipping_info_tag.text.strip() if shipping_info_tag else "Shipping info not found"

            # Add all the details to product_data
            product_data.append({
                "pos": len(product_data) + 1,  # Position number
                "url": url,
                "asin": item.get('data-asin', 'No ASIN'),
                "price": float(price.replace('£', '').replace(',', '').strip()) if price != "No price found" else 0.0,
                "title": title,
                "rating": float(rating.split()[0]) if rating != "No rating" else 0.0,
                "currency": "GBP",  # Assuming GBP for Amazon UK
                "is_prime": is_prime,
                "url_image": url_image,
                "best_seller": False,  # Assuming not best seller for now
                "price_upper": float(price.replace('£', '').replace(',', '').strip()) if price != "No price found" else 0.0,
                "is_sponsored": is_sponsored,
                "manufacturer": "",  # Placeholder
                "pricing_count": 1,  # Placeholder for now
                "reviews_count": reviews_count,
                "is_amazons_choice": False,  # Placeholder
                "price_strikethrough": float(strike_through_price.replace('£', '').replace(',', '').strip()) if strike_through_price != "No strike through price" else 0.0,
                "shipping_information": shipping_info
            })

        logger.info(f"Extracted {len(product_data)} items.")
        return product_data

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching Amazon UK data: {str(e)}")
        return {"error": f"Error: {str(e)}"}

# Function to upload data to Google Sheets
def upload_to_google_sheets(data):
    """Uploads the scraped data to Google Sheets."""
    CREDS_FILE = r'C:\Users\digit\Desktop\web scrap\credentials.json'  # Path to your downloaded credentials.json file
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_FILE, scope)
    client = gspread.authorize(creds)

    # Open the Google Sheets file and select the first sheet
    spreadsheet_id = "17gLw2AdW0tY_2JOQTI6fe63vLqHt67pSnu8OtcS5C3U"  # Replace with your Google Sheets ID
    spreadsheet = client.open_by_key(spreadsheet_id)
    
    # Create a new sheet called 'Amazon'
    try:
        sheet = spreadsheet.add_worksheet(title="Amazon", rows="100", cols="10")
    except gspread.exceptions.APIError as e:
        sheet = spreadsheet.worksheet("Amazon")  # If the sheet already exists, select it
    
    # Insert headers
    headers = ["Position", "URL", "ASIN", "Price", "Title", "Rating", "Currency", "Is Prime", "Image URL", "Best Seller", "Price Upper", "Is Sponsored", "Manufacturer", "Pricing Count", "Reviews Count", "Is Amazon's Choice", "Price Strikethrough", "Shipping Information"]
    sheet.insert_row(headers, 1)

    # Prepare and insert the data into the sheet
    for product in data:
        row = [
            product['pos'],
            product['url'],
            product['asin'],
            product['price'],
            product['title'],
            product['rating'],
            product['currency'],
            product['is_prime'],
            product['url_image'],
            product['best_seller'],
            product['price_upper'],
            product['is_sponsored'],
            product['manufacturer'],
            product['pricing_count'],
            product['reviews_count'],
            product['is_amazons_choice'],
            product['price_strikethrough'],
            product['shipping_information'],
        ]
        sheet.append_row(row)

    logger.info("Data uploaded successfully to Google Sheets!")

# Main function
if __name__ == "__main__":
    search_query = input("Enter search query: ")
    scraped_data = scrape_amazon_uk(search_query)

    if isinstance(scraped_data, list) and len(scraped_data) > 0:
        upload_to_google_sheets(scraped_data)
    else:
        logger.error("Scraping failed or no data found.")
