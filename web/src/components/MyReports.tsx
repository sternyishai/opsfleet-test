import { useEffect, useState } from "react";
import { api, type GetReportOk, type ListReportsOutput } from "../api/client";

export function MyReports(props: { userId: string; refresh: number }) {
  const [listed, setListed] = useState<ListReportsOutput | null>(null);
  const [open, setOpen] = useState<GetReportOk | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setOpen(null);
    api.reports(props.userId).then(
      (out) => {
        setListed(out);
        setError(null);
      },
      (e: Error) => setError(e.message),
    );
  }, [props.userId, props.refresh]);

  const show = (reportId: string) =>
    api.report(props.userId, reportId).then(setOpen, (e: Error) => setError(e.message));

  return (
    <section>
      <h2>My reports</h2>
      {error && <p className="error">{error}</p>}
      {listed && listed.reports.length === 0 && <p>You have no saved reports yet.</p>}
      <ol className="reports">
        {listed?.reports.map((r) => (
          <li key={r.report_id}>
            <button onClick={() => show(r.report_id)} disabled={!r.accessible}>
              {r.title}
            </button>
            <span className="date"> {new Date(r.created_at).toLocaleDateString()}</span>
            {!r.accessible && <span className="reason"> (covers Brands you no longer have)</span>}
          </li>
        ))}
      </ol>
      {open && (
        <article className="report">
          <h3>{open.report.title}</h3>
          <p className="date">{open.label}</p>
          <pre>{open.report.markdown}</pre>
          <button onClick={() => setOpen(null)}>Close</button>
        </article>
      )}
    </section>
  );
}
