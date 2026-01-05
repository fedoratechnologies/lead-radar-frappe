from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import frappe
import requests


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
		fields=["source_id", "enabled", "source_type", "source_name", "url", "tags"],
		order_by="modified desc",
	):
		sources.append(
			{
				"id": row.source_id,
				"enabled": bool(row.enabled),
				"type": row.source_type or "rss",
				"name": row.source_name,
				"url": row.url,
				"tags": _parse_tags(row.tags),
			}
		)

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


@frappe.whitelist()
def publish_config() -> dict[str, Any]:
	frappe.only_for("System Manager")

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
