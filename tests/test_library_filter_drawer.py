"""The library's filter panel, which on phones slides in from the left."""
import re


def _tab(html: str) -> str:
    m = re.search(r'<button[^>]*class="filter-tab"[^>]*>.*?</button>', html, re.S)
    assert m, "no Filters tab on the page"
    return m.group(0)


def test_the_tab_opens_the_filter_panel(client):
    html = client.get("/").text
    tab = _tab(html)
    assert 'aria-controls="filters-panel"' in tab and 'aria-expanded="false"' in tab
    assert re.search(r'<aside class="filters" id="filters-panel"', html)
    assert 'class="filter-scrim"' in html and 'class="drawer-close"' in html


def test_search_stays_on_the_page_not_in_the_panel(client):
    html = client.get("/").text
    aside = re.search(r'<aside class="filters".*?</aside>', html, re.S).group(0)
    assert 'id="q"' not in aside and 'id="q"' in html
    # the search still sends the current filters along
    assert 'hx-include="#filters"' in html and 'id="filters"' in aside


def test_the_tab_counts_active_filters(client):
    assert "badge" not in _tab(client.get("/").text)
    assert re.search(r'class="badge"[^>]*>1<', _tab(client.get("/?fav=1").text))
    assert re.search(r'class="badge"[^>]*>2<', _tab(client.get("/?fav=1&sort=title").text))
