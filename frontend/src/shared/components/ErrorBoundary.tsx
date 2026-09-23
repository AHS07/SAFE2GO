/** Per-feature error boundary: a clear message and a retry, never a blank screen. */
import React from "react";
import Button from "@/shared/ui/Button";
import Notice from "@/shared/ui/Notice";

interface Props {
  feature: string;
  children: React.ReactNode;
}

interface State {
  failed: boolean;
}

export default class ErrorBoundary extends React.Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error): void {
    console.error(`${this.props.feature} failed`, error);
  }

  render(): React.ReactNode {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="mx-auto w-full max-w-[1780px] px-4 py-4 lg:px-6">
        <Notice tone="critical" role="alert">
          <p>{this.props.feature} could not be shown.</p>
          <Button onClick={() => this.setState({ failed: false })}>
            Try again
          </Button>
        </Notice>
      </div>
    );
  }
}
