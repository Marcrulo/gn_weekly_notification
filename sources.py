"""The newsletter's extra sections: a joke, GN's share price, ML papers, an xkcd.

Every fetcher takes the set of ids already sent (from history.json) and skips
those, so nothing repeats. Each returns None rather than raising when its
source is down: the newsletter then simply leaves that section out.
"""

from __future__ import annotations

import json
import random
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

TIMEOUT = 30
USER_AGENT = "Mozilla/5.0 (weekly-newsletter)"  # Yahoo rejects Python's default

HISTORY_FILE = Path(__file__).resolve().parent / "history.json"
HISTORY_KEYS = ("jokes", "papers", "xkcd")


def _get_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


def _get_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def _warn(section: str, error: Exception) -> None:
    print(f"{section}: left out ({error})", file=sys.stderr)


# --- History ---------------------------------------------------------------


def load_history(path: Path = HISTORY_FILE) -> dict[str, list]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    return {key: list(data.get(key, [])) for key in HISTORY_KEYS}


def save_history(history: dict[str, list], path: Path = HISTORY_FILE) -> None:
    Path(path).write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")


# --- Joke ------------------------------------------------------------------

JOKE_URL = (
    "https://v2.jokeapi.dev/joke/Programming?safe-mode&amount=10"
    "&blacklistFlags=nsfw,religious,political,racist,sexist,explicit"
)


@dataclass
class Joke:
    id: int
    setup: str
    delivery: str = ""  # empty for one-liners


def joke(seen: set, url: str = JOKE_URL, attempts: int = 5) -> Joke | None:
    """A safe-mode programming joke whose id is not in `seen`."""
    try:
        for _ in range(attempts):  # each call returns 10 random jokes
            data = _get_json(url)
            for item in data.get("jokes", [data]):
                if item.get("error") or not item.get("safe") or item["id"] in seen:
                    continue
                if item["type"] == "single":
                    return Joke(item["id"], item["joke"])
                return Joke(item["id"], item["setup"], item["delivery"])
        raise LookupError("no unseen joke found")
    except Exception as error:  # noqa: BLE001 - a section must never sink the mail
        _warn("Joke", error)
        return None


# --- Stock -----------------------------------------------------------------

STOCK_SYMBOL = "GN.CO"  # GN Store Nord on Nasdaq Copenhagen (Yahoo's ticker)
# How the stock is named in the mail. Not "GN.CO": mail clients turn that into
# a link to the gn.co website.
STOCK_LABEL = "Nasdaq Copenhagen: GN"
STOCK_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d"


@dataclass
class Stock:
    name: str
    currency: str
    month: list[tuple[date, float]]  # every close in the last month, oldest first
    week_start: int  # index into `month` of the latest week's first trading day

    @property
    def week(self) -> list[tuple[date, float]]:
        """The trading days of the latest week (the one ending this Sunday)."""
        return self.month[self.week_start:]

    @property
    def previous_close(self) -> float:
        """The close just before the latest week."""
        return self.month[self.week_start - 1][1]

    @property
    def change(self) -> float:
        return self.month[-1][1] - self.previous_close

    @property
    def change_pct(self) -> float:
        return 100 * self.change / self.previous_close


def stock(symbol: str = STOCK_SYMBOL) -> Stock | None:
    try:
        result = _get_json(STOCK_URL.format(symbol=urllib.parse.quote(symbol)))["chart"]["result"][0]
        meta = result["meta"]
        closes = [
            (datetime.fromtimestamp(ts, timezone.utc).date(), close)
            for ts, close in zip(result["timestamp"], result["indicators"]["quote"][0]["close"])
            if close is not None
        ]
        if len(closes) < 2:
            raise LookupError(f"only {len(closes)} closes returned")
        # The latest week = trading days in the same ISO week as the last close.
        last_week = closes[-1][0].isocalendar()[:2]
        start = next(i for i, (d, _) in enumerate(closes) if d.isocalendar()[:2] == last_week)
        if start == 0:
            raise LookupError("no close before the latest week to compare with")
        return Stock(
            name=meta.get("shortName") or symbol,
            currency=meta.get("currency", ""),
            month=closes,
            week_start=start,
        )
    except Exception as error:  # noqa: BLE001
        _warn("Stock", error)
        return None


# --- Papers ----------------------------------------------------------------

# Hugging Face is the main source: a documented public API, curated by humans
# and ranked by upvotes. alphaXiv's "hot this week" feed is the fallback for
# when Hugging Face is down; it is undocumented, so it may change without notice.
# (Papers with Code, the other obvious source, was shut down in July 2025.)
PAPERS_URL = "https://huggingface.co/api/daily_papers?week={week}&limit=100"
ALPHAXIV_URL = "https://api.alphaxiv.org/papers/v3/feed?pageNum=0&sort=Hot&pageSize=30&interval=7%20Days"
# The human-readable pages the "upvotes" line links to.
HF_WEEK_PAGE = "https://huggingface.co/papers/week/{week}"
ALPHAXIV_WEEK_PAGE = "https://www.alphaxiv.org/?sort=Hot&interval=7+Days"
ARXIV_ID = re.compile(r"^\d{4}\.\d{4,5}$")


@dataclass
class Paper:
    id: str  # arXiv id on Hugging Face, alphaXiv's paper id otherwise
    title: str
    summary: str
    upvotes: int
    url: str = ""  # the article itself: arXiv when the id is an arXiv id
    list_url: str = ""  # the source's page of this week's papers
    source: str = "Hugging Face"

    def __post_init__(self):
        self.url = self.url or f"https://arxiv.org/abs/{self.id}"


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=\.)\s", text or "")[0]


def _hf_papers(seen: set, today: date) -> list[Paper]:
    year, week, _ = today.isocalendar()
    week = f"{year}-W{week:02d}"
    items = _get_json(PAPERS_URL.format(week=week))
    found = []
    for item in sorted(items, key=lambda i: -i["paper"].get("upvotes", 0)):
        paper = item["paper"]
        if paper["id"] in seen:
            continue
        # ai_summary is a one-liner; fall back to the abstract's first sentence.
        summary = paper.get("ai_summary") or _first_sentence(paper.get("summary", ""))
        found.append(Paper(paper["id"], _squash(paper["title"]), _squash(summary),
                           paper.get("upvotes", 0), list_url=HF_WEEK_PAGE.format(week=week)))
    return found


def _alphaxiv_papers(seen: set) -> list[Paper]:
    found = []
    for paper in _get_json(ALPHAXIV_URL)["papers"]:
        pid = paper.get("universal_paper_id") or paper["id"]
        if pid in seen:
            continue
        summary = paper.get("feed_description") or _first_sentence(paper.get("abstract", ""))
        votes = (paper.get("metrics") or {}).get("public_total_votes") or 0
        # Most alphaXiv papers carry their arXiv id; the rest only exist there.
        url = "" if ARXIV_ID.match(pid) else f"https://www.alphaxiv.org/abs/{pid}"
        found.append(Paper(pid, _squash(paper["title"]), _squash(summary), votes, url=url,
                           list_url=ALPHAXIV_WEEK_PAGE, source="alphaXiv"))
    return found


def papers(seen: set, today: date | None = None, count: int = 3) -> list[Paper] | None:
    """The week's top papers not sent before: Hugging Face, else alphaXiv."""
    for name, fetch in (("Hugging Face", lambda: _hf_papers(seen, today or date.today())),
                        ("alphaXiv", lambda: _alphaxiv_papers(seen))):
        try:
            found = fetch()[:count]
            if not found:
                raise LookupError("no unseen papers this week")
            return found
        except Exception as error:  # noqa: BLE001
            _warn(f"Papers from {name}", error)
    return None


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# --- xkcd ------------------------------------------------------------------

XKCD_CATEGORIES = ("Artificial_Intelligence", "Computers")
CATEGORY_URL = (
    "https://www.explainxkcd.com/wiki/api.php?action=query&list=categorymembers"
    "&cmtitle=Category:{name}&cmnamespace=0&cmlimit=500&format=json"
)


@dataclass
class Comic:
    num: int
    title: str
    img: str
    alt: str
    # The image itself, embedded in the mail: many clients block or fail to
    # load images from other servers. Empty if the download failed, in which
    # case the mail falls back to linking `img`.
    image: bytes = b""

    @property
    def url(self) -> str:
        return f"https://xkcd.com/{self.num}/"

    @property
    def image_subtype(self) -> str:
        """'png', 'jpeg' or 'gif', from the image's file name."""
        ext = self.img.rsplit(".", 1)[-1].lower()
        return "jpeg" if ext in ("jpg", "jpeg") else ext if ext in ("png", "gif") else "png"


def comic_numbers(categories=XKCD_CATEGORIES) -> set[int]:
    """Comic numbers in the explainxkcd categories. Pages titled '329: Turing
    Test' are comics; the rest ('unixkcd', 'Five-Minute Comics') are not."""
    numbers = set()
    for name in categories:
        url = CATEGORY_URL.format(name=name)
        cont = ""
        while True:
            data = _get_json(url + cont)
            for member in data["query"]["categorymembers"]:
                match = re.match(r"(\d+):", member["title"])
                if match:
                    numbers.add(int(match.group(1)))
            if "continue" not in data:
                break
            cont = "&cmcontinue=" + urllib.parse.quote(data["continue"]["cmcontinue"])
    return numbers


def comic(seen: set, rng: random.Random | None = None) -> Comic | None:
    try:
        candidates = sorted(comic_numbers() - set(seen))
        if not candidates:
            raise LookupError("every comic in the categories has been sent")
        num = (rng or random).choice(candidates)
        data = _get_json(f"https://xkcd.com/{num}/info.0.json")
        comic = Comic(num, data["safe_title"], data["img"], data["alt"])
        try:
            comic.image = _get_bytes(comic.img)
        except Exception as error:  # noqa: BLE001 - a linked image is still better than none
            _warn("xkcd image (linking it instead)", error)
        return comic
    except Exception as error:  # noqa: BLE001
        _warn("xkcd", error)
        return None
