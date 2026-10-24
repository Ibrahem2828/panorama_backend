import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import print_agent as agent  # noqa: E402

CONFIG = agent.Config(
    api_base="https://api.example.test",
    identifier="agent@example.test",
    password="x",
    printer="HP LaserJet",
    sumatra_path="S.exe",
)


class FakeClient:
    def __init__(self, pdf=b"%PDF-1.7 fake"):
        self.pdf = pdf
        self.statuses: list[tuple] = []

    def set_status(self, job_id, status, error="", printer=""):
        self.statuses.append((job_id, status, error, printer))

    def download(self, job_id):
        return self.pdf


class SettingsTest(unittest.TestCase):
    def test_two_sided_long_and_short_edge(self):
        self.assertIn("duplexlong", agent.sumatra_settings({"double_sided": True, "duplex_flip": "long_edge"}))
        self.assertIn("duplexshort", agent.sumatra_settings({"double_sided": True, "duplex_flip": "short_edge"}))

    def test_one_sided_colour_and_paper(self):
        settings = agent.sumatra_settings({"double_sided": False, "color_mode": "color", "paper_size": "a3"})
        self.assertEqual(settings.split(","), ["simplex", "color", "paper=A3", "fit"])

    def test_the_file_is_printed_once(self):
        # every copy is already inside the file: no copies option may be added
        import re

        self.assertIsNone(re.search(r"(^|,)\d+x(,|$)", agent.sumatra_settings({"copies": 5})))

    def test_command(self):
        command = agent.sumatra_command(CONFIG, "job.pdf", {"double_sided": True})
        self.assertEqual(command[:3], ["S.exe", "-print-to", "HP LaserJet"])
        self.assertEqual(command[-1], "job.pdf")
        self.assertIn("-silent", command)


class ConfigTest(unittest.TestCase):
    def test_https_is_required(self):
        with self.assertRaises(ValueError):
            agent.Config("http://api.example.test", "a", "b", "P", "S").validate()

    def test_plain_http_only_when_explicitly_allowed(self):
        agent.Config("http://127.0.0.1:8000", "a", "b", "P", "S", allow_insecure_http=True).validate()

    def test_a_printer_is_required(self):
        with self.assertRaises(ValueError):
            agent.Config("https://x.test", "a", "b", "", "S").validate()

    def test_load_reads_the_file(self):
        path = Path(__file__).with_name("tmp-config.json")
        try:
            path.write_text(
                json.dumps(
                    {"api_base": "https://api.example.test/", "identifier": "a", "password": "b", "printer": "P"}
                ),
                encoding="utf-8",
            )
            config = agent.Config.load(path)
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(config.api_base, "https://api.example.test")
        self.assertGreaterEqual(config.poll_seconds, 2)


class PrintJobTest(unittest.TestCase):
    def test_success_reports_printing_then_done(self):
        client = FakeClient()
        ran = []
        ok = agent.print_job(
            client,
            CONFIG,
            {"id": 5, "options": {"double_sided": True}},
            run=lambda cmd, **kw: ran.append(cmd) or SimpleNamespace(returncode=0),
        )
        self.assertTrue(ok)
        self.assertEqual([entry[1] for entry in client.statuses], ["printing", "done"])
        self.assertEqual(client.statuses[0][3], "HP LaserJet")
        self.assertEqual(len(ran), 1)

    def test_a_printer_error_is_reported_as_failed(self):
        client = FakeClient()
        ok = agent.print_job(client, CONFIG, {"id": 6}, run=lambda cmd, **kw: SimpleNamespace(returncode=3))
        self.assertFalse(ok)
        self.assertEqual(client.statuses[-1][1], "failed")
        self.assertIn("code 3", client.statuses[-1][2])

    def test_something_that_is_not_a_pdf_is_never_sent_to_the_printer(self):
        client = FakeClient(pdf=b"<html>login</html>")
        ran = []
        ok = agent.print_job(client, CONFIG, {"id": 7}, run=lambda cmd, **kw: ran.append(cmd))
        self.assertFalse(ok)
        self.assertEqual(ran, [])
        self.assertEqual(client.statuses[-1][1], "failed")

    def test_a_crash_while_printing_does_not_stop_the_agent(self):
        client = FakeClient()

        def boom(cmd, **kw):
            raise OSError("spooler stopped")

        self.assertFalse(agent.print_job(client, CONFIG, {"id": 8}, run=boom))
        self.assertEqual(client.statuses[-1][1], "failed")


class ClientTest(unittest.TestCase):
    def test_a_401_signs_in_again_once(self):
        calls = []

        class Response:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return self.body

        def opener(request, timeout):
            calls.append((request.get_method(), request.full_url))
            if request.full_url.endswith("/auth/login/"):
                return Response(json.dumps({"data": {"access": f"token-{len(calls)}"}}).encode())
            if len(calls) == 2:  # first authenticated call is refused
                import urllib.error

                raise urllib.error.HTTPError(request.full_url, 401, "no", {}, None)  # type: ignore[arg-type]
            return Response(json.dumps({"data": {"results": [{"id": 1}]}}).encode())

        client = agent.Client(CONFIG, opener=opener)
        self.assertEqual(client.queued_jobs(), [{"id": 1}])
        self.assertEqual(len([c for c in calls if c[1].endswith("/auth/login/")]), 2)


if __name__ == "__main__":
    unittest.main()
