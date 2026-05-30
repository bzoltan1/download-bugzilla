#!/usr/bin/env python3
"""
Stage 1: Download Bugzilla bug reports to a local JSONL file.

Usage:
    bugzilla-download [--output FILE] [--limit N] [--since DATE] [--verbose]
    python download_bugzilla.py [--output FILE] [--limit N] [--since DATE] [--verbose]

Configuration is read from .env (see .env.example).
"""

import argparse
import datetime
import json
import logging
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

BUGZILLA_BASE_URL = os.getenv("BUGZILLA_BASE_URL", "https://bugzilla.suse.com/rest/bug")
_raw_keys = os.getenv("BUGZILLA_API_KEYS", "")
API_KEYS = [k.strip() for k in _raw_keys.split(",") if k.strip()]

if not API_KEYS:
    raise RuntimeError(
        "No API keys found. Set BUGZILLA_API_KEYS in your .env file as a comma-separated list."
    )

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

current_key_index = 0


def _rotate_key():
    global current_key_index
    current_key_index = (current_key_index + 1) % len(API_KEYS)
    logger.debug("Rotated to API key index %d", current_key_index)


def _current_key():
    return API_KEYS[current_key_index]


# ---------------------------------------------------------------------------
# JSONL helpers
# ---------------------------------------------------------------------------

def _last_bug_id(output_file: str) -> int:
    """Return the highest bug_number seen in the JSONL file, or 0 if empty/missing."""
    if not os.path.exists(output_file):
        return 0
    last_id = 0
    try:
        with open(output_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    bug_id = record.get("bug_number", 0)
                    if bug_id > last_id:
                        last_id = bug_id
                except json.JSONDecodeError:
                    logger.warning("Skipping corrupt JSONL line: %s", line[:80])
        logger.info("Resuming from last bug ID: %d", last_id)
    except OSError as e:
        logger.error("Could not read %s: %s", output_file, e)
    return last_id


def _count_lines(output_file: str) -> int:
    if not os.path.exists(output_file):
        return 0
    with open(output_file, "r") as f:
        return sum(1 for line in f if line.strip())


def _append_bug(output_file: str, bug_record: dict):
    with open(output_file, "a") as f:
        f.write(json.dumps(bug_record) + "\n")


# ---------------------------------------------------------------------------
# API fetch helpers
# ---------------------------------------------------------------------------

def fetch_comments(bug_id: int) -> list | None:
    """Fetch comments for a single bug. Returns list of comment dicts, or None on fatal error."""
    comments_url = f"{BUGZILLA_BASE_URL}/{bug_id}/comment"
    network_error_attempts = 0

    while True:
        try:
            response = requests.get(
                comments_url,
                params={"api_key": _current_key()},
                timeout=30
            )
            response.raise_for_status()

            comments_data = (
                response.json()
                .get("bugs", {})
                .get(str(bug_id), {})
                .get("comments", [])
            )
            return [
                {
                    "name": c.get("creator", "Unknown"),
                    "date": c.get("creation_time", "Unknown"),
                    "text": c.get("text", ""),
                }
                for c in comments_data
            ]

        except requests.exceptions.Timeout:
            logger.warning("Timeout fetching comments for bug #%d. Rotating key.", bug_id)
            _rotate_key()
            time.sleep(5)

        except requests.exceptions.HTTPError as e:
            status = response.status_code
            if status == 429:
                logger.warning("Rate limit (429) on comments for bug #%d. Rotating key.", bug_id)
                _rotate_key()
                time.sleep(60)
            elif status == 503:
                logger.warning("Service unavailable (503) for bug #%d comments. Rotating key.", bug_id)
                _rotate_key()
                network_error_attempts += 1
                time.sleep(30)
            else:
                logger.error("HTTP %d fetching comments for bug #%d: %s", status, bug_id, e)
                return None

        except requests.exceptions.RequestException as e:
            wait = min(60 * (2 ** network_error_attempts), 43200)
            logger.warning(
                "Network error for bug #%d comments: %s. Waiting %dm %ds.",
                bug_id, e, wait // 60, wait % 60
            )
            _rotate_key()
            time.sleep(wait)
            network_error_attempts += 1


def fetch_bugs(output_file: str, limit: int | None = None, since: str | None = None):
    """
    Download bugs from Bugzilla API and append them to output_file (JSONL).

    Args:
        output_file: Path to the output JSONL file.
        limit: Stop after this many newly downloaded bugs (dev mode).
        since: ISO 8601 date string; fetch only bugs changed on or after this date.
    """
    last_id = _last_bug_id(output_file)
    existing_count = _count_lines(output_file)
    logger.info("Output file has %d existing records.", existing_count)

    # When --since is used, the API filters to a subset of bugs, so the offset
    # must start at 0 relative to that filtered result set — not at existing_count.
    # For a full (non-filtered) download, offset resumes from where we left off.
    initial_offset = 0 if since else existing_count

    params = {
        "limit": 500,
        "offset": initial_offset,
    }
    if since:
        params["last_change_time"] = since
        logger.info("Fetching bugs changed since %s", since)

    downloaded = 0
    network_error_attempts = 0

    while True:
        if limit is not None and downloaded >= limit:
            logger.info("Reached --limit %d. Stopping.", limit)
            break

        try:
            response = requests.get(
                BUGZILLA_BASE_URL,
                params={**params, "api_key": _current_key()},
                timeout=60  # last_change_time queries can take 7-8s server-side
            )
            response.raise_for_status()

            try:
                data = response.json()
            except json.JSONDecodeError:
                logger.error("JSON decode error on bug list response. Retrying.")
                time.sleep(10)
                continue

            if "bugs" not in data:
                logger.error("Unexpected response structure (no 'bugs' key): %s", str(data)[:200])
                time.sleep(60)
                continue

            bugs = data["bugs"]
            if not bugs:
                logger.info("No more bugs returned by API. Download complete.")
                break

            for bug in bugs:
                if limit is not None and downloaded >= limit:
                    break

                bug_id = bug.get("id")

                # Skip bugs we already have (can happen on resume with offset drift)
                if bug_id <= last_id and last_id > 0:
                    logger.debug("Skipping already-seen bug #%d", bug_id)
                    continue

                logger.info("Fetching bug #%d: %s", bug_id, bug.get("summary", "")[:80])
                comments = fetch_comments(bug_id)

                if comments is None:
                    logger.error("Fatal error fetching comments for bug #%d. Stopping.", bug_id)
                    return

                bug_record = {
                    # Core identity
                    "bug_number":      bug_id,
                    "title":           bug.get("summary", "No title available"),
                    # Classification
                    "Product":         bug.get("product", "Unknown"),
                    "version":         bug.get("version", "Unknown"),
                    "Component":       bug.get("component", "Unknown"),
                    # Lifecycle
                    "Reported":        bug.get("creation_time", "Unknown"),
                    "last_change_time": bug.get("last_change_time", ""),
                    "Status":          bug.get("status", "Unknown"),
                    "Resolution":      bug.get("resolution", ""),
                    # Triage
                    "priority":        bug.get("priority", ""),
                    "severity":        bug.get("severity", ""),
                    # Structured tags
                    "aliases":         bug.get("alias", []),    # CVE IDs etc.
                    "keywords":        bug.get("keywords", []),
                    "whiteboard":      bug.get("whiteboard", ""),
                    "url":             bug.get("url", ""),
                    # Content
                    "Comments":        comments,
                }

                _append_bug(output_file, bug_record)
                downloaded += 1
                if bug_id > last_id:
                    last_id = bug_id

            params["offset"] += len(bugs)
            network_error_attempts = 0
            time.sleep(2)

        except requests.exceptions.HTTPError as e:
            status = response.status_code
            if status == 429:
                logger.warning("Rate limit (429) on bug list. Rotating key.")
                _rotate_key()
                time.sleep(60)
            else:
                logger.error("HTTP %d fetching bug list: %s. Stopping.", status, e)
                break

        except requests.exceptions.RequestException as e:
            wait = min(30 * (2 ** network_error_attempts), 43200)
            logger.warning(
                "Network error fetching bug list at offset %d: %s. Waiting %dm %ds.",
                params["offset"], e, wait // 60, wait % 60
            )
            _rotate_key()
            time.sleep(wait)
            network_error_attempts += 1

    total = _count_lines(output_file)
    logger.info("Done. %d bugs downloaded this run. Total in file: %d.", downloaded, total)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Download Bugzilla bug reports to a local JSONL file."
    )
    parser.add_argument(
        "--output", default=os.getenv("BUGZILLA_JSONL", "data/bug_reports.jsonl"),
        metavar="FILE", help="Output JSONL file path (default: data/bug_reports.jsonl)"
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Stop after N bugs (dev mode)"
    )
    parser.add_argument(
        "--since", default=None, metavar="DATE",
        help="Fetch only bugs changed since DATE (ISO 8601, e.g. 2025-01-01)"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable DEBUG logging"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Ensure output directory exists
    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    logger.info("Bugzilla URL: %s", BUGZILLA_BASE_URL)
    logger.info("API keys loaded: %d", len(API_KEYS))
    logger.info("Output file: %s", args.output)
    if args.limit:
        logger.info("Dev mode: limit = %d bugs", args.limit)
    if args.since:
        logger.info("Incremental mode: since = %s", args.since)

    start = datetime.datetime.now()
    try:
        fetch_bugs(args.output, limit=args.limit, since=args.since)
    except KeyboardInterrupt:
        logger.info("Interrupted by user. Progress is saved in %s.", args.output)

    elapsed = (datetime.datetime.now() - start).total_seconds()
    logger.info("Total time: %.1f seconds.", elapsed)


if __name__ == "__main__":
    main()
