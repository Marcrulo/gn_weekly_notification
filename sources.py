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
from dataclasses import dataclass, field
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

STOCK_SYMBOL = "GN.CO"  # GN Store Nord on Nasdaq Copenhagen
STOCK_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d"


@dataclass
class Stock:
    name: str
    currency: str
    days: list[tuple[date, float]]  # the last five trading days, oldest first
    previous_close: float  # the close before the first of those days

    @property
    def change(self) -> float:
        return self.days[-1][1] - self.previous_close

    @property
    def change_pct(self) -> float:
        return 100 * self.change / self.previous_close


def stock(symbol: str = STOCK_SYMBOL, days: int = 5) -> Stock | None:
    try:
        result = _get_json(STOCK_URL.format(symbol=urllib.parse.quote(symbol)))["chart"]["result"][0]
        meta = result["meta"]
        closes = [
            (datetime.fromtimestamp(ts, timezone.utc).date(), close)
            for ts, close in zip(result["timestamp"], result["indicators"]["quote"][0]["close"])
            if close is not None
        ]
        if len(closes) < days + 1:
            raise LookupError(f"only {len(closes)} closes returned")
        return Stock(
            name=meta.get("shortName") or symbol,
            currency=meta.get("currency", ""),
            days=closes[-days:],
            previous_close=closes[-days - 1][1],
        )
    except Exception as error:  # noqa: BLE001
        _warn("Stock", error)
        return None


# --- Papers ----------------------------------------------------------------

PAPERS_URL = "https://huggingface.co/api/daily_papers?week={week}&limit=100"


@dataclass
class Paper:
    id: str  # arXiv id
    title: str
    summary: str
    upvotes: int
    url: str = field(init=False)

    def __post_init__(self):
        self.url = f"https://huggingface.co/papers/{self.id}"


def papers(seen: set, today: date | None = None, count: int = 3) -> list[Paper] | None:
    """The week's most upvoted papers on Hugging Face that were not sent before."""
    year, week, _ = (today or date.today()).isocalendar()
    try:
        items = _get_json(PAPERS_URL.format(week=f"{year}-W{week:02d}"))
        found = []
        for item in sorted(items, key=lambda i: -i["paper"].get("upvotes", 0)):
            paper = item["paper"]
            if paper["id"] in seen:
                continue
            # ai_summary is a one-liner; fall back to the abstract's first sentence.
            summary = paper.get("ai_summary") or re.split(r"(?<=\.)\s", paper.get("summary", ""))[0]
            found.append(Paper(paper["id"], _squash(paper["title"]), _squash(summary),
                               paper.get("upvotes", 0)))
            if len(found) == count:
                break
        if not found:
            raise LookupError("no unseen papers this week")
        return found
    except Exception as error:  # noqa: BLE001
        _warn("Papers", error)
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

    @property
    def url(self) -> str:
        return f"https://xkcd.com/{self.num}/"

    @property
    def explain_url(self) -> str:
        return f"https://www.explainxkcd.com/wiki/index.php/{self.num}"


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
        return Comic(num, data["safe_title"], data["img"], data["alt"])
    except Exception as error:  # noqa: BLE001
        _warn("xkcd", error)
        return None
