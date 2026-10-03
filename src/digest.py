"""Digest formatting and delivery.

Idempotency matters here as much as in the DB: a retried run must not
double-post to Telegram. The sent_digests table is the guard.
"""
import html
import json
import logging
import os
from pathlib import Path

import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from db import query, upsert_many
from filters import describe_reason

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Hackathon code of conduct: "Products must not provide financial advice...
# Include a disclaimer where relevant." Kept short since this is read on a
# phone.
DISCLAIMER = "<i>Information only, not investment advice.</i>"


def already_sent(run_date):
    rows = query("SELECT 1 FROM sent_digests WHERE run_date = %s", (run_date,))
    return bool(rows)


def mark_sent(run_date, item_count):
    upsert_many(
        "sent_digests",
        ["run_date", "item_count"],
        [{"run_date": run_date, "item_count": item_count}],
        conflict_cols=["run_date"],
    )


def format_digest(run_date, items, stages):
    """Telegram HTML. Keep it scannable — this is read on a phone."""
    if not items:
        return (
            f"<b>IDX digest — {run_date}</b>\n\n"
            f"Nothing crossed threshold today.\n"
            f"<i>Scanned {stages.get('raw', 0):,} items.</i>\n"
            f"{DISCLAIMER}"
        )

    lines = [f"<b>IDX digest — {run_date}</b>", ""]
    for it in items:
        title = html.escape((it.get("title") or "")[:140])
        symbol = html.escape(it.get("symbol") or "—")
        reason = html.escape(describe_reason(it))
        lines.append(f"<b>{symbol}</b> · <i>{reason}</i>")
        if it.get("url"):
            lines.append(f'<a href="{html.escape(it["url"])}">{title}</a>')
        else:
            lines.append(title)
        lines.append("")

    compression = 1 - (len(items) / stages["raw"]) if stages.get("raw") else 0
    lines.append(
        f"<i>{len(items)} of {stages.get('raw', 0):,} items "
        f"({compression:.1%} filtered)</i>"
    )
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("telegram not configured, printing instead\n%s", text)
        return False

    resp = requests.post(
        TELEGRAM_API.format(token=TELEGRAM_BOT_TOKEN),
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"telegram {resp.status_code}: {resp.text[:200]}")
    return True


def send_failure_alert(run_date, error):
    """Dead man's switch.

    A silent failure looks IDENTICAL to 'no alerts today'. Without this, a
    broken pipeline is indistinguishable from a quiet market — which is the
    single most dangerous failure mode an autonomous system can have.
    """
    text = (
        f"<b>⚠️ Pipeline failed — {run_date}</b>\n\n"
        f"<code>{html.escape(str(error)[:400])}</code>"
    )
    try:
        send_telegram(text)
    except Exception:
        log.exception("failure alert could not be delivered")


def write_dashboard_json(run_date, items, stages, path="docs/data/latest.json"):
    """Written back into the repo by CI; GitHub Pages serves it statically.

    No server, no API layer, no credentials in the browser.
    """
    payload = {
        "run_date": str(run_date),
        "status": "success",
        "trigger": os.environ.get("GITHUB_EVENT_NAME"),
        "stages": stages,
        "items": [
            {
                "symbol": it.get("symbol"),
                "headline": it.get("title"),
                "url": it.get("url"),
                "volume_z": round(it["volume_z"], 2) if it.get("volume_z") else None,
                "return_z": round(it["return_z"], 2) if it.get("return_z") else None,
                "reason": describe_reason(it),
            }
            for it in items
        ],
    }
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    log.info("wrote %s", path)
    return payload


def deliver(run_date, items, stages):
    if already_sent(run_date):
        log.info("digest for %s already sent, skipping", run_date)
        return False
    send_telegram(format_digest(run_date, items, stages))
    mark_sent(run_date, len(items))
    return True
