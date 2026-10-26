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
}: {
  title: string;
  description: string;
  confirmLabel?: string;
  loading?: boolean;
  disabled?: boolean;
  onConfirm: () => void;
}) {
  const [open, setOpen] = useState(false);
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
        open={open}
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
