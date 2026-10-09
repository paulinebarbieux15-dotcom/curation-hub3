"""Manual login to the shared team social account (fallback when auto-login is blocked).

Run from the backend folder, on a machine with a screen:

    python -m app.social_login
    python -m app.social_login https://x.com/login https://www.linkedin.com/login

1. A Chrome window opens on the login page(s).
2. Log in BY HAND with the team account (including the 2FA code).
3. Come back to the terminal and press Enter.
4. Session cookies are saved to data/secrets/social_session.json.
"""
import sys

from . import config, social


def main(pages: list[str]) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        opts = {"headless": False}
        if config.BROWSER_CHANNEL:
            opts["channel"] = config.BROWSER_CHANNEL
        browser = p.chromium.launch(**opts)
        context = browser.new_context(
            storage_state=str(config.SOCIAL_SESSION_FILE) if social.session_present() else None,
            locale="en-US",
        )
        for url in pages:
            context.new_page().goto(url)
        print("\n=== Shared account login ===")
        print("1. Log in in the Chrome window that just opened.")
        print("2. Check that you can see the account's feed.")
        input("3. Come back here and press Enter to save the session... ")
        context.storage_state(path=str(config.SOCIAL_SESSION_FILE))
        social.protect_file()
        browser.close()
    print(f"\nSession saved to {config.SOCIAL_SESSION_FILE}")
    print("Remember to enable social capture in Settings > Social account.")


if __name__ == "__main__":
    main(sys.argv[1:] or config.SOCIAL_LOGIN_PAGES)
