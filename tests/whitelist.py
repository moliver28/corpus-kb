"""Vulture whitelist (Wave 3 dead-code gate).

Scanned by the ``poe dead-code`` gate as an extra path:

    vulture src/ tests/whitelist.py

Every name below was flagged by vulture and then VERIFIED as a false
positive: each was cross-referenced repo-wide (src/tests/scripts) and
either (a) is genuinely referenced (tests, dynamic dispatch, attribute
access, same-module registries), (b) is declared interface/data surface
(Protocol stubs, GraphStore contract, dataclass fields, copy catalog),
or (c) lives in a module outside the Wave-3 file scope and is queued for
a follow-up dead-code wave. No entry may be added without the same
verification. The file is NEVER imported: it only parses for vulture.
"""

# singleton getter/resetter seams (tests and wiring reach them by attribute): 19 name(s)
set_research_pool
reset_socket_server
reset_coding_handler
reset_command_handler
reset_graph_handler
get_idempotency_checker
reset_idempotency_checker
reset_research_handler
reset_router_handler
reset_tag_handler
reset_versioning_handler
get_checkpoint_manager
reset_checkpoint_manager
get_dlq_handler
reset_dlq_handler
get_documents_projection
reset_documents_projection
get_embed_projection
reset_embed_projection

# Typer CLI commands (registered via decorator, dispatched by name): 15 name(s)
_root
coding_inductive
doctor
research_ask
research_demo
research_evidence
research_overlap
research_uncoded
setup
release_assess_cmd
release_compare_cmd
release_execute_cmd
release_request_cmd
release_review_cmd
release_status_cmd

# typed Protocol/stub parameters (interface-required, never called directly): 30 name(s)
X
cluster_selection_method
embedding_
metric
n_neighbors
# -- Wave 4 topics engine Protocols (the lazy bertopic/umap seam) --------
orient
umap_model
calculate_probabilities
verbose
transform
prompt_description
text_or_documents
add_special_tokens
attention_mask
convert_ids_to_tokens
exc_type
input_ids
keepdim
max_length
max_seq_length
normalize_embeddings
padding
return_offsets_mapping
return_tensors
show_progress_bar
skip_special_tokens
tb
truncation
IrrCodeVerdict
prevalence_affected

# dynamically-dispatched or test-patched names (verified by repo-wide grep): 149 name(s)
aggregate_signals
class_priors
evaluate_agreement
n_observed
signal_accuracy
construct
propose_theory
tradition
calibration_metrics
out_of_bounds
test_metrics
y_max
y_min
spotcheck_reroute
assigned_label
label_triage
logistic_fold_predictor
n_flagged
oos_probability
precision_of_flags
runner_up_label
runner_up_probability
aggregation
pool_score
inclusion_probability
calibration_risk
cp_upper_bound
n_calibration
n_test
enforcement_blocks
raw_reply
run_canary
structured_call
ESCALATION_BAND
assert_reasons_closed
escalated
n_samples
run_cascade
mode_value
n_samples
fixture_judge
fixture_records_to_json
nli_mutual_entailment
fit_weights_l2
kmedoids_prototypes
max_prototype_sim
entailment_pairs
n_samples
tier0_sim
tier1_entropy
tier2_hedges
ci_high
ci_low
delta_f1
promoted
_types
dropped_entities
dropped_relations
handle_errors
handle_untag_document
detection_confidence
detection_reason
mapped_source_type
_disjoint
_entity_descriptions_declared
_non_empty_unique
_relation_descriptions_declared
is_permanent_failure
list_failures
mark_resolved
ReleaseProjector
embed_batch_matryoshka
embed_exchange
encode_pooled
forward_calls
windows_used
embed_batch_matryoshka
RERANK_CONFIG_DEFAULTS
HALFVEC_PROBE_SQL
explain_plan
chunked
run_resumable
fit_code_thresholds
fit_temperature
ChildChunk
embedded_text
end_seq
parent_exchange_seq
parent_unit_seq
text_shas
CACHE_CONFIG_DEFAULTS
compute_cache_key
normalize_prompt_static_first
put
combined_metrics
dropped_signals
halt_gate
human_only
human_parity
parity_breach
run_g3_audit
codebook_diff
codebook_label
coverage_curve
coverage_explicit
coverage_qdep
deductive_coverage_note
g3_audit
generated_at
gray_zone_share
isr_by_source_type
link_accuracy
schema_version
tier3_disagreement_rate
MAX_META_DECISIONS_PER_RUN
active_release_or_none
assert_promotable
build_code_prototypes
RELEASE_STATES
release_event
DEFAULTS
determinism_proof
seed_sweep
StructuredOutputStats
to_report
evaluate_arms
load_reviewed_labels
mrr
ndcg_at_10
query_id
recommend_default
RETRIEVAL_CONFIG_DEFAULTS
diversity_applied
n_unreviewed
calibrator_kind
n_auto_accept
n_review
score_unit_views
delete_source
health
retrieve
delete_source
health
node_id
retrieve
detection_confidence
detection_reason
supported_suffixes
fetched_at

# dataclass/pydantic model fields (data-model surface, attribute-read): 21 name(s)
_deleted
reset_app
snapshotting_intervals
recalibrate_thresholds
refine_definition
MAX_ASSIGNMENTS_PER_EVENT
abstained
cited_chunk_ids
groundedness
source_types_for_units
Branch
Stats
Version
branch_id
head_version
last_updated
model_config
parent_chunk_id
sibling_count
storage_size_bytes
updated_at

# error taxonomy fields (socket_code/cli_exit resolved via ERROR_MAP lookups): 7 name(s)
ConflictError
DependencyMissingError
InternalError
NotFoundError
ValidationError
cli_exit
socket_code

# report copy inventory (module docstring: plain-language string catalog): 30 name(s)
CONFORMAL_ACTION
COVERAGE_ACTION
CYCLE_INDUCTIVE_UNAVAILABLE
CYCLE_LOOK_DEDUCTIVE
CYCLE_LOOK_INDUCTIVE
CYCLE_LOOK_INGEST
CYCLE_LOOK_KEYWORDS
CYCLE_LOOK_NOTEBOOK
CYCLE_LOOK_REPORT
CYCLE_NO_QUESTION
CYCLE_STAGE_DEDUCTIVE
CYCLE_STAGE_DEDUCTIVE_DOC
CYCLE_STAGE_INDUCTIVE
CYCLE_STAGE_INDUCTIVE_DOC
CYCLE_STAGE_INGEST
CYCLE_STAGE_INGEST_DOC
CYCLE_STAGE_KEYWORDS
CYCLE_STAGE_KEYWORDS_DOC
CYCLE_STAGE_NOTEBOOK
CYCLE_STAGE_NOTEBOOK_DOC
CYCLE_STAGE_REPORT
CYCLE_STAGE_REPORT_DOC
G3_ACTION
ISR_ACTION
KEYWORDS_ACTION
MISSING_CODES_ACTION
RELEASE_BLOCKED_HEADER
RELEASE_BLOCKED_REMEDY
RELEASE_SUBTITLE
TAU_RES_ACTION

# GraphStore abstract interface (backend contract, not called in-tree): 6 name(s)
add_chunk
add_document
add_chunk
add_document
batch_add_entities
batch_add_relations

# MCP tool wrapper dynamics (mcp_tools.build_wrapper: the SDK reads the
# advertised signature via inspect.signature(); __qualname__ is set for
# honest tool-frame names): 2 name(s)
__qualname__
__signature__

# surface registry/parity helpers (tests + gen_harness_pack dispatch by name): 10 name(s)
SERVER_ARGS
SERVER_AUTO_APPROVE
SERVER_COMMAND
SERVER_DESCRIPTION
SERVER_NAME
argument_hint
claudemd_section
http_route
assert_full_surface_parity
assert_parity_outside_allowlist

# PEP 562 module protocol (corpus_kb/__init__.py): the interpreter calls the
# module-level __getattr__ on failed attribute access (corpus_kb.__version__,
# single-sourced from importlib.metadata) and __dir__ on dir(). Verified live:
# tests/test_cli.py asserts --version and the Wave-4B clean-venv check runs
# dir(corpus_kb): 2 name(s)
__getattr__
__dir__

# config-v2 typed-reader fields owned for contract completeness (the block
# declares the whole canonical namespace even where W1 has no live consumer
# yet; qa_context_in_fts gates migration-023 index maintenance):
qa_context_in_fts
used_legacy_keys
mode_error
profile_for_model
native_dimensions
EmbeddingModelProfile
EMBEDDING_MODEL_PROFILES

# UX Wave 3 NiceGUI Protocols (the lazy nicegui seam in ui/app.py): the
# keyword-argument parameters of the pinned element surface. Vulture sees
# them as unused because NiceGUI's library code calls the handlers the
# pages pass in (on_click -> the real Button.on_click wiring; sent_by_user
# -> the ChatMessage constructor), never this Protocol.
on_click
sent_by_user

# UX Wave 5 eval harness (the lazy deepeval seam): the OllamaJudgeLLM
# methods exist for deepeval's DeepEvalBaseLLM contract (the library calls
# them, never our code), and the _TestCaseModule Protocol parameters mirror
# deepeval's LLMTestCase keyword API. build_golden is a Typer command
# (registered by decorator, invoked by the CLI entry point).
build_golden
actual_output
expected_output
retrieval_context
load_model
load_responses
a_generate
get_model_name
