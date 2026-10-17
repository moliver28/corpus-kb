"""The ``corpus-kb quickstart`` step machine (novice first-run path).

Six idempotent steps take a fresh clone to a first successful search:

  1. preflight — Ollama reachable (report version), configured DSN probed
  2. postgres  — resolve a reachable DSN: configured, then --dsn, then
     the localhost:5432 twin, then provisioning via --superuser-dsn
  3. schema    — storage/schema.sql plus numbered migrations (the ONE
     implementation shared with the test harness)
  4. write-back— persist the adopted DSN into the local config.yaml
  5. models    — pull (or print commands for) the configured Ollama
     models; the reranker gets an honest llama-server note
  6. doctor    — the doctor report, then three next-step commands

All network and subprocess work lives behind the :class:`QuickstartDeps`
callables, so tests run the machine with fakes only. Config problems
(missing DSN, unparsable DSN, ambiguous write target) raise ValueError —
the CLI maps that to exit code 3. The shared dataclasses, states, and
config readers live in ``quickstart_types``.
"""

from __future__ import annotations

from corpus_kb._setup.db_provision import DsnParts, fallback_dsn, parse_dsn
from corpus_kb._setup.quickstart_types import (
    STATE_FAIL,
    STATE_FIX,
    STATE_OK,
    STATE_SKIP,
    QuickstartDeps,
    QuickstartOptions,
    QuickstartOutcome,
    StepResult,
    cfg_dict,
    cfg_str,
    model_present,
    reranker_note,
    runtime_dsn,
)


class QuickstartRunner:
    """Runs the six steps against injected collaborators."""

    def __init__(self, opts: QuickstartOptions, deps: QuickstartDeps) -> None:
        self._opts = opts
        self._deps = deps
        self.outcome = QuickstartOutcome()
        self._configured = runtime_dsn(deps.config)
        self._adopted: str | None = None

    async def run(self) -> int:
        """Run every step; return 0 when nothing failed, else 1."""
        await self._step_preflight()
        await self._step_postgres()
        if self._adopted is not None:
            await self._step_schema()
            await self._step_writeback()
        await self._step_models()
        await self._step_doctor()
        failed = any(step.state == STATE_FAIL and step.gates_exit for step in self.outcome.steps)
        self.outcome.exit_code = 1 if failed else 0
        return self.outcome.exit_code

    def _add(
        self,
        name: str,
        state: str,
        detail: str,
        extra: tuple[str, ...] = (),
        *,
        gates_exit: bool = True,
    ) -> None:
        self.outcome.steps.append(StepResult(name, state, detail, extra, gates_exit))

    def _adopt(self, dsn: str, detail: str) -> None:
        self._adopted = dsn
        self.outcome.adopted_dsn = dsn
        self._add("postgres", STATE_OK, f"{detail}; using {dsn}")

    async def _step_preflight(self) -> None:
        base_url = cfg_str(cfg_dict(self._deps.config, "embedding"), "base_url")
        status, version = await self._ollama_version(base_url)
        if status is None:
            self._add(
                "preflight-ollama",
                STATE_FAIL,
                f"Ollama unreachable at {base_url}. Start it with: ollama serve",
                gates_exit=False,
            )
        elif status != 200:
            self._add(
                "preflight-ollama",
                STATE_FAIL,
                f"Ollama answered HTTP {status}",
                gates_exit=False,
            )
        else:
            self._add("preflight-ollama", STATE_OK, f"Ollama version {version}", gates_exit=False)
        probe = await self._deps.pg_probe(self._configured)
        state = STATE_OK if probe.ok else STATE_FAIL
        label = "reachable" if probe.ok else f"unreachable: {probe.detail}"
        self._add("preflight-postgres", state, f"Configured DSN {label}", gates_exit=False)

    async def _step_postgres(self) -> None:
        if self._opts.dsn is not None:
            await self._resolve(self._opts.dsn, "--dsn")
            return
        await self._resolve(self._configured, "configured DSN")

    async def _resolve(self, dsn: str, label: str) -> None:
        probe = await self._deps.pg_probe(dsn)
        if probe.ok:
            self._adopt(dsn, f"{label} reachable")
            return
        parts = parse_dsn(dsn)
        provisionable = probe.database_missing or probe.auth_failed
        if provisionable and self._opts.superuser_dsn is not None:
            await self._provision_and_adopt(dsn, parts, self._opts.superuser_dsn)
            return
        twin = fallback_dsn(parts)
        if not provisionable and twin != dsn and (await self._deps.pg_probe(twin)).ok:
            self._adopt(
                twin,
                f"{label} unreachable ({probe.detail}); adopted the localhost:5432 twin",
            )
            return
        self._add("postgres", STATE_FAIL, f"No reachable Postgres ({probe.detail}).", self._hints())

    def _hints(self) -> tuple[str, ...]:
        if self._deps.docker_available():
            return (
                "Docker is available: run `corpus-kb setup` to start the compose stack,",
                "then re-run `corpus-kb quickstart`.",
            )
        return (
            "Install Postgres, pass --dsn, or run `corpus-kb setup` with Docker.",
            "To provision a missing role/database, pass --superuser-dsn.",
        )

    async def _provision_and_adopt(self, dsn: str, parts: DsnParts, superuser: str) -> None:
        report = await self._deps.provision(superuser, parts)
        probe = await self._deps.pg_probe(dsn)
        if not probe.ok:
            self._add("provision", STATE_FAIL, f"{report}; still unreachable: {probe.detail}")
            return
        self._add("provision", STATE_FIX, report)
        self._adopt(dsn, "reachable after provisioning")

    async def _step_schema(self) -> None:
        if self._adopted is None:  # unreachable: run() gates this step
            raise ValueError("Schema step ran without an adopted DSN")
        try:
            result = await self._deps.apply_schema(self._adopted)
        except Exception as exc:
            self._add("schema", STATE_FAIL, f"Schema application failed: {exc}")
            return
        extra = tuple(
            f"WARN: skipped {name} (needs the AGE/pgml server extensions)"
            for name in result.skipped
        )
        self._add(
            "schema",
            STATE_OK,
            f"{len(result.applied)} migrations applied, {len(result.skipped)} skipped",
            extra,
        )

    async def _step_writeback(self) -> None:
        if self._adopted is None:
            raise ValueError("Write-back step ran without an adopted DSN")
        if self._opts.no_write:
            self._add("write-back", STATE_SKIP, "--no-write: config.yaml not modified")
            return
        if self._configured == self._adopted:
            self._add("write-back", STATE_SKIP, "The config already holds the adopted DSN")
            return
        notice = self._deps.write_dsn(self._adopted)
        self._add("write-back", STATE_FIX, notice)

    async def _step_models(self) -> None:
        base_url = cfg_str(cfg_dict(self._deps.config, "embedding"), "base_url")
        answer = await self._deps.ollama_get(base_url, "/api/tags")
        if answer is None or answer[0] != 200:
            detail = "Ollama did not answer /api/tags" if answer is None else f"HTTP {answer[0]}"
            self._add("models", STATE_FAIL, f"{detail}; cannot check the models")
            return
        listed = answer[1].get("models")
        present = {
            str(entry.get("name", ""))
            for entry in (listed if isinstance(listed, list) else [])
            if isinstance(entry, dict)
        }
        missing = [name for name in self._wanted_models() if not model_present(present, name)]
        note = reranker_note(cfg_str(cfg_dict(self._deps.config, "reranking"), "model"))
        if not missing:
            self._add("models", STATE_OK, "All configured models are present", note)
        elif not self._opts.pull_models:
            extra = tuple(f"ollama pull {name}" for name in missing) + note
            self._add("models", STATE_FIX, "Missing models; pull commands printed", extra)
        else:
            await self._pull_missing(missing, note)

    async def _pull_missing(self, missing: list[str], note: tuple[str, ...]) -> None:
        failed = [name for name in missing if self._deps.pull_model(name) != 0]
        if failed:
            extra = tuple(f"ollama pull {name}" for name in failed) + note
            self._add("models", STATE_FAIL, f"Pull failed: {', '.join(failed)}", extra)
            return
        self._add(
            "models",
            STATE_FIX,
            f"Pulled {len(missing)} models: {', '.join(missing)}",
            note,
        )

    def _wanted_models(self) -> list[str]:
        config = self._deps.config
        wanted = (
            cfg_str(cfg_dict(config, "embedding"), "model"),
            cfg_str(cfg_dict(config, "judge"), "model"),
            cfg_str(cfg_dict(cfg_dict(config, "retrieval"), "self_query"), "model"),
            cfg_str(cfg_dict(cfg_dict(config, "ingest"), "contextualize"), "model"),
        )
        seen: list[str] = []
        for name in wanted:
            if name and name not in seen:
                seen.append(name)
        return seen

    async def _step_doctor(self) -> None:
        adopted_config = dict(self._deps.config)
        if self._adopted is not None:
            adopted_config["database"] = {"connection_string": self._adopted}
            self._deps.set_env_dsn(self._adopted)
        code = await self._deps.run_doctor(adopted_config)
        state = STATE_OK if code == 0 else STATE_FAIL
        detail = "Doctor report above" if state == STATE_OK else "Doctor reported failures"
        self._add("doctor", state, detail)

    async def _ollama_version(self, base_url: str) -> tuple[int | None, str]:
        answer = await self._deps.ollama_get(base_url, "/api/version")
        if answer is None:
            return None, ""
        return answer[0], str(answer[1].get("version", "unknown"))
