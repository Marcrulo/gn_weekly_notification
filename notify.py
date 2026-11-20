"""Build AI-fred's weekly newsletter and mail it to everyone in recipients.txt.

Preview (sends nothing):
    python notify.py --dry-run
    python notify.py --html-out preview.html

Send a test to yourself only (does not touch history.json):
    python notify.py --to you@example.com

Send to the list (SMTP_PASSWORD is the only thing not in this repo):
    python notify.py
"""

from __future__ import annotations

import argparse
import getpass
import os
import smtplib
import sys
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from pathlib import Path

import newsletter
import sources

# --- Configuration ---------------------------------------------------------
# Everything here is safe to keep in the repo. The password is not: it comes
# from the SMTP_PASSWORD environment variable (a GitHub Actions secret).
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587  # 587 = STARTTLS, 465 = implicit TLS
SMTP_USER = "mpautomation123@gmail.com"
MAIL_FROM = SMTP_USER  # Gmail rewrites From to the authenticated account anyway

# One person per line; '#' starts a comment. Edit this file to change the list.
RECIPIENTS_FILE = Path(__file__).resolve().parent / "recipients.txt"
# ---------------------------------------------------------------------------


def _parse_contact(entry: str) -> tuple[str, str]:
    """'Dr. Bott <a@x.dk>', 'Dr. Bott a@x.dk' or 'a@x.dk' -> (name, address)."""
    if "<" in entry:
        name, address = parseaddr(entry)
        return name.strip(), address.strip()
    words = entry.split()
    at = [i for i, word in enumerate(words) if "@" in word]
    if not at:
        return "", ""
    i = at[-1]
    return " ".join(words[:i] + words[i + 1:]).strip(), words[i]


def contacts(path: Path | str = RECIPIENTS_FILE) -> list[tuple[str, str]]:
    """Read the mailing list as (name, address) pairs.

    The name is how AI-fred addresses that person, title and all. Blank lines,
    # comments and repeated addresses are ignored.
    """
    found: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        name, address = _parse_contact(entry)
        if address and address not in found:
            found[address] = name
    return [(name, address) for address, name in found.items()]


def recipients(path: Path | str = RECIPIENTS_FILE) -> list[str]:
    """Just the addresses from the mailing list."""
    return [address for _, address in contacts(path)]


def _password(user: str) -> str:
    # Gmail displays app passwords in groups of four ("abcd efgh ijkl mnop");
    # the spaces are presentation only and must not be sent.
    password = "".join(os.environ.get("SMTP_PASSWORD", "").split())
    if user and not password:
        if not sys.stdin.isatty():
            raise SystemExit("SMTP_PASSWORD is not set (add it as a GitHub Actions secret).")
        password = "".join(getpass.getpass(f"SMTP password for {user}: ").split())
    return password


def send_each(mails: list[tuple[str, str, str, str, str]]) -> None:
    """Send personal mails over one SMTP session.

    Each item is (name, address, subject, text, html). Every recipient gets
    their own mail addressed to them alone, so nobody sees anyone else.
    """
    host = os.environ.get("SMTP_HOST", SMTP_HOST)
    port = int(os.environ.get("SMTP_PORT", SMTP_PORT))
    user = os.environ.get("SMTP_USER", SMTP_USER)
    sender = os.environ.get("MAIL_FROM", MAIL_FROM) or user
    password = _password(user)

    server = smtplib.SMTP_SSL(host, port, timeout=30) if port == 465 else smtplib.SMTP(host, port, timeout=30)
    with server:
        if port != 465:
            server.starttls()
        if user:
            server.login(user, password)
        for name, address, subject, text, html in mails:
            mail = EmailMessage()
            mail["Subject"] = subject
            mail["From"] = formataddr((newsletter.AUTHOR, sender))
            mail["To"] = formataddr((name, address)) if name else address
            # multipart/alternative: clients that cannot render HTML show `text`.
            mail.set_content(text)
            mail.add_alternative(html, subtype="html")
            server.send_message(mail)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print the plain-text mail instead of sending")
    parser.add_argument("--html-out", metavar="PATH", help="write the HTML mail to a file to preview it")
    parser.add_argument(
        "--to",
        action="append",
        help="send to this address instead of recipients.txt (repeatable; for testing). "
        "Does not record anything in history.json.",
    )
    args = parser.parse_args(argv)

    # --to is for testing: those addresses only, matched to a name from
    # recipients.txt where one is listed there.
    names = {address: name for name, address in contacts()}
    to = [(names.get(a, ""), a) for a in args.to] if args.to else contacts()

    history = sources.load_history()
    issue = newsletter.gather(history)
    subject = newsletter.subject(issue)

    preview_name = to[0][0] if to else ""
    if args.html_out:
        Path(args.html_out).write_text(newsletter.html(issue, preview_name), encoding="utf-8")
        print(f"Wrote {args.html_out} - open it in a browser to preview.")
        return 0

    # Printed on a real send too, so the job log shows exactly what went out.
    print(f"Subject: {subject}\n\n{newsletter.text(issue, preview_name)}")
    if args.dry_run:
        return 0

    if not to:
        print(f"{RECIPIENTS_FILE.name} has no addresses - nothing to send.", file=sys.stderr)
        return 1

    send_each([
        (name, address, subject, newsletter.text(issue, name), newsletter.html(issue, name))
        for name, address in to
    ])
    print(f"Sent '{subject}' to {len(to)} recipient(s).")

    # Only a send to the real list counts as "used": a --to test run must not
    # burn this week's joke, comic and papers.
    if not args.to:
        sources.save_history(newsletter.record(issue, history))
        print(f"Recorded the sent joke, papers and comic in {sources.HISTORY_FILE.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
