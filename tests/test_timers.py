"""Kitchen timers keep running when you leave the cook page, and ring until stopped."""
import pytest


def test_every_page_carries_the_timers(client):
    from recipelib.db.engine import session_scope
    from recipelib.db.models import Recipe, Step
    with session_scope() as s:
        r = Recipe(title="Soup", status="ready", page_count=1)
        r.steps.append(Step(position=0, text="Simmer.", minutes=1))
        s.add(r)
        s.flush()
        rid = r.id
    for url in ("/", f"/recipes/{rid}", f"/recipes/{rid}/cook", "/shopping", "/settings"):
        html = client.get(url).text
        assert "timers.js" in html and "timers.css" in html, url


def test_a_timer_survives_leaving_the_cook_page_and_rings_until_stopped(live):
    from recipelib.db.engine import session_scope
    from recipelib.db.models import Recipe, Step
    with session_scope() as s:
        r = Recipe(title="Tomato soup", status="ready")
        r.steps.append(Step(position=0, text="Simmer.", minutes=1))
        s.add(r)
        s.flush()
        rid = r.id
    browser, base = live
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/recipes/{rid}/cook")
    page.click(".tabs button[data-tab=steps]") if page.is_visible(".tabs button[data-tab=steps]") else None
    page.click("button.timer")
    dock = "#timerDock .t"
    assert page.locator(dock).count() == 1
    assert "Step 1" in page.inner_text(dock) and "Tomato soup" in page.inner_text(dock)

    # leave the cook page: the timer is still there and still counting down
    page.goto(f"{base}/shopping")
    page.wait_for_selector(dock)
    left = page.inner_text(f"{dock} .left")
    assert left.startswith("0:5") or left.startswith("1:00"), left

    # make it finish (the end time is what is saved), then come back to another page
    page.evaluate("""() => { const l = JSON.parse(localStorage.getItem('rl_timers'));
                             l[0].end = Date.now() - 65000; localStorage.setItem('rl_timers', JSON.stringify(l)); }""")
    page.goto(f"{base}/")
    page.wait_for_selector(f"{dock}.over")
    assert "+1:0" in page.inner_text(f"{dock} .left")             # overdue, counting up
    assert page.evaluate("window.rlTimers.ringing()") is True

    # the link goes back to the recipe's cook page
    assert page.get_attribute(f"{dock} a.lbl", "href").endswith(f"/recipes/{rid}/cook")

    # stopping it stops it everywhere
    page.click(f"{dock} button.stop")
    assert page.locator(dock).count() == 0
    assert page.evaluate("window.rlTimers.ringing()") is False
    page.goto(f"{base}/settings")
    assert page.locator(dock).count() == 0
    assert errors == []


def test_two_timers_and_another_tab(live):
    browser, base = live
    ctx = browser.new_context()
    a, b = ctx.new_page(), ctx.new_page()
    a.goto(f"{base}/")
    b.goto(f"{base}/shopping")
    a.evaluate("window.rlTimers.start(120, 'Rice')")
    a.evaluate("window.rlTimers.start(300, 'Oven')")
    b.wait_for_function("document.querySelectorAll('#timerDock .t').length === 2")
    a.click("#timerDock .t:first-child button.stop")
    b.wait_for_function("document.querySelectorAll('#timerDock .t').length === 1")
    assert "Oven" in b.inner_text("#timerDock")
