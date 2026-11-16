import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** The agent's Markdown (tables included). Already through the output PII filter. */
export function Markdown(props: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{props.text}</ReactMarkdown>
    </div>
  );
}
