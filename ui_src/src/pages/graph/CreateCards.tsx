import { Button, Card } from "antd";
import { useState } from "react";
import LabeledField from "../../components/LabeledField";
import StatusBanner from "../../components/StatusBanner";
import { useFormWrite } from "../../hooks/useFormWrite";
import { strings } from "../../strings/graph";
import EntityPicker from "./EntityPicker";

/**
 * The workbench's two create forms (Phase-2 Wave 4): one entity, one
 * relation. Both writes run through the form-to-write seam hook
 * (useFormWrite) over the add-entity / add-relation manifest ops, and
 * every ok answer refreshes the caller's window. The page owns the
 * write epoch, so the pickers re-read after an entity create too (the
 * Wave-4 review's P1-1). The relation's endpoints are picked through
 * the entity picker — never typed.
 */

/** The default entity type the catalogue row pins. */
const DEFAULT_ENTITY_TYPE = "concept";
/** The default relation type the catalogue row pins. */
const DEFAULT_RELATION_TYPE = "related_to";

export function CreateEntityCard({ refresh }: { refresh: () => void }) {
  const [name, setName] = useState("");
  const [entityType, setEntityType] = useState(DEFAULT_ENTITY_TYPE);
  const [nameError, setNameError] = useState("");
  const [typeError, setTypeError] = useState("");
  const { running, error, saved, rootRef, submit } = useFormWrite(
    "add-entity",
    refresh,
    strings.graphCreateSaved,
  );

  async function onSubmit(): Promise<void> {
    const trimmedName = name.trim();
    const trimmedType = entityType.trim();
    const nextNameError = trimmedName === "" ? strings.graphNameRequired : "";
    const nextTypeError = trimmedType === "" ? strings.graphTypeRequired : "";
    setNameError(nextNameError);
    setTypeError(nextTypeError);
    if (nextNameError !== "" || nextTypeError !== "") {
      return;
    }
    const ok = await submit({ name: trimmedName, entity_type: trimmedType });
    if (ok) {
      setName("");
      setEntityType(DEFAULT_ENTITY_TYPE);
    }
  }

  return (
    <Card title={strings.graphCreateTitle}>
      <div className="ui-stack">
        <StatusBanner kind="info">{strings.graphCreateBody}</StatusBanner>
        <div ref={rootRef} className="ui-stack">
          <LabeledField
            label={strings.graphEntityNameLabel}
            value={name}
            onChange={(next) => {
              setName(next);
              if (nameError !== "") {
                setNameError("");
              }
            }}
            error={nameError}
            required
            onPressEnter={() => void onSubmit()}
          />
          <LabeledField
            label={strings.graphEntityTypeLabel}
            value={entityType}
            onChange={(next) => {
              setEntityType(next);
              if (typeError !== "") {
                setTypeError("");
              }
            }}
            error={typeError}
            required
            machine
            onPressEnter={() => void onSubmit()}
          />
        </div>
        {error !== null && <StatusBanner kind="warn">{error}</StatusBanner>}
        {saved !== null && <StatusBanner kind="ok">{saved}</StatusBanner>}
        <div className="ui-actions">
          <Button type="primary" loading={running} onClick={() => void onSubmit()}>
            {strings.graphCreateTitle}
          </Button>
        </div>
      </div>
    </Card>
  );
}

export function CreateRelationCard({
  refresh,
  reloadKey = 0,
}: {
  refresh: () => void;
  /** Bumped by the page after every ok write; the pickers re-read. */
  reloadKey?: number;
}) {
  const [sourceId, setSourceId] = useState("");
  const [targetId, setTargetId] = useState("");
  const [relationType, setRelationType] = useState(DEFAULT_RELATION_TYPE);
  const [sourceError, setSourceError] = useState("");
  const [targetError, setTargetError] = useState("");
  const [typeError, setTypeError] = useState("");
  const { running, error, saved, rootRef, submit } = useFormWrite(
    "add-relation",
    refresh,
    strings.graphRelationSaved,
  );

  async function onSubmit(): Promise<void> {
    const trimmedType = relationType.trim();
    const nextSourceError = sourceId === "" ? strings.graphSourceRequired : "";
    const nextTargetError = targetId === "" ? strings.graphTargetRequired : "";
    const nextTypeError = trimmedType === "" ? strings.graphRelationTypeRequired : "";
    setSourceError(nextSourceError);
    setTargetError(nextTargetError);
    setTypeError(nextTypeError);
    if (nextSourceError !== "" || nextTargetError !== "" || nextTypeError !== "") {
      return;
    }
    const ok = await submit({
      source_entity_id: sourceId,
      target_entity_id: targetId,
      relation_type: trimmedType,
    });
    if (ok) {
      setSourceId("");
      setTargetId("");
      setRelationType(DEFAULT_RELATION_TYPE);
    }
  }

  return (
    <Card title={strings.graphRelationTitle}>
      <div className="ui-stack">
        <StatusBanner kind="info">{strings.graphRelationBody}</StatusBanner>
        <div ref={rootRef} className="ui-stack">
          <EntityPicker
            label={strings.graphSourceLabel}
            value={sourceId}
            onChange={(next) => {
              setSourceId(next);
              if (sourceError !== "") {
                setSourceError("");
              }
            }}
            error={sourceError}
            reloadKey={reloadKey}
          />
          <EntityPicker
            label={strings.graphTargetLabel}
            value={targetId}
            onChange={(next) => {
              setTargetId(next);
              if (targetError !== "") {
                setTargetError("");
              }
            }}
            error={targetError}
            excludeId={sourceId === "" ? undefined : sourceId}
            reloadKey={reloadKey}
          />
          <LabeledField
            label={strings.graphRelationTypeLabel}
            value={relationType}
            onChange={(next) => {
              setRelationType(next);
              if (typeError !== "") {
                setTypeError("");
              }
            }}
            error={typeError}
            required
            machine
            onPressEnter={() => void onSubmit()}
          />
        </div>
        {error !== null && <StatusBanner kind="warn">{error}</StatusBanner>}
        {saved !== null && <StatusBanner kind="ok">{saved}</StatusBanner>}
        <div className="ui-actions">
          <Button type="primary" loading={running} onClick={() => void onSubmit()}>
            {strings.graphRelationTitle}
          </Button>
        </div>
      </div>
    </Card>
  );
}
