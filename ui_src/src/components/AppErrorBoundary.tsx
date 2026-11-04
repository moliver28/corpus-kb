import { Button } from "antd";
import {
  Component,
  useEffect,
  useRef,
  useState,
  type ErrorInfo,
  type ReactNode,
} from "react";
import pkg from "../../package.json";
import { strings } from "../strings";
import JsonViewer from "./JsonViewer";
import StatusBanner from "./StatusBanner";

/**
 * The app-level crash net: one render error in any card degrades to
 * this boundary's fallback instead of a white screen. The fallback
 * reuses the ErrorPanel pattern — plain headline, the raw diagnostic,
 * and a copy button for the bug report.
 */

/** The diagnostic blob: one plain object with everything a bug report needs. */
interface DiagnosticBlob {
  spa: string;
  version: string;
  at: string;
  message: string;
  stack: string;
  componentStack: string;
}

function diagnosticBlob(error: Error, componentStack: string | null): DiagnosticBlob {
  return {
    spa: "corpus-kb-ui",
    version: pkg.version,
    at: new Date().toISOString(),
    message: error.message,
    stack: error.stack ?? "",
    componentStack: componentStack ?? "",
  };
}

/** The boundary fallback: ErrorPanel's pattern for a thrown render error. */
function BoundaryFallback({
  error,
  componentStack,
  onRetry,
}: {
  error: Error;
  componentStack: string | null;
  onRetry: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<number | null>(null);

  // The copied label flips back on its own; the timer never survives us.
  useEffect(
    () => () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    },
    [],
  );

  function copy(): void {
    // jsdom and hardened browsers expose no clipboard: the button stays
    // honest (never promises what it cannot do) and never throws.
    navigator.clipboard
      ?.writeText(JSON.stringify(diagnosticBlob(error, componentStack), null, 2))
      .then(() => {
        setCopied(true);
        timerRef.current = window.setTimeout(() => setCopied(false), 2000);
      })
      .catch(() => undefined);
  }

  return (
    <div className="ui-stack" role="alert">
      <StatusBanner kind="error">{strings.boundaryTitle}</StatusBanner>
      <p>{strings.boundaryBody}</p>
      <JsonViewer value={diagnosticBlob(error, componentStack)} />
      <div className="ui-actions">
        <Button onClick={copy}>
          {copied ? strings.errorCopied : strings.errorCopyDiagnostic}
        </Button>
        <Button onClick={onRetry}>{strings.retryLabel}</Button>
      </div>
    </div>
  );
}

interface BoundaryState {
  error: Error | null;
  componentStack: string | null;
}

/** Catch any render error under the router; retry re-mounts the tree. */
export default class AppErrorBoundary extends Component<
  { children: ReactNode },
  BoundaryState
> {
  state: BoundaryState = { error: null, componentStack: null };

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error, componentStack: null };
  }

  componentDidCatch(_error: Error, info: ErrorInfo): void {
    this.setState({ componentStack: info.componentStack ?? null });
  }

  render() {
    const { error, componentStack } = this.state;
    if (error !== null) {
      return (
        <BoundaryFallback
          error={error}
          componentStack={componentStack}
          onRetry={() => this.setState({ error: null, componentStack: null })}
        />
      );
    }
    return this.props.children;
  }
}
