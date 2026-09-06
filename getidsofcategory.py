import os, sys

from dotenv import load_dotenv
from pathlib import Path
from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

RUN_HEADLESS = True # set False to see the browser

load_dotenv() # load environment variables from .env file

# command line argument validation before everything else is run
if len(sys.argv) < 1:
    print("Usage: python3 getidsofcategory.py <link to first picture of category>")
    sys.exit(1)
try:
    first_pic_link = sys.argv[1]
except ValueError:
    print("Error: Please provide valid numeric image IDs")
    sys.exit(1)

def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=RUN_HEADLESS)
    context = browser.new_context()
    page = login(os.getenv("EMAIL"), os.getenv("PASSWORD"), context)

    page.goto(first_pic_link)

    # first get the name of the category
    title = page.locator("h1").inner_text()
    # then create the file
    output_path = Path(f"category_ids/{title}.txt")

    with open(output_path, "a", encoding="utf-8") as output_file:
        is_end_reached = False
        while not is_end_reached:
            current_id_text = page.get_by_role("link", name="Edit picture №").inner_text()
            current_id = current_id_text.replace("Edit picture №", "").strip()
            print(current_id)
            output_file.write(f"{current_id},")

            # onto the next one
            if page.get_by_role("link", name="next").count() > 0:
                page.get_by_role("link", name="next").click()
                page.wait_for_function(
                    """(oldId) => {
                        const divs = document.querySelectorAll('p > a');
                        const targetDiv = Array.from(divs).find(div => 
                            div.innerText.includes("Edit picture №")
                        );

                        return targetDiv &&
                            targetDiv.innerText !== "Edit picture №" + oldId;
                    }""",
                    arg=current_id # wait for the database id to change to ensure the new stuff has loaded
                )
            else:
                is_end_reached = True

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

with sync_playwright() as playwright:
    run(playwright)