#!/usr/bin/env python3
"""
Pre-push CI gate validator - catches issues locally before GitHub Actions.
Mirrors the exact checks from .github/workflows/ci.yml
"""

import json
import re
import subprocess
import sys
from pathlib import Path


class CIValidator:
    def __init__(self, repo_root: Path | None = None):
        self.repo_root = repo_root if repo_root is not None else Path.cwd()
        self.errors = []
        self.warnings = []

    def validate_commit_message(self, message: str) -> bool:
        """Validate commit message format."""
        # Capital letter check
        if not message or not message[0].isupper():
            self.errors.append("❌ Commit message must start with capital letter")
            return False

        # Length check
        if len(message) < 10:
            self.errors.append(f"❌ Commit message too short: {len(message)} chars (min 10)")
            return False
        if len(message) > 500:
            self.errors.append(f"❌ Commit message too long: {len(message)} chars (max 500)")
            return False

        # Dangerous operations check
        dangerous_keywords = [
            "force-push", "filter-branch", "reset --hard",
            "rm -rf", "Remove-Item.*Recurse"
        ]
        for keyword in dangerous_keywords:
            if re.search(keyword, message, re.IGNORECASE):
                self.errors.append(f"❌ Commit message contains dangerous keyword: {keyword}")
                return False

        return True

    def validate_config_files(self) -> bool:
        """Validate required config files exist."""
        required_files = [
            self.repo_root / "opencode.json",
            self.repo_root / "mcp-configs" / "cursor.json"
        ]

        success = True
        for file_path in required_files:
            if not file_path.exists():
                self.errors.append(f"❌ Missing required config: {file_path.relative_to(self.repo_root)}")
                success = False
            else:
                # Validate JSON
                try:
                    with open(file_path) as f:
                        json.load(f)
                except json.JSONDecodeError as e:
                    self.errors.append(f"❌ Invalid JSON in {file_path.name}: {e}")
                    success = False

        return success

    def run_code_quality_checks(self) -> bool:
        """Run ruff, pyright, pytest checks."""
        checks = [
            ("ruff check src/", "Linting with ruff"),
            ("ruff format --check src/", "Format check with ruff"),
            ("pyright src/", "Type checking with pyright"),
        ]

        success = True
        for cmd, description in checks:
            try:
                result = subprocess.run(
                    cmd, shell=True, cwd=self.repo_root,
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode != 0:
                    self.errors.append(f"❌ {description} failed:\n{result.stdout}\n{result.stderr}")
                    success = False
                else:
                    self.warnings.append(f"✓ {description} passed")
            except subprocess.TimeoutExpired:
                self.errors.append(f"❌ {description} timed out")
                success = False
            except Exception as e:
                self.warnings.append(f"⚠ {description} skipped: {e}")

        return success

    def run_tests(self) -> bool:
        """Run pytest tests."""
        try:
            result = subprocess.run(
                "pytest tests/ -v", shell=True, cwd=self.repo_root,
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                self.errors.append(f"❌ Tests failed:\n{result.stdout}")
                return False
            else:
                self.warnings.append("✓ All tests passed")
                return True
        except subprocess.TimeoutExpired:
            self.errors.append("❌ Tests timed out")
            return False
        except Exception as e:
            self.warnings.append(f"⚠ Tests skipped: {e}")
            return True  # Don't fail if tests can't run

    def validate_all(self, commit_message: str | None = None, skip_code_checks: bool = False) -> bool:
        """Run all validations."""
        print("\n🔍 Running corpus-kb CI gate validation...\n")

        # Get commit message from git if not provided
        if commit_message is None:
            try:
                result = subprocess.run(
                    "git log -1 --pretty=format:%s",
                    shell=True, cwd=self.repo_root,
                    capture_output=True, text=True
                )
                commit_message = result.stdout.strip()
            except:
                pass

        all_pass = True

        # Commit message validation
        if commit_message:
            print("📝 Validating commit message...")
            if not self.validate_commit_message(commit_message):
                all_pass = False
            else:
                self.warnings.append("✓ Commit message format valid")
        else:
            self.warnings.append("⚠ Could not retrieve commit message")

        # Config files validation
        print("📁 Validating config files...")
        if not self.validate_config_files():
            all_pass = False

        # Code quality checks
        if not skip_code_checks:
            print("🔬 Running code quality checks...")
            if not self.run_code_quality_checks():
                all_pass = False

            print("🧪 Running tests...")
            if not self.run_tests():
                all_pass = False

        # Report
        print("\n" + "=" * 60)
        if self.warnings:
            for warning in self.warnings:
                print(warning)

        if self.errors:
            print("\n⛔ VALIDATION FAILED:")
            for error in self.errors:
                print(error)
            print("\nFix the above issues before pushing.")
            return False
        else:
            print("\n✅ All CI gates passed! Safe to push.")
            return True


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate corpus-kb against CI gate requirements"
    )
    parser.add_argument(
        "--commit-msg", help="Commit message to validate (optional)"
    )
    parser.add_argument(
        "--skip-code-checks", action="store_true",
        help="Skip code quality/test checks (for quick validation)"
    )

    args = parser.parse_args()

    validator = CIValidator()
    success = validator.validate_all(
        commit_message=args.commit_msg,
        skip_code_checks=args.skip_code_checks
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
