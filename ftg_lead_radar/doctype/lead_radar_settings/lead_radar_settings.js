frappe.ui.form.on("Lead Radar Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Publish GitOps Config"), async () => {
			const r = await frappe.call({
				method: "ftg_lead_radar.api.publish_config",
				freeze: true,
			});

			const commit_url = r?.message?.commit_url;
			if (commit_url) {
				frappe.msgprint({
					title: __("Published"),
					message: __("Committed to GitHub: {0}", [
						`<a href="${encodeURI(commit_url)}" target="_blank" rel="noreferrer">${commit_url}</a>`,
					]),
					indicator: "green",
				});
			} else {
				frappe.msgprint({
					title: __("Published"),
					message: __("Committed to GitHub."),
					indicator: "green",
				});
			}

			await frm.reload_doc();
		});
	},
});

