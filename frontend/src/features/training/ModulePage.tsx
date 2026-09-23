import React from "react";
import { Link, useParams } from "react-router-dom";
import { useSession } from "@/features/auth/session";
import ErrorNotice from "@/shared/components/ErrorNotice";
import Markdown from "@/shared/components/Markdown";
import { useResource } from "@/shared/hooks/useResource";
import Icon from "@/shared/ui/Icon";
import Panel from "@/shared/ui/Panel";
import { trainingApi } from "./api";
import QuizForm from "./QuizForm";
import StatusBadge from "./StatusBadge";

export default function ModulePage(): React.ReactElement {
  const { moduleId = "" } = useParams();
  const { session } = useSession();
  const token = session?.token ?? "";
  const module = useResource(() => trainingApi.getModule(token, moduleId), [token, moduleId]);

  return (
    <main className="mx-auto w-full max-w-[900px] flex-1 space-y-4 px-4 py-4 lg:px-6">
      <Link
        to="/training"
        className="tactile-btn inline-flex min-h-12 items-center gap-1.5 rounded border border-line bg-panel-head px-4 font-display text-sm font-bold uppercase text-slate-100 hover:bg-raised"
      >
        <Icon name="arrow_back" className="text-base" /> Back to training
      </Link>

      {module.loading && !module.data && <p className="text-sm text-slate-400">Loading module.</p>}
      {module.error !== null && !module.data && <ErrorNotice error={module.error} onRetry={module.reload} />}

      {module.data && (
        <>
          <Panel
            title={`${module.data.duration_min} min module`}
            icon="menu_book"
            aside={<StatusBadge status={module.data.status} />}
          >
            <Markdown source={module.data.body_markdown} />
          </Panel>
          <Panel title="Quiz" icon="quiz">
            <QuizForm
              key={module.data.module_id}
              questions={module.data.quiz}
              onSubmit={(answers) => trainingApi.submitQuiz(token, moduleId, answers)}
            />
          </Panel>
        </>
      )}
    </main>
  );
}
