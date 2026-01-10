from __future__ import annotations

import frappe

no_cache = 1


def get_context(context: dict) -> dict:
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/due-diligence"
		raise frappe.Redirect

	if not (frappe.has_role("COS") or frappe.has_role("System Manager")):
		frappe.throw("Not permitted.", frappe.PermissionError)

	context["title"] = "Due Diligence"
	context["default_staff_url"] = "https://holytrinity-hs.org/about/staff/"

	project_name = "Client - Holy Trinity High School - AD Provisioning"
	hths_project = frappe.db.get_value("Project", {"project_name": project_name}, "name")
	context["hths_project_id"] = hths_project
	context["hths_project_url"] = f"/app/project/{hths_project}" if hths_project else ""

	return context

