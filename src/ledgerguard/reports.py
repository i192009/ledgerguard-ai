import io
import json
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy import select

from ledgerguard.db import public
from ledgerguard.models import ApprovalRequest, Report, ToolExecution, now
from ledgerguard.security import audit


async def generate_report(db, principal, run_id, state):
    from ledgerguard.approvals import details

    existing = await db.scalar(
        select(Report).where(Report.tenant_id == principal.tenant_id, Report.run_id == run_id)
    )
    approvals = (
        await db.scalars(
            select(ApprovalRequest).where(
                ApprovalRequest.tenant_id == principal.tenant_id, ApprovalRequest.run_id == run_id
            )
        )
    ).all()
    calls = (
        await db.scalars(
            select(ToolExecution).where(
                ToolExecution.tenant_id == principal.tenant_id, ToolExecution.run_id == run_id
            )
        )
    ).all()
    content = {
        "generated_at": now().isoformat(),
        "request": state.get("request"),
        "plan": state.get("plan"),
        "transactions": state.get("transactions", {}),
        "risk": state.get("risk", {}),
        "reconciliation": state.get("reconciliation", {}),
        "narratives": state.get("narratives", {}),
        "approvals": [await details(db, principal, a.id) for a in approvals],
        "executions": state.get("executions", []),
        "tool_calls": [
            {"server": c.server, "tool": c.tool, "status": c.status, "duration_ms": c.duration_ms}
            for c in calls
        ],
        "limitations": [
            "Synthetic sandbox data only",
            "Risk indicators are not confirmed fraud or an AML determination",
            "Unapproved actions are never executed",
            "Totals are separated by currency; no FX conversion",
        ],
    }
    if existing:
        existing.content = content
        report = existing
    else:
        report = Report(
            tenant_id=principal.tenant_id,
            run_id=run_id,
            title="Daily Financial Operations Review",
            content=content,
        )
        db.add(report)
    await db.flush()
    audit(db, principal, "REPORT_GENERATED", report.id, {"run_id": run_id})
    return public(report)


def report_pdf(report):
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=(8.27 * inch, 11.69 * inch),
        rightMargin=42,
        leftMargin=42,
        topMargin=45,
        bottomMargin=45,
        title=report.title,
        author="LedgerGuard AI · Sandbox",
    )
    styles = getSampleStyleSheet()
    styles["Title"].textColor = colors.HexColor("#153048")
    story = [
        Paragraph("LEDGERGUARD AI", styles["Title"]),
        Paragraph(escape(report.title), styles["Heading1"]),
        Paragraph("SANDBOX OPERATIONS · Evidence-based review", styles["Normal"]),
        Spacer(1, 16),
    ]
    for section, value in report.content.items():
        story.append(Paragraph(escape(section.replace("_", " ").title()), styles["Heading2"]))
        rendered = (
            json.dumps(value, indent=2, ensure_ascii=True, default=str)
            if not isinstance(value, str)
            else value
        )
        # Paragraph wrapping supports arbitrarily long evidence, without truncating the report.
        for line in rendered.splitlines():
            story.append(Paragraph(escape(line), styles["Code"]))
        story.append(Spacer(1, 8))

    def footer(canvas, doc):
        canvas.setFont("Helvetica", 8)
        canvas.drawString(42, 25, f"LedgerGuard AI | Report {report.id} | Synthetic sandbox")
        canvas.drawRightString(550, 25, str(doc.page))

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
