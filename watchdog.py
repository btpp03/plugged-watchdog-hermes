#!/usr/bin/env python3
"""Keep the plugged.host Thai node started: if the server is Offline, press Start.

Runs on GitHub Actions (or anywhere) with Playwright. Auth comes from a saved
cookie jar in the NOPROFIT_COOKIES secret; if the session is dead it falls back to
PLUGGED_LOGIN / PLUGGED_PWD (Turnstile is solved with a real CDP mouse click).

Env:
  PLUGGED_SERVER   short uuid or name slug, e.g. 9b071dd5      (required)
  NOPROFIT_COOKIES  raw JSON or base64 JSON of the cookie array (required)
  PLUGGED_LOGIN / PLUGGED_PWD  optional fallback credentials
"""
import base64
import json
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

SERVER = os.environ.get("PLUGGED_SERVER", "9b071dd5")
BASE = "https://panel.plugged.host"
COOKIE_RAW = os.environ.get("NOPROFIT_COOKIES", "").strip()
LOGIN = os.environ.get("PLUGGED_LOGIN", "")
PWD = os.environ.get("PLUGGED_PWD", "")

for _k in list(os.environ):
    if "proxy" in _k.lower():
        os.environ.pop(_k, None)


def load_cookies():
    if not COOKIE_RAW:
        return []
    txt = COOKIE_RAW
    if not txt.lstrip().startswith(("[", "{")):
        txt = base64.b64decode(txt).decode()
    data = json.loads(txt)
    if isinstance(data, dict):
        data = data.get("cookies", [])
    out = []
    for c in data:
        if not c.get("domain"):
            continue
        out.append({
            "name": c["name"], "value": c["value"], "domain": c["domain"],
            "path": c.get("path", "/"), "secure": c.get("secure", True),
            "httpOnly": c.get("httpOnly", False),
            "sameSite": c.get("sameSite", "Lax") if c.get("sameSite") in ("Lax", "Strict", "None") else "Lax",
        })
    return out


def walk(node, out):
    if not isinstance(node, dict):
        return
    if node.get("nodeName") == "IFRAME":
        out.append(node)
    for key in ("children", "shadowRoots"):
        for child in node.get(key) or []:
            walk(child, out)
    if node.get("contentDocument"):
        walk(node["contentDocument"], out)


def solve_turnstile(ctx, page):
    """Real mouse click on the Turnstile widget (works in closed shadow roots)."""
    cdp = ctx.new_cdp_session(page)
    for _ in range(6):
        doc = cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
        iframes = []
        walk(doc["root"], iframes)
        for node in iframes:
            attrs = node.get("attributes") or []
            src = ""
            for i in range(0, len(attrs) - 1, 2):
                if attrs[i] == "src":
                    src = attrs[i + 1]
            if "challenges.cloudflare.com" not in src:
                continue
            try:
                oid = cdp.send("DOM.resolveNode", {"nodeId": node["nodeId"]})["object"]["objectId"]
                rect = json.loads(cdp.send("Runtime.callFunctionOn", {
                    "objectId": oid,
                    "functionDeclaration": "function(){var b=this.getBoundingClientRect();"
                                           "return JSON.stringify({x:b.x,y:b.y,w:b.width,h:b.height});}",
                    "returnByValue": True})["result"]["value"])
                if rect["w"] < 10 or rect["h"] < 10:
                    continue
                x, y = rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2
                for evt in ("mouseMoved", "mousePressed", "mouseReleased"):
                    cdp.send("Input.dispatchMouseEvent",
                             {"type": evt, "x": x, "y": y, "button": "left", "clickCount": 1})
                    time.sleep(0.15)
            except Exception as exc:  # noqa: BLE001
                print("turnstile click error:", exc)
        time.sleep(3)
        lens = page.evaluate(
            "Array.from(document.querySelectorAll('[name=\"cf-turnstile-response\"]'))"
            ".map(function(e){return (e.value||'').length;})")
        if any(length > 10 for length in lens):
            return True
    return False


def login(page, ctx):
    print("session dead -> trying credential login")
    if not (LOGIN and PWD):
        print("RESULT: no cookies and no credentials; nothing I can do")
        return False
    page.goto(f"https://accounts.noprofit.space/login", wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    page.fill("input[name=login]", LOGIN)
    page.fill("input[name=password]", PWD)
    try:
        page.check("input[name=legal_acceptance]", timeout=3000)
    except Exception:  # noqa: BLE001
        pass
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(2)
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(1)
    ok = solve_turnstile(ctx, page)
    print("turnstile:", "solved" if ok else "failed")
    page.evaluate("document.querySelector('form').submit()")
    time.sleep(6)
    body = " ".join(page.evaluate("() => document.body.innerText").split())
    good = "credentials do not match" not in body.lower() and ("welcome back" in body.lower() or "overview" in body.lower())
    print("login:", "ok" if good else "failed")
    return good


def status_of(page):
    text = " ".join(page.evaluate("() => document.body.innerText").split())
    m = re.search(r"Status\s+(Offline|Stopped|Starting|Running|Stopping|Suspended|Installing)", text)
    return (m.group(1) if m else "?"), text


def wait_status(page, timeout=60):
    """Panel renders the Status row a few seconds after load (slower on CI IPs)."""
    deadline = time.time() + timeout
    status, text = "?", ""
    while time.time() < deadline:
        status, text = status_of(page)
        if status != "?":
            return status, text
        time.sleep(3)
    return status, text


def health_ok():
    """Public launcher endpoint: if it answers, the container + node + agent are up."""
    import urllib.request
    url = os.environ.get("PLUGGED_HEALTH_URL", f"http://{os.environ.get('PLUGGED_HOST', 'th1.plugged.host')}:{os.environ.get('PLUGGED_PORT', '30124')}/health")
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.loads(r.read().decode())
        print("health:", data, "|", url)
        return bool(data.get("up") or data.get("agent_pids"))
    except Exception as exc:  # noqa: BLE001
        print("health failed:", str(exc)[:120], "|", url)
        return False


def main():
    if health_ok():
        print("RESULT: ONLINE-NO-ACTION (health endpoint answered)")
        return 0
    print("health endpoint dead -> checking panel, will press Start if the server is Offline")

    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = br.new_context(viewport={"width": 1400, "height": 1000}, locale="en-US")
        cookies = load_cookies()
        print("cookies:", len(cookies))
        if cookies:
            ctx.add_cookies(cookies)
        page = ctx.new_page()
        target = f"{BASE}/server/{SERVER}/console"
        page.goto(target, wait_until="domcontentloaded", timeout=60000)
        time.sleep(6)
        if "/login" in page.url or "accounts.noprofit.space/login" in page.url:
            if not login(page, ctx):
                print("RESULT: AUTH-FAIL")
                return 1
            page.goto(target, wait_until="domcontentloaded", timeout=60000)
            time.sleep(6)

        status, text = wait_status(page)
        print("status:", status, "| url:", page.url)
        if "suspended" in text.lower():
            m = re.search(r"What the scan found[^.]*\.", text)
            print("SUSPENDED:", m.group(0) if m else "see panel")
            print("RESULT: SUSPENDED-NO-ACTION")
            return 2
        if status in ("Running", "Starting", "Stopping", "Installing"):
            print("RESULT: ONLINE-NO-ACTION")
            return 0
        if status == "?":
            print("RESULT: UNKNOWN-STATUS (page never rendered a Status row)")
            print("TEXT:", text[:400])
            return 4
        # Offline / Stopped -> press Start
        clicked = False
        for name in ("Start", "Start server"):
            try:
                btn = page.get_by_role("button", name=name, exact=True).first
                if btn.is_visible():
                    btn.click(timeout=10000)
                    clicked = True
                    break
            except Exception as exc:  # noqa: BLE001
                print(f"click {name} failed:", str(exc)[:100])
        if not clicked:
            try:
                page.evaluate("""() => {
                    var b = Array.from(document.querySelectorAll('button'))
                        .find(function(x){ return (x.innerText||'').trim().toLowerCase() === 'start' && !x.disabled; });
                    if (b) b.click();
                }""")
                clicked = True
            except Exception as exc:  # noqa: BLE001
                print("js click failed:", str(exc)[:100])
        time.sleep(15)
        status2, _ = wait_status(page, timeout=45)
        print("status after start:", status2)
        for _ in range(9):  # up to ~3 min for the container to boot
            if health_ok():
                print("RESULT: RESTARTED (health endpoint is answering again)")
                break
            time.sleep(20)
        else:
            print("RESULT:", "RESTART-REQUESTED-BUT-NOT-UP" if clicked else "RESTART-FAILED")
            json.dump(ctx.cookies(), open("/tmp/plugged_cookies_after.json", "w"))
            br.close()
            return 3
        json.dump(ctx.cookies(), open("/tmp/plugged_cookies_after.json", "w"))
        br.close()
        return 0 if clicked else 3


if __name__ == "__main__":
    sys.exit(main())
