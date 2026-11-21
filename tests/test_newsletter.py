"""Tests for the newsletter. Nothing here touches the network or the model:
sources._get_json and the model call are swapped for stubs."""

import json
import os
import re
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
    def stub(self, fn, image=b"PNG"):
        for name, value in (("_get_json", fn), ("_get_bytes", lambda url: image)):
            self.addCleanup(setattr, sources, name, getattr(sources, name))
            setattr(sources, name, value)


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
        self.assertEqual(found[0].url, "https://arxiv.org/abs/c")
        self.assertEqual(found[0].list_url, "https://huggingface.co/papers/week/2026-W47")

    def test_comic_skips_seen_numbers(self):
        def fake(url):
            if "explainxkcd" in url:
                return {"query": {"categorymembers": [
                    {"title": "329: Turing Test"}, {"title": "1263: Reassuring"}, {"title": "unixkcd"}]}}
            return {"safe_title": "Reassuring", "img": "i.png", "alt": "a"}
        self.stub(fake)
        found = sources.comic({329})
        self.assertEqual((found.num, found.image), (1263, b"PNG"))
        self.assertIsNone(sources.comic({329, 1263}))


class TestSafety(unittest.TestCase):
    def test_only_the_mailing_list_is_mailed(self):
        listed = ["marcus.presutti.eu@gmail.com", "abott@gnhearing.com"]
        notify.check_allowed(["Marcus.Presutti.EU@gmail.com", "abott@gnhearing.com"], listed)
        with self.assertRaises(SystemExit):
            notify.check_allowed(["marcus.presutti.eu@gmail.com", "stranger@x.dk"], listed)

    def test_the_whole_list_is_on(self):
        self.assertEqual(len(notify.recipients()), 8)
        notify.check_allowed(notify.recipients())
        notify.check_allowed(["marcus.presutti.eu@gmail.com"])  # what test-send.yml uses

    def test_mail_carries_the_comic_as_an_inline_image(self):
        sent = []

        class FakeSMTP:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def starttls(self): pass
            def login(self, *a): pass
            def send_message(self, mail): sent.append(mail)
        original = notify.smtplib.SMTP
        notify.smtplib.SMTP = FakeSMTP
        self.addCleanup(setattr, notify.smtplib, "SMTP", original)
        os.environ["SMTP_PASSWORD"] = "x"
        self.addCleanup(os.environ.pop, "SMTP_PASSWORD")
        html = newsletter.html(issue(), "x")
        notify.send_each([("x", "marcus.presutti.eu@gmail.com", "s", "t", html)],
                         images=newsletter.images(issue()))
        [mail] = sent
        [image] = [part for part in mail.walk() if part.get_content_maintype() == "image"]
        self.assertEqual(image["Content-ID"], "<xkcd-comic>")
        self.assertEqual(image.get_content(), b"PNGDATA")
        self.assertIn('src="cid:xkcd-comic"', mail.get_body(("html",)).get_content())

    def test_send_refuses_before_connecting(self):
        connected = []
        original = notify.smtplib.SMTP
        notify.smtplib.SMTP = lambda *a, **k: connected.append(a)
        self.addCleanup(setattr, notify.smtplib, "SMTP", original)
        with self.assertRaises(SystemExit):
            notify.send_each([("", "someone@else.dk", "s", "t", "h")])
        self.assertEqual(connected, [])


def yahoo(points):
    from datetime import datetime, timezone
    stamps = [int(datetime(d.year, d.month, d.day, 8, tzinfo=timezone.utc).timestamp()) for d, _ in points]
    return {"chart": {"result": [{"meta": {"shortName": "GN Store Nord", "currency": "DKK"},
                                  "timestamp": stamps,
                                  "indicators": {"quote": [{"close": [c for _, c in points]}]}}]}}


class TestStock(Stubbed):
    def test_month_with_the_latest_week_marked(self):
        # Thu 12/11, Fri 13/11, then Mon 16 .. Wed 18/11 (a short week), one null.
        points = [(date(2026, 11, 12), 90.0), (date(2026, 11, 13), 100.0),
                  (date(2026, 11, 16), 104.0), (date(2026, 11, 17), None), (date(2026, 11, 18), 95.0)]
        self.stub(lambda url: yahoo(points))
        s = sources.stock()
        self.assertEqual(len(s.month), 4)
        self.assertEqual([d.day for d, _ in s.week], [16, 18])
        self.assertEqual(s.previous_close, 100.0)
        self.assertAlmostEqual(s.change_pct, -5.0)

    def test_chart_highlights_only_the_latest_week(self):
        html = newsletter._stock_html(issue().stock)
        self.assertEqual(html.count(f"background:{newsletter.PAST};"), 1)
        self.assertEqual(html.count(f"background:{newsletter.UP};"), 1)
        self.assertIn("+10.0%", html)


class TestPaperSources(Stubbed):
    def test_falls_back_to_alphaxiv(self):
        def fake(url):
            if "huggingface" in url:
                raise ConnectionError("down")
            return {"papers": [{"id": "u1", "universal_paper_id": "2611.1", "title": "Seen"},
                               {"id": "u2", "universal_paper_id": "2611.22222", "title": "New  one",
                                "feed_description": "Does X.", "metrics": {"public_total_votes": 4}}]}
        self.stub(fake)
        [paper] = sources.papers({"2611.1"}, today=date(2026, 11, 22))
        self.assertEqual((paper.title, paper.source, paper.upvotes), ("New one", "alphaXiv", 4))
        self.assertEqual(paper.url, "https://arxiv.org/abs/2611.22222")
        self.assertEqual(paper.list_url, sources.ALPHAXIV_WEEK_PAGE)

    def test_alphaxiv_only_papers_link_to_alphaxiv(self):
        def fake(url):
            if "huggingface" in url:
                raise ConnectionError("down")
            return {"papers": [{"id": "u", "universal_paper_id": "2611.harness-cg", "title": "T"}]}
        self.stub(fake)
        [paper] = sources.papers(set(), today=date(2026, 11, 22))
        self.assertEqual(paper.url, "https://www.alphaxiv.org/abs/2611.harness-cg")

    def test_both_down_leaves_the_section_out(self):
        def fake(url):
            raise ConnectionError("down")
        self.stub(fake)
        self.assertIsNone(sources.papers(set(), today=date(2026, 11, 22)))


def issue(words=None):
    return newsletter.Issue(
        date(2026, 11, 22),
        sources.Stock("GN", "DKK", [(date(2026, 11, 13), 100.0), (date(2026, 11, 20), 110.0)], 1),
        [sources.Paper("2611.12345", "Paper <one>", "Sum.", 9,
                       list_url="https://huggingface.co/papers/week/2026-W47")],
        sources.Comic(329, "Turing Test", "https://imgs.xkcd.com/comics/turing_test.png", "alt",
                      image=b"PNGDATA"),
        sources.Joke(7, "Why?", "Because."),
        words=words or Words(f"Good evening, {PLACEHOLDER}.", "Hello & welcome.", "Farewell, kind soul."),
    )


def reply(**pieces):
    return lambda request: json.dumps(pieces)


class TestAIfred(unittest.TestCase):
    def test_the_model_is_asked_once_and_told_nothing_specific(self):
        seen = []

        def call(request):
            seen.append(request)
            return json.dumps({"greeting": f"Hello {PLACEHOLDER}", "letter": f"Up 10% {EMOJI}",
                               "farewell": "Bye"})
        words = newsletter.write(issue(), call=call)
        self.assertEqual(len(seen), 1)
        prompt = seen[0]["messages"][1]["content"]
        # The intro is generic: no numbers, titles or punchlines reach the model.
        for detail in ("10.0", "110", "Turing Test", "Paper <one>", "Why?", "Because."):
            self.assertNotIn(detail, prompt)
        self.assertEqual(words.greet("Dr. Bott"), "Hello Dr. Bott")

    def test_model_failure_falls_back_to_the_template(self):
        def broken(request):
            raise ConnectionError("no ollama")
        words = newsletter.write(issue(), call=broken)
        self.assertIn(PLACEHOLDER, words.greeting)
        self.assertNotIn("10.0", words.letter)
        self.assertIn(EMOJI, words.letter)

    def test_garbage_reply_falls_back(self):
        words = newsletter.write(issue(), call=lambda r: "not json")
        self.assertIn(PLACEHOLDER, words.greeting)

    def test_missing_placeholder_is_added(self):
        words = newsletter.write(issue(), call=reply(greeting="Good evening, sir.", letter="L",
                                                     farewell="F"))
        self.assertEqual(words.greet("Miss Leschly"), "Dear Miss Leschly, good evening, sir.")

    def test_placeholder_variants_and_duplicates(self):
        words = newsletter.write(issue(), call=reply(greeting="Ah, { NAME } and {name}.",
                                                     letter=f"Hi {PLACEHOLDER}", farewell="F"))
        self.assertEqual(words.greet("Dr. Bott"), "Ah, Dr. Bott and .")
        self.assertNotIn(PLACEHOLDER, words.letter)

    def test_long_intro_is_cut_to_two_sentences(self):
        words = newsletter.write(issue(), call=reply(
            greeting=f"Hi {PLACEHOLDER}", letter="One. Two! Three? Four.", farewell="A. B. C. D."))
        self.assertTrue(words.letter.startswith("One. Two!"))
        self.assertNotIn("Three", words.letter)
        self.assertEqual(words.farewell, "A. B. C.")

    def test_greeting_is_cut_to_one_sentence(self):
        words = newsletter.write(issue(), call=reply(
            greeting=f"Dear {PLACEHOLDER}, welcome. Please accept my bow.", letter="L", farewell="F"))
        self.assertEqual(words.greet("Dr. Bott"), "Dear Dr. Bott, welcome.")

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

    def test_subject_names_the_week_that_starts_tomorrow(self):
        # Sunday 22/11/2026 closes ISO week 47; the issue opens week 48.
        self.assertEqual(newsletter.subject(issue()), f"{EMOJI} AI-fred's weekly · Week 48")

    def test_sections_alternate_white_and_shaded(self):
        html = newsletter.html(issue(), "x")
        cells = re.findall(r'<td style="padding:[^"]*background:(#[0-9a-f]{6});', html)
        # intro, stock, xkcd, papers, joke, farewell
        W, S = newsletter.WHITE, newsletter.SHADE
        self.assertEqual(cells, [W, S, W, S, W, S])
        order = [html.index(h) for h in ("GN · Nasdaq Copenhagen: GN", ">xkcd<", "ML papers of the week",
                                           "Joke of the week")]
        self.assertEqual(order, sorted(order), "xkcd comes right after the stock")
        self.assertNotIn(f"border-top:1px solid {newsletter.RULE}", html)

    def test_xkcd_stays_white_without_the_stock(self):
        bare = issue()
        bare.stock = None
        html = newsletter.html(bare, "x")
        cells = re.findall(r'<td style="padding:[^"]*background:(#[0-9a-f]{6});', html)
        W, S = newsletter.WHITE, newsletter.SHADE
        # intro, xkcd (forced white, with a hairline), papers, joke, farewell
        self.assertEqual(cells, [W, W, S, W, S])
        self.assertIn(f"border-top:1px solid {newsletter.RULE}", html)

    def test_paper_title_links_the_article_and_upvotes_the_week(self):
        html = newsletter.html(issue(), "x")
        self.assertIn('<a href="https://arxiv.org/abs/2611.12345" ', html)
        self.assertRegex(html, r'<a href="https://huggingface.co/papers/week/2026-W47" '
                               r'[^>]*>&#9650; 9 upvotes on Hugging Face')
        self.assertIn("https://huggingface.co/papers/week/2026-W47", newsletter.text(issue(), "x"))

    def test_comic_is_embedded_not_linked(self):
        html = newsletter.html(issue(), "x")
        self.assertIn('src="cid:xkcd-comic"', html)
        self.assertNotIn('src="https://imgs.xkcd.com', html)
        self.assertEqual(newsletter.images(issue()), [("xkcd-comic", b"PNGDATA", "png")])
        # A browser preview cannot resolve cid:, so it links the image instead.
        self.assertIn('src="https://imgs.xkcd.com/comics/turing_test.png"',
                      newsletter.html(issue(), "x", embedded=False))

    def test_failed_download_falls_back_to_the_link(self):
        bare = issue()
        bare.comic.image = b""
        self.assertIn('src="https://imgs.xkcd.com/comics/turing_test.png"', newsletter.html(bare, "x"))
        self.assertEqual(newsletter.images(bare), [])

    def test_stock_is_never_written_as_a_domain(self):
        for out in (newsletter.html(issue(), "x"), newsletter.text(issue(), "x")):
            self.assertNotIn("GN.CO", out)
            self.assertIn("Nasdaq Copenhagen: GN", out)

    def test_no_explain_xkcd_link(self):
        self.assertNotIn("explainxkcd", newsletter.html(issue(), "x"))

    def test_ai_fred_knows_the_week_starts_tomorrow(self):
        text = newsletter.facts(issue(), mood="calm")
        self.assertIn("Sunday 22 November 2026, in the evening", text)
        self.assertIn("On Monday 23 November, week 48 begins", text)

    def test_record_appends_what_was_sent(self):
        history = newsletter.record(issue(), {"jokes": [1], "papers": [], "xkcd": []})
        self.assertEqual(history, {"jokes": [1, 7], "papers": ["2611.12345"], "xkcd": [329]})


if __name__ == "__main__":
    unittest.main()
