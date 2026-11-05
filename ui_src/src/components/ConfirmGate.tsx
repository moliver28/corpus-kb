import { Button, Modal } from "antd";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { strings } from "../strings";

/**
 * The gate props split on the opener: with a trigger the caller owns
 * the opener control (its loading and disabled state included), so the
 * default-button props would be silent dead weight on a destructive op
 * — the never-typed members turn that misuse into a compile error.
 */
type ConfirmGateProps = {
  title: string;
  description: string;
  onConfirm: () => void;
  /** Controlled open state; leave unset and the gate owns it. */
  open?: boolean;
  /** Reports every open change (open button, OK, cancel). */
  onOpenChange?: (open: boolean) => void;
} & (
  | {
      /** The caller's opener (a row button); it replaces the default button. */
      trigger: ReactNode;
      confirmLabel?: never;
      loading?: never;
      disabled?: never;
    }
  | {
      trigger?: undefined;
      confirmLabel?: string;
      loading?: boolean;
      disabled?: boolean;
    }
);

/** The gate before a long or destructive op: the run starts only on confirm. */
export default function ConfirmGate(props: ConfirmGateProps) {
  const { title, description, onConfirm, open, onOpenChange } = props;
  const [innerOpen, setInnerOpen] = useState(false);
  const isOpen = open ?? innerOpen;
  // One confirm per open cycle: a second OK while the caller keeps the
  // dialog open (the request still in flight) is refused.
  const confirmedRef = useRef(false);
  useEffect(() => {
    if (!isOpen) {
      confirmedRef.current = false;
    }
  }, [isOpen]);
  const setOpen = (next: boolean): void => {
    if (open === undefined) {
      setInnerOpen(next);
    }
    onOpenChange?.(next);
  };
  const opener =
    props.trigger === undefined ? (
      <Button
        type="primary"
        loading={props.loading ?? false}
        disabled={props.disabled ?? false}
        onClick={() => setOpen(true)}
      >
        {props.confirmLabel ?? strings.runnerRunLabel}
      </Button>
    ) : (
      // The opener control (normally an antd Button) keeps its own
      // keyboard activation; the click bubbles to this span.
      <span onClick={() => setOpen(true)}>{props.trigger}</span>
    );
  return (
    <>
      {opener}
      {/* antd 6's declarative Modal has no autoFocusButton: the OK button
          is not auto-focused, so a stray Enter cannot confirm the gate.
          ConfirmGate.test pins that behavior. */}
      <Modal
        title={title}
        open={isOpen}
        onOk={() => {
          if (confirmedRef.current) {
            return;
          }
          confirmedRef.current = true;
          setOpen(false);
          onConfirm();
        }}
        onCancel={() => setOpen(false)}
        okText={strings.confirmConfirm}
        cancelText={strings.confirmCancel}
      >
        <p>{description}</p>
      </Modal>
    </>
  );
}
