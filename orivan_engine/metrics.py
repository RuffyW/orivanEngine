"""Small Prometheus exporter for aggregates and a bounded current lead table."""

from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread


def label(value):
    """Escape an untrusted string for the Prometheus text exposition format."""
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", " ")


def render_metrics(db, timezone_name, engine):
    groups, totals, settings, companies = db.metrics_snapshot(timezone_name)
    values = {
        "orivan_leads": totals["leads"],
        "orivan_priority_leads": totals["priority"],
        "orivan_leads_without_website": totals["without_website"],
        "orivan_drafts": totals["drafts"],
        "orivan_leads_discovered_today": totals["new_today"],
        "orivan_leads_checked_today": totals["checked_today"],
        "orivan_search_running": int(engine.run_lock.locked()),
        "orivan_last_search_new": int(settings.get("last_search_new", 0)),
        "orivan_last_search_checked": int(settings.get("last_search_checked", 0)),
        "orivan_last_search_errors": int(settings.get("last_search_errors", 0)),
    }
    last_run = settings.get("last_run")
    values["orivan_last_search_timestamp_seconds"] = (
        int(datetime.fromisoformat(last_run).timestamp()) if last_run else 0
    )
    lines = []
    for name, value in values.items():
        lines.extend((f"# TYPE {name} gauge", f"{name} {int(value or 0)}"))
    lines.append("# TYPE orivan_leads_by_status_profile gauge")
    for group in groups:
        # Status values are validated by the bot; profiles come from fixed search profiles.
        status = group["status"]
        profile = group["profile"]
        if status.isidentifier() and profile.isidentifier():
            lines.append(f'orivan_leads_by_status_profile{{status="{status}",profile="{profile}"}} {group["amount"]}')
    lines.append("# TYPE orivan_company_priority gauge")
    for company in companies:
        labels = {
            "lead_id": company["id"], "name": company["name"][:200],
            "profile": company["profile"], "status": company["status"],
            "website": (company["website"] or "")[:240],
        }
        attrs = ",".join(f'{key}="{label(value)}"' for key, value in labels.items())
        lines.append(f'orivan_company_priority{{{attrs}}} {company["score"]}')
    return ("\n".join(lines) + "\n").encode("utf-8")


def start_metrics(db, timezone_name, engine):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/metrics":
                self.send_error(404)
                return
            body = render_metrics(db, timezone_name, engine)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("0.0.0.0", 9108), Handler)
    Thread(target=server.serve_forever, daemon=True, name="metrics-http").start()
    return server
