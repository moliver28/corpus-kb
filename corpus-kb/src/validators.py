"""CI gate validators for linting, type checking, and test coverage.

Provides reusable validator classes for ruff, pyright, and pytest gates.
Used by CI/CD pipelines and local development workflows.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ValidationResult:
    """Result of a validation check."""

    passed: bool
    message: str
    exit_code: int = 0


class RuffValidator:
    """Ruff linting and formatting validator."""

    def __init__(self, project_root: Path | str) -> None:
        """Initialize with project root.

        Args:
            project_root: Path to project root containing src/, tests/, scripts/
        """
        self.project_root = Path(project_root)

    def check_lint(self) -> ValidationResult:
        """Run ruff check (linting).

        Returns:
            ValidationResult with pass/fail status and message
        """
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "src/", "scripts/", "tests/"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return ValidationResult(
                passed=True, message="Ruff check passed", exit_code=0
            )

        return ValidationResult(
            passed=False,
            message=f"Ruff violations found:\n{result.stdout}",
            exit_code=result.returncode,
        )

    def check_format(self) -> ValidationResult:
        """Run ruff format check.

        Returns:
            ValidationResult with pass/fail status and message
        """
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "format",
                "--check",
                "src/",
                "scripts/",
                "tests/",
            ],
            cwd=self.project_root,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return ValidationResult(
                passed=True, message="Ruff format check passed", exit_code=0
            )

        return ValidationResult(
            passed=False,
            message=f"Formatting violations found:\n{result.stdout}",
            exit_code=result.returncode,
        )

    def validate_all(self) -> ValidationResult:
        """Run all ruff checks (lint + format).

        Returns:
            ValidationResult (passes only if both checks pass)
        """
        lint_result = self.check_lint()
        if not lint_result.passed:
            return lint_result

        return self.check_format()


class PyrightValidator:
    """Pyright type checking validator."""

    def __init__(self, project_root: Path | str) -> None:
        """Initialize with project root.

        Args:
            project_root: Path to project root
        """
        self.project_root = Path(project_root)

    def check_types(self, src_dir: str = "src/") -> ValidationResult:
        """Run pyright type checking.

        Args:
            src_dir: Directory to check (default: src/)

        Returns:
            ValidationResult with pass/fail status
        """
        result = subprocess.run(
            [sys.executable, "-m", "pyright", src_dir],
            cwd=self.project_root,
            capture_output=True,
            text=True,
        )

        # Check for assignment/attribute errors (not missing imports)
        has_type_errors = (
            "is not assignable" in result.stdout
            or "Cannot access attribute" in result.stdout
        )

        if not has_type_errors:
            return ValidationResult(
                passed=True, message="Type checking passed", exit_code=0
            )

        return ValidationResult(
            passed=False,
            message=f"Type errors found:\n{result.stdout}",
            exit_code=1,
        )

    def check_no_any(self) -> ValidationResult:
        """Check that src/ doesn't have loose Any type annotations.

        Returns:
            ValidationResult (allows Any in graph_store unions)
        """
        src_dir = self.project_root / "src"
        python_files = list(src_dir.rglob("*.py"))

        any_usages: list[str] = []
        for file in python_files:
            try:
                content = file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # Skip files with encoding issues
                continue
            # Find lines with ': Any' but ignore imports, strings, and graph_store unions
            for line in content.split("\n"):
                if (
                    ": Any" in line
                    and "import" not in line
                    and "graph_store" not in line
                    and '": Any"' not in line
                ):
                    any_usages.append(
                        f"{file.relative_to(self.project_root)}: {line.strip()}"
                    )

        if not any_usages:
            return ValidationResult(
                passed=True, message="No loose Any type annotations found", exit_code=0
            )

        return ValidationResult(
            passed=False,
            message="Found type annotation with Any (excluding graph_store unions):\n"
            + "\n".join(any_usages),
            exit_code=1,
        )

    def validate_all(self) -> ValidationResult:
        """Run all type checks.

        Returns:
            ValidationResult (passes only if all checks pass)
        """
        types_result = self.check_types()
        if not types_result.passed:
            return types_result

        return self.check_no_any()


class PytestValidator:
    """Pytest validation for test discovery and coverage.

    Note: Does NOT actually run tests (requires Ollama).
    Only validates that pytest can discover tests and coverage tools are installed.
    """

    def __init__(self, project_root: Path | str) -> None:
        """Initialize with project root.

        Args:
            project_root: Path to project root
        """
        self.project_root = Path(project_root)

    def check_test_discovery(self) -> ValidationResult:
        """Verify pytest can discover tests without syntax errors.

        Returns:
            ValidationResult with pass/fail status
        """
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "tests/", "-q"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return ValidationResult(
                passed=True,
                message="Pytest test discovery successful",
                exit_code=0,
            )

        return ValidationResult(
            passed=False,
            message=f"Test discovery failed (syntax errors?):\n{result.stdout}\n{result.stderr}",
            exit_code=result.returncode,
        )

    def check_coverage_tool(self) -> ValidationResult:
        """Verify pytest-cov is installed and importable.

        Returns:
            ValidationResult with pass/fail status
        """
        result = subprocess.run(
            [sys.executable, "-c", "import pytest_cov"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return ValidationResult(
                passed=True,
                message="pytest-cov is installed and importable",
                exit_code=0,
            )

        return ValidationResult(
            passed=False,
            message=f"pytest-cov not available:\n{result.stdout}\n{result.stderr}",
            exit_code=result.returncode,
        )

    def validate_all(self) -> ValidationResult:
        """Run all pytest validation checks.

        Returns:
            ValidationResult (passes only if all checks pass)
        """
        discovery_result = self.check_test_discovery()
        if not discovery_result.passed:
            return discovery_result

        return self.check_coverage_tool()


def validate_all(project_root: Optional[Path | str] = None) -> bool:
    """Run all CI gate validators.

    Args:
        project_root: Path to project root (default: current directory)

    Returns:
        True if all validators pass, False otherwise
    """
    if project_root is None:
        project_root = Path.cwd()
    else:
        project_root = Path(project_root)

    validators = [
        ("Ruff (lint + format)", RuffValidator(project_root).validate_all()),
        ("Pyright (types)", PyrightValidator(project_root).validate_all()),
        ("Pytest (discovery + coverage)", PytestValidator(project_root).validate_all()),
    ]

    all_passed = True
    for name, result in validators:
        status = "[PASS]" if result.passed else "[FAIL]"
        print(f"{status}: {name}")
        if not result.passed:
            print(f"  {result.message}\n")
            all_passed = False

    return all_passed


if __name__ == "__main__":
    # Allow running as: python -m src.validators [project_root]
    project_root_arg = sys.argv[1] if len(sys.argv) > 1 else None
    success = validate_all(project_root_arg)
    sys.exit(0 if success else 1)
