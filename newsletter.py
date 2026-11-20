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
from datetime import date
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
INK, BODY, MUTED, RULE, ACCENT = "#1b1c1a", "#3d413a", "#8a8f85", "#ecefe8", "#5c4a7a"
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
        return self.when.isocalendar()[1]


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
You are {AUTHOR}, the butler who writes a weekly newsletter for a small team of
engineers at GN, a Danish hearing-aid and headset company.

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
- letter: three or four sentences, under 90 words, presenting this week's
  issue as a butler would present a meal. Give GN's share movement with its
  real numbers, and mention one paper or the comic by name. Do not reveal the
  joke, only hint that one awaits.
- farewell: two or three sentences, under 60 words: a gracious, slightly
  overwrought goodbye until next week. Do not sign it; the signature is added.

Use the emoji {EMOJI} at least once across the three. No other emoji, no
markdown, no lists. Use only the facts given; never invent numbers."""

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
    lines = [f"Date: {issue.when:%A %d %B %Y} (week {issue.week})",
             f"Your mood this week: {mood or random.choice(MOODS)}"]
    if issue.stock:
        s = issue.stock
        direction = "rose" if s.change >= 0 else "fell"
        lines.append(f"GN share: {direction} {abs(s.change_pct):.1f}% over the week, "
                     f"closing at {s.days[-1][1]:.2f} {s.currency}")
    if issue.papers:
        lines.append("ML papers of the week: " + "; ".join(p.title for p in issue.papers))
    if issue.comic:
        lines.append(f"xkcd comic: \"{issue.comic.title}\"")
    if issue.joke:
        lines.append("There is also a programming joke at the end.")
    return "\n".join(lines)


FALLBACK_GREETINGS = [
    f"Good day to you, {PLACEHOLDER}, and a most humble welcome.",
    f"Ah, {PLACEHOLDER}! Do come in; your newsletter is served.",
    f"My deepest bow to you, {PLACEHOLDER}.",
]


def fallback_words(issue: Issue) -> Words:
    """Used when the model is unavailable - stiff, but never wrong."""
    parts = ["I took the liberty of preparing this week's dispatch myself."]
    if issue.stock:
        verb = "rose" if issue.stock.change >= 0 else "fell"
        parts.append(f"GN's share {verb} {abs(issue.stock.change_pct):.1f}% this week, "
                     f"if I may be so bold as to mention it {EMOJI}.")
    parts.append("Papers, a comic and a modest jest await you below.")
    return Words(
        greeting=random.choice(FALLBACK_GREETINGS),
        letter=" ".join(parts),
        farewell="That is all for this week. I remain, as ever, at your service.",
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
        if len(words.letter) > 900 or len(words.farewell) > 600 or len(words.greeting) > 200:
            raise ValueError("a piece ran far too long")
        return _repair(words)
    except Exception as error:  # noqa: BLE001 - the newsletter goes out regardless
        print(f"AI-fred's words: using the template ({type(error).__name__}: {error})",
              file=sys.stderr)
        return fallback_words(issue)


def _repair(words: Words) -> Words:
    """Fix the slips a small model makes, rather than throwing its work away."""
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
        out += ["", f"== {s.name.upper()} ({sources.STOCK_SYMBOL}) =="]
        out += [f"  {d:%a %d/%m}  {close:8.2f} {s.currency}" for d, close in s.days]
        out.append(f"  Week: {s.change:+.2f} ({s.change_pct:+.1f}%)")
    if issue.papers:
        out += ["", "== ML PAPERS OF THE WEEK =="]
        for p in issue.papers:
            out += [f"  * {p.title} ({p.upvotes} upvotes)", f"    {p.summary}", f"    {p.url}"]
    if issue.comic:
        c = issue.comic
        out += ["", "== XKCD ==", f"  {c.num}: {c.title}", f"  {c.url}", f"  \"{c.alt}\""]
    if issue.joke:
        out += ["", "== JOKE =="] + [f"  {line}" for line in issue.joke.setup.splitlines()]
        if issue.joke.delivery:
            out.append(f"  ... {issue.joke.delivery}")
    out += ["", "", w.farewell, "", f"— {SIGNATURE}"]
    return "\n".join(out) + "\n"


# --- HTML ------------------------------------------------------------------


def _heading(label: str) -> str:
    return (f'<div style="font-size:12px;font-weight:700;color:{MUTED};letter-spacing:.08em;'
            f'text-transform:uppercase;padding-bottom:8px;">{escape(label)}</div>')


def _section(label: str, inner: str) -> str:
    return (f'<tr><td style="padding:20px 24px;border-top:1px solid {RULE};">'
            f"{_heading(label)}{inner}</td></tr>")


def _stock_html(s: sources.Stock) -> str:
    color = UP if s.change >= 0 else DOWN
    arrow = "&#9650;" if s.change >= 0 else "&#9660;"
    closes = [c for _, c in s.days]
    low, high = min(closes), max(closes)
    rows = []
    for day, close in s.days:
        # Bars are divs, not images or SVG: Gmail renders neither inline.
        width = 100 if high == low else round(25 + 75 * (close - low) / (high - low))
        rows.append(
            f'<tr><td style="font-size:13px;color:{MUTED};padding:3px 10px 3px 0;white-space:nowrap;">'
            f"{day:%a %d/%m}</td>"
            f'<td width="100%" style="padding:3px 0;"><div style="background:{color};opacity:.75;'
            f'height:10px;width:{width}%;border-radius:2px;"></div></td>'
            f'<td style="font-size:13px;color:{INK};padding:3px 0 3px 10px;text-align:right;'
            f'white-space:nowrap;">{close:.2f}</td></tr>'
        )
    return (
        f'<div style="font-size:20px;font-weight:700;color:{INK};">{closes[-1]:.2f} '
        f'<span style="font-size:13px;color:{MUTED};font-weight:400;">{escape(s.currency)}</span> '
        f'<span style="font-size:14px;color:{color};font-weight:700;">{arrow} '
        f"{s.change:+.2f} ({s.change_pct:+.1f}%)</span></div>"
        f'<div style="font-size:12px;color:{MUTED};padding:2px 0 10px;">over the week, '
        f"from {s.previous_close:.2f}</div>"
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">'
        f'{"".join(rows)}</table>'
    )


def _papers_html(papers: list[sources.Paper]) -> str:
    return "".join(
        f'<div style="padding:6px 0 10px;">'
        f'<a href="{escape(p.url)}" style="font-size:15px;font-weight:700;color:{INK};'
        f'text-decoration:none;">{escape(p.title)}</a>'
        f'<div style="font-size:13px;line-height:1.5;color:{BODY};padding-top:3px;">'
        f"{escape(p.summary)}</div>"
        f'<div style="font-size:12px;color:{MUTED};padding-top:3px;">&#9650; {p.upvotes} '
        f"upvotes on Hugging Face</div></div>"
        for p in papers
    )


def _comic_html(c: sources.Comic) -> str:
    return (
        f'<a href="{escape(c.url)}" style="font-size:15px;font-weight:700;color:{INK};'
        f'text-decoration:none;">{c.num}: {escape(c.title)}</a>'
        f'<div style="padding:10px 0;"><a href="{escape(c.url)}">'
        f'<img src="{escape(c.img)}" alt="{escape(c.alt)}" title="{escape(c.alt)}" '
        f'style="max-width:100%;height:auto;border:0;"></a></div>'
        f'<div style="font-size:13px;font-style:italic;color:{MUTED};">{escape(c.alt)}</div>'
        f'<a href="{escape(c.explain_url)}" style="font-size:12px;color:{ACCENT};'
        f'text-decoration:none;">Explain xkcd &rarr;</a>'
    )


def _joke_html(j: sources.Joke) -> str:
    punch = (f'<div style="font-size:15px;font-weight:700;color:{INK};padding-top:6px;">'
             f"{escape(j.delivery)}</div>") if j.delivery else ""
    setup = "<br>".join(escape(line) for line in j.setup.splitlines())
    return f'<div style="font-size:15px;line-height:1.5;color:{BODY};">{setup}</div>{punch}'


def html(issue: Issue, name: str | None) -> str:
    w = issue.words
    sections = []
    if issue.stock:
        sections.append(_section(f"{issue.stock.name} · {sources.STOCK_SYMBOL}", _stock_html(issue.stock)))
    if issue.papers:
        sections.append(_section("ML papers of the week", _papers_html(issue.papers)))
    if issue.comic:
        sections.append(_section("xkcd", _comic_html(issue.comic)))
    if issue.joke:
        sections.append(_section("Joke of the week", _joke_html(issue.joke)))

    return f"""\
<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f4f3f6;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
       style="background:#f4f3f6;padding:24px 12px;">
<tr><td align="center">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600"
       style="max-width:600px;width:100%;background:#ffffff;border:1px solid #e3e1e8;
              border-radius:10px;font-family:{FONT};">

<tr><td style="padding:24px 24px 20px 24px;">
  <div style="font-size:12px;font-weight:700;color:{MUTED};letter-spacing:.08em;
              text-transform:uppercase;">{AUTHOR}'s weekly · Week {issue.week}</div>
  <div style="font-size:20px;font-weight:700;color:{INK};padding-top:14px;line-height:1.35;">
    {escape(w.greet(name))}</div>
  <div style="font-size:15px;line-height:1.6;color:{BODY};padding-top:10px;">
    {escape(w.letter)}</div>
</td></tr>

{"".join(sections)}

<tr><td style="padding:22px 24px 26px 24px;border-top:1px solid {RULE};">
  <div style="font-size:15px;line-height:1.6;color:{BODY};font-style:italic;">
    {escape(w.farewell)}</div>
  <div style="font-size:15px;color:{INK};font-weight:700;padding-top:12px;">
    &mdash; {escape(SIGNATURE)}</div>
</td></tr>

</table>
</td></tr></table>
</body></html>"""
