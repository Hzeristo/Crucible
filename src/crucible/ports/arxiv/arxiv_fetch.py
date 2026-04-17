"""Fetch latest arXiv papers and download PDFs."""

from __future__ import annotations

import csv
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from src.crucible.core.config import Settings

logger = logging.getLogger(__name__)


class ArxivFetcher:
    """Fetch arXiv metadata from official API and download PDF files."""

    API_URL = "http://export.arxiv.org/api/query"
    REQUEST_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36 "
            "ProjectChimera/1.0"
        )
    }

    def __init__(
        self, settings: Settings | None = None, timeout_seconds: int = 50
    ) -> None:
        self.settings = settings or Settings()
        self.timeout_seconds = timeout_seconds
        self.seen_ids = self._load_seen_ids()
        self.seen_arxiv_ids = {
            normalized_id
            for seen_id in self.seen_ids
            if (normalized_id := self._extract_arxiv_core_id(seen_id)) is not None
        }

    def fetch_metadata(self) -> list[dict]:
        pm = self.settings.paper_miner_or_default
        params = {
            "search_query": pm.arxiv_query,
            "start": 0,
            "max_results": pm.arxiv_max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }

        try:
            response = requests.get(
                self.API_URL,
                params=params,
                headers=self.REQUEST_HEADERS,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            logger.debug("Arxiv URL: %s", response.url)
        except requests.Timeout:
            logger.error("Arxiv API connection timed out. Check your TUN/Proxy.")
            return []
        except requests.RequestException as exc:
            logger.warning("Arxiv API request failed: %s", exc)
            return []

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            logger.warning("Failed to parse arXiv Atom response: %s", exc)
            return []

        atom_ns = "{http://www.w3.org/2005/Atom}"
        cutoff_date = datetime.now(timezone.utc).date() - timedelta(days=3)
        records: list[dict] = []

        for entry in root.findall(f"{atom_ns}entry"):
            raw_id = self._extract_entry_id(entry, atom_ns)
            title = self._extract_entry_title(entry, atom_ns)
            pdf_url = self._extract_pdf_url(entry, atom_ns)
            submitted_date = self._extract_submitted_date(entry, atom_ns)

            if not raw_id or not title or not pdf_url or submitted_date is None:
                continue
            if submitted_date < cutoff_date:
                continue

            records.append({"id": raw_id, "title": title, "pdf_url": pdf_url})

        logger.info("Fetched %s arXiv records since %s", len(records), cutoff_date)
        return records

    def fetch_single_metadata(self, arxiv_id: str) -> dict | None:
        try:
            response = requests.get(
                self.API_URL,
                params={"id_list": arxiv_id},
                headers=self.REQUEST_HEADERS,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            root = ET.fromstring(response.text)
        except requests.Timeout:
            logger.debug("fetch_single_metadata timed out for %s", arxiv_id)
            return None
        except requests.RequestException:
            logger.debug(
                "fetch_single_metadata request failed for %s", arxiv_id, exc_info=True
            )
            return None
        except ET.ParseError:
            logger.debug(
                "fetch_single_metadata XML parse failed for %s", arxiv_id, exc_info=True
            )
            return None

        try:
            atom_ns = "{http://www.w3.org/2005/Atom}"
            entries = root.findall(f"{atom_ns}entry")
            if not entries:
                return None
            entry = entries[0]
            submitted = self._extract_submitted_date(entry, atom_ns)
            if submitted is None:
                return None
            authors = self._extract_entry_authors(entry, atom_ns)
            return {"year": str(submitted.year), "authors": authors}
        except Exception:
            logger.debug(
                "fetch_single_metadata extraction failed for %s", arxiv_id, exc_info=True
            )
            return None

    def download_pdfs(self, paper_records: list[dict], target_dir: Path) -> int:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("Failed to prepare target directory %s: %s", target_dir, exc)
            return 0

        downloaded_count = 0
        for record in paper_records:
            paper_id = record.get("id")
            pdf_url = record.get("pdf_url")

            if not isinstance(paper_id, str) or not isinstance(pdf_url, str):
                logger.warning("Skip invalid record (missing id/pdf_url): %s", record)
                continue
            if self._is_seen_paper(paper_id):
                logger.info("Skip seen paper from audit log: %s", paper_id)
                continue

            pdf_path = target_dir / f"{paper_id}.pdf"
            if pdf_path.exists():
                logger.info("Skip existing PDF: %s", pdf_path.name)
                continue

            try:
                with requests.get(
                    pdf_url,
                    stream=True,
                    headers=self.REQUEST_HEADERS,
                    timeout=self.timeout_seconds,
                ) as response:
                    response.raise_for_status()
                    with pdf_path.open("wb") as file_obj:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                file_obj.write(chunk)
                downloaded_count += 1
                logger.info("Downloaded PDF: %s", pdf_path.name)
            except requests.RequestException as exc:
                logger.warning(
                    "Failed to download %s from %s: %s", paper_id, pdf_url, exc
                )
            except OSError as exc:
                logger.warning("Failed to write PDF %s: %s", pdf_path, exc)

        return downloaded_count

    def _load_seen_ids(self) -> set[str]:
        audit_log_path = self.settings.project_root / "papers" / "audit_log.csv"
        if not audit_log_path.exists():
            logger.info("Audit log not found, skip seen-id preload: %s", audit_log_path)
            return set()

        seen_ids: set[str] = set()
        try:
            with audit_log_path.open("r", encoding="utf-8", newline="") as file_obj:
                reader = csv.DictReader(file_obj)
                for row in reader:
                    paper_id = (row.get("paper_id") or "").strip()
                    if paper_id:
                        seen_ids.add(paper_id)
        except OSError as exc:
            logger.warning("Failed to read audit log %s: %s", audit_log_path, exc)
            return set()
        except csv.Error as exc:
            logger.warning("Invalid audit CSV format %s: %s", audit_log_path, exc)
            return set()

        logger.info("Preloaded %s seen paper ids from audit log", len(seen_ids))
        return seen_ids

    def _is_seen_paper(self, paper_id: str) -> bool:
        if paper_id in self.seen_ids:
            return True
        normalized_id = self._extract_arxiv_core_id(paper_id)
        if normalized_id is None:
            return False
        return normalized_id in self.seen_arxiv_ids

    @staticmethod
    def _extract_arxiv_core_id(raw_id: str) -> str | None:
        match = re.search(r"(\d{4}\.\d{4,5})(?:v\d+)?", raw_id)
        if not match:
            return None
        return match.group(1)

    @staticmethod
    def _extract_entry_id(entry: ET.Element, atom_ns: str) -> str | None:
        id_node = entry.find(f"{atom_ns}id")
        if id_node is None or not id_node.text:
            return None

        raw = id_node.text.strip().rstrip("/")
        paper_id = raw.split("/")[-1]
        match = re.match(r"^(.+?)v\d+$", paper_id)
        return match.group(1) if match else paper_id

    @staticmethod
    def _extract_entry_title(entry: ET.Element, atom_ns: str) -> str | None:
        title_node = entry.find(f"{atom_ns}title")
        if title_node is None or not title_node.text:
            return None
        return " ".join(title_node.text.split())

    @staticmethod
    def _extract_pdf_url(entry: ET.Element, atom_ns: str) -> str | None:
        for link in entry.findall(f"{atom_ns}link"):
            title = (link.attrib.get("title") or "").lower()
            href = link.attrib.get("href")
            if title == "pdf" and href:
                return href if href.endswith(".pdf") else f"{href}.pdf"
        return None

    @staticmethod
    def _extract_submitted_date(
        entry: ET.Element, atom_ns: str
    ) -> datetime.date | None:
        published_node = entry.find(f"{atom_ns}published")
        if published_node is None or not published_node.text:
            return None
        try:
            dt = datetime.fromisoformat(published_node.text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt.astimezone(timezone.utc).date()

    @staticmethod
    def _extract_entry_authors(entry: ET.Element, atom_ns: str) -> str:
        names: list[str] = []
        for author_el in entry.findall(f"{atom_ns}author"):
            name_el = author_el.find(f"{atom_ns}name")
            if name_el is not None and name_el.text:
                names.append(" ".join(name_el.text.split()))
        return ", ".join(names)
