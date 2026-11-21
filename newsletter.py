"""The weekly newsletter: AI-fred's letter, GN's week, papers, xkcd, a joke, farewell.

`gather()` fetches everything once; `render` (html/text) then produces one
personal mail per recipient. AI-fred's words are written fresh every week by
the local model, in a single call: the greeting carries a {name} placeholder
that is filled in per recipient, so the cost does not grow with the list.
"""

from __future__ import annotations

import json
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from html import escape

import llm
import sources

AUTHOR = "AI-fred"
EMOJI = "🧐"  # AI-fred's monocle; guaranteed to appear at least once
SIGNATURE = f"{AUTHOR} {EMOJI}"
# How AI-fred addresses someone listed in recipients.txt without a name.
DEFAULT_NAME = "Esteemed Reader"
PLACEHOLDER = "{name}"

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
# Headings: a classic book serif, fitting for a butler. Web fonts do not load in
# Gmail or Outlook, so this is a stack of fonts already installed everywhere.
SERIF = "'Palatino Linotype',Palatino,'Book Antiqua',Georgia,serif"
# Sections alternate between these two backgrounds, starting white.
WHITE, SHADE = "#ffffff", "#f6f5f8"
INK, BODY, MUTED, RULE = "#1b1c1a", "#3d413a", "#8a8f85", "#ecefe8"
UP, DOWN = "#1d6b34", "#a33a2a"


@dataclass
class Words:
    """What AI-fred writes this week. `greeting` contains PLACEHOLDER."""

    greeting: str
    letter: str
    farewell: str

    def greet(self, name: str | None) -> str:
        return self.greeting.replace(PLACEHOLDER, name or DEFAULT_NAME)


@dataclass
class Issue:
    when: date
    stock: sources.Stock | None
    papers: list[sources.Paper] | None
    comic: sources.Comic | None
    joke: sources.Joke | None
    words: Words = field(default=None)  # filled by gather()/write()

    @property
    def week(self) -> int:
        """The week this issue opens. It goes out on Sunday evening, so that is
        the week starting the next day, not the ISO week the Sunday belongs to."""
        return self.monday.isocalendar()[1]

    @property
    def monday(self) -> date:
        """The Monday after `when` (tomorrow, on the scheduled Sunday run)."""
        return self.when + timedelta(days=7 - self.when.weekday())


def gather(history: dict[str, list], today: date | None = None) -> Issue:
    today = today or date.today()
    issue = Issue(
        when=today,
        stock=sources.stock(),
        papers=sources.papers(set(history["papers"]), today=today),
        comic=sources.comic(set(history["xkcd"])),
        joke=sources.joke(set(history["jokes"])),
    )
    issue.words = write(issue)
    return issue


def record(issue: Issue, history: dict[str, list]) -> dict[str, list]:
    """Add what this issue sent to the history, so it is never sent again."""
    if issue.joke:
        history["jokes"].append(issue.joke.id)
    if issue.papers:
        history["papers"].extend(p.id for p in issue.papers)
    if issue.comic:
        history["xkcd"].append(issue.comic.num)
    return history


# --- AI-fred ---------------------------------------------------------------

SYSTEM = f"""\
You are {AUTHOR}, the butler who writes a weekly newsletter for a small machine
learning team at GN, a Danish hearing-aid and headset company.

Your character: a wildly over-the-top, old-fashioned English butler. Absurdly
polite, gentle and deferential. You bow, you fuss, you apologise for things
that are not your fault, you treat a share price or a research paper as if
presenting a silver tray at a grand dinner. Theatrical, florid vocabulary
("I took the liberty", "if I may be so bold", "most humbly", "pray forgive
me"). Warmly funny, never rude, never sarcastic, never cold or newsy.

Write three pieces, in that voice, new every week - never reuse a stock line:

- greeting: one short, theatrical opening line addressed to the reader. Write
  the literal token {PLACEHOLDER} where their name goes, exactly once. Do not
  add a title such as Mr. or Sir before it: the name already carries one.
- letter: a short intro of at most two short sentences, under 35 words, presenting
  the issue as a butler presents a tray. Keep it general: do not mention any
  numbers, share prices, papers, comics or jokes - the reader finds those below.
  The four sections are GN stock prices, a comic strip, popular machine learning
  papers this week, and a joke.
- farewell: two or three sentences, under 60 words. The newsletter arrives on
  Sunday evening and a new working week begins tomorrow, Monday - this mail is
  the first thing of that week. Send the reader off into the week ahead with a
  gracious, slightly overwrought wish for it to go splendidly. Do not speak of
  the week as over or of a weekend ahead. Do not sign it; the signature is
  added.

Use the emoji {EMOJI} at least once across the three. No other emoji, no
markdown, no lists, no numbers."""

# A different mood each week, so the letters do not all come out alike.
MOODS = [
    "It is a cold, dark November evening and you have lit the fire.",
    "You have just finished polishing the family silver, rather proudly.",
    "You are slightly flustered: the soufflé collapsed this morning.",
    "You have been reading poetry and it shows.",
    "The household cat has been unusually demanding this week.",
    "You are preparing afternoon tea and are in excellent spirits.",
    "You were up all night ironing newspapers and are heroically composed.",
    "The weather is dreadful and you blame yourself for it.",
    "You have discovered a computer and are very proud of understanding it.",
    "Spring cleaning has gone splendidly; everything gleams.",
]

WORDS_SCHEMA = {
    "type": "object",
    "properties": {
        "greeting": {"type": "string"},
        "letter": {"type": "string"},
        "farewell": {"type": "string"},
    },
    "required": ["greeting", "letter", "farewell"],
}


def facts(issue: Issue, mood: str | None = None) -> str:
    lines = [f"Sent: {issue.when:%A %d %B %Y}, in the evening. "
             f"On Monday {issue.monday:%d %B}, week {issue.week} begins.",
             f"Your mood this week: {mood or random.choice(MOODS)}"]
    # Deliberately nothing about the stock, papers, comic or joke: the intro is
    # meant to be generic, and what the model is not told it cannot misquote.
    return "\n".join(lines)


FALLBACK_GREETINGS = [
    f"Good day to you, {PLACEHOLDER}, and a most humble welcome.",
    f"Ah, {PLACEHOLDER}! Do come in; your newsletter is served.",
    f"My deepest bow to you, {PLACEHOLDER}.",
]


def fallback_words(issue: Issue) -> Words:
    """Used when the model is unavailable - stiff, but never wrong."""
    letter = f"Your weekly dispatch is served, freshly pressed and warmed {EMOJI}."
    return Words(
        greeting=random.choice(FALLBACK_GREETINGS),
        letter=letter,
        farewell=("And so a new week begins. May it treat you splendidly; "
                  "I remain, as ever, at your service."),
    )


def _clean(text: str) -> str:
    return " ".join(str(text).split())


def write(issue: Issue, call=None) -> Words:
    """Ask the local model for this week's words; fall back to a template."""
    request = {
        "model": llm.model(),
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": facts(issue)},
        ],
        "stream": False,
        # Qwen 3.x reasons before answering unless told not to; on a CPU runner
        # that costs minutes for no visible gain in a letter.
        "think": False,
        "format": WORDS_SCHEMA,
        # Warm: the point is that the letter is different every week.
        "options": {"temperature": 0.9, "num_predict": 600},
    }
    try:
        data = json.loads((call or llm.chat)(request))
        words = Words(*(_clean(data[k]) for k in ("greeting", "letter", "farewell")))
        if not all((words.greeting, words.letter, words.farewell)):
            raise ValueError("an empty piece")
        words = _repair(words)
        if len(words.letter) > 500 or len(words.farewell) > 600 or len(words.greeting) > 200:
            raise ValueError("a piece ran far too long")
        return words
    except Exception as error:  # noqa: BLE001 - the newsletter goes out regardless
        print(f"AI-fred's words: using the template ({type(error).__name__}: {error})",
              file=sys.stderr)
        return fallback_words(issue)


def _sentences(text: str, limit: int) -> str:
    """The first `limit` sentences: the model reliably ignores word limits."""
    return " ".join(re.split(r"(?<=[.!?])\s+", text.strip())[:limit])


def _repair(words: Words) -> Words:
    """Fix the slips a small model makes, rather than throwing its work away."""
    words = Words(_sentences(words.greeting, 1), _sentences(words.letter, 2),
                  _sentences(words.farewell, 3))
    greeting = words.greeting
    # Normalise "{ name }", "{NAME}" etc., then make sure there is exactly one.
    greeting = re.sub(r"\{\s*name\s*\}", PLACEHOLDER, greeting, flags=re.I)
    if PLACEHOLDER not in greeting:
        greeting = f"Dear {PLACEHOLDER}, " + greeting[:1].lower() + greeting[1:]
    first = greeting.index(PLACEHOLDER) + len(PLACEHOLDER)
    greeting = greeting[:first] + greeting[first:].replace(PLACEHOLDER, "")
    letter = words.letter.replace(PLACEHOLDER, "")
    farewell = words.farewell.replace(PLACEHOLDER, "")
    if EMOJI not in greeting + letter + farewell:
        letter = f"{letter} {EMOJI}"
    return Words(greeting, letter, farewell)


# --- Subject ---------------------------------------------------------------


def subject(issue: Issue) -> str:
    """'🧐 AI-fred's weekly · Week 47'."""
    return f"{EMOJI} {AUTHOR}'s weekly · Week {issue.week}"


# --- Plain text ------------------------------------------------------------


def text(issue: Issue, name: str | None) -> str:
    w = issue.words
    out = [w.greet(name), "", w.letter]
    if issue.stock:
        s = issue.stock
        out += ["", f"== {s.name.upper()} ({sources.STOCK_LABEL}) =="]
        (first_day, first), (_, last) = s.month[0], s.month[-1]
        out.append(f"  Month: {first:.2f} on {first_day:%d/%m} -> {last:.2f} {s.currency} "
                   f"({100 * (last - first) / first:+.1f}%)")
        out.append("  Latest week:")
        out += [f"  {d:%a %d/%m}  {close:8.2f} {s.currency}" for d, close in s.week]
        out.append(f"  Week: {s.change:+.2f} ({s.change_pct:+.1f}%)")
    if issue.comic:
        c = issue.comic
        out += ["", "== XKCD ==", f"  {c.num}: {c.title}", f"  {c.url}", f"  \"{c.alt}\""]
    if issue.papers:
        out += ["", "== ML PAPERS OF THE WEEK =="]
        for p in issue.papers:
            out += [f"  * {p.title} ({p.upvotes} upvotes on {p.source})", f"    {p.summary}",
                    f"    {p.url}"]
        if issue.papers[0].list_url:
            out.append(f"  All of this week's papers: {issue.papers[0].list_url}")
    if issue.joke:
        out += ["", "== JOKE =="] + [f"  {line}" for line in issue.joke.setup.splitlines()]
        if issue.joke.delivery:
            out.append(f"  ... {issue.joke.delivery}")
    out += ["", "", w.farewell, "", f"— {SIGNATURE}"]
    return "\n".join(out) + "\n"


# --- HTML ------------------------------------------------------------------


def images(issue: Issue) -> list[tuple[str, bytes, str]]:
    """(content id, data, image subtype) for each image to embed in the mail."""
    c = issue.comic
    return [(COMIC_CID, c.image, c.image_subtype)] if c and c.image else []


def _heading(label: str) -> str:
    return (f'<div style="font-family:{SERIF};font-size:21px;font-style:italic;color:{INK};'
            f'padding-bottom:10px;">{escape(label)}</div>')


def _section(label: str, inner: str, background: str, rule: bool = False) -> str:
    # `rule`: a hairline on top, for when the section above has the same colour.
    border = f"border-top:1px solid {RULE};" if rule else ""
    return (f'<tr><td style="padding:22px 24px;background:{background};{border}">'
            f"{_heading(label)}{inner}</td></tr>")


CHART_HEIGHT = 110  # px of the tallest bar
PAST, BAND = "#d6d3dd", "#f4f3f6"  # earlier days' bars; the latest week's backdrop


def _stock_html(s: sources.Stock, band: str = BAND) -> str:
    """A month of closes as a column chart, with the latest week highlighted.

    Built from table cells and divs: Gmail renders neither SVG nor data-URI
    images, and an external chart image would be blocked by default.
    """
    color = UP if s.change >= 0 else DOWN
    arrow = "&#9650;" if s.change >= 0 else "&#9660;"
    closes = [c for _, c in s.month]
    low, high = min(closes), max(closes)
    cols, labels = [], []
    for i, (day, close) in enumerate(s.month):
        latest = i >= s.week_start
        # Bars start a little above zero so the lowest day is still visible.
        height = CHART_HEIGHT if high == low else round(
            14 + (CHART_HEIGHT - 14) * (close - low) / (high - low))
        tip = f"{day:%a %d/%m}: {close:.2f}"
        cols.append(
            f'<td valign="bottom" title="{tip}" style="padding:0 1px;height:{CHART_HEIGHT + 8}px;'
            f'{f"background:{band};" if latest else ""}">'
            f'<div style="height:{height}px;background:{color if latest else PAST};'
            f'border-radius:2px 2px 0 0;font-size:0;line-height:0;">&nbsp;</div></td>'
        )
        if latest:
            labels.append(
                f'<td style="background:{band};font-size:10px;color:{MUTED};text-align:center;'
                f'padding:4px 0 2px;">{day:%a}<br><span style="color:{INK};font-weight:700;">'
                f"{close:.0f}</span></td>"
            )
    past = s.week_start
    label_row = (
        f'<td colspan="{past}" style="font-size:11px;color:{MUTED};padding-top:4px;'
        f'vertical-align:top;">{s.month[0][0]:%d %b}</td>' if past else ""
    ) + "".join(labels)
    month_pct = 100 * (closes[-1] - closes[0]) / closes[0]
    return (
        f'<div style="font-size:20px;font-weight:700;color:{INK};">{closes[-1]:.2f} '
        f'<span style="font-size:13px;color:{MUTED};font-weight:400;">{escape(s.currency)}</span> '
        f'<span style="font-size:14px;color:{color};font-weight:700;">{arrow} '
        f"{s.change:+.2f} ({s.change_pct:+.1f}%)</span></div>"
        f'<div style="font-size:12px;color:{MUTED};padding:2px 0 12px;">over the latest week, '
        f"from {s.previous_close:.2f} &middot; {month_pct:+.1f}% over the month</div>"
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="table-layout:fixed;border-bottom:1px solid {RULE};">'
        f'<tr>{"".join(cols)}</tr></table>'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="table-layout:fixed;"><tr>{label_row}</tr></table>'
        f'<div style="font-size:11px;color:{MUTED};padding-top:6px;">'
        f'High {high:.2f} &middot; Low {low:.2f} &middot; '
        f'<span style="color:{color};font-weight:700;">&#9632;</span> latest week</div>'
    )


def _papers_html(papers: list[sources.Paper]) -> str:
    return "".join(
        f'<div style="padding:6px 0 10px;">'
        f'<a href="{escape(p.url)}" style="font-family:{SERIF};font-size:17px;font-weight:700;color:{INK};'
        f'text-decoration:none;">{escape(p.title)}</a>'
        f'<div style="font-size:13px;line-height:1.5;color:{BODY};padding-top:3px;">'
        f"{escape(p.summary)}</div>"
        f'<div style="font-size:12px;padding-top:3px;"><a href="{escape(p.list_url or p.url)}" '
        f'style="color:{MUTED};text-decoration:none;">&#9650; {p.upvotes} '
        f"upvotes on {escape(p.source)}</a></div></div>"
        for p in papers
    )


# Content-ID of the comic image attached to the mail; the HTML points at it.
COMIC_CID = "xkcd-comic"


def _comic_html(c: sources.Comic, embedded: bool) -> str:
    src = f"cid:{COMIC_CID}" if embedded and c.image else c.img
    return (
        f'<a href="{escape(c.url)}" style="font-family:{SERIF};font-size:17px;font-weight:700;color:{INK};'
        f'text-decoration:none;">{c.num}: {escape(c.title)}</a>'
        f'<div style="padding:10px 0;"><a href="{escape(c.url)}">'
        f'<img src="{escape(src)}" alt="{escape(c.alt)}" title="{escape(c.alt)}" '
        f'style="max-width:100%;height:auto;border:0;"></a></div>'
        f'<div style="font-size:13px;font-style:italic;color:{MUTED};">{escape(c.alt)}</div>'
    )


def _joke_html(j: sources.Joke) -> str:
    punch = (f'<div style="font-size:15px;font-weight:700;color:{INK};padding-top:6px;">'
             f"{escape(j.delivery)}</div>") if j.delivery else ""
    setup = "<br>".join(escape(line) for line in j.setup.splitlines())
    return f'<div style="font-size:15px;line-height:1.5;color:{BODY};">{setup}</div>{punch}'


def html(issue: Issue, name: str | None, embedded: bool = True) -> str:
    """The HTML mail. `embedded`: the comic is referenced as an attachment of
    the mail (see `images`); pass False for a file to open in a browser."""
    w = issue.words
    # (label, render, forced background). Rendered once the background is known.
    parts = []
    if issue.stock:
        # The chart's latest-week band must stand out from the section behind it.
        parts.append((f"{issue.stock.name} · {sources.STOCK_LABEL}",
                      lambda bg: _stock_html(issue.stock, band=SHADE if bg == WHITE else WHITE),
                      None))
    if issue.comic:
        # Always on white: the comic is a white image and looks pasted-on over grey.
        parts.append(("xkcd", lambda bg: _comic_html(issue.comic, embedded), WHITE))
    if issue.papers:
        parts.append(("ML papers of the week", lambda bg: _papers_html(issue.papers), None))
    if issue.joke:
        parts.append(("Joke of the week", lambda bg: _joke_html(issue.joke), None))

    # The intro is white, then each section flips colour - shaded, white,
    # shaded ... down to the farewell. With every section present the comic
    # lands on white anyway; it is only forced when the stock is missing.
    sections, previous = [], WHITE
    for label, render, forced in parts:
        bg = forced or (SHADE if previous == WHITE else WHITE)
        sections.append(_section(label, render(bg), bg, rule=bg == previous))
        previous = bg
    sections = "".join(sections)
    last = SHADE if previous == WHITE else WHITE

    return f"""\
<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#ebe9ef;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
       style="background:#ebe9ef;padding:24px 12px;">
<tr><td align="center">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600"
       style="max-width:600px;width:100%;background:{WHITE};border:1px solid #dedbe4;
              border-radius:10px;font-family:{FONT};border-collapse:separate;overflow:hidden;">

<tr><td style="padding:28px 24px 24px 24px;background:{WHITE};border-radius:10px 10px 0 0;">
  <div style="font-family:{SERIF};font-size:30px;color:{INK};line-height:1.1;">
    {AUTHOR}&rsquo;s <span style="font-style:italic;">Weekly</span></div>
  <div style="font-size:11px;font-weight:700;color:{MUTED};letter-spacing:.14em;
              text-transform:uppercase;padding-top:6px;">Week {issue.week}</div>
  <div style="font-family:{SERIF};font-size:20px;color:{INK};padding-top:22px;line-height:1.4;">
    {escape(w.greet(name))}</div>
  <div style="font-size:15px;line-height:1.6;color:{BODY};padding-top:8px;">
    {escape(w.letter)}</div>
</td></tr>

{sections}

<tr><td style="padding:24px 24px 28px 24px;background:{last};
               border-radius:0 0 10px 10px;">
  <div style="font-size:15px;line-height:1.6;color:{BODY};font-style:italic;">
    {escape(w.farewell)}</div>
  <div style="font-family:{SERIF};font-size:18px;color:{INK};font-style:italic;padding-top:14px;">
    &mdash; {escape(SIGNATURE)}</div>
</td></tr>

</table>
</td></tr></table>
</body></html>"""
