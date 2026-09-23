/**
 * Manual search (prd.md F9.1): TF-IDF retrieval over the sample manuals on the edge.
 * Explain (F9.2, F9.3) adds a plain-language answer from the cloud when it is
 * reachable; the matching sections are shown either way.
 */
import React, { useState } from "react";
import { operatorApi } from "@/api/operator";
import { useSession } from "@/features/auth/session";
import DemoControlBar from "@/features/demo-controls/DemoControlBar";
import ErrorNotice from "@/shared/components/ErrorNotice";
import Badge from "@/shared/ui/Badge";
import Button from "@/shared/ui/Button";
import Icon from "@/shared/ui/Icon";
import PageHeader from "@/shared/ui/PageHeader";
import Notice from "@/shared/ui/Notice";
import type { ExplainResponse, ManualSearchResponse } from "@/shared/types/api";
import { label } from "@/shared/utils/format";

const SUGGESTIONS = [
  "Hydraulic lockout lever",
  "Check tyre pressure",
  "Lower the stabilisers",
  "Working near power lines",
  "Refuelling procedure",
];

export default function ManualSearchPage(): React.ReactElement {
  const { session } = useSession();
  const [query, setQuery] = useState("");
  const [response, setResponse] = useState<ManualSearchResponse | ExplainResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [searching, setSearching] = useState(false);
  const [explaining, setExplaining] = useState(false);

  const search = async (term: string) => {
    if (!term.trim()) return;
    setSearching(true);
    setError(null);
    try {
      setResponse(await operatorApi.searchManuals(session?.token ?? "", term.trim()));
    } catch (err) {
      setError(err);
    } finally {
      setSearching(false);
    }
  };

  const explain = async () => {
    if (!query.trim()) return;
    setExplaining(true);
    setError(null);
    try {
      setResponse(await operatorApi.explain(session?.token ?? "", query.trim()));
    } catch (err) {
      setError(err);
    } finally {
      setExplaining(false);
    }
  };

  const explained = response && "notice" in response ? response : null;

  return (
    <div className="flex flex-1 flex-col space-y-4 pb-6">
      <DemoControlBar />
      <main className="mx-auto w-full max-w-[1780px] flex-1 space-y-4 px-4 lg:px-6">
        <PageHeader
          icon="menu_book"
          title="Manuals"
          subtitle="Search the sample machine manuals and site guide. Runs on the machine unit, so it works without the cloud."
        />

        <div className="space-y-3 rounded-lg border border-line bg-panel p-4 shadow-hud">
          <form
            role="search"
            onSubmit={(e) => {
              e.preventDefault();
              void search(query);
            }}
            className="flex flex-col gap-2 sm:flex-row"
          >
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search the manuals"
              placeholder="Ask a question or enter keywords"
              className="min-h-12 flex-1 rounded border border-line bg-well px-4 text-sm text-slate-100 outline-none focus:border-brand focus:ring-1 focus:ring-brand"
            />
            <Button type="submit" variant="primary" disabled={searching || explaining || !query.trim()}>
              <Icon name="search" className="text-base" /> {searching ? "Searching" : "Search"}
            </Button>
            <Button onClick={() => void explain()} disabled={searching || explaining || !query.trim()}>
              <Icon name="lightbulb" className="text-base" /> {explaining ? "Explaining" : "Explain"}
            </Button>
          </form>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 font-display text-[11px] font-bold uppercase text-slate-400">Try:</span>
            {SUGGESTIONS.map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => {
                  setQuery(item);
                  void search(item);
                }}
                className="tactile-btn min-h-12 rounded border border-line bg-well px-3 font-mono text-xs text-slate-300 hover:bg-raised-hover"
              >
                {item}
              </button>
            ))}
          </div>
        </div>

        {error !== null && <ErrorNotice error={error} onRetry={() => void search(query)} />}

        {explained?.explanation && (
          <section aria-label="Explanation" className="space-y-2 rounded-lg border border-brand/40 bg-panel p-4 shadow-hud">
            <h2 className="flex items-center gap-2 font-display text-lg font-bold uppercase text-white">
              <Icon name="lightbulb" className="text-brand" /> Explanation
            </h2>
            <p className="whitespace-pre-line text-sm leading-relaxed text-slate-200">{explained.explanation}</p>
            <p className="font-mono text-xs text-slate-400">
              Generated from the manual sections below. Check the sections before acting. Not for emergencies: use the Emergency button.
            </p>
          </section>
        )}
        {explained?.notice && (
          <Notice tone="info" role="status">
            <p>{explained.notice}</p>
          </Notice>
        )}

        {response && response.results.length === 0 && (
          <p className="rounded-lg border border-line bg-panel p-4 text-sm text-slate-400">
            No matching sections for "{response.query}". Try different words.
          </p>
        )}

        {response && response.results.length > 0 && (
          <section className="space-y-3" aria-label="Search results">
            {response.results.map((hit) => (
              <article key={`${hit.manual_id}-${hit.section_title}`} className="space-y-2 rounded-lg border border-line bg-panel p-4 shadow-hud">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-2">
                  <Badge tone="brand">{hit.manual_title}</Badge>
                  <span className="font-mono text-xs text-slate-400">
                    {hit.machine_type ? label(hit.machine_type) : "All machines"} · relevance {hit.score.toFixed(2)}
                  </span>
                </div>
                <h2 className="font-display text-lg font-bold uppercase text-white">{hit.section_title}</h2>
                <p className="text-sm leading-relaxed text-slate-300">{hit.text}</p>
              </article>
            ))}
          </section>
        )}
      </main>
    </div>
  );
}
