"""Browser replay for the add-on report. Run with Playwright and Chromium installed."""
import mimetypes
import os
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None

APP = Path(__file__).resolve().parents[1] / "addon" / "app"
ROBOTS = [{"vacuum": "vacuum.one", "title": "First robot"},
          {"vacuum": "vacuum.two", "title": "Second robot"}]


def sample_report(vacuum):
    rooms = [
        {"seg": "1", "name": "Kitchen", "status": "cleaned", "status_today": "cleaned",
         "cleaned_at": "2026-05-14T09:00:00+01:00", "day_names": ["Thu"]},
        {"seg": "2", "name": "Hallway", "status": "skipped", "status_today": "pending",
         "blocked_reason": "Door closed", "scheduled_today": True, "day_names": ["Thu"]},
        {"seg": "3", "name": "Library", "status": "cleaned", "status_today": "not_scheduled",
         "cleaned_at": "2026-05-12T09:00:00+01:00", "day_names": ["Tue"]},
        {"seg": "4", "name": "Living room", "status": "pending", "status_today": "pending",
         "scheduled_today": True, "day_names": ["Thu"]},
        {"seg": "5", "name": "Guest room", "status": "not_scheduled", "status_today": "not_scheduled"},
    ] if vacuum == "vacuum.one" else [
        {"seg": "1", "name": "Workshop", "status": "cleaned", "status_today": "not_scheduled"}
    ]
    return {"found": True, "entry_id": vacuum, "map": "Ground floor", "rooms": rooms,
            "generated": "2026-05-14T12:00:00+01:00", "week_start": "2026-05-11", "cleaned_area_today": 16,
            "history": [{"ts": "2026-05-14T09:00:00+01:00", "weekday": 3, "kind": "daily",
                         "per_room": {"1": {"status": "cleaned"}}}]}


@unittest.skipUnless(sync_playwright, "Requires Playwright and Chromium")
class ReportBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(executable_path=os.environ.get("DREAME_TEST_CHROMIUM"))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 360, "height": 800})
        self.addCleanup(self.page.close)
        self.report = sample_report
        self.requests = []
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.route("**/*", self.route)
        self.page.goto("http://addon.test/")
        self.page.locator(".deck-card").first.wait_for()

    def route(self, route):
        url = urlparse(route.request.url)
        vacuum = parse_qs(url.query).get("vacuum", ["vacuum.one"])[0]
        if url.path == "/":
            template = (APP / "templates/index.html").read_text()
            route.fulfill(content_type="text/html", body=template.replace("{{ ASSET_V }}", "test").replace("{{ ADDON_V }}", "test"))
        elif url.path.startswith("/static/"):
            path = APP / url.path.lstrip("/")
            route.fulfill(path=path, content_type=mimetypes.guess_type(path)[0])
        elif url.path == "/api/ha/config":
            route.fulfill(json={"found": True, "vacuum": vacuum, "title": "Demo", "vacuums": ROBOTS})
        elif url.path == "/api/ha/report":
            self.requests.append(vacuum)
            route.fulfill(json=self.report(vacuum))
        else:
            route.fulfill(json={})

    def open_report(self):
        if self.page.locator("#nav-burger").is_visible():
            self.page.locator("#nav-burger").click()
        self.page.get_by_role("button", name="Report", exact=True).click()
        self.page.locator("#report-results").wait_for()

    def test_scope_filters_and_history_follow_the_robot(self):
        page = self.page
        self.open_report()
        expect(page.locator("#report-clean-count")).to_have_text("2 of 4 rooms cleaned")
        missed = page.locator('[data-filter="skipped"]')
        missed.focus()
        page.keyboard.press("Enter")
        expect(missed).to_be_focused()
        expect(page.locator(".report-room-name")).to_have_text(["Hallway"])
        expect(page.locator(".report-room-meta")).to_contain_text("Door closed")
        page.get_by_role("button", name="Today", exact=True).click()
        expect(page.locator("#report-rooms")).to_have_text("No rooms match this filter.")
        page.locator('[data-filter="all"]').click()
        expect(page.locator(".report-room-name")).to_have_text(["Kitchen", "Hallway", "Living room"])
        missed.focus()
        page.evaluate("loadReport()")
        expect(missed).to_be_focused()
        page.locator("#vac-picker-wrap select").select_option("vacuum.two")
        expect(page.locator("#report-rooms")).to_have_text("No rooms scheduled or recorded today.")
        expect(page.locator("#report-history .ih-chip")).to_contain_text("Workshop")
        self.assertEqual(self.requests[-1], "vacuum.two")
        self.assertFalse(self.errors)

    def test_card_yaml_updates_when_switching_robots(self):
        self.page.set_viewport_size({"width": 1200, "height": 800})
        self.page.get_by_role("button", name="Add to HA", exact=True).click()
        card = self.page.locator("#dash-cards pre").first
        expect(card).to_contain_text("vacuum_entity: vacuum.one")
        self.page.locator("#vac-picker-wrap select").select_option("vacuum.two")
        expect(card).to_contain_text("vacuum_entity: vacuum.two")

    def test_retry_recovers_after_an_unavailable_report(self):
        self.open_report()
        self.report = lambda vacuum: {"found": False}
        self.page.evaluate("loadReport()")
        expect(self.page.locator("#report-results")).to_be_hidden()
        expect(self.page.locator("#report-history-card")).to_be_hidden()
        self.page.get_by_role("button", name="Today", exact=True).click()
        expect(self.page.locator("#report-results")).to_be_hidden()
        self.report = sample_report
        self.page.get_by_role("button", name="Retry", exact=True).click()
        expect(self.page.locator("#report-history .ih-chip")).to_contain_text("Kitchen")
        expect(self.page.locator("#report-results")).to_be_visible()

    def test_older_payload_keeps_past_results_out_of_today(self):
        def legacy(vacuum):
            report = sample_report(vacuum)
            for room in report["rooms"]:
                room.pop("status_today")
            return report

        self.report = legacy
        self.open_report()
        self.page.get_by_role("button", name="Today", exact=True).click()
        expect(self.page.locator(".report-room-name")).to_have_text(["Kitchen", "Hallway", "Living room"])
        expect(self.page.locator(".report-outcome")).to_have_text(["Cleaned", "Pending", "Pending"])
        self.assertFalse(self.errors)


if __name__ == "__main__":
    unittest.main()
