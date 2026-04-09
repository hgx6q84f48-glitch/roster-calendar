from playwright.sync_api import sync_playwright

USERNAME = "SBW412E"
PASSWORD = "9532"


def get_token():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        page = browser.new_page()

        print("Opening login page...")
        page.goto("https://saacrewconnect.cocre8.africa/html/home.html")

        # Wait for login form
        page.wait_for_selector('input[type="password"]', timeout=15000)

        print("Entering credentials...")
        page.fill('input[type="text"]', USERNAME)
        page.fill('input[type="password"]', PASSWORD)

        print("Attempting login...")

        # 🔥 MULTI-STRATEGY LOGIN (no more guessing)
        try:
            # Strategy 1: Press Enter
            page.press('input[type="password"]', 'Enter')
        except:
            pass

        # Give page a moment
        page.wait_for_timeout(2000)

        try:
            # Strategy 2: Playwright click visible button
            page.locator("button:visible").first.click(timeout=3000)
        except:
            pass

        try:
            # Strategy 3: DOM click (most reliable)
            page.evaluate("""
                () => {
                    const buttons = Array.from(document.querySelectorAll('button'));
                    const loginBtn = buttons.find(btn =>
                        btn.innerText.toLowerCase().includes('login') ||
                        btn.innerText.toLowerCase().includes('sign')
                    );
                    if (loginBtn) loginBtn.click();
                }
            """)
        except:
            pass

        print("Waiting for login to complete...")

        # Wait for either navigation OR network idle
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except:
            pass

        print("Waiting for token...")

        # Wait until token appears
        page.wait_for_function("""
            () => {
                const data = localStorage.getItem('jStorage');
                if (!data) return false;
                try {
                    return JSON.parse(data).crew_token !== undefined;
                } catch {
                    return false;
                }
            }
        """, timeout=20000)

        token = page.evaluate("""
            JSON.parse(localStorage.getItem('jStorage')).crew_token
        """)

        print("✅ TOKEN:", token)

        browser.close()
        return token


if __name__ == "__main__":
    try:
        get_token()
    except Exception as e:
        print("❌ FAILED:", e)