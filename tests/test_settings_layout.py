"""Settings: Updates gets a full-width row under This server and Health, so its details have room."""
import os


def test_updates_sits_below_server_and_health_full_width(live):
    browser, base = live
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(f"{base}/settings")
    box = {name: page.locator(f"section.panel:has(h2:text-is('{name}'))").bounding_box()
           for name in ("This server", "Health", "Updates")}
    grid = page.locator(".capture-grid").bounding_box()
    shot = os.environ.get("RL_SHOT")
    if shot:
        page.screenshot(path=shot, full_page=True)

    assert box["Health"]["y"] == box["This server"]["y"]            # side by side
    below = max(box["This server"]["y"] + box["This server"]["height"],
                box["Health"]["y"] + box["Health"]["height"])
    assert box["Updates"]["y"] >= below                             # under both
    assert abs(box["Updates"]["width"] - grid["width"]) < 2         # the whole row


def test_server_and_health_share_the_row_and_stack_on_phones(live):
    browser, base = live
    for width, side_by_side in ((1280, True), (390, False)):
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.goto(f"{base}/settings")
        srv = page.locator("section.panel:has(h2:text-is('This server'))").bounding_box()
        hl = page.locator("section.panel:has(h2:text-is('Health'))").bounding_box()
        grid = page.locator(".capture-grid").bounding_box()
        maint = page.locator("section.panel:has(h2:text-is('Maintenance'))").bounding_box()
        btn = page.locator(".maint-row >> nth=0 >> button").bounding_box()
        about = page.locator(".maint-row >> nth=0 >> p").bounding_box()
        assert abs(maint["width"] - grid["width"]) < 2                  # Maintenance: the whole row
        if side_by_side:
            assert srv["y"] == hl["y"] and srv["width"] + hl["width"] > grid["width"] * 0.95
            assert abs(hl["height"] - srv["height"]) < 1                # same height
            assert about["x"] > btn["x"] + btn["width"]                 # what it does, beside the button
        else:
            assert hl["y"] > srv["y"] and abs(hl["width"] - grid["width"]) < 2
            assert about["y"] >= btn["y"] + btn["height"]               # under it on a phone
        # nothing pushes the page sideways, long folder paths included
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if os.environ.get("RL_SHOT"):
            page.screenshot(path=os.environ["RL_SHOT"].replace(".png", f"_{width}.png"), full_page=True)
