"""Tests for the newsletter. Nothing here touches the network or the model:
sources._get_json and the model call are swapped for stubs."""

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import newsletter  # noqa: E402
import notify  # noqa: E402
import sources  # noqa: E402
from newsletter import EMOJI, PLACEHOLDER, Words  # noqa: E402


class Stubbed(unittest.TestCase):
    def stub(self, fn):
        original = sources._get_json
        sources._get_json = fn
        self.addCleanup(setattr, sources, "_get_json", original)


def write_list(text):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
        handle.write(text)
    return handle.name


class TestContacts(unittest.TestCase):
    def test_repo_list_is_readable_and_named(self):
        people = notify.contacts()
        self.assertTrue(people, "recipients.txt has no addresses")
        for name, address in people:
            self.assertIn("@", address)
            self.assertNotIn("@", name)

    def test_all_three_line_forms(self):
        path = write_list(
            "# c\n"
            "Dr. Bott <bott@x.dk>\n"
            "Miss Leschly  lesch@x.dk   # trailing comment\n"
            "bare@y.dk\n"
            "Dr. Bott bott@x.dk\n"  # repeated address: ignored
        )
        self.assertEqual(
            notify.contacts(path),
            [("Dr. Bott", "bott@x.dk"), ("Miss Leschly", "lesch@x.dk"), ("", "bare@y.dk")],
        )
        self.assertEqual(notify.recipients(path), ["bott@x.dk", "lesch@x.dk", "bare@y.dk"])

    def test_non_ascii_names(self):
        self.assertEqual(notify.contacts(write_list("Kris Sørensen k@x.dk\n")),
                         [("Kris Sørensen", "k@x.dk")])


class TestNoRepeats(Stubbed):
    def test_joke_skips_seen_ids(self):
        jokes = [{"id": i, "safe": True, "type": "single", "joke": f"j{i}"} for i in (1, 2, 3)]
        self.stub(lambda url: {"jokes": jokes})
        self.assertEqual(sources.joke({1, 2}).id, 3)

    def test_joke_gives_up_quietly(self):
        self.stub(lambda url: {"jokes": [{"id": 1, "safe": True, "type": "single", "joke": "x"}]})
        self.assertIsNone(sources.joke({1}))

    def test_papers_skip_seen_and_sort_by_upvotes(self):
        items = [{"paper": {"id": pid, "title": pid, "summary": "S. T.", "upvotes": up}}
                 for pid, up in (("a", 5), ("b", 50), ("c", 20), ("d", 10))]
        self.stub(lambda url: items)
        found = sources.papers({"b"}, today=date(2026, 11, 20), count=2)
        self.assertEqual([p.id for p in found], ["c", "d"])
        self.assertEqual(found[0].summary, "S.")

    def test_comic_skips_seen_numbers(self):
        def fake(url):
            if "explainxkcd" in url:
                return {"query": {"categorymembers": [
                    {"title": "329: Turing Test"}, {"title": "1263: Reassuring"}, {"title": "unixkcd"}]}}
            return {"safe_title": "Reassuring", "img": "i.png", "alt": "a"}
        self.stub(fake)
        self.assertEqual(sources.comic({329}).num, 1263)
        self.assertIsNone(sources.comic({329, 1263}))


def issue(words=None):
    return newsletter.Issue(
        date(2026, 11, 22),
        sources.Stock("GN", "DKK", [(date(2026, 11, 20), 110.0)], 100.0),
        [sources.Paper("2611.1", "Paper <one>", "Sum.", 9)],
        sources.Comic(329, "Turing Test", "i.png", "alt"),
        sources.Joke(7, "Why?", "Because."),
        words=words or Words(f"Good evening, {PLACEHOLDER}.", "Hello & welcome.", "Farewell, kind soul."),
    )


def reply(**pieces):
    return lambda request: json.dumps(pieces)


class TestAIfred(unittest.TestCase):
    def test_the_model_is_asked_once_with_the_facts(self):
        seen = []

        def call(request):
            seen.append(request)
            return json.dumps({"greeting": f"Hello {PLACEHOLDER}", "letter": f"Up 10% {EMOJI}",
                               "farewell": "Bye"})
        words = newsletter.write(issue(), call=call)
        self.assertEqual(len(seen), 1)
        self.assertIn("rose 10.0%", seen[0]["messages"][1]["content"])
        self.assertIn("Turing Test", seen[0]["messages"][1]["content"])
        self.assertNotIn("Because.", seen[0]["messages"][1]["content"], "never spoil the joke")
        self.assertEqual(words.greet("Dr. Bott"), "Hello Dr. Bott")

    def test_model_failure_falls_back_to_the_template(self):
        def broken(request):
            raise ConnectionError("no ollama")
        words = newsletter.write(issue(), call=broken)
        self.assertIn(PLACEHOLDER, words.greeting)
        self.assertIn("rose 10.0%", words.letter)
        self.assertIn(EMOJI, words.letter)

    def test_garbage_reply_falls_back(self):
        words = newsletter.write(issue(), call=lambda r: "not json")
        self.assertIn(PLACEHOLDER, words.greeting)

    def test_missing_placeholder_is_added(self):
        words = newsletter.write(issue(), call=reply(greeting="Good evening, sir.", letter="L",
                                                     farewell="F"))
        self.assertEqual(words.greet("Miss Leschly"), "Dear Miss Leschly, good evening, sir.")

    def test_placeholder_variants_and_duplicates(self):
        words = newsletter.write(issue(), call=reply(greeting="Ah, { NAME }! Yes, {name}.",
                                                     letter=f"Hi {PLACEHOLDER}", farewell="F"))
        self.assertEqual(words.greet("Dr. Bott"), "Ah, Dr. Bott! Yes, .")
        self.assertNotIn(PLACEHOLDER, words.letter)

    def test_emoji_is_guaranteed(self):
        words = newsletter.write(issue(), call=reply(greeting=f"Hi {PLACEHOLDER}", letter="L",
                                                     farewell="F"))
        self.assertIn(EMOJI, words.greeting + words.letter + words.farewell)

    def test_no_name_gets_a_polite_default(self):
        self.assertEqual(issue().words.greet(""), f"Good evening, {newsletter.DEFAULT_NAME}.")


class TestRender(unittest.TestCase):
    def test_html_is_personal_escaped_and_signed(self):
        html = newsletter.html(issue(), "Dr. Bott")
        self.assertIn("Good evening, Dr. Bott.", html)
        self.assertIn("Paper &lt;one&gt;", html)
        self.assertIn("Hello &amp; welcome.", html)
        self.assertIn("Farewell, kind soul.", html)
        self.assertIn(newsletter.SIGNATURE, html)
        # The farewell closes the mail: it comes after every section.
        self.assertGreater(html.index("Farewell"), html.index("Joke of the week"))

    def test_text_ends_with_the_signed_farewell(self):
        text = newsletter.text(issue(), "Dr. Bott").rstrip()
        self.assertTrue(text.startswith("Good evening, Dr. Bott."))
        self.assertTrue(text.endswith(f"— {newsletter.SIGNATURE}"))

    def test_missing_sections_are_left_out(self):
        bare = issue()
        bare.stock = bare.papers = bare.comic = bare.joke = None
        html = newsletter.html(bare, "x")
        self.assertNotIn("xkcd", html)
        self.assertIn("Farewell", html)

    def test_subject(self):
        self.assertEqual(newsletter.subject(issue()), f"{EMOJI} AI-fred's weekly · Week 47")

    def test_record_appends_what_was_sent(self):
        history = newsletter.record(issue(), {"jokes": [1], "papers": [], "xkcd": []})
        self.assertEqual(history, {"jokes": [1, 7], "papers": ["2611.1"], "xkcd": [329]})


if __name__ == "__main__":
    unittest.main()
