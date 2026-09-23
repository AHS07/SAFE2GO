/** Compact manual search for the cockpit. Results come from the edge TF-IDF index only. */
import React, { useState } from "react";
import { Link } from "react-router-dom";
import { operatorApi } from "@/api/operator";
import { useSession } from "@/features/auth/session";
import ErrorNotice from "@/shared/components/ErrorNotice";
import Button from "@/shared/ui/Button";
import Icon from "@/shared/ui/Icon";
import Panel from "@/shared/ui/Panel";
import type { ManualSearchHit } from "@/shared/types/api";

export default function ManualQuickSearch(): React.ReactElement {
  const { session } = useSession();
  const [query, setQuery] = useState("");
  const [hit, setHit] = useState<ManualSearchHit | null | undefined>(undefined);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const search = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!query.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const response = await operatorApi.searchManuals(session?.token ?? "", query.trim());
      setHit(response.results[0] ?? null);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title="Manual search"
      icon="menu_book"
      footer={
        <>
          <span>Searches the sample machine manuals on this unit</span>
          <Link to="/manuals" className="font-bold text-brand hover:underline">
            Open manuals
          </Link>
        </>
      }
    >
      <form onSubmit={search} className="flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search the manuals"
          placeholder="For example: check hydraulic oil level"
          className="min-h-12 flex-1 rounded border border-line bg-well px-3 text-sm text-slate-100 outline-none focus:border-brand focus:ring-1 focus:ring-brand"
        />
        <Button type="submit" variant="primary" disabled={busy || !query.trim()}>
          <Icon name="search" className="text-base" /> {busy ? "Searching" : "Search"}
        </Button>
      </form>
      <div className="mt-3">
        {error !== null && <ErrorNotice error={error} />}
        {hit === null && <p className="text-sm text-slate-400">No matching section. Try different words.</p>}
        {hit && (
          <article className="rounded-lg border border-line bg-well p-3.5">
            <p className="font-mono text-[11px] font-bold uppercase text-brand">{hit.manual_title}</p>
            <h3 className="mt-1 font-display text-base font-bold uppercase text-white">{hit.section_title}</h3>
            <p className="mt-1.5 line-clamp-4 text-sm leading-relaxed text-slate-300">{hit.text}</p>
          </article>
        )}
      </div>
    </Panel>
  );
}
