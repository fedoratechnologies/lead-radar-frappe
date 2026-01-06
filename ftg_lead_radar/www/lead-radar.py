from __future__ import annotations

import frappe

no_cache = 1


def get_context(context: dict) -> dict:
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/lead-radar"
		raise frappe.Redirect

	if not (frappe.has_role("COS") or frappe.has_role("System Manager")):
		frappe.throw("Not permitted.", frappe.PermissionError)

	settings = frappe.get_single("Lead Radar Settings")
	context["title"] = "Lead Radar"
	context["settings"] = settings
	context["sources_count"] = frappe.db.count("Lead Radar Source")
	context["packs_count"] = frappe.db.count("Lead Radar Keyword Pack")
	context["last_published_on"] = settings.last_published_on
	context["last_publish_commit_sha"] = settings.last_publish_commit_sha
	context["last_publish_commit_url"] = settings.last_publish_commit_url
	return context
