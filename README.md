# AI-fred's weekly

A weekly newsletter, mailed every Sunday by **AI-fred** 🧐 — an absurdly
polite butler of a robot — to everyone in `recipients.txt`. It goes out on
Sunday evening and opens the coming week (the subject names that week). Each
issue holds:

1. **A greeting and a short, generic intro from AI-fred**, addressed to each reader by name
2. **GN Store Nord (CPH: GN)** — a chart of the last month, latest week highlighted
3. **ML papers of the week** — the three most upvoted on Hugging Face (alphaXiv if it is down)
4. **An xkcd** — from the *Artificial Intelligence* or *Computers* topic
5. **A programming joke** — safe-mode, from JokeAPI
6. **AI-fred's farewell** into the new week, signed

AI-fred's words are written from scratch every week by a language model that
runs on the GitHub Actions runner itself. The joke, papers and comic never
repeat: everything sent is remembered in `history.json`.

Python 3.9+, standard library only — nothing to install. No API keys either.

## Quick start

```bash
python notify.py --dry-run                     # print the plain-text issue, send nothing
python notify.py --html-out preview.html       # the HTML mail, open it in a browser
python notify.py --to you@example.com          # real send to one address only
python -m unittest discover -s tests           # offline: no network, no model
```

Without Ollama running, AI-fred falls back to his template words (see below),
so the three commands above work anywhere. To hear him properly, run the model
locally — any Ollama model works for a preview:

```bash
ollama pull MobiusDevelopment/Bonsai-27B-Q1_0-gguf   # what the workflow uses
OLLAMA_MODEL=qwen3:8b python notify.py --dry-run   # or whatever you have pulled
```

## Files

| File | Purpose |
| --- | --- |
| `notify.py` | CLI: build the issue, send one personal mail per recipient over SMTP |
| `newsletter.py` | AI-fred's prompt and character, the HTML and plain-text layout — **edit wording and looks here** |
| `sources.py` | Fetchers for the joke, stock, papers and xkcd; reads and writes `history.json` |
| `llm.py` | The call to the local model (Ollama) |
| `recipients.txt` | The mailing list, with the name AI-fred calls each person — **edit this to add people** |
| `history.json` | Joke ids, paper ids and xkcd numbers already sent — committed back by the workflow |
| `tests/` | Offline unit tests |
| `.github/workflows/ai-fred-newsletter.yml` | Sunday 23:05 Danish time |
| `.github/workflows/test-send.yml` | Manual only: a real send to marcus.presutti.eu@gmail.com alone, history untouched |

## The mailing list

One person per line: the name AI-fred should use, then the address.

```
Dr. Bott abott@gnhearing.com
Master Presutti <mpresutti@gnhearing.com>
newcomer@gnhearing.com
```

The name is used **exactly as written**, title included — AI-fred never adds
one of his own, so write it how you want to be addressed. A bare address is
greeted as "Esteemed Reader". `#` starts a comment; blank lines and repeated
addresses are ignored.

Every recipient gets **their own mail**, addressed to them alone (the greeting
is personal, so a shared Bcc mail would not work). Recipients never see each
other.

## AI-fred

### His character

An over-the-top, old-fashioned English butler: absurdly polite, gentle and
deferential, prone to apologising for things that are not his fault, and
fond of presenting a share price as though it were a silver tray. He uses 🧐 at
least once per issue. All of this lives in `SYSTEM` in `newsletter.py`.

### How the words are made

One model call per week, not one per recipient. The model returns three
pieces as JSON — `greeting`, `letter`, `farewell` — constrained by a JSON
schema, so it cannot omit one. The greeting contains the token `{name}`, which
is swapped for each reader's name when the mail is rendered. So eight
recipients cost the same as one.

The model is given only facts: the date, the share's real movement, the paper
titles and the comic's title — never the joke's punchline, so he cannot spoil
it. It is also given a **mood**, picked at random from `MOODS` (the soufflé
collapsed; the cat has been demanding), and sampled at `temperature: 0.9`, so
each week's letter comes out genuinely different.

Small models slip, so the reply is repaired rather than discarded:

- `{ NAME }` and friends are normalised to `{name}`; a greeting without one
  gets `Dear {name},` in front; a second `{name}` is dropped.
- If 🧐 appears nowhere, it is appended to the letter.
- If the call fails outright — Ollama not running, timeout, unparseable or
  absurdly long reply — AI-fred uses template words (`fallback_words`), prints
  a warning to stderr, and the issue still goes out.

### The model

PrismML's 1-bit **Bonsai 27B**
([`MobiusDevelopment/Bonsai-27B-Q1_0-gguf`](https://ollama.com/MobiusDevelopment/Bonsai-27B-Q1_0-gguf),
a community upload, ~4.4 GB, needs Ollama ≥ 0.32.5) via
[Ollama](https://ollama.com), on the runner, over `localhost:11434`. At 1 bit a
27B model fits the 16 GB runner. No key, no account, no per-call cost, nothing leaves the
runner. Set `OLLAMA_MODEL` in the workflow to swap it; `OLLAMA_HOST` points at
a different server. Requests send `"think": false` — the Qwen-based model
otherwise reasons first, which costs minutes on a CPU for no visible gain.

The model ignores word limits, so `_repair` keeps the first two sentences of
the intro, one of the greeting and three of the farewell rather than discarding
the reply. The model is told nothing about the issue's contents, so the intro
stays generic and cannot misquote a number.

Expect a few minutes on the 4-core runner (it has not been timed there yet). The client timeout is 15 minutes.

## The sections

Each fetcher returns nothing rather than raising when its source is down; that
section is then simply left out and the rest of the issue goes out as normal.
Warnings go to stderr, so check the job log if a section is missing.

| Section | Source | Never repeats by |
| --- | --- | --- |
| Joke | [JokeAPI](https://v2.jokeapi.dev/) `Programming`, `safe-mode` plus every flag blacklisted | joke `id` |
| Stock | Yahoo Finance chart endpoint for `GN.CO` (no key; unofficial, so it can change). Shown in the mail as "Nasdaq Copenhagen: GN" — written as `GN.CO`, mail clients link it to the gn.co website | — |
| Papers | Hugging Face `api/daily_papers?week=YYYY-Www`, top 3 by upvotes; fallback alphaXiv `papers/v3/feed` (Hot, 7 days; undocumented) | paper id |
| xkcd | Image downloaded and embedded in the mail (clients often block remote images). [explainxkcd](https://www.explainxkcd.com/wiki/index.php/Category:Comics_by_topic) categories → `xkcd.com/N/info.0.json` for the image | comic number |

- **Stock:** every close of the last month as a column chart; the latest week
  (the trading days in the same ISO week as the last close) is coloured green
  or red on a tinted band, with its closes labelled. The headline change is
  that week's, from the close before it. The bars are plain table cells,
  because Gmail renders neither SVG nor inline charts.
- **Papers:** the ISO week of the run (the job runs on Sunday, so the week just
  ending). If every top paper has been sent, it moves down the list.
- **xkcd:** a random pick from the ~230 comics in *Artificial Intelligence* and
  *Computers*. Category pages that are not single comics (`unixkcd`) are
  skipped. When all have been sent, the section disappears — a few years away.

### history.json

```json
{ "jokes": [48], "papers": ["2611.22115"], "xkcd": [1263] }
```

Updated **only after a send to the whole list**. Dry runs, `--html-out` and
`--to` test sends never touch it, so testing does not burn next Sunday's
material. The workflow commits it back to the repo after each send (that is
why the job has `contents: write`). To allow something to be sent again,
delete it from the file.

## GitHub setup

| What | Where |
| --- | --- |
| SMTP server, port, account, From | `SMTP_HOST` … `MAIL_FROM` at the top of `notify.py` |
| Mailing list | `recipients.txt` |
| Who may be mailed at all | Only addresses in `recipients.txt` — `--to` included; anything else is refused |
| **App password** | GitHub secret `SMTP_PASSWORD` — the only secret |
| AI-fred's model | No secret: it runs on the runner |

Add the secret under Settings → Secrets and variables → Actions, named
`SMTP_PASSWORD`. For Gmail that is a 16-character
[App Password](https://myaccount.google.com/apppasswords), which requires
2-Step Verification; the normal login password is rejected. Spaces are
stripped before use. Every setting also accepts an environment variable of the
same name as an override.

### The workflow

It runs **Sunday at 23:05 Danish time**, all year. GitHub's cron is UTC only,
so two schedules are set — `5 21 * * 0` (23:05 in summer, CEST) and
`5 22 * * 0` (23:05 in winter, CET) — and a small `timing` job lets through
only the one that matches Copenhagen's offset that day; the other run ends
there as "skipped". It decides by which schedule fired, not by the clock, so a
run GitHub starts late is still handled right. Five past rather than on the
hour because GitHub can *drop* scheduled jobs at peak times, and the top of
every hour is peak. Expect some delay regardless.

Steps: run the tests → install Ollama → restore the model from the Actions
cache (or pull it) → **build and send** → commit `history.json`. Building and
sending is a single step, and the plain-text issue is printed to the log, so
the log shows exactly what was mailed.

The two workflows run the same steps:

| Workflow | When | Sends to | `history.json` |
| --- | --- | --- | --- |
| `ai-fred-newsletter.yml` | Sunday 23:05, or **Run workflow** | everyone in `recipients.txt` | updated and committed |
| `test-send.yml` | **Run workflow** only | marcus.presutti.eu@gmail.com | untouched |

Running the full one by hand is a real send to everyone (for a missed Sunday,
say). To try a change, run the test one.

Notes on the Ollama steps:

- The installer's systemd service is stopped on purpose: it runs as the
  `ollama` user and keeps weights in `/usr/share/ollama`, which would hold the
  port and bypass the cache. The workflow runs its own server as the runner
  user with `OLLAMA_MODELS` pointed at the cached directory.
- GitHub evicts caches after 7 days without a hit and this job runs every 7
  days, so expect the occasional miss — it just re-downloads.
- If the weights never arrive, the job fails loudly rather than quietly
  mailing template words.

## Adjusting things

- **AI-fred's voice** — `SYSTEM` and `MOODS` in `newsletter.py`. Preview a few
  letters with `--dry-run`; each run is different.
- **His template words** (used when the model is down) — `fallback_words` and
  `FALLBACK_GREETINGS`.
- **Signature, emoji, subject** — `SIGNATURE`, `EMOJI`, `subject()`.
- **Number of papers** — `count` in `sources.papers`.
- **xkcd topics** — `XKCD_CATEGORIES` in `sources.py`; use the category's page
  name with underscores.
- **Another stock** — `STOCK_SYMBOL` in `sources.py` (Yahoo ticker, e.g. `NOVO-B.CO`).
- **Colours and layout** — the constants and `html()` in `newsletter.py`.
  Styles are inline (mail clients drop `<style>`), and the layout is nested
  tables because that is what survives Outlook. Everything from outside is
  HTML-escaped.
