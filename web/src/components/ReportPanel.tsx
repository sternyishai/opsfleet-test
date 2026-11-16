import { useEffect, useState } from "react";
import { api, type GetReportOk } from "../api/client";
import { Icon } from "./Icon";
import { Markdown } from "./Markdown";

/** A saved report, opened beside the chat (`GET /api/reports/{id}` → `Views.report`). */
export function ReportPanel(props: { userId: string; reportId: string; onClose: () => void }) {
  const [report, setReport] = useState<GetReportOk | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setReport(null);
    api.report(props.userId, props.reportId).then(setReport, (e: Error) => setError(e.message));
  }, [props.userId, props.reportId]);

  return (
    <aside className="panel">
      <header>
        <div>
          <div className="eyebrow">Saved report</div>
          <h2>{report?.report.title ?? (error ? "Not available" : "Loading…")}</h2>
          {report && <span className="muted">{report.label}</span>}
        </div>
        <button className="icon-button" onClick={props.onClose} aria-label="Close">
          <Icon name="x" />
        </button>
      </header>
      <div className="panel-body">
        {error && <p className="error-text">{error}</p>}
        {report && <Markdown text={report.report.markdown} />}
      </div>
    </aside>
  );
}
