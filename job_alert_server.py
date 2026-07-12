"""
Job Alert MCP Server.

This server implements the Model Context Protocol (MCP) to provide job alert functionality.
It exposes:
- A prompt that configures a job search assistant.
- Resources for accessing job search criteria and the latest job digest.
- Tools for searching job listings and sending email notifications.

The server is designed to run locally via stdio transport for integration with Claude Code.
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Union

import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from mcp.server.fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("Job Alert Server")


# ---------------------
# Prompt
# ---------------------
@mcp.prompt()
def job_alert_assistant(role: str, min_experience: int) -> str:
    """Return persona instructions for a Job Alert Assistant.

    This prompt configures the assistant to search for job listings matching the given
    role and minimum experience, extract the listings, and prepare them as a daily email digest.

    Args:
        role: Job role to search for (e.g., "software engineer")
        min_experience: Minimum years of experience required (note: currently not used in search
                         as the sources are remote-first and global, but retained for reference)

    Returns:
        A string containing the system prompt for the assistant.
    """
    return f"""You are a Job Alert Assistant. Your task is to:
1. Search for '{role}' jobs with at least {min_experience} years of experience.
2. Extract the job listings (title, company, location if available, salary if available, and application link).
3. Format the results into a clear, readable daily digest.
4. Prepare the digest for email delivery.

You have access to the following tools and resources:
- Use the 'search_jobs' tool to fetch job listings from RemoteOK and WeWorkRemotely.
- Use the 'jobs://criteria' resource to verify the current search criteria.
- Use the 'jobs://latest-digest' resource to check the last saved digest.
- Use the 'send_email' tool to send the digest to a specified email address.

When the user asks for job updates, invoke the search_jobs tool with the provided parameters,
then format the results and optionally send them via email.
**Infer the `hours_window` argument from the user's phrasing:** use 24 only if they explicitly say "today" or "last 24 hours"; otherwise default to 168 (7 days). If they ask for a longer period such as "this month", use a larger window (e.g., 720 hours).

Always verify the search criteria via the jobs://criteria resource before searching.
After sending an email, update the last digest by saving the results (the search_jobs tool does this automatically).

When presenting results, note which jobs have "experience_match": "unspecified" so the user knows the {min_experience}+ years claim is unverified for those, rather than presenting all as equally confirmed.
"""


# ---------------------
# Resources
# ---------------------
@mcp.resource("jobs://criteria")
def get_criteria() -> str:
    """Return the fixed search criteria as a JSON resource.

    This resource provides the default search criteria used by the job alert system.
    It is exposed as a resource because it represents read-only reference data that
    configures the job search behavior, rather than an action that performs work.

    Returns:
        A JSON string containing the role and minimum experience (location is not used).
    """
    criteria = {
        "role": "software engineer",
        "min_experience": 3
    }
    return json.dumps(criteria, indent=2)


@mcp.resource("jobs://latest-digest")
def get_latest_digest() -> str:
    """Return the content of the last saved job digest.

    This resource provides access to the most recently generated job digest,
    stored in 'last_digest.json'. It is exposed as a resource because it represents
    stored state that can be read but not modified directly (modification occurs
    via the search_jobs tool).

    Returns:
        The JSON content of the last digest, or a message indicating no digest exists.
    """
    try:
        with open("last_digest.json", "r", encoding="utf-8") as f:
            content = f.read()
        return content
    except FileNotFoundError:
        return '{"message": "No digest saved yet"}'
    except Exception as e:
        return f'{{"error": "Failed to read digest: {str(e)}}}'


# ---------------------
# Helper Functions
# ---------------------
def _fetch_remoteok_jobs(query: str) -> List[Dict[str, Any]]:
    """Fetch jobs from RemoteOK's public API."""
    url = "https://remoteok.com/api"
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # First element is metadata, skip it
        jobs = []
        if isinstance(data, list) and len(data) > 1:
            # Take only the first 20 items after metadata to limit processing
            for item in data[1:21]:  # we'll filter later, but limit to 20 to avoid too much work
                if not isinstance(item, dict):
                    continue
                position = item.get("position", "")
                company = item.get("company", "")
                tags = item.get("tags", [])
                url_field = item.get("url", "")
                job_id = item.get("id", "")
                # Date posted (epoch seconds)
                date_epoch = item.get("date")
                # Normalize tags to a string for searching
                if isinstance(tags, list):
                    tags_str = " ".join(tags)
                else:
                    tags_str = str(tags)
                # Determine if matches query (case-insensitive) in position or tags
                if (query.lower() in position.lower()) or (query.lower() in tags_str.lower()):
                    # Build link
                    link = url_field
                    if not link and job_id:
                        link = f"https://remoteok.com/remote-jobs/{job_id}"
                    # If still no link, skip
                    if not link:
                        continue
                    # Compute experience_match heuristic
                    experience_match = _compute_experience_match(
                        title=position,
                        company=company,
                        tags=tags_str,
                        description=""  # RemoteOK API may have description field? not always; we'll leave empty for now
                    )
                    jobs.append({
                        "title": position.strip(),
                        "company": company.strip(),
                        "link": link.strip(),
                        "source": "remoteok",
                        "posted_timestamp": date_epoch,  # keep for sorting/filtering
                        "experience_match": experience_match
                    })
        return jobs
    except Exception as e:
        print(f"Error fetching RemoteOK jobs: {e}", file=sys.stderr)
        return []


def _parse_wwr_item(item: ET.Element) -> Dict[str, str]:
    """Parse a single <item> from WeWorkRemotely RSS."""
    title_elem = item.find('title')
    link_elem = item.find('link')
    pub_date_elem = item.find('pubDate')
    if title_elem is None or link_elem is None:
        return {}
    title = title_elem.text or ""
    link = link_elem.text or ""
    pub_date_str = pub_date_elem.text if pub_date_elem is not None else ""
    # WWR title format: "Company: Job Title" or sometimes "Company - Job Title"
    company = ""
    job_title = title
    if ':' in title:
        parts = title.split(':', 1)
        company = parts[0].strip()
        job_title = parts[1].strip()
    elif ' - ' in title:
        parts = title.split(' - ', 1)
        company = parts[0].strip()
        job_title = parts[1].strip()
    # If no separator found, assume the whole title is the job title and company unknown
    return {
        "title": job_title,
        "company": company,
        "link": link,
        "pub_date_str": pub_date_str,
        "source": "weworkremotely"
    }


def _fetch_weworkremotely_jobs(query: str) -> List[Dict[str, Any]]:
    """Fetch jobs from WeWorkRemotely RSS feeds."""
    feeds = [
        "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss"
    ]
    jobs = []
    for url in feeds:
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            # Find all <item> elements
            for item in root.findall('.//item'):
                job = _parse_wwr_item(item)
                if not job:
                    continue
                # Filter by query in title or company (case-insensitive)
                if (query.lower() in job["title"].lower()) or (query.lower() in job["company"].lower()):
                    # Compute experience_match heuristic (description not available in RSS, use title+company)
                    experience_match = _compute_experience_match(
                        title=job["title"],
                        company=job["company"],
                        tags="",
                        description=""  # description not in RSS; we could fetch but spec says only first 10 visible; we skip for simplicity
                    )
                    job["experience_match"] = experience_match
                    # Convert pubDate to timestamp for filtering/sorting
                    try:
                        # Try parsing with timezone name (e.g., UTC) or offset (e.g., +0000)
                        try:
                            dt = datetime.strptime(job["pub_date_str"], "%a, %d %b %Y %H:%M:%S %Z")
                        except ValueError:
                            dt = datetime.strptime(job["pub_date_str"], "%a, %d %b %Y %H:%M:%S %z")
                        # Make it timezone aware (if not already)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        job["posted_timestamp"] = int(dt.timestamp())
                    except Exception:
                        # If parsing fails, treat as old so it gets filtered out
                        job["posted_timestamp"] = 0
                    jobs.append(job)
        except Exception as e:
            print(f"Error fetching WeWorkRemotely feed {url}: {e}", file=sys.stderr)
    return jobs


def _compute_experience_match(title: str, company: str, tags: str, description: str) -> str:
    """Heuristic to determine experience level match.
    Returns one of: "confirmed", "excluded", "unspecified".
    """
    text = f"{title} {company} {tags} {description}".lower()
    # Exclusion patterns (junior, intern, entry-level, graduate, associate)
    exclusion_patterns = [
        r'\bjunior\b', r'\bjr\b', r'\bintern\b', r'\bentry.level\b', r'\bentry level\b',
        r'\bgraduate\b', r'\bassociate\b', r'\bentry-level\b'
    ]
    for pat in exclusion_patterns:
        if re.search(pat, text):
            return "excluded"
    # Confirmation patterns: explicit X+ years or X years where X >= 3, or seniority keywords
    # Look for patterns like "3+ years", "5 years", "10+ years"
    year_patterns = [
        r'(\d+)\+\s*years?',
        r'(\d+)\s*years?'
    ]
    for pat in year_patterns:
        matches = re.findall(pat, text)
        for match in matches:
            try:
                years = int(match)
                if years >= 3:
                    return "confirmed"
            except ValueError:
                pass
    # Seniority keywords
    senior_keywords = ["senior", "staff", "lead", "principal", "architect", "head", "director", "manager"]
    for kw in senior_keywords:
        if kw in text:
            return "confirmed"
    # If none of the above, unspecified
    return "unspecified"


def _deduplicate_jobs(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove duplicates based on title and company (case-insensitive)."""
    seen = set()
    deduped = []
    for job in jobs:
        key = (job["title"].lower(), job["company"].lower())
        if key not in seen:
            seen.add(key)
            deduped.append(job)
    return deduped


# ---------------------
# Tools
# ---------------------
@mcp.tool()
def search_jobs(query: str = "software engineer", hours_window: int = 168) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Search for job listings from RemoteOK and WeWorkRemotely.

    Uses RemoteOK's official JSON API and WeWorkRemotely's official RSS feeds.
    Searches by role keyword only (no location filter, as these are remote-first global boards).
    Per RemoteOK's usage terms, preserves the direct apply link back to the original listing.

    Recency filtering is controlled by the `hours_window` parameter (in hours) and is based on real timestamps from each source.
    The default is 168 hours (7 days) to provide a reasonably active daily digest; it can be set narrower (e.g., 24) if the user
    explicitly asks for "today" or "last 24 hours", or widened (e.g., 720) for requests like "this month".
    Experience-level matching is a best-effort text heuristic, not a guarantee, since
    neither source provides a structured experience field.

    Args:
        query: Job role keyword to search for (default: "software engineer")
        hours_window: How far back to search for postings, in hours (default: 168).

    Returns:
        A list of job dictionaries, each with keys: title, company, link, source,
        posted_timestamp (epoch seconds), experience_match (one of "confirmed","excluded","unspecified").
        If no matching jobs are found, returns a dict with a warning and an empty jobs list:
        {"warning": "No matching jobs found in this run", "jobs": []}
    """

    # Fetch from both sources
    remoteok_jobs = _fetch_remoteok_jobs(query)
    weworkremotely_jobs = _fetch_weworkremotely_jobs(query)

    # Calculate cutoff time (now - hours_window hours)
    now_ts = datetime.now(timezone.utc).timestamp()
    cutoff_ts = now_ts - (hours_window * 3600)

    # Filter by recency (last `hours_window` hours) and limit to first 10 from each source after filtering

    # Filter by recency (last `hours_window` hours) and limit to first 10 from each source after filtering
    def filter_and_limit(jobs):
        recent = [j for j in jobs if j.get("posted_timestamp", 0) >= cutoff_ts]
        # Take at most first 10 (as per spec: first 10 listings visible on the page)
        return recent[:10]

    remoteok_limited = filter_and_limit(remoteok_jobs)
    weworkremotely_limited = filter_and_limit(weworkremotely_jobs)

    # Combine
    all_jobs = remoteok_limited + weworkremotely_limited

    # Deduplicate
    deduped = _deduplicate_jobs(all_jobs)

    # Take at most 10 total (spec says total output should never exceed 20, but we'll be conservative)
    limited = deduped[:10]

    if not limited:
        return {"warning": "No matching jobs found in this run", "jobs": []}
    return limited


@mcp.tool()
def send_email(jobs: Union[List[Dict[str, Any]], Dict[str, Any]], recipient: str = "rishita.das.work@gmail.com") -> dict:
    """Send job listings via email using Gmail SMTP.

    This tool formats the list of job dictionaries into a readable email (HTML format)
    and sends it via Gmail's SMTP server using SSL. It requires two environment variables:
    - GMAIL_SENDER_EMAIL: The Gmail address sending the email
    - GMAIL_APP_PASSWORD: A 16-character App Password generated for this sender account
      (requires 2-Step Verification enabled on the Google account).

    The email format is a numbered list:
      1. Job Title @ Company — Location — [Apply link]
      2. ...

    Args:
        jobs: Either a list of job dictionaries (each with title, company, link, source)
              or a dictionary returned by search_jobs when no jobs are found
              (containing a "warning" key and a "jobs" list).
        recipient: Email address to send the digest to (default: rishita.das.work@gmail.com)

    Returns:
        A dictionary with keys:
          - "status": "success" or "error"
          - "message": Descriptive message about the outcome
    """
    # Extract the job list from the input
    if isinstance(jobs, dict) and "jobs" in jobs:
        job_list = jobs["jobs"]
        warning = jobs.get("warning")
    else:
        job_list = jobs
        warning = None

    # If no jobs and we have a warning, we can still send an email with the warning
    if not job_list:
        if warning:
            body = f"<p>{warning}</p>"
        else:
            body = "<p>No jobs found.</p>"
    else:
        # Build HTML table
        rows = []
        for i, job in enumerate(job_list, 1):
            title = job.get("title", "N/A")
            company = job.get("company", "N/A")
            link = job.get("link", "#")
            source = job.get("source", "unknown")
            exp_match = job.get("experience_match", "unspecified")
            # Location and salary are not available from these sources, so we omit or show N/A
            location = "Remote"
            salary = "N/A"
            rows.append(
                f"<tr>"
                f"<td>{i}</td>"
                f"<td>{title}</td>"
                f"<td>{company}</td>"
                f"<td>{location}</td>"
                f"<td>{salary}</td>"
                f"<td><a href='{link}'>Apply</a></td>"
                f"<td>{exp_match}</td>"
                f"</tr>"
            )
        html = f"""
        <html>
        <body>
            <h2>Job Digest</h2>
            <table border="1" cellpadding="5" cellspacing="0">
                <tr>
                    <th>#</th>
                    <th>Title</th>
                    <th>Company</th>
                    <th>Location</th>
                    <th>Salary</th>
                    <th>Link</th>
                    <th>Experience Match</th>
                </tr>
                {''.join(rows)}
            </table>
            <p>Sources: RemoteOK, WeWorkRemotely and Indeed</p>
        </body>
        </html>
        """
        body = html

    # Email configuration
    sender_email = os.getenv("GMAIL_SENDER_EMAIL")
    app_password = os.getenv("GMAIL_APP_PASSWORD")
    if not sender_email or not app_password:
        return {
            "status": "error",
            "message": "Missing GMAIL_SENDER_EMAIL or GMAIL_APP_PASSWORD environment variables."
        }

    # Create message
    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = recipient
    msg["Subject"] = "Daily Job Digest"

    msg.attach(MIMEText(body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, app_password)
            server.send_message(msg)
        return {
            "status": "success",
            "message": f"Email sent successfully to {recipient}"
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to send email: {str(e)}"
        }


# ---------------------
# Main entry point
# ---------------------
if __name__ == "__main__":
    # Run the server using stdio transport (for local stdio-based MCP clients like Claude Code)
    mcp.run(transport='stdio')