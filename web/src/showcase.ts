// The design material the navigator shows next to the prototype. Vite imports each file from
// docs/ (the one copy in the repo) and fingerprints it into dist/assets at build time.
import architecture from "../../docs/hld/diagrams/architecture.png";
import agentFlow from "../../docs/hld/diagrams/agent-flow.png";
import dataFlow from "../../docs/hld/diagrams/data-flow.png";
import storyMap from "../../docs/hld/diagrams/story-map.png";
import goldenTrioFlow from "../../docs/hld/diagrams/golden-trio-flow.png";
import agentGraph from "../../docs/lld/diagrams/agent-graph.png";
import sysflow from "../../docs/films/sysflow.mp4";
import agentflow from "../../docs/films/agentflow.mp4";
import piiflow from "../../docs/films/piiflow.mp4";
import sysflowPoster from "../../docs/films/sysflow-poster.jpg";
import agentflowPoster from "../../docs/films/agentflow-poster.jpg";
import piiflowPoster from "../../docs/films/piiflow-poster.jpg";

// `poster` is a film's cover: the frame its .film.json names, cut with ffmpeg.
export type Item = { title: string; caption: string; src: string; source: string; poster?: string };

export const DIAGRAMS: Item[] = [
  {
    title: "Reference architecture",
    caption: "The channels, the agent, its ports and the stores behind them.",
    src: architecture,
    source: "HLD 00 · Overview",
  },
  {
    title: "Agent flow",
    caption: "What each step of a turn reads and writes.",
    src: agentFlow,
    source: "HLD 01 · Architecture",
  },
  {
    title: "Data flow",
    caption: "What is stored, and when it is written.",
    src: dataFlow,
    source: "HLD 01 · Architecture",
  },
  {
    title: "Story map",
    caption: "Every flow, every component, every store.",
    src: storyMap,
    source: "HLD 01 · Architecture",
  },
  {
    title: "Golden Trio flow",
    caption: "From an analyst report to a stored Trio, and how a question finds it.",
    src: goldenTrioFlow,
    source: "HLD 02 · ADR 0005",
  },
  {
    title: "The compiled graph",
    caption: "Every node, what it reads and calls, and what it writes.",
    src: agentGraph,
    source: "LLD 03 · Graph state",
  },
];

export const FILMS: Item[] = [
  {
    title: "Where the data goes",
    caption: "Where each piece of data goes, and what is kept.",
    src: sysflow,
    poster: sysflowPoster,
    source: "1:50",
  },
  {
    title: "How the agent answers a question",
    caption: "Each step of a turn, from the question to the answer.",
    src: agentflow,
    poster: agentflowPoster,
    source: "1:38",
  },
  {
    title: "PII: never in, never out",
    caption: "How personal data is kept out of every turn.",
    src: piiflow,
    poster: piiflowPoster,
    source: "1:42",
  },
];
