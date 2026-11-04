import { Button, Modal } from "antd";
import { useState, type ReactNode } from "react";
import { strings } from "../strings";

/** The gate before a long or destructive op: the run starts only on confirm. */
export default function ConfirmGate({
  title,
  description,
  confirmLabel,
  loading = false,
  disabled = false,
  trigger,
  onConfirm,
  open,
  onOpenChange,
}: {
  title: string;
  description: string;
  confirmLabel?: string;
  loading?: boolean;
  disabled?: boolean;
  /** The caller's opener (a row button); it replaces the default button. */
  trigger?: ReactNode;
  onConfirm: () => void;
  /** Controlled open state; leave unset and the gate owns it. */
  open?: boolean;
  /** Reports every open change (open button, OK, cancel). */
  onOpenChange?: (open: boolean) => void;
}) {
  const [innerOpen, setInnerOpen] = useState(false);
  const isOpen = open ?? innerOpen;
  const setOpen = (next: boolean): void => {
    if (open === undefined) {
      setInnerOpen(next);
    }
    onOpenChange?.(next);
  };
  const opener =
    trigger === undefined ? (
      <Button
        type="primary"
        loading={loading}
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        {confirmLabel ?? strings.runnerRunLabel}
      </Button>
    ) : (
      // The opener control (normally an antd Button) keeps its own
      // keyboard activation; the click bubbles to this span.
      <span onClick={() => setOpen(true)}>{trigger}</span>
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
