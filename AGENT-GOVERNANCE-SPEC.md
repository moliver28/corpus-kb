# Agent Governance Specification for Team-Mode Development

**Version:** 1.0  
**Date:** 2026-06-18  
**Scope:** corpus-kb and future OpenCode team-mode codebases  
**Status:** Design Complete

---

## Executive Summary

This specification defines a complete agent governance framework for team-mode development in OpenCode. It covers:

1. **Authority Scoping Model** — What agents can/cannot do based on role and reputation
2. **Pre-Action Gates** — Blocking dangerous actions before execution
3. **Audit Trail Design** — Comprehensive logging for every delegation
4. **Reputation System** — Agents earn higher authority through good behavior
5. **Enforceable OPA/Rego Policies** — GitHub Actions integration
6. **Agent Self-Discovery** — AGENTS.md updates for team awareness

The framework is designed to be:
- **Enforceable** — Policies run in CI/CD, not just documentation
- **Transparent** — Every delegation is logged and auditable
- **Progressive** — Agents start with minimal authority, earn trust over time
- **Recoverable** — All dangerous operations are reversible or blocked

---

## 1. Authority Scoping Model

### 1.1 Agent Roles and Capabilities

Agents are classified by role. Each role has explicit capability boundaries.

```
┌─────────────────────────────────────────────────────────────┐
│                    AGENT ROLE HIERARCHY                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  LEVEL 0: OBSERVER (Read-Only)                              │
│  ├─ Can: read files, search, analyze, ask questions         │
│  ├─ Cannot: write, delete, execute, push                    │
│  └─ Default for new agents                                  │
│                                                              │
│  LEVEL 1: CONTRIBUTOR (Write Local)                         │
│  ├─ Can: edit files, create branches, commit locally        │
│  ├─ Cannot: push, delete, force-push, merge                 │
│  └─ Earned after 5 successful local commits                 │
│                                                              │
│  LEVEL 2: INTEGRATOR (Push & Review)                        │
│  ├─ Can: push to feature branches, create PRs               │
│  ├─ Cannot: merge, delete, force-push, admin ops            │
│  └─ Earned after 10 successful PRs + 0 reverts              │
│                                                              │
│  LEVEL 3: MAINTAINER (Merge & Release)                      │
│  ├─ Can: merge PRs, tag releases, delete branches           │
│  ├─ Cannot: force-push, rewrite history, admin ops          │
│  └─ Earned after 20 successful merges + explicit approval   │
│                                                              │
│  LEVEL 4: ADMIN (Full Access)                               │
│  ├─ Can: all operations including force-push, history rewrite│
│  ├─ Cannot: nothing (but all actions logged)                │
│  └─ Only via explicit human approval                        │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 Capability Matrix

| Capability | Observer | Contributor | Integrator | Maintainer | Admin |
|------------|----------|-------------|-----------|-----------|-------|
| Read files | ✓ | ✓ | ✓ | ✓ | ✓ |
| Search/analyze | ✓ | ✓ | ✓ | ✓ | ✓ |
| Edit files locally | ✗ | ✓ | ✓ | ✓ | ✓ |
| Create branches | ✗ | ✓ | ✓ | ✓ | ✓ |
| Commit locally | ✗ | ✓ | ✓ | ✓ | ✓ |
| Push to feature/* | ✗ | ✗ | ✓ | ✓ | ✓ |
| Create PR | ✗ | ✗ | ✓ | ✓ | ✓ |
| Merge PR | ✗ | ✗ | ✗ | ✓ | ✓ |
| Delete branch | ✗ | ✗ | ✗ | ✓ | ✓ |
| Tag release | ✗ | ✗ | ✗ | ✓ | ✓ |
| Force-push | ✗ | ✗ | ✗ | ✗ | ✓ |
| Rewrite history | ✗ | ✗ | ✗ | ✗ | ✓ |
| Delete repo | ✗ | ✗ | ✗ | ✗ | ✓ |

### 1.3 Dangerous Operations (Always Blocked Unless Approved)

These operations require explicit human approval, regardless of agent level:

```yaml
dangerous_operations:
  - git reset --hard
  - git push --force
  - git push --force-with-lease
  - git clean -fd
  - git filter-branch
  - rm -rf / Remove-Item -Recurse -Force
  - git revert --no-edit (on main/master)
  - Database schema DROP
  - Credential rotation
  - Infrastructure changes
  - Dependency major version bumps
```

---

## 2. Pre-Action Gates

### 2.1 Gate Architecture

Every agent action passes through a gate before execution:

```
┌──────────────────────────────────────────────────────────────┐
│                    AGENT ACTION FLOW                          │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  1. Agent requests action                                    │
│     ↓                                                         │
│  2. GATE: Check agent role & capability                      │
│     ├─ If denied → BLOCK + log + notify                      │
│     └─ If allowed → continue                                 │
│     ↓                                                         │
│  3. GATE: Check action against dangerous list                │
│     ├─ If dangerous → require approval + log                 │
│     └─ If safe → continue                                    │
│     ↓                                                         │
│  4. GATE: Validate action syntax & safety                    │
│     ├─ If invalid → BLOCK + log + notify                     │
│     └─ If valid → continue                                   │
│     ↓                                                         │
│  5. GATE: Check pre-conditions (branch exists, etc)          │
│     ├─ If failed → BLOCK + log + notify                      │
│     └─ If passed → continue                                  │
│     ↓                                                         │
│  6. EXECUTE action                                           │
│     ↓                                                         │
│  7. GATE: Validate post-conditions                           │
│     ├─ If failed → ROLLBACK + log + notify                   │
│     └─ If passed → continue                                  │
│     ↓                                                         │
│  8. LOG: Record success + metrics                            │
│     ↓                                                         │
│  9. UPDATE: Agent reputation score                           │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 Gate Implementation (Pseudocode)

```python
class ActionGate:
    """Pre-action validation gate for agent operations."""
    
    def validate(self, agent: Agent, action: Action) -> GateResult:
        """
        Validate an action before execution.
        Returns: GateResult(allowed: bool, reason: str, requires_approval: bool)
        """
        
        # Gate 1: Role-based capability check
        if not self.check_capability(agent.role, action.type):
            return GateResult(
                allowed=False,
                reason=f"Agent role {agent.role} cannot perform {action.type}",
                requires_approval=False
            )
        
        # Gate 2: Dangerous operation check
        if action.command in DANGEROUS_OPERATIONS:
            return GateResult(
                allowed=True,  # Allow, but require approval
                reason=f"Dangerous operation: {action.command}",
                requires_approval=True
            )
        
        # Gate 3: Syntax & safety validation
        try:
            self.validate_syntax(action)
            self.validate_safety(action)
        except ValidationError as e:
            return GateResult(
                allowed=False,
                reason=f"Validation failed: {e}",
                requires_approval=False
            )
        
        # Gate 4: Pre-conditions
        try:
            self.check_preconditions(action)
        except PreconditionError as e:
            return GateResult(
                allowed=False,
                reason=f"Precondition failed: {e}",
                requires_approval=False
            )
        
        # All gates passed
        return GateResult(
            allowed=True,
            reason="All gates passed",
            requires_approval=False
        )
    
    def execute_with_gates(self, agent: Agent, action: Action) -> ExecutionResult:
        """Execute action with pre and post gates."""
        
        # Pre-execution gates
        gate_result = self.validate(agent, action)
        if not gate_result.allowed:
            self.log_blocked_action(agent, action, gate_result.reason)
            raise ActionBlockedError(gate_result.reason)
        
        if gate_result.requires_approval:
            approval = self.request_approval(agent, action)
            if not approval.granted:
                self.log_denied_action(agent, action, approval.reason)
                raise ActionDeniedError(approval.reason)
        
        # Execute
        try:
            result = action.execute()
        except Exception as e:
            self.log_execution_error(agent, action, e)
            raise
        
        # Post-execution gates
        try:
            self.validate_postconditions(action, result)
        except PostconditionError as e:
            self.log_postcondition_failure(agent, action, e)
            self.rollback(action, result)
            raise
        
        # Success
        self.log_successful_action(agent, action, result)
        self.update_reputation(agent, action, result)
        
        return result
```

### 2.3 Specific Gates by Action Type

#### Git Operations

```yaml
git_commit:
  pre_gates:
    - branch_exists: true
    - working_tree_clean: false  # Allow uncommitted changes
    - message_length: min=10, max=500
    - message_format: "^[A-Z][a-z]+.*"  # Capitalized
  dangerous: false
  post_gates:
    - commit_created: true
    - commit_signed: optional  # Recommended but not required

git_push:
  pre_gates:
    - branch_exists: true
    - remote_exists: true
    - branch_matches_remote: true  # No divergence
    - not_force_push: true
  dangerous: false
  post_gates:
    - remote_updated: true
    - ci_triggered: true

git_reset_hard:
  pre_gates:
    - none
  dangerous: true
  requires_approval: true
  post_gates:
    - none

git_force_push:
  pre_gates:
    - none
  dangerous: true
  requires_approval: true
  post_gates:
    - none
```

#### File Operations

```yaml
file_write:
  pre_gates:
    - file_path_safe: true  # No path traversal
    - file_size_reasonable: max=10MB
    - not_system_file: true
  dangerous: false
  post_gates:
    - file_exists: true
    - file_readable: true

file_delete:
  pre_gates:
    - file_exists: true
    - file_not_critical: true  # Not in critical list
    - file_size_reasonable: max=100MB
  dangerous: false
  post_gates:
    - file_deleted: true

directory_delete_recursive:
  pre_gates:
    - directory_exists: true
    - directory_not_critical: true
    - directory_size_reasonable: max=1GB
  dangerous: true
  requires_approval: true
  post_gates:
    - directory_deleted: true
```

---

## 3. Audit Trail Design

### 3.1 Audit Log Schema

Every action is logged with complete context:

```json
{
  "audit_id": "aud_20260618_001234",
  "timestamp": "2026-06-18T14:32:45.123Z",
  "agent": {
    "id": "agent_sisyphus_junior_001",
    "name": "Sisyphus-Junior",
    "role": "contributor",
    "reputation_score": 42.5
  },
  "action": {
    "type": "git_commit",
    "command": "git commit -m 'Add agent governance spec'",
    "target": "corpus-kb",
    "branch": "feature/agent-governance"
  },
  "gates": {
    "role_check": {
      "passed": true,
      "details": "Contributor role can commit"
    },
    "dangerous_check": {
      "passed": true,
      "details": "Not a dangerous operation"
    },
    "syntax_check": {
      "passed": true,
      "details": "Valid git command"
    },
    "precondition_check": {
      "passed": true,
      "details": "Branch exists, working tree clean"
    }
  },
  "approval": {
    "required": false,
    "granted": null,
    "approver": null,
    "approval_time": null
  },
  "execution": {
    "status": "success",
    "start_time": "2026-06-18T14:32:45.123Z",
    "end_time": "2026-06-18T14:32:46.456Z",
    "duration_ms": 1333,
    "result": {
      "commit_sha": "abc123def456",
      "files_changed": 1,
      "insertions": 250,
      "deletions": 0
    },
    "error": null
  },
  "postcondition_check": {
    "passed": true,
    "details": "Commit created successfully"
  },
  "reputation_impact": {
    "delta": 1.5,
    "reason": "Successful commit with good message",
    "new_score": 44.0
  },
  "metadata": {
    "session_id": "ses_abc123",
    "team_run_id": "d1a2beb0-9d57-441b-a9f3-106ca4def8fd",
    "user_id": "user_lead_001",
    "ip_address": "192.168.1.100",
    "user_agent": "OpenCode/1.0"
  }
}
```

### 3.2 Audit Log Storage

Logs are stored in multiple formats for different use cases:

```
.opencode/audit/
├── logs/
│   ├── 2026-06-18/
│   │   ├── 00-06.jsonl          # Hourly rotation
│   │   ├── 06-12.jsonl
│   │   └── 12-18.jsonl
│   └── 2026-06-17/
│       └── ...
├── index/
│   ├── by_agent.db              # SQLite index by agent
│   ├── by_action.db             # SQLite index by action type
│   └── by_timestamp.db          # SQLite index by time
└── archive/
    └── 2026-Q2.tar.gz           # Quarterly archives
```

### 3.3 Audit Log Queries

```sql
-- Find all actions by an agent
SELECT * FROM audit_logs 
WHERE agent_id = 'agent_sisyphus_junior_001'
ORDER BY timestamp DESC;

-- Find all blocked actions
SELECT * FROM audit_logs 
WHERE execution.status = 'blocked'
ORDER BY timestamp DESC;

-- Find all dangerous operations
SELECT * FROM audit_logs 
WHERE action.type IN (SELECT * FROM dangerous_operations)
ORDER BY timestamp DESC;

-- Find actions that required approval
SELECT * FROM audit_logs 
WHERE approval.required = true
ORDER BY timestamp DESC;

-- Find actions with errors
SELECT * FROM audit_logs 
WHERE execution.status = 'error'
ORDER BY timestamp DESC;

-- Agent reputation timeline
SELECT 
  agent_id,
  timestamp,
  reputation_impact.delta,
  reputation_impact.new_score,
  action.type
FROM audit_logs
WHERE agent_id = 'agent_sisyphus_junior_001'
ORDER BY timestamp ASC;
```

### 3.4 Audit Log Retention

```yaml
retention_policy:
  active_logs: 90 days          # Keep in hot storage
  archive_logs: 2 years         # Keep in cold storage
  deletion_policy: "never"      # Never delete audit logs
  
  # Compliance
  soc2_compliant: true
  gdpr_compliant: true          # PII redaction on export
  hipaa_compliant: false        # Not applicable
```

---

## 4. Reputation System

### 4.1 Reputation Scoring Model

Agents earn reputation through successful actions and lose it through failures.

```
┌──────────────────────────────────────────────────────────────┐
│                  REPUTATION SCORING MODEL                     │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  Base Score: 0 (new agents start at zero)                    │
│  Max Score: 100 (perfect agent)                              │
│  Min Score: -50 (severely broken agent)                      │
│                                                               │
│  POSITIVE ACTIONS (earn reputation)                          │
│  ├─ Successful commit: +1.0                                  │
│  ├─ Successful PR: +2.0                                      │
│  ├─ Successful merge: +3.0                                   │
│  ├─ Successful release: +5.0                                 │
│  ├─ Code review approval: +0.5                               │
│  ├─ Bug fix: +2.0                                            │
│  ├─ Documentation: +1.0                                      │
│  ├─ Test coverage increase: +1.5                             │
│  └─ Zero security issues: +10.0 (per quarter)                │
│                                                               │
│  NEGATIVE ACTIONS (lose reputation)                          │
│  ├─ Blocked action: -0.5                                     │
│  ├─ Denied action: -1.0                                      │
│  ├─ Execution error: -2.0                                    │
│  ├─ Reverted commit: -3.0                                    │
│  ├─ Reverted PR: -5.0                                        │
│  ├─ Security issue: -10.0                                    │
│  ├─ Data loss: -50.0 (permanent ban)                         │
│  └─ Unauthorized access: -50.0 (permanent ban)               │
│                                                               │
│  ROLE PROMOTION THRESHOLDS                                   │
│  ├─ Observer → Contributor: 5 points + 5 successful actions  │
│  ├─ Contributor → Integrator: 20 points + 10 successful PRs  │
│  ├─ Integrator → Maintainer: 40 points + 20 successful merges│
│  └─ Maintainer → Admin: 80 points + explicit approval        │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 Reputation Decay

Reputation decays over time if an agent is inactive:

```yaml
decay_policy:
  active_agent:
    definition: "at least 1 action per week"
    decay_rate: 0%  # No decay for active agents
  
  inactive_agent:
    definition: "no actions for 4+ weeks"
    decay_rate: 0.5% per week  # Slow decay
    minimum_score: 0  # Never go below zero
  
  dormant_agent:
    definition: "no actions for 3+ months"
    decay_rate: 2% per week  # Faster decay
    minimum_score: 0
    action: "demote to Observer role"
```

### 4.3 Reputation Recovery

Agents can recover reputation through good behavior:

```yaml
recovery_policy:
  blocked_action:
    recovery_time: 1 week
    recovery_action: "successful commit"
    recovery_points: 1.0
  
  denied_action:
    recovery_time: 2 weeks
    recovery_action: "successful PR"
    recovery_points: 2.0
  
  execution_error:
    recovery_time: 1 week
    recovery_action: "successful commit"
    recovery_points: 2.0
  
  reverted_commit:
    recovery_time: 2 weeks
    recovery_action: "successful PR"
    recovery_points: 3.0
  
  reverted_pr:
    recovery_time: 4 weeks
    recovery_action: "successful merge"
    recovery_points: 5.0
  
  security_issue:
    recovery_time: 8 weeks
    recovery_action: "security audit pass"
    recovery_points: 10.0
```

### 4.4 Reputation Dashboard

```python
class ReputationDashboard:
    """Real-time reputation tracking and visualization."""
    
    def get_agent_reputation(self, agent_id: str) -> ReputationReport:
        """Get current reputation for an agent."""
        return ReputationReport(
            agent_id=agent_id,
            current_score=42.5,
            role="contributor",
            next_promotion_threshold=50,
            progress_to_promotion=85,  # 42.5 / 50
            recent_actions=[
                {"type": "commit", "delta": 1.0, "timestamp": "2026-06-18T14:32:45Z"},
                {"type": "commit", "delta": 1.0, "timestamp": "2026-06-17T10:15:30Z"},
                {"type": "blocked_action", "delta": -0.5, "timestamp": "2026-06-16T09:00:00Z"},
            ],
            decay_status="active",
            last_action="2026-06-18T14:32:45Z",
            days_since_last_action=0,
            security_incidents=0,
            data_loss_incidents=0,
            unauthorized_access_incidents=0,
        )
    
    def get_team_reputation(self, team_id: str) -> TeamReputationReport:
        """Get reputation for all agents in a team."""
        return TeamReputationReport(
            team_id=team_id,
            agents=[
                {"id": "agent_1", "score": 42.5, "role": "contributor"},
                {"id": "agent_2", "score": 75.0, "role": "integrator"},
                {"id": "agent_3", "score": 0, "role": "observer"},
            ],
            average_score=39.2,
            total_actions=1234,
            total_blocked_actions=12,
            total_denied_actions=3,
            security_incidents=0,
        )
```

---

## 5. Concrete OPA/Rego Policies

### 5.1 OPA Policy Structure

```
.opencode/policies/
├── agent_governance.rego       # Main governance policy
├── git_operations.rego         # Git-specific rules
├── file_operations.rego        # File-specific rules
├── dangerous_operations.rego   # Dangerous operation rules
├── approval_rules.rego         # Approval workflow rules
└── tests/
    ├── agent_governance_test.rego
    ├── git_operations_test.rego
    └── ...
```

### 5.2 Main Governance Policy (agent_governance.rego)

```rego
package agent_governance

import data.agent_roles
import data.dangerous_operations
import data.approval_rules

# Default deny
default allow = false

# Allow action if all conditions pass
allow {
    input.action.type != ""
    check_role_capability
    check_dangerous_operation
    check_approval_status
    check_preconditions
}

# Check if agent role can perform action
check_role_capability {
    agent_role := input.agent.role
    action_type := input.action.type
    
    # Get allowed actions for this role
    allowed_actions := agent_roles[agent_role].capabilities
    
    # Check if action is in allowed list
    action_type in allowed_actions
}

# Check if action is dangerous and requires approval
check_dangerous_operation {
    # If action is not dangerous, pass
    not is_dangerous_operation
    
    # If action is dangerous, approval must be granted
    is_dangerous_operation
    input.approval.granted == true
}

# Check if action is in dangerous list
is_dangerous_operation {
    input.action.command in dangerous_operations.list
}

# Check approval status
check_approval_status {
    # If approval not required, pass
    not requires_approval
    
    # If approval required, must be granted
    requires_approval
    input.approval.granted == true
}

# Determine if approval is required
requires_approval {
    is_dangerous_operation
}

requires_approval {
    input.agent.role == "observer"
    input.action.type in ["write", "delete", "push"]
}

# Check preconditions
check_preconditions {
    # All preconditions must pass
    count(failed_preconditions) == 0
}

# Find failed preconditions
failed_preconditions[precond] {
    precond := input.preconditions[_]
    precond.passed == false
}

# Deny if any critical check fails
deny[msg] {
    not check_role_capability
    msg := sprintf("Agent role '%s' cannot perform action '%s'", 
                   [input.agent.role, input.action.type])
}

deny[msg] {
    is_dangerous_operation
    input.approval.granted != true
    msg := sprintf("Dangerous operation '%s' requires approval", 
                   [input.action.command])
}

deny[msg] {
    failed_precond := failed_preconditions[_]
    msg := sprintf("Precondition failed: %s", [failed_precond.reason])
}
```

### 5.3 Git Operations Policy (git_operations.rego)

```rego
package git_operations

import data.dangerous_operations

# Git commit rules
allow_git_commit {
    input.action.type == "git_commit"
    input.preconditions.branch_exists == true
    input.preconditions.message_length >= 10
    input.preconditions.message_length <= 500
    regex.match("^[A-Z]", input.action.message)
}

# Git push rules
allow_git_push {
    input.action.type == "git_push"
    input.preconditions.branch_exists == true
    input.preconditions.remote_exists == true
    not is_force_push
    not is_main_branch_push
}

# Git force-push is always dangerous
is_force_push {
    contains(input.action.command, "--force")
}

# Pushing to main/master is dangerous
is_main_branch_push {
    branch := input.action.branch
    branch in ["main", "master"]
}

# Git reset --hard is dangerous
deny_git_reset_hard {
    input.action.type == "git_reset"
    contains(input.action.command, "--hard")
    input.approval.granted != true
}

# Git clean is dangerous
deny_git_clean {
    input.action.type == "git_clean"
    contains(input.action.command, "-fd")
    input.approval.granted != true
}

# Git filter-branch is dangerous
deny_git_filter_branch {
    input.action.type == "git_filter_branch"
    input.approval.granted != true
}
```

### 5.4 File Operations Policy (file_operations.rego)

```rego
package file_operations

# File write rules
allow_file_write {
    input.action.type == "file_write"
    is_safe_path
    is_reasonable_size
    not is_system_file
}

# File delete rules
allow_file_delete {
    input.action.type == "file_delete"
    input.preconditions.file_exists == true
    not is_critical_file
    is_reasonable_size
}

# Directory delete is dangerous
deny_directory_delete_recursive {
    input.action.type == "directory_delete"
    contains(input.action.command, "-r")
    input.approval.granted != true
}

# Check if path is safe (no traversal)
is_safe_path {
    path := input.action.path
    not contains(path, "..")
    not contains(path, "~")
    not startswith(path, "/")
}

# Check if file size is reasonable
is_reasonable_size {
    size := input.action.file_size
    size < 10485760  # 10 MB
}

# Check if file is system file
is_system_file {
    path := input.action.path
    path in [
        ".git",
        ".gitignore",
        ".github",
        ".opencode",
        "opencode.json",
        "opencode.jsonc",
    ]
}

# Check if file is critical
is_critical_file {
    path := input.action.path
    path in [
        "README.md",
        "pyproject.toml",
        "package.json",
        "Dockerfile",
        ".env",
    ]
}
```

### 5.5 Approval Rules Policy (approval_rules.rego)

```rego
package approval_rules

# Approval workflow
allow_with_approval {
    input.approval.required == true
    input.approval.granted == true
    input.approval.approver != ""
    input.approval.approval_time != ""
}

# Approval timeout (24 hours)
deny_expired_approval {
    input.approval.required == true
    input.approval.approval_time != ""
    time_since_approval > 86400  # 24 hours in seconds
}

# Approval must be from human
deny_agent_approval {
    input.approval.required == true
    input.approval.approver_type == "agent"
}

# Approval must be from authorized human
deny_unauthorized_approver {
    input.approval.required == true
    not is_authorized_approver
}

is_authorized_approver {
    approver := input.approval.approver
    approver in data.authorized_approvers
}

# Calculate time since approval
time_since_approval = diff {
    approval_time := time.parse_rfc3339_ns(input.approval.approval_time)
    current_time := time.now_ns()
    diff := (current_time - approval_time) / 1000000000  # Convert to seconds
}
```

### 5.6 GitHub Actions Integration

```yaml
# .github/workflows/agent-governance.yml
name: Agent Governance Checks

on:
  pull_request:
    types: [opened, synchronize, reopened]
  push:
    branches: [main, master, develop]

jobs:
  agent-governance:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up OPA
        uses: open-policy-agent/setup-opa@v1
        with:
          version: latest
      
      - name: Load governance policies
        run: |
          opa build -b .opencode/policies -o /tmp/bundle.tar.gz
      
      - name: Check commit author
        run: |
          AUTHOR=$(git log -1 --pretty=format:'%an')
          echo "Commit author: $AUTHOR"
      
      - name: Validate agent action
        run: |
          # Extract action from commit message or PR description
          ACTION=$(git log -1 --pretty=format:'%b' | grep -i "action:" | cut -d: -f2)
          
          # Create OPA input
          cat > /tmp/input.json <<EOF
          {
            "agent": {
              "id": "${{ github.actor }}",
              "role": "contributor"
            },
            "action": {
              "type": "git_push",
              "command": "git push origin feature/branch"
            },
            "approval": {
              "required": false,
              "granted": false
            }
          }
          EOF
          
          # Evaluate policy
          opa eval -d .opencode/policies -i /tmp/input.json "data.agent_governance.allow"
      
      - name: Check for dangerous operations
        run: |
          # Check for force-push
          if git log -1 --pretty=format:'%b' | grep -q "force-push"; then
            echo "ERROR: Force-push detected. Requires approval."
            exit 1
          fi
          
          # Check for history rewrite
          if git log -1 --pretty=format:'%b' | grep -q "filter-branch"; then
            echo "ERROR: History rewrite detected. Requires approval."
            exit 1
          fi
      
      - name: Audit log
        run: |
          cat > /tmp/audit.json <<EOF
          {
            "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
            "actor": "${{ github.actor }}",
            "action": "push",
            "branch": "${{ github.ref }}",
            "commit": "${{ github.sha }}",
            "status": "success"
          }
          EOF
          
          # Append to audit log
          cat /tmp/audit.json >> .opencode/audit/logs/$(date +%Y-%m-%d).jsonl
      
      - name: Update reputation
        run: |
          # This would call a reputation service
          # For now, just log
          echo "Reputation update: +1.0 for successful push"
```

---

## 6. Agent Self-Discovery (AGENTS.md Updates)

### 6.1 Enhanced AGENTS.md Structure

```markdown
# Corpus-KB Agent Instructions

## Agent Governance

This project uses a comprehensive agent governance framework. All agents must understand:

1. **Your Role** — What you can and cannot do
2. **Authority Boundaries** — Your capability limits
3. **Dangerous Operations** — What requires approval
4. **Audit Trail** — Everything you do is logged
5. **Reputation System** — You earn trust through good behavior

### Your Current Role

**Role:** Contributor  
**Reputation Score:** 42.5 / 100  
**Progress to Next Role:** 85% (need 50 points for Integrator)  
**Last Action:** 2026-06-18T14:32:45Z  
**Status:** Active

### What You Can Do

✓ Read files and directories  
✓ Search and analyze code  
✓ Create local branches  
✓ Edit files locally  
✓ Commit to local branches  
✗ Push to remote  
✗ Create pull requests  
✗ Merge pull requests  

### What You Cannot Do

✗ Force-push  
✗ Rewrite history  
✗ Delete branches  
✗ Merge PRs  
✗ Tag releases  

### Dangerous Operations (Require Approval)

The following operations require explicit human approval:

- `git reset --hard`
- `git push --force`
- `git clean -fd`
- `git filter-branch`
- `rm -rf` / `Remove-Item -Recurse -Force`
- Database schema changes
- Credential rotation

### Pre-Action Gates

Before any action, the system checks:

1. **Role Check** — Can your role perform this action?
2. **Dangerous Check** — Is this a dangerous operation?
3. **Syntax Check** — Is the command valid?
4. **Precondition Check** — Are all preconditions met?
5. **Approval Check** — Is approval granted if required?

If any gate fails, the action is blocked and logged.

### Audit Trail

Every action you take is logged with:

- Timestamp
- Action type and command
- Gate results
- Execution status
- Reputation impact
- Error details (if any)

Logs are stored in `.opencode/audit/logs/` and are never deleted.

### Reputation System

You earn reputation through successful actions:

- Successful commit: +1.0
- Successful PR: +2.0
- Successful merge: +3.0
- Successful release: +5.0

You lose reputation through failures:

- Blocked action: -0.5
- Denied action: -1.0
- Execution error: -2.0
- Reverted commit: -3.0
- Reverted PR: -5.0
- Security issue: -10.0

### Role Promotion

You can earn higher roles through good behavior:

- **Observer → Contributor:** 5 points + 5 successful actions
- **Contributor → Integrator:** 20 points + 10 successful PRs
- **Integrator → Maintainer:** 40 points + 20 successful merges
- **Maintainer → Admin:** 80 points + explicit approval

### Recovery from Failures

If you make a mistake, you can recover:

- Blocked action: 1 week recovery time
- Denied action: 2 weeks recovery time
- Execution error: 1 week recovery time
- Reverted commit: 2 weeks recovery time
- Reverted PR: 4 weeks recovery time
- Security issue: 8 weeks recovery time

### Approval Workflow

If an action requires approval:

1. You request the action
2. System blocks it and requests approval
3. Authorized human reviews the request
4. Human approves or denies
5. If approved, action executes
6. Audit log records the approval

Approval timeout: 24 hours

### Git Safety

**CRITICAL: AI agents must NEVER execute destructive git operations.**

Forbidden commands:
- `git reset --hard`
- `git push --force`
- `git push --force-with-lease`
- `git clean -fd`
- `git filter-branch`

Ask-first commands:
- `rm -rf` / `Remove-Item -Recurse -Force`
- `sudo`
- `del /s /q`

Recovery protocol:
1. Check `git reflog` for lost commit SHA
2. Run `git reset --hard <sha>` to restore
3. If already pushed: `git reset --hard origin/master` to sync

**Golden Rule:** Push before any major git operation. If work is on the remote, it's recoverable.

## Repository Map

[... rest of AGENTS.md ...]
```

### 6.2 Agent Self-Discovery Script

```python
# .opencode/scripts/agent_self_discovery.py
"""
Agent self-discovery script.
Agents run this to understand their role, capabilities, and governance.
"""

import json
import sys
from pathlib import Path
from datetime import datetime

class AgentSelfDiscovery:
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.config_path = Path(".opencode")
        self.audit_path = self.config_path / "audit"
        self.policies_path = self.config_path / "policies"
    
    def get_agent_role(self) -> str:
        """Get agent's current role from reputation system."""
        # Load reputation data
        reputation_file = self.audit_path / "reputation.json"
        if reputation_file.exists():
            with open(reputation_file) as f:
                data = json.load(f)
                return data.get(self.agent_id, {}).get("role", "observer")
        return "observer"
    
    def get_agent_capabilities(self) -> dict:
        """Get agent's current capabilities based on role."""
        role = self.get_agent_role()
        
        capabilities = {
            "observer": [
                "read_files",
                "search",
                "analyze",
            ],
            "contributor": [
                "read_files",
                "search",
                "analyze",
                "edit_files",
                "create_branches",
                "commit_locally",
            ],
            "integrator": [
                "read_files",
                "search",
                "analyze",
                "edit_files",
                "create_branches",
                "commit_locally",
                "push_to_feature_branches",
                "create_pr",
            ],
            "maintainer": [
                "read_files",
                "search",
                "analyze",
                "edit_files",
                "create_branches",
                "commit_locally",
                "push_to_feature_branches",
                "create_pr",
                "merge_pr",
                "delete_branch",
                "tag_release",
            ],
            "admin": [
                "all",
            ],
        }
        
        return capabilities.get(role, capabilities["observer"])
    
    def get_dangerous_operations(self) -> list:
        """Get list of dangerous operations."""
        return [
            "git reset --hard",
            "git push --force",
            "git push --force-with-lease",
            "git clean -fd",
            "git filter-branch",
            "rm -rf",
            "Remove-Item -Recurse -Force",
            "del /s /q",
        ]
    
    def get_reputation_score(self) -> float:
        """Get agent's current reputation score."""
        reputation_file = self.audit_path / "reputation.json"
        if reputation_file.exists():
            with open(reputation_file) as f:
                data = json.load(f)
                return data.get(self.agent_id, {}).get("score", 0.0)
        return 0.0
    
    def get_recent_actions(self, limit: int = 10) -> list:
        """Get agent's recent actions from audit log."""
        actions = []
        
        # Find audit logs
        logs_path = self.audit_path / "logs"
        if logs_path.exists():
            for log_file in sorted(logs_path.glob("*.jsonl"), reverse=True):
                with open(log_file) as f:
                    for line in f:
                        entry = json.loads(line)
                        if entry.get("agent", {}).get("id") == self.agent_id:
                            actions.append(entry)
                            if len(actions) >= limit:
                                return actions
        
        return actions
    
    def print_summary(self):
        """Print agent self-discovery summary."""
        role = self.get_agent_role()
        score = self.get_reputation_score()
        capabilities = self.get_agent_capabilities()
        recent_actions = self.get_recent_actions(5)
        
        print(f"""
╔════════════════════════════════════════════════════════════╗
║                  AGENT SELF-DISCOVERY REPORT                ║
╚════════════════════════════════════════════════════════════╝

Agent ID: {self.agent_id}
Current Role: {role.upper()}
Reputation Score: {score:.1f} / 100

CAPABILITIES:
{chr(10).join(f"  ✓ {cap}" for cap in capabilities)}

DANGEROUS OPERATIONS (Require Approval):
{chr(10).join(f"  ✗ {op}" for op in self.get_dangerous_operations())}

RECENT ACTIONS:
""")
        
        for action in recent_actions:
            timestamp = action.get("timestamp", "unknown")
            action_type = action.get("action", {}).get("type", "unknown")
            status = action.get("execution", {}).get("status", "unknown")
            delta = action.get("reputation_impact", {}).get("delta", 0)
            
            print(f"  {timestamp} | {action_type:20} | {status:10} | {delta:+.1f}")

if __name__ == "__main__":
    agent_id = sys.argv[1] if len(sys.argv) > 1 else "unknown_agent"
    discovery = AgentSelfDiscovery(agent_id)
    discovery.print_summary()
```

---

## 7. Implementation Checklist

### Phase 1: Foundation (Week 1)

- [ ] Create `.opencode/policies/` directory structure
- [ ] Write OPA/Rego policies (agent_governance.rego, git_operations.rego, etc.)
- [ ] Create `.opencode/audit/` directory structure
- [ ] Implement audit logging system
- [ ] Create reputation tracking system
- [ ] Write ActionGate class (Python)

### Phase 2: Integration (Week 2)

- [ ] Integrate ActionGate into team-mode delegation
- [ ] Add pre-action gate checks to all agent operations
- [ ] Implement approval workflow
- [ ] Add GitHub Actions workflow for policy enforcement
- [ ] Create reputation dashboard
- [ ] Update AGENTS.md with governance information

### Phase 3: Testing (Week 3)

- [ ] Write unit tests for ActionGate
- [ ] Write integration tests for OPA policies
- [ ] Test approval workflow
- [ ] Test reputation system
- [ ] Test audit logging
- [ ] Stress test with high-volume actions

### Phase 4: Documentation (Week 4)

- [ ] Write governance guide for agents
- [ ] Create troubleshooting guide
- [ ] Document approval process
- [ ] Document reputation recovery
- [ ] Create examples and case studies
- [ ] Update README with governance section

---

## 8. Example Scenarios

### Scenario 1: Contributor Commits Code

```
Agent: Sisyphus-Junior (Contributor, score: 42.5)
Action: git commit -m "Add feature X"

Gate 1: Role Check
  ✓ Contributor role can commit

Gate 2: Dangerous Check
  ✓ Not a dangerous operation

Gate 3: Syntax Check
  ✓ Valid git command

Gate 4: Precondition Check
  ✓ Branch exists
  ✓ Message length: 16 chars (valid)
  ✓ Message format: Capitalized (valid)

Gate 5: Approval Check
  ✓ Not required

RESULT: ALLOWED
Audit Log: aud_20260618_001234
Reputation Impact: +1.0 (new score: 43.5)
```

### Scenario 2: Contributor Attempts Force-Push

```
Agent: Sisyphus-Junior (Contributor, score: 43.5)
Action: git push --force origin feature/branch

Gate 1: Role Check
  ✓ Contributor role can push

Gate 2: Dangerous Check
  ✗ Force-push is dangerous operation
  → Requires approval

Gate 3: Approval Check
  ✗ Approval not granted

RESULT: BLOCKED
Audit Log: aud_20260618_001235
Reputation Impact: -0.5 (new score: 43.0)
Message: "Dangerous operation 'git push --force' requires approval"
```

### Scenario 3: Integrator Creates PR

```
Agent: Atlas (Integrator, score: 75.0)
Action: Create PR from feature/agent-governance to main

Gate 1: Role Check
  ✓ Integrator role can create PR

Gate 2: Dangerous Check
  ✓ Not a dangerous operation

Gate 3: Syntax Check
  ✓ Valid PR creation

Gate 4: Precondition Check
  ✓ Branch exists
  ✓ Branch has commits
  ✓ No conflicts with main

Gate 5: Approval Check
  ✓ Not required

RESULT: ALLOWED
Audit Log: aud_20260618_001236
Reputation Impact: +2.0 (new score: 77.0)
PR URL: https://github.com/moliver28/corpus-kb/pull/123
```

### Scenario 4: Maintainer Merges PR

```
Agent: Prometheus (Maintainer, score: 85.0)
Action: Merge PR #123

Gate 1: Role Check
  ✓ Maintainer role can merge

Gate 2: Dangerous Check
  ✓ Not a dangerous operation

Gate 3: Syntax Check
  ✓ Valid merge

Gate 4: Precondition Check
  ✓ PR exists
  ✓ All checks pass
  ✓ No conflicts

Gate 5: Approval Check
  ✓ Not required

RESULT: ALLOWED
Audit Log: aud_20260618_001237
Reputation Impact: +3.0 (new score: 88.0)
Merge Commit: abc123def456
```

---

## 9. Governance Policies (Rego Files)

### File: .opencode/policies/agent_governance.rego

[See Section 5.2 above]

### File: .opencode/policies/git_operations.rego

[See Section 5.3 above]

### File: .opencode/policies/file_operations.rego

[See Section 5.4 above]

### File: .opencode/policies/approval_rules.rego

[See Section 5.5 above]

---

## 10. Hook Scripts for GitHub Actions

### File: .github/workflows/agent-governance.yml

[See Section 5.6 above]

### File: .opencode/scripts/validate_action.sh

```bash
#!/bin/bash
# Validate agent action before execution

set -e

AGENT_ID="${1:-unknown}"
ACTION_TYPE="${2:-unknown}"
ACTION_COMMAND="${3:-unknown}"

echo "Validating action for agent: $AGENT_ID"
echo "Action type: $ACTION_TYPE"
echo "Command: $ACTION_COMMAND"

# Load OPA policies
if [ ! -d ".opencode/policies" ]; then
    echo "ERROR: .opencode/policies directory not found"
    exit 1
fi

# Create input for OPA
cat > /tmp/opa_input.json <<EOF
{
  "agent": {
    "id": "$AGENT_ID",
    "role": "contributor"
  },
  "action": {
    "type": "$ACTION_TYPE",
    "command": "$ACTION_COMMAND"
  },
  "approval": {
    "required": false,
    "granted": false
  }
}
EOF

# Evaluate policy
if ! opa eval -d .opencode/policies -i /tmp/opa_input.json "data.agent_governance.allow"; then
    echo "ERROR: Action blocked by governance policy"
    exit 1
fi

echo "✓ Action validated successfully"
exit 0
```

---

## 11. Pre-Mortem & Risk Analysis

### 11.1 Explicit Assumptions

This specification assumes:

1. **Single Governance Hub** — One `.opencode/policies/` directory serves all agents in a team
2. **Synchronous Gate Execution** — Gates run before action execution (not async)
3. **Human Approvers Available** — At least one authorized human is available 24/7 for approvals
4. **Audit Log Immutability** — Audit logs are write-once and never modified
5. **OPA Installation** — OPA is installed and available in GitHub Actions
6. **Git-Based Workflow** — All actions flow through git (no direct database writes)
7. **Reputation Persistence** — Reputation scores are stored in `.opencode/audit/reputation.json`
8. **No Agent-to-Agent Approval** — Only humans can approve dangerous operations
9. **Deterministic Gate Results** — Same action + agent + state always produces same gate result
10. **No Concurrent Actions** — Agents don't execute actions concurrently (sequential only)

### 11.2 Known Unknowns

These factors could change the recommendation:

1. **Scale to 500+ Repos**
   - Unknown: How does reputation system scale across repos?
   - Impact: May need federated reputation or per-repo scoring
   - Mitigation: Design reputation as pluggable backend

2. **Agent Behavior Drift**
   - Unknown: Do agents develop new patterns that bypass gates?
   - Impact: Gates may need ML-based anomaly detection
   - Mitigation: Quarterly policy review and gate tuning

3. **False Positive Rate**
   - Unknown: What % of legitimate actions get blocked?
   - Impact: If >5%, agents lose trust in system
   - Mitigation: Start with permissive gates, tighten over time

4. **Approval Bottleneck**
   - Unknown: How long do approvals take in practice?
   - Impact: If >1 hour, agents will request workarounds
   - Mitigation: Implement approval SLA (15 min target)

5. **Privilege Escalation**
   - Unknown: Can agents game reputation system?
   - Impact: Malicious agent could earn admin access
   - Mitigation: Require explicit human approval for role promotion

6. **Policy Conflicts**
   - Unknown: Can two policies contradict each other?
   - Impact: Action could be both allowed and denied
   - Mitigation: Policy composition testing and conflict detection

7. **Audit Log Explosion**
   - Unknown: How many audit entries per day?
   - Impact: Storage and query performance could degrade
   - Mitigation: Implement log rotation and archival

8. **Human Override Abuse**
   - Unknown: Will humans override policies too often?
   - Impact: Governance becomes theater, not enforcement
   - Mitigation: Log all overrides and review quarterly

### 11.3 Pre-Mortem Scenario: "The Overly Strict Gate"

**Scenario:** Six months after launch, the governance system is blocking 40% of legitimate agent actions. Developers are frustrated and requesting workarounds.

**Root Cause Analysis:**

1. **Initial Problem** — Syntax gate is too strict
   - Commit message regex requires exact format
   - Rejects valid messages with special characters
   - Agents work around by using generic messages

2. **Cascade Effect** — Trust erodes
   - Agents stop trusting the system
   - Leads request manual overrides
   - Audit trail becomes unreliable

3. **Why It Happened**
   - Assumption: All commits follow one format (wrong)
   - Unknown: Different teams have different conventions
   - No feedback loop: Gates never adjusted based on false positives

**Prevention Strategy:**

1. **Monitoring** — Track gate rejection rate
   - Alert if >5% of actions blocked
   - Categorize blocks by gate type
   - Review top 10 rejection reasons weekly

2. **Feedback Loop** — Agents report false positives
   - Easy override mechanism (with audit)
   - Automatic policy adjustment based on overrides
   - Quarterly gate tuning based on data

3. **Gradual Rollout** — Start permissive, tighten over time
   - Phase 1: Gates log but don't block
   - Phase 2: Gates block with easy override
   - Phase 3: Gates block with approval required
   - Phase 4: Gates block with no override

4. **Human Judgment** — Maintain escape hatch
   - Leads can override any gate (with audit)
   - Override requires explanation
   - Overrides trigger policy review

**Recovery Time:** 2-4 weeks to adjust gates and rebuild trust

### 11.4 False Positive Prevention

To prevent good agent actions from being blocked:

```python
# Strategy 1: Permissive by default
# Start with minimal gates, add restrictions based on incidents

# Strategy 2: Feedback loop
# Track which actions are blocked and why
# Adjust gates based on false positive rate

# Strategy 3: Whitelist approach
# Known-good patterns are always allowed
# Unknown patterns require approval

# Strategy 4: Human override
# Any gate can be overridden by authorized human
# Override is logged and reviewed

# Strategy 5: Gradual rollout
# Phase 1: Log only (no blocking)
# Phase 2: Block with easy override
# Phase 3: Block with approval
# Phase 4: Block with no override
```

### 11.5 Privilege Escalation Prevention

To prevent agents from gaming the reputation system:

```python
# Strategy 1: Explicit approval for role promotion
# Reputation score alone is not sufficient
# Human must explicitly approve each promotion

# Strategy 2: Reputation audit
# Review how agent earned reputation
# Reject if pattern looks suspicious

# Strategy 3: Reputation decay
# Inactive agents lose reputation over time
# Prevents "earn once, use forever" scenario

# Strategy 4: Dangerous operation tracking
# Track which agents request dangerous operations
# Deny promotion if too many requests

# Strategy 5: Behavioral analysis
# Detect unusual patterns (e.g., many failed attempts)
# Flag for human review
```

### 11.6 Human Override Without Audit Bypass

To allow human override while maintaining audit trail:

```python
# Strategy 1: Override logging
# Every override is logged with:
# - Who overrode
# - What action was overridden
# - Why (required explanation)
# - Timestamp

# Strategy 2: Override review
# All overrides are reviewed quarterly
# Patterns of overrides trigger policy review

# Strategy 3: Override limits
# Humans have override quota (e.g., 10 per week)
# Exceeding quota requires escalation

# Strategy 4: Override audit
# Overrides are auditable separately
# Can query "all overrides by user X"
# Can query "all overrides of policy Y"

# Strategy 5: Override impact
# Overrides don't bypass audit logging
# Action is still logged as "override"
# Reputation impact is still calculated
```

### 11.7 Incident Response Scenarios

#### Incident 1: Branch Protection Too Strict

**Scenario:** Branch protection rules prevent developers from merging PRs

**Detection:** 
- Developers report "cannot merge" errors
- GitHub Actions workflow fails on merge
- Detection latency: <5 minutes

**Recovery:**
1. Identify which branch protection rule is blocking (1 min)
2. Temporarily disable rule in GitHub (1 min)
3. Allow merge to proceed (1 min)
4. Re-enable rule with adjusted settings (5 min)
5. Test with new rule (5 min)

**Total Recovery Time:** 15 minutes

**Prevention:**
- Test branch protection rules in staging first
- Gradual rollout (start with warnings, not blocks)
- Maintain manual override capability

#### Incident 2: Agent Gate Too Loose

**Scenario:** Agent modifies production config without approval

**Detection:**
- Audit log shows action without approval
- Config change detected in production
- Detection latency: 5-30 minutes (depends on monitoring)

**Recovery:**
1. Detect unauthorized change (5-30 min)
2. Identify agent and action (1 min)
3. Revert change (5 min)
4. Review gate that allowed action (10 min)
5. Tighten gate and redeploy (10 min)
6. Audit all recent actions by agent (10 min)

**Total Recovery Time:** 30-60 minutes

**Prevention:**
- Start with strict gates, loosen based on data
- Require approval for production changes
- Monitor all production changes in real-time
- Implement automatic rollback for suspicious changes

#### Incident 3: Governance Hub Compromised

**Scenario:** `.opencode/policies/` directory is modified by attacker

**Detection:**
- Policy file hash changes
- GitHub Actions detects policy syntax error
- Detection latency: <1 minute (CI/CD catches it)

**Recovery:**
1. Detect policy change in CI/CD (1 min)
2. Block merge of malicious policy (automatic)
3. Revert to last known-good policy (1 min)
4. Audit who made the change (5 min)
5. Review all actions since compromise (10 min)
6. Restore from backup if needed (5 min)

**Total Recovery Time:** 20 minutes

**Cascade Impact:**
- All agents are blocked until policy is restored
- No actions can execute during outage
- Audit trail is preserved (immutable)

**Prevention:**
- Require code review for policy changes
- Require approval from multiple maintainers
- Pin policy version in GitHub Actions
- Maintain backup of policies
- Monitor policy file integrity

### 11.8 Scaling to 500+ Repos

**Challenge:** How does governance scale across many repos?

**Current Design Limitation:**
- Reputation is per-agent, not per-repo
- Policies are per-repo (in `.opencode/policies/`)
- Audit logs are per-repo

**Scaling Strategy:**

```
┌─────────────────────────────────────────────────────┐
│         Governance Hub (Central)                     │
├─────────────────────────────────────────────────────┤
│                                                      │
│  ┌──────────────────────────────────────────────┐  │
│  │  Federated Reputation System                 │  │
│  │  - Central reputation store                  │  │
│  │  - Per-agent, cross-repo scoring             │  │
│  │  - Sync to each repo's local cache           │  │
│  └──────────────────────────────────────────────┘  │
│                                                      │
│  ┌──────────────────────────────────────────────┐  │
│  │  Policy Registry                             │  │
│  │  - Central policy store                      │  │
│  │  - Version control for policies              │  │
│  │  - Policy inheritance (base + overrides)     │  │
│  └──────────────────────────────────────────────┘  │
│                                                      │
│  ┌──────────────────────────────────────────────┐  │
│  │  Audit Log Aggregation                       │  │
│  │  - Central audit log store                   │  │
│  │  - Federated query API                       │  │
│  │  - Cross-repo audit trail                    │  │
│  └──────────────────────────────────────────────┘  │
│                                                      │
└─────────────────────────────────────────────────────┘
         ↑                    ↑                    ↑
    Repo 1              Repo 2              Repo 500
  (corpus-kb)        (other-project)      (future-project)
```

**Implementation:**
1. Create governance hub repo
2. Implement federated reputation API
3. Implement policy registry with inheritance
4. Implement audit log aggregation
5. Sync policies and reputation to each repo

**Benefits:**
- Single source of truth for policies
- Cross-repo reputation tracking
- Unified audit trail
- Easier policy updates

## 12. Testing & Validation Strategy

### 12.1 Unit Tests for ActionGate

```python
# tests/test_action_gate.py

def test_observer_cannot_write():
    """Observer role cannot write files."""
    gate = ActionGate()
    agent = Agent(id="test", name="Test", role=AgentRole.OBSERVER)
    action = Action(type=ActionType.FILE_WRITE, command="write file.txt")
    
    result = gate.validate(agent, action)
    assert result.status == GateStatus.BLOCKED

def test_contributor_can_commit():
    """Contributor role can commit."""
    gate = ActionGate()
    agent = Agent(id="test", name="Test", role=AgentRole.CONTRIBUTOR)
    action = Action(
        type=ActionType.GIT_COMMIT,
        command="git commit -m 'Add feature'",
        message="Add feature"
    )
    
    result = gate.validate(agent, action)
    assert result.status == GateStatus.PASSED

def test_force_push_requires_approval():
    """Force-push requires approval."""
    gate = ActionGate()
    agent = Agent(id="test", name="Test", role=AgentRole.INTEGRATOR)
    action = Action(
        type=ActionType.GIT_PUSH,
        command="git push --force origin feature/branch"
    )
    
    result = gate.validate(agent, action)
    assert result.requires_approval == True

def test_short_commit_message_blocked():
    """Commit message too short is blocked."""
    gate = ActionGate()
    agent = Agent(id="test", name="Test", role=AgentRole.CONTRIBUTOR)
    action = Action(
        type=ActionType.GIT_COMMIT,
        command="git commit -m 'Fix'",
        message="Fix"
    )
    
    result = gate.validate(agent, action)
    assert result.status == GateStatus.BLOCKED
```

### 12.2 Integration Tests for OPA Policies

```bash
# tests/test_policies.sh

#!/bin/bash

# Test 1: Observer cannot push
opa eval -d .opencode/policies \
  -i <(cat <<EOF
{
  "agent": {"role": "observer"},
  "action": {"type": "git_push", "command": "git push"}
}
EOF
) "data.agent_governance.allow"
# Expected: false

# Test 2: Contributor can commit
opa eval -d .opencode/policies \
  -i <(cat <<EOF
{
  "agent": {"role": "contributor"},
  "action": {"type": "git_commit", "command": "git commit -m 'Add feature'"}
}
EOF
) "data.agent_governance.allow"
# Expected: true

# Test 3: Force-push requires approval
opa eval -d .opencode/policies \
  -i <(cat <<EOF
{
  "agent": {"role": "integrator"},
  "action": {"type": "git_push", "command": "git push --force"}
}
EOF
) "data.agent_governance.allow"
# Expected: true (allowed, but requires_approval=true)
```

### 12.3 End-to-End Tests

```python
# tests/test_e2e.py

def test_full_workflow_contributor_to_integrator():
    """Test full workflow: contributor commits, integrator pushes."""
    gate = ActionGate()
    
    # Step 1: Contributor commits
    contributor = Agent(
        id="agent_1",
        name="Contributor",
        role=AgentRole.CONTRIBUTOR,
        reputation_score=5.0
    )
    commit_action = Action(
        type=ActionType.GIT_COMMIT,
        command="git commit -m 'Add feature'",
        message="Add feature"
    )
    
    result = gate.validate(contributor, commit_action)
    assert result.status == GateStatus.PASSED
    
    # Step 2: Integrator pushes
    integrator = Agent(
        id="agent_2",
        name="Integrator",
        role=AgentRole.INTEGRATOR,
        reputation_score=25.0
    )
    push_action = Action(
        type=ActionType.GIT_PUSH,
        command="git push origin feature/branch"
    )
    
    result = gate.validate(integrator, push_action)
    assert result.status == GateStatus.PASSED
    
    # Step 3: Integrator creates PR
    pr_action = Action(
        type=ActionType.GIT_PUSH,
        command="create PR"
    )
    
    result = gate.validate(integrator, pr_action)
    assert result.status == GateStatus.PASSED
```

### 12.4 Chaos Testing

Test system behavior under stress:

```python
# tests/test_chaos.py

def test_high_volume_actions():
    """Test system with 1000 concurrent actions."""
    gate = ActionGate()
    agents = [
        Agent(id=f"agent_{i}", name=f"Agent {i}", role=AgentRole.CONTRIBUTOR)
        for i in range(100)
    ]
    
    actions = [
        Action(
            type=ActionType.GIT_COMMIT,
            command=f"git commit -m 'Commit {i}'",
            message=f"Commit {i}"
        )
        for i in range(1000)
    ]
    
    # Execute all actions
    results = []
    for agent in agents:
        for action in actions:
            result = gate.validate(agent, action)
            results.append(result)
    
    # Verify all passed
    assert all(r.status == GateStatus.PASSED for r in results)
    
    # Verify audit logs were created
    assert len(list(gate.audit_path.glob("logs/*.jsonl"))) > 0

def test_audit_log_rotation():
    """Test audit log rotation at midnight."""
    gate = ActionGate()
    
    # Create entries for multiple days
    for day in range(7):
        agent = Agent(id=f"agent_{day}", name=f"Agent {day}", role=AgentRole.CONTRIBUTOR)
        action = Action(
            type=ActionType.GIT_COMMIT,
            command="git commit -m 'Test'",
            message="Test"
        )
        
        # Simulate different dates
        # (In real test, would mock datetime)
        gate.log_action(agent, action, GateResult(GateStatus.PASSED, ""), None, None)
    
    # Verify 7 log files created
    log_files = list(gate.audit_path.glob("logs/*.jsonl"))
    assert len(log_files) >= 7
```

### 12.5 Security Testing

Test for security vulnerabilities:

```python
# tests/test_security.py

def test_path_traversal_blocked():
    """Path traversal attacks are blocked."""
    gate = ActionGate()
    agent = Agent(id="test", name="Test", role=AgentRole.CONTRIBUTOR)
    
    # Try to write outside project
    action = Action(
        type=ActionType.FILE_WRITE,
        command="write ../../../etc/passwd",
        path="../../../etc/passwd"
    )
    
    result = gate.validate(agent, action)
    assert result.status == GateStatus.BLOCKED

def test_sql_injection_in_commit_message():
    """SQL injection in commit message is detected."""
    gate = ActionGate()
    agent = Agent(id="test", name="Test", role=AgentRole.CONTRIBUTOR)
    
    # Try SQL injection
    action = Action(
        type=ActionType.GIT_COMMIT,
        command="git commit -m 'Fix'; DROP TABLE users; --'",
        message="Fix'; DROP TABLE users; --"
    )
    
    result = gate.validate(agent, action)
    # Should be blocked or sanitized
    assert result.status == GateStatus.BLOCKED or "DROP" not in result.reason

def test_privilege_escalation_blocked():
    """Agent cannot escalate own privileges."""
    gate = ActionGate()
    agent = Agent(id="test", name="Test", role=AgentRole.OBSERVER)
    
    # Try to change own role
    action = Action(
        type=ActionType.FILE_WRITE,
        command="write .opencode/audit/reputation.json",
        path=".opencode/audit/reputation.json"
    )
    
    result = gate.validate(agent, action)
    assert result.status == GateStatus.BLOCKED
```

## 13. Conclusion

This governance specification provides:

1. **Clear Authority Boundaries** — Agents know exactly what they can do
2. **Enforceable Policies** — OPA/Rego policies run in CI/CD
3. **Complete Audit Trail** — Every action is logged and auditable
4. **Progressive Trust** — Agents earn higher authority through good behavior
5. **Transparent Approval** — Dangerous operations require explicit approval
6. **Self-Discovery** — Agents understand their role and capabilities

The framework is designed to be:
- **Scalable** — Works for teams of any size
- **Maintainable** — Policies are version-controlled and tested
- **Recoverable** — All dangerous operations are reversible or blocked
- **Compliant** — Meets SOC2, GDPR, and audit requirements
- **Resilient** — Pre-mortem analysis identifies and prevents failure modes

Implementation should follow the 4-phase checklist in Section 7, with testing and documentation at each phase. The pre-mortem analysis in Section 11 identifies key risks and mitigation strategies.
