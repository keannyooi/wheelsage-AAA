import os, re, logging, sys, json, traceback, io, requests

from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path
from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright
from PIL import Image
from PIL.ExifTags import TAGS
from ftfy import fix_text

RUN_HEADLESS = True # set False to see the browser

# command line argument validation before everything else is run
if len(sys.argv) < 3:
    print("Usage: python3 authorstuff.py <begin_pic_id> <end_pic_id>")
    sys.exit(1)
try:
    begin_pic_id = int(sys.argv[1])
    end_pic_id = int(sys.argv[2])
    if begin_pic_id < end_pic_id:
        print("Error: begin_pic_id should be greater than or equal to end_pic_id")
        sys.exit(1)
except ValueError:
    print("Error: Please provide valid numeric image IDs")
    sys.exit(1)

# author copyright dictionary
# dynamic list of special-case copyright text which would need conversion, will keep on adding stuff here
author_dict = json.load(open("authors.json", "r", encoding="utf-8"))
exif_search_list = [ # these copyright blocks trigger a deeper EXIF search
    "Daimler Truck",
    "Mercedes-Benz AG",
    "Mercedes-Benz Group AG",
    "FerreroCommunication",
    "Scania CV AB",
    "Motroring Media Network"
]

RM_SOTHEBYS_REGEX = r"((©( )?\d{4} (Co(u)?rtesy of )?)|\/)RM ((Sotheby(')?s)|Auctions)"
DAIMLER_REGEX = r"(© )?(Mercedes-Benz|(Daimler( Truck)?))( AG)?"

def search_author_dict(copyright_text: str) -> tuple:
    text = copyright_text
    copyright_block = ""

    # remove the common part in the copyright field to improve matching accuracy
    if re.search(RM_SOTHEBYS_REGEX, text) != None:
        text = re.sub(RM_SOTHEBYS_REGEX, "", text).strip()
        copyright_block = "RM Sotheby's"
    if re.search(DAIMLER_REGEX, text) != None:
        text = re.sub(DAIMLER_REGEX, "", text).strip()
        copyright_block = "Daimler A. G."
    
    if len(text) == 0:
        return text, copyright_block

    # search for the copyright text in the author_dict keys (case-insensitive)
    for key in author_dict.keys():
        if key.lower() in text.lower(): 
            return author_dict[key], copyright_block
    
    return text, copyright_block

def get_exif_author_from_link(url: str, default: str) -> str:
    try:
        # Stream the image file from the link
        response = requests.get(url, stream=True, timeout=10)
        # check HTTP response code, throws error if 4xx / 5xx
        response.raise_for_status()
        
        # Load the bytes into an in-memory file-like object
        image_bytes = io.BytesIO(response.content)
        
        # Open the image with Pillow
        with Image.open(image_bytes) as img:
            # Extract raw EXIF data
            exif_data = img.getexif()
            if not exif_data:
                print("No EXIF metadata found in this image.")
                return default
            
            for tag_id, value in exif_data.items():
                tag_name = TAGS.get(tag_id, tag_id)
                if tag_name == "Artist":
                    value = fix_text(value) # fix mojibake from misinterpreted characters
                    print(f"Artist according to EXIF: {value}")
                    return value
            
            print("No Artist field found, moving on...")
            return default
    except requests.exceptions.RequestException as e:
        print(f"Network error, returning None: {e}")
        logging.info(f"ERROR   | Network error, returning None: {e}")
        return default
    except Exception as e:
        print(f"Error obtaining EXIF metadata, returning None: {e}")
        traceback.print_exc()
        logging.info(f"ERROR   | Error obtaining EXIF metadata, returning None: {e}")
        return default

def to_next_id(page: Page, current_pic_id: int):
    page.get_by_role("link", name="<< previous", exact=True).click()
    page.wait_for_function(
        """(oldId) => {
            const divs = document.querySelectorAll('.card-body > div');
            const targetDiv = Array.from(divs).find(div => 
                div.innerText.includes("Database id:")
            );

            return targetDiv &&
                targetDiv.innerText !== "Database id: " + oldId;
        }""",
        arg=current_pic_id # wait for the database id to change to ensure the new stuff has loaded
    )

# ================================================================================
# setup logging
date_str = datetime.now().strftime("%Y-%m-%d")
log_filename = f'logs/authorstuff_{date_str}.log'
unverified_path = Path(f"unverified/{date_str}.txt")
failed_path = Path(f"failed/{date_str}.txt")
logging.basicConfig(
    filename=log_filename,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

load_dotenv() # load environment variables from .env file

def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=RUN_HEADLESS)
    context = browser.new_context()
    page = login(os.getenv("EMAIL"), os.getenv("PASSWORD"), context)

    # now we travel to the latest image page
    # hardcoded link for now
    page.goto(f"https://en.wheelsage.org/moder/pictures/{begin_pic_id}")
    current_pic_id = int(re.search(r'/pictures/(\d+)', page.url).group(1)) # ids may skip due to deleted pictures no longer existing

    previous_author = None
    while current_pic_id >= end_pic_id:
        try:
            # check copyright field
            current_copyright = page.locator("textarea").input_value().strip() # text fields use input_value instead of text_content
            current_pic_status = page.locator("strong.text-bg-warning, strong.text-bg-success, strong.text-bg-danger").inner_text()
            current_pic_id = int(re.search(r'/pictures/(\d+)', page.url).group(1))
            current_pic_link = page.get_by_role("link", name="https://").text_content()

            if current_pic_status == "In delete queue":
                logging.info(f"SKIPPED | Image ID {current_pic_id} is deleted, skipping over")
                to_next_id(page, current_pic_id)
                continue
            elif current_pic_status == "Not accepted":
                logging.info(f"SKIPPED | Image ID {current_pic_id} has yet to be accepted, skipping over")
                if len(current_copyright) > 0:
                    with open(unverified_path, "a", encoding="utf-8") as unverified_log_file:
                        unverified_log_file.write(f"ID {current_pic_id}: {current_copyright}\n")
                
                to_next_id(page, current_pic_id)
                continue
            
            # search for text if copyright text is in exif_search_list
            for keyword in exif_search_list:
                if keyword.lower() in current_copyright.lower():
                    current_copyright += " " + get_exif_author_from_link(current_pic_link, current_copyright)
            
            # resolve author name from copyright info using author_dict
            current_author, current_copyright_block = search_author_dict(current_copyright)
            print(f"PicID {current_pic_id}: {current_author if len(current_author) > 0 else 'No copyright info'}")

            if len(current_author) > 0:
                success = handle_pic_author(page, current_pic_id, previous_author, current_author, current_copyright_block)
                if success:
                    # only update previous_author if the current one is successfully processed
                    # this is to make sure the shortcut button actually appears and works for consecutive images with the same author
                    previous_author = current_author
            else:
                if len(current_copyright) > 0:
                    # no author info apart from copyright block, log as failed
                    logging.info(f"ABORTED | Image ID {current_pic_id}: Author '{current_copyright}' not found, manual review needed")
                    with open(failed_path, "a", encoding="utf-8") as failed_log_file:
                        failed_log_file.write(f"ID {current_pic_id}: Author '{current_copyright}' not found\n")
                else:
                    logging.info(f"SKIPPED | Image ID {current_pic_id} has no copyright info")

            # travel to previous image
            to_next_id(page, current_pic_id)
        except Exception as e:
            if page.get_by_role("heading", name="Page not found").is_visible():
                logging.error(f"TIMEOUT | Image ID {current_pic_id}: Timed out waiting for page to load, reloading...")
            else:
                print(e)
                traceback.print_exc()
                logging.error(f"ERROR | Image ID {current_pic_id}: Error encountered, retrying: {str(e)}")
            
            page.goto(f"https://en.wheelsage.org/moder/pictures/{current_pic_id}")

    logging.info(f"--- Process completed for images down to ID {current_pic_id} ---")
    with open(failed_path, "a", encoding="utf-8") as failed_log_file:
        failed_log_file.write(f"--- Process completed for images down to ID {current_pic_id} ---\n")
    
    # cleanup
    context.close()
    browser.close()

def login(email: str, password: str, context: BrowserContext) -> Page:
     # Open new page
    page = context.new_page()
    page.goto("https://en.wheelsage.org/")
    page.wait_for_selector(".navbar", state="visible") # wait for the navbar to load to ensure the page is fully loaded
    
    print("regen=Q is " + ("logged in" if page.get_by_role("button", name="Moderator menu").is_visible() else "not logged in"))
    if not page.get_by_role("button", name="Moderator menu").is_visible():
        # login here
        page.get_by_role("button", name="Sign in").click() # name here doesn't mean the name attribute in forms
        
        page.get_by_label("Username or email").fill(email)
        page.get_by_role("textbox", name="Password").fill(password)
        page.get_by_role("button", name="Sign In").click()

        page.wait_for_selector(".navbar", state="visible") # wait for the navbar to load again after login
        print("regen=Q is " + ("logged in" if page.get_by_role("button", name="Moderator menu").is_visible() else "not logged in"))
    
    return page

def handle_pic_author(page: Page, current_pic_id: int, previous_author: str, current_author: str, current_copyright_block: str):
    # case 0: copyright field cleanup for images with authors already assigned
    if page.locator("table").get_by_role("link", name=current_author).is_visible():
        print("Author already assigned, clearing copyright info and skipping to next image")
        logging.info(f"CLEANUP | Image ID {current_pic_id} already has author '{current_author}' assigned, cleared copyright info")
        clear_copyright_field(page)
        return False

    # case 1: if the copyright info is the same as the previous image, we can assume it's the same author and skip searching
    print(f"Previous author: {previous_author if previous_author else 'None'}, Current author: {current_author if current_author else 'None'}")
    # if len(current_copyright_block) == 0 and previous_author and previous_author.lower() == current_author.lower():
    #     page.get_by_role("button", name="add to (author)").click()
    #     logging.info(f"SUCCESS | Image ID {current_pic_id} assigned to {current_author}")
    #     clear_copyright_field(page)
    #     return True
    
    # case 2: search for author in the website using the copyright info
    # for some reason there's a small chance that there would be a token error here
    # gotta handle it to prevent the program from crashing, wheelsage is a well-built website indeed
    search_completed = False
    retries = 0
    while not search_completed:
        page.get_by_role("link", name="add to …").click() # go to "Move picture" page
        page.get_by_role("link", name=" (author)").click() # go to authors tab

        try:
            page.wait_for_selector("app-paginator", state="visible")
            old_search_results = page.locator(".text-start").all_text_contents() # search results have class "text-start"
            # print(old_search_results)

            page.get_by_role("textbox", name="Type to search …").fill(current_author) # fill in the search field
            page.wait_for_function(
                """
                ([selector, oldResults]) => {
                    const buttons = [
                        ...document.querySelectorAll(selector)
                    ];

                    const current = buttons.map(
                        b => b.textContent.trim()
                    );

                    return JSON.stringify(current) !==
                        JSON.stringify(oldResults);
                }
                """,
                arg=[".text-start", old_search_results]
            )
            
            search_completed = True
        except Exception as e:
            print(e)
            print(f"Error encountered, retrying...")
            logging.info(f"TIMEOUT | Image ID {current_pic_id} encountered an error, retrying...")
            page.goto(f"https://en.wheelsage.org/moder/pictures/{current_pic_id}")
            
            retries += 1
            if retries >= 3:
                logging.error(f"ERROR | Image ID {current_pic_id} failed to load after 3 retries, skipping to next image")
                return False
    
    search_results = page.locator(".text-start")
    if (search_results.count() == 0):
        print(f"Author not found, logged to {log_filename} for manual review");
        # backtrack twice
        page.go_back()
        page.go_back()

        logging.info(f"ABORTED | Image ID {current_pic_id}: Author '{current_author}' not found, manual review needed")
        with open(failed_path, "a", encoding="utf-8") as failed_log_file:
            failed_log_file.write(f"ID {current_pic_id}: Author '{current_author}' not found\n")
        return False
    elif (search_results.count() == 1):
        print(f"Author found: {search_results.first.inner_text()}")
        search_results.first.click() # assign author to image

        if len(current_copyright_block) > 0:
            # additional copyright stuff
            page.get_by_role("link", name="add to …").click()
            page.get_by_role("link", name="Copyright blocks").click()
            page.get_by_role("button", name=current_copyright_block).click()

        logging.info(f"SUCCESS | Image ID {current_pic_id} assigned to {current_author}")
        clear_copyright_field(page)
        return True
    else: # search_results.count() > 1
        print(f"Duplicate author found, aborted and logged to {log_filename}");

        # backtrack twice
        page.go_back()
        page.go_back()
        
        logging.info(f"ABORTED | Image ID {current_pic_id}: Author '{current_author}' is duplicated, manual review needed")
        with open(failed_path, "a", encoding="utf-8") as failed_log_file:
            failed_log_file.write(f"ID {current_pic_id}: Duplicate author '{current_author}'\n")
        return False

def clear_copyright_field(page: Page):
    page.wait_for_selector("textarea", state="visible") # wait for the move picture form to close
    page.locator("textarea").clear()
    page.get_by_role("button", name="Submit").last.click()

with sync_playwright() as playwright:
    run(playwright)