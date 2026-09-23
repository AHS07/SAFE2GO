import React, { useState } from "react";
import ErrorNotice from "@/shared/components/ErrorNotice";
import Button from "@/shared/ui/Button";
import Notice from "@/shared/ui/Notice";
import type { QuizQuestion, QuizResultResponse } from "@/shared/types/api";

interface Props {
  questions: QuizQuestion[];
  onSubmit: (answers: number[]) => Promise<QuizResultResponse>;
}

function optionClass(result: QuizResultResponse | null, questionIndex: number, optionIndex: number, chosen: boolean): string {
  const base = "flex min-h-12 cursor-pointer items-center gap-3 rounded-md border px-4 py-2 text-sm";
  if (!result) {
    return `${base} ${chosen ? "border-brand bg-brand/10 text-white" : "border-line bg-well text-slate-200 hover:border-line-strong"}`;
  }
  if (optionIndex === result.questions[questionIndex].correct_option) {
    return `${base} border-emerald-500 bg-emerald-500/10 text-white`;
  }
  if (chosen) return `${base} border-red-500 bg-red-500/10 text-white`;
  return `${base} border-line bg-well text-slate-400`;
}

export default function QuizForm({ questions, onSubmit }: Props): React.ReactElement {
  const [answers, setAnswers] = useState<(number | null)[]>(() => questions.map(() => null));
  const [result, setResult] = useState<QuizResultResponse | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const complete = answers.every((a) => a !== null);

  const choose = (questionIndex: number, optionIndex: number) => {
    setAnswers((prev) => prev.map((a, i) => (i === questionIndex ? optionIndex : a)));
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!complete) return;
    setSubmitting(true);
    setError(null);
    try {
      setResult(await onSubmit(answers as number[]));
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  };

  const retake = () => {
    setAnswers(questions.map(() => null));
    setResult(null);
  };

  return (
    <form className="space-y-5" onSubmit={submit}>
      {questions.map((q, qi) => (
        <fieldset key={qi} className="space-y-2" disabled={result !== null}>
          <legend className="mb-2 font-display text-lg font-bold text-white">
            {qi + 1}. {q.question}
          </legend>
          {q.options.map((option, oi) => (
            <label key={oi} className={optionClass(result, qi, oi, answers[qi] === oi)}>
              <input
                type="radio"
                name={`question-${qi}`}
                checked={answers[qi] === oi}
                onChange={() => choose(qi, oi)}
                className="h-5 w-5 shrink-0 accent-[#ffcd11]"
              />
              <span>{option}</span>
            </label>
          ))}
          {result && <p className="text-sm text-slate-400">{result.questions[qi].explanation}</p>}
        </fieldset>
      ))}

      {error !== null && <ErrorNotice error={error} />}

      {result ? (
        <Notice tone={result.passed ? "ok" : "warning"} role="status">
          <p>
            Score {result.score}%. {result.passed ? "Module passed." : `${result.pass_pct}% is needed to pass.`}
          </p>
          {!result.passed && (
            <Button onClick={retake}>
              Retake quiz
            </Button>
          )}
        </Notice>
      ) : (
        <Button type="submit" variant="primary" disabled={!complete || submitting}>
          {submitting ? "Submitting" : "Submit answers"}
        </Button>
      )}
    </form>
  );
}
