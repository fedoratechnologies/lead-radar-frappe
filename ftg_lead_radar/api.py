from __future__ import annotations

import html
import ipaddress
import re
import socket
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import frappe
import requests
from bs4 import BeautifulSoup


def _parse_tags(raw: str | None) -> list[str]:
	if not raw:
		return []
	return [part.strip() for part in raw.split(",") if part.strip()]


def _publisher_endpoint(base_url: str) -> str:
	base_url = (base_url or "").strip()
	if not base_url:
		raise ValueError("Publisher URL is required")
	if base_url.endswith("/publish"):
		return base_url
	return base_url.rstrip("/") + "/publish"


def build_config_payload() -> dict[str, Any]:
	settings = frappe.get_single("Lead Radar Settings")

	scoring = {
		"window_days": int(settings.window_days or 90),
		"half_life_days": int(settings.half_life_days or 14),
		"min_signal_confidence": float(settings.min_signal_confidence or 0.7),
		"promote_threshold": float(settings.promote_threshold or 70),
	}

	sources: list[dict[str, Any]] = []
	for row in frappe.get_all(
		"Lead Radar Source",
		fields=[
			"source_id",
			"enabled",
			"source_type",
			"source_name",
			"source_weight",
			"url",
			"max_items",
			"include_regex",
			"exclude_regex",
			"tags",
		],
		order_by="modified desc",
	):
		src: dict[str, Any] = {
			"id": row.source_id,
			"enabled": bool(row.enabled),
			"type": row.source_type or "rss",
			"name": row.source_name,
			"url": row.url,
			"tags": _parse_tags(row.tags),
			"weight": float(row.source_weight or 1.0),
			"max_items": int(row.max_items or 20),
		}
		if row.include_regex:
			src["include_regex"] = str(row.include_regex)
		if row.exclude_regex:
			src["exclude_regex"] = str(row.exclude_regex)
		sources.append(src)

	keyword_packs: list[dict[str, Any]] = []
	for row in frappe.get_all(
		"Lead Radar Keyword Pack",
		fields=["name", "pack_id", "enabled", "pack_name", "tags"],
		order_by="modified desc",
	):
		doc = frappe.get_doc("Lead Radar Keyword Pack", row.name)
		keywords = []
		for kw in doc.keywords:
			if not kw.keyword:
				continue
			keywords.append({"keyword": kw.keyword, "weight": float(kw.weight or 0)})

		keyword_packs.append(
			{
				"id": row.pack_id,
				"enabled": bool(row.enabled),
				"name": row.pack_name,
				"tags": _parse_tags(row.tags),
				"keywords": keywords,
			}
		)

	return {"scoring": scoring, "sources": sources, "keyword_packs": keyword_packs}


def _require_internal_permission() -> None:
	if not (frappe.has_role("COS") or frappe.has_role("System Manager")):
		frappe.throw("Not permitted.", frappe.PermissionError)


def _validate_public_http_url(raw_url: str) -> str:
	raw_url = (raw_url or "").strip()
	if not raw_url:
		raise ValueError("URL is required")

	parsed = urlparse(raw_url)
	if parsed.scheme not in {"http", "https"}:
		raise ValueError("Only http/https URLs are allowed")
	if not parsed.netloc:
		raise ValueError("Invalid URL")
	if parsed.username or parsed.password:
		raise ValueError("URLs with embedded credentials are not allowed")

	host = (parsed.hostname or "").strip().lower()
	if not host:
		raise ValueError("Invalid host")
	if host in {"localhost"}:
		raise ValueError("Host not allowed")
	if host.endswith((".local", ".internal", ".cluster.local", ".svc")):
		raise ValueError("Host not allowed")

	if parsed.port and parsed.port not in {80, 443}:
		raise ValueError("Only ports 80 and 443 are allowed")

	port = parsed.port or (443 if parsed.scheme == "https" else 80)
	try:
		resolved = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
	except socket.gaierror as exc:
		raise ValueError(f"Failed to resolve host: {host}") from exc

	for _family, _socktype, _proto, _canonname, sockaddr in resolved:
		ip = sockaddr[0]
		ip_obj = ipaddress.ip_address(ip)
		if (
			ip_obj.is_private
			or ip_obj.is_loopback
			or ip_obj.is_link_local
			or ip_obj.is_reserved
			or ip_obj.is_multicast
		):
			raise ValueError("Host resolves to a non-public IP address")

	return raw_url


def _scrape_staff_cards_avada(html_text: str, source_url: str) -> list[dict[str, str]]:
	soup = BeautifulSoup(html_text, "html.parser")

	out: list[dict[str, str]] = []
	seen: set[str] = set()
	for card in soup.select("li.fusion-post-cards-grid-column"):
		name_el = card.find("h3")
		if not name_el:
			continue
		full_name = re.sub(r"\s+", " ", name_el.get_text(" ", strip=True)).strip()
		if not full_name:
			continue

		title_el = card.find("p")
		title = re.sub(r"\s+", " ", title_el.get_text(" ", strip=True)).strip() if title_el else ""

		email = ""
		mail_el = card.find("a", href=True)
		if mail_el:
			href = html.unescape(str(mail_el.get("href") or "")).strip()
			if href.lower().startswith("mailto:"):
				email = href.split(":", 1)[1].split("?", 1)[0].strip()

		key = (email.lower() if email else f"{full_name}|{title}".lower()).strip()
		if not key or key in seen:
			continue
		seen.add(key)

		out.append(
			{
				"full_name": full_name,
				"title": title,
				"email": email,
				"source_url": source_url,
			}
		)

	out.sort(key=lambda r: (r["title"].lower(), r["full_name"].lower()))
	return out


@frappe.whitelist()
def scrape_staff_directory(url: str) -> dict[str, Any]:
	_require_internal_permission()

	source_url = _validate_public_http_url(url)

	try:
		resp = requests.get(
			source_url,
			headers={
				"User-Agent": "FTG Lead Radar (due diligence)",
				"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
			},
			timeout=30,
		)
	except Exception:
		frappe.throw("Failed to fetch staff page.")

	if resp.status_code != 200:
		frappe.throw(f"Staff page returned {resp.status_code}")

	content_type = (resp.headers.get("Content-Type") or "").lower()
	if "html" not in content_type and "xml" not in content_type:
		frappe.throw("Staff page is not HTML.")

	html_text = resp.text or ""
	if len(html_text) > 1_000_000:
		html_text = html_text[:1_000_000]

	staff = _scrape_staff_cards_avada(html_text, source_url=resp.url or source_url)
	return {"ok": True, "source_url": resp.url or source_url, "count": len(staff), "staff": staff}


@frappe.whitelist()
def publish_config() -> dict[str, Any]:
	_require_internal_permission()

	settings = frappe.get_single("Lead Radar Settings")
	endpoint = _publisher_endpoint(settings.publisher_url)

	payload = build_config_payload()
	if not payload["sources"]:
		frappe.throw("Create at least one Lead Radar Source before publishing.")
	if not payload["keyword_packs"]:
		frappe.throw("Create at least one Lead Radar Keyword Pack before publishing.")
	ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
	payload["message"] = f"Lead Radar publish from ERPNext ({frappe.local.site}) {ts}"

	try:
		resp = requests.post(endpoint, json=payload, timeout=30)
	except Exception:
		frappe.throw("Failed to contact publisher service.")

	if not resp.ok:
		frappe.throw(f"Publisher failed ({resp.status_code}): {resp.text}")

	try:
		data = resp.json()
	except Exception:
		frappe.throw(f"Publisher returned non-JSON response: {resp.text}")

	if not data.get("ok"):
		frappe.throw(f"Publisher error: {data}")

	settings.db_set("last_publish_commit_sha", data.get("commit_sha") or "")
	settings.db_set("last_publish_commit_url", data.get("commit_url") or "")
	settings.db_set("last_published_on", frappe.utils.now_datetime())

	return {"commit_sha": data.get("commit_sha"), "commit_url": data.get("commit_url")}
