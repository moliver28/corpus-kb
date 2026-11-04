import { Input } from "antd";
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ChangeEvent,
  type RefObject,
} from "react";

export interface LabeledFieldProps {
  /** Visible label text; doubles as the placeholder. */
  label: string;
  value: string;
  onChange: (value: string) => void;
  /** Inline error under the control; empty or undefined hides the slot. */
  error?: string;
  /** A machine value (a path, an id, JSON): no spellcheck, no autocomplete. */
  machine?: boolean;
  /** Multiline text (a TextArea) instead of one line. */
  multiline?: boolean;
  /** Marks the label with " *" and sets aria-required. */
  required?: boolean;
  /** The autofill name, normally the server field key. */
  name?: string;
  /** Width class from the ui-narrow tokens. */
  widthClass?: string;
  /** Press Enter to submit (single line only). */
  onPressEnter?: () => void;
  /** Test hook for the page tests. */
  testId?: string;
}

/**
 * The one labeled-input pattern: a visible label bound to its control
 * with htmlFor/id, an inline error slot under the control, and the
 * aria-invalid mark the first-error focus looks for. Machine values
 * (paths, ids) turn spellcheck and autocomplete off.
 */
export default function LabeledField({
  label,
  value,
  onChange,
  error,
  machine = false,
  multiline = false,
  required = false,
  name,
  widthClass = "ui-narrow",
  onPressEnter,
  testId,
}: LabeledFieldProps) {
  const id = useId();
  const errorId = `${id}-error`;
  const invalid = error !== undefined && error !== "";
  function pick(event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>): void {
    onChange(event.target.value);
  }
  return (
    <div className="ui-field">
      <label className="ui-field-label" htmlFor={id}>
        {label}
        {required ? " *" : ""}
      </label>
      {multiline ? (
        <Input.TextArea
          id={id}
          name={name}
          className={widthClass}
          value={value}
          onChange={pick}
          placeholder={label}
          aria-invalid={invalid || undefined}
          aria-describedby={invalid ? errorId : undefined}
          aria-required={required || undefined}
          spellCheck={machine ? false : undefined}
          autoComplete={machine ? "off" : undefined}
          data-testid={testId}
        />
      ) : (
        <Input
          id={id}
          name={name}
          className={widthClass}
          value={value}
          onChange={pick}
          placeholder={label}
          onPressEnter={onPressEnter}
          aria-invalid={invalid || undefined}
          aria-describedby={invalid ? errorId : undefined}
          aria-required={required || undefined}
          spellCheck={machine ? false : undefined}
          autoComplete={machine ? "off" : undefined}
          data-testid={testId}
        />
      )}
      {invalid && (
        <div className="ui-field-error" id={errorId} role="alert">
          {error}
        </div>
      )}
    </div>
  );
}

/**
 * The focus wiring for a form's failed submit: call `flag` when
 * validation fails; after the errors reach the DOM, the first control
 * marked aria-invalid inside `rootRef` gets focus. Attach `rootRef` to
 * the element that wraps every field of the form.
 */
export function useFirstErrorFocus(): {
  rootRef: RefObject<HTMLDivElement | null>;
  flag: () => void;
} {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [fails, setFails] = useState(0);
  const flag = useCallback(() => setFails((n) => n + 1), []);
  useEffect(() => {
    if (fails === 0) {
      return;
    }
    rootRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
  }, [fails]);
  return { rootRef, flag };
}
