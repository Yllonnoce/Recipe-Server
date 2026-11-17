from recipelib import home as H


def test_status_table_lines_up():
    t = H.status_table([H.Check("Server", "ok", "http://127.0.0.1:8000 answering"), H.Check("Ollama", "down", "not answering"),
                        H.Check("Library", "info", "25 recipes"), H.Check("X", "unknown", "who knows")])
    assert t.splitlines() == [
        "  Server   running  http://127.0.0.1:8000 answering",
        "  Ollama   DOWN     not answering",
        "  Library           25 recipes",
        "  X        ?        who knows",
    ]


def test_menu_choices():
    assert H.parse_choice(" 1 ") == "start" and H.parse_choice("3") == "restart"
    assert H.parse_choice("0") == "quit" and H.parse_choice("Q") == "quit"
    assert H.parse_choice("") is None and H.parse_choice("x") is None and H.parse_choice("11") is None
    lines = H.menu_lines()
    assert len(lines) == 5 and lines[0].startswith("  1  Start") and lines[0].rstrip().endswith("6  Open in the browser")
    assert lines[4].rstrip().endswith("0  Quit")
    assert len({k for k, _a, _l in H.MENU}) == len(H.MENU)


def test_icon_and_launcher():
    e = H.desktop_entry("/home/me/.local/share/recipelib/recipes-home.sh")
    assert "Name=Recipe Library Server" in e and "Terminal=true" in e
    assert "Exec=/home/me/.local/share/recipelib/recipes-home.sh" in e
    s = H.launcher_script("/opt/venv/bin/recipes")
    assert s.startswith("#!/usr/bin/env bash") and '"/opt/venv/bin/recipes" home' in s and "Press Enter to close" in s


def test_menu_quits_and_refreshes(monkeypatch):
    monkeypatch.setattr(H, "gather_checks", lambda cfg=None: [H.Check("Server", "ok", "fine")])
    out, answers = [], iter(["nonsense", "4", "q"])
    H.menu(out.append, lambda _prompt: next(answers))
    assert out.count(H.TITLE) == 3 and any("Server  running" in ln for ln in out)


def test_stop_asks_while_capturing(monkeypatch):
    monkeypatch.setattr(H.sys, "platform", "linux")
    monkeypatch.setattr(H.shutil, "which", lambda _n: "/usr/bin/systemctl")
    monkeypatch.setattr(H, "_library", lambda cfg: (5, 2, 0))
    monkeypatch.setattr(H, "systemctl", lambda args: "active")
    ran = []
    monkeypatch.setattr(H.subprocess, "run", lambda *a, **k: ran.append(a))
    out = []
    H.control("stop", out.append, lambda prompt: (out.append(prompt), "n")[1])
    assert not ran and "2 recipes are being captured" in out[0] and "left alone" in out[1]
