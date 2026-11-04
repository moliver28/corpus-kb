import { Button, Modal } from "antd";
import { useState } from "react";
import { strings } from "../strings";

/** The gate before a long or destructive op: the run starts only on confirm. */
export default function ConfirmGate({
  title,
  description,
  confirmLabel,
  loading = false,
  disabled = false,
  onConfirm,
  open,
  onOpenChange,
}: {
  title: string;
  description: string;
  confirmLabel?: string;
  loading?: boolean;
  disabled?: boolean;
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
  return (
    <>
      <Button
        type="primary"
        loading={loading}
        disabled={disabled}
        onClick={() => setOpen(true)}
      >
        {confirmLabel ?? strings.runnerRunLabel}
      </Button>
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
