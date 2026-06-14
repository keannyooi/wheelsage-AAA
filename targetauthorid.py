import logging, sys, os

from email.policy import default
from datetime import datetime
from dotenv import load_dotenv
from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

# command line argument validation before everything else is run
if len(sys.argv) < 2:
    print("Usage: python3 targetauthorid.py <path to id list> <author name>")
    sys.exit(1)
try:
    id_list_path = sys.argv[1]
    author_name = " ".join(sys.argv[2:])
    
    with open(id_list_path, "r") as f:
        id_list = f.readline().split(",")
except FileNotFoundError:
    print(f"File '{id_list_path}' not found, unable to open and initiate tool.")
    sys.exit(1)

# setup logging
log_filename = f'logs/authorstuff_{datetime.now().strftime("%Y-%m-%d")}.log'
logging.basicConfig(
    filename=log_filename,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

load_dotenv() # load environment variables from .env file

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

def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=True) # set headless=False to see the browser
    context = browser.new_context()
    page = login(os.getenv("EMAIL"), os.getenv("PASSWORD"), context)

    is_first_id = True
    for id in id_list:
        page.goto(f"https://en.wheelsage.org/moder/pictures/{id}")
        print(f"Assigning author {author_name} to image ID {id}...", end="")
        logging.info(f"TARGET  | Assigning author {author_name} to image ID {id}...")
        
        if is_first_id:
            success = handle_pic_author(page, id, author_name)
            if not success:
                return
        else:
            handle_pic_author(page, id, author_name)

        if is_first_id:
            is_first_id = False
        
    logging.info(f"--- Targeted author assignment task completed ---")
    
    # cleanup
    context.close()
    browser.close()

# since i'm already forced to violate DRY and copy this function, might as well optimize it for this use case
def handle_pic_author(page: Page, current_pic_id: int, current_copyright: str):
    # case 2: search for author in the website using the copyright info
    search_completed = False
    while not search_completed:
        page.get_by_role("link", name="add to …").click() # go to "Move picture" page
        page.get_by_role("link", name=" (author)").click() # go to authors tab

        try:
            page.wait_for_selector("app-paginator", state="visible")
            page.get_by_role("textbox", name="Type to search …").fill(current_copyright) # fill in the search field
            page.wait_for_selector("app-paginator", state="hidden") # wait for search results to load, this is a bit hacky but it works
            search_completed = True
        except Exception as e:
            print(f"Error encountered, retrying...")
            logging.info(f"TIMEOUT | Image ID {current_pic_id} encountered an error, retrying...")
            page.reload()
    
    search_results = page.locator(".text-start") # search results have class "text-start"
    if (search_results.count() == 0):
        print(f" [FAIL | AUTHOR NOT FOUND]");
        logging.info(f"ABORTED | Image ID {current_pic_id}: Author '{current_copyright}' not found")
        return False
    elif (search_results.count() == 1):
        print(" [SUCCESS]")
        search_results.first.click() # assign author to image
        logging.info(f"SUCCESS | Image ID {current_pic_id} assigned to {current_copyright}")

        clear_copyright_field(page)
        return True
    else: # search_results.count() > 1
        print(f" [FAIL | AUTHOR DUPLICATED]");
        logging.info(f"ABORTED | Image ID {current_pic_id}: Author '{current_copyright}' is duplicated")
        return False

def clear_copyright_field(page: Page):
    page.wait_for_selector("textarea", state="visible") # wait for the move picture form to close
    page.locator("textarea").clear()
    page.get_by_role("button", name="Submit").last.click()

with sync_playwright() as playwright:
    run(playwright)