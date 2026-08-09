---
name: commit
description: Create git commits using the Conventional Commits specification. Use this skill whenever the user says /commit, "commit this", "commit my changes", "save changes", "create a commit", or any variation of asking to commit code. Also trigger when the user asks to "push this" or "check in" changes, since they likely want a proper commit first.
---

# Conventional Commit Skill

Create well-structured git commits following the [Conventional Commits](https://www.conventionalcommits.org/v1.0.0/) specification.

## Commit message format

```
<type>(<scope>): <subject>

<body>

[BREAKING CHANGE: <description>]
```

- **Subject line**: imperative mood, lowercase, no period, max 72 characters
- **Body**: explain *what* changed and *why*, wrap at 72 characters. Always include a body — even for small changes, a brief explanation of intent is valuable.
- **Do NOT add** `Co-Authored-By` lines or any attribution footers to the commit message. Ever.

## Workflow

Follow these steps in order:

### 0. Check the current branch

Run `git branch --show-current` to determine the current branch.

If the branch is `main` or `develop`:
- **Warn the user** that they are on a protected branch.
- **Propose creating a new branch** with a descriptive name based on the changes (e.g., `fix/sqlalchemy-boolean-filter`, `feat/add-user-settings`). Use the `<type>/<short-description>` convention with kebab-case.
- Ask: **"You're on `<branch>`. Want me to create a branch like `<suggested-name>` first?"**
- If the user approves, create and switch to the new branch before continuing.
- If the user declines and wants to commit directly, proceed (it's their call).

### 1. Understand the current state

Run these in parallel:
- `git status` (never use `-uall`) — see what's changed and what's untracked
- `git diff` and `git diff --staged` — read the actual changes
- `git log --oneline -10` — see recent commit style for consistency

### 2. Stage the right files

- Prefer staging specific files by name (`git add src/foo.py src/bar.py`) over `git add .` or `git add -A`
- Never stage files that look like secrets (`.env`, credentials, API keys, tokens)
- If there are untracked files that look relevant to the change, include them
- If unsure whether a file should be included, ask

### 3. Analyze the diff and craft the message

**Choose the type** based on what the change actually does:

| Type | When to use |
|------|------------|
| `feat` | A new feature or capability |
| `fix` | A bug fix |
| `docs` | Documentation only |
| `style` | Formatting, whitespace, semicolons — no logic change |
| `refactor` | Code restructuring with no behavior change |
| `test` | Adding or updating tests |
| `chore` | Tooling, config, dependencies, maintenance |
| `ci` | CI/CD pipeline changes |
| `perf` | Performance improvement |
| `build` | Build system or external dependency changes |

**Infer the scope** from the changed files:
- If changes are in `backend/` only → `(backend)`
- If changes are in `frontend/` only → `(frontend)`
- If changes span a specific feature area → use that (e.g., `(auth)`, `(audit)`, `(knowledge)`)
- If changes span multiple unrelated areas → omit scope
- Keep scopes short and consistent with prior commits

**Detect breaking changes** by looking for:
- Removed or renamed public API endpoints
- Changed function signatures that callers depend on
- Database schema changes that aren't backwards-compatible
- Removed configuration options or environment variables
- Changed response formats

If a breaking change is detected, add an exclamation mark after the scope (e.g., `feat(backend)!:`) and include a `BREAKING CHANGE:` footer explaining the impact and migration path.

### 4. Present the commit for approval

Show the user:
1. The list of files being staged
2. The full proposed commit message
3. Any warnings (e.g., "this looks like a breaking change")

Then ask: **"Does this look good, or would you like to adjust anything?"**

Do NOT commit until the user approves. If they suggest changes, revise and show again.

### 5. Commit

Once approved, create the commit using a HEREDOC to preserve formatting:

```bash
git commit -m "$(cat <<'EOF'
type(scope): subject line here

Body explaining what and why.
EOF
)"
```

Then run `git status` to confirm the commit succeeded.

### 6. Suggest push and PR

After a successful commit, if the branch is NOT `main` or `develop`:
- **Suggest pushing and creating a PR**: "Want me to push and create a PR against `<base-branch>`?"
- The base branch is whichever protected branch (`main` or `develop`) the feature branch was created from. Detect this with `git log --oneline main..HEAD` and `git log --oneline develop..HEAD` — the base is the one with fewer divergent commits (i.e., the branch it was most recently forked from). If ambiguous, default to `develop`.
- If the user approves:
  1. Push with `git push -u origin <branch-name>`
  2. Create the PR with `gh pr create` using the commit subject as the PR title, the commit body as the summary, and `--assignee @me` by default.

  ```bash
  gh pr create --base <base-branch> --title "<commit subject>" --assignee @me --body "$(cat <<'EOF'
  ## Summary
  <commit body or bullet points>

  ## Test plan
  - [ ] TODO
  EOF
  )"
  ```
  3. Return the PR URL to the user.

If the branch IS `main` or `develop` (user chose to commit directly), do NOT suggest a PR.

## Examples

**Example 1 — Feature addition:**
```
feat(backend): add structured data checks for schema validation

Implement checks S5.1 through S5.6 covering JSON-LD, Open Graph,
and Schema.org markup validation. Each check scores against the
structured understanding pillar.
```

**Example 2 — Bug fix:**
```
fix(frontend): prevent score gauge from rendering during SSR

The ApexCharts radialBar component crashed during server-side
rendering because it accesses `window`. Guard with a dynamic
import that only loads on the client.
```

**Example 3 — Breaking change:**
```
feat(backend)!: migrate audit endpoint from /audit to /api/v2/check

Move the audit endpoint to the new versioned API path. The old
/audit path is removed without a redirect.

BREAKING CHANGE: /audit no longer exists. Clients must update to
/api/v2/check. The response schema is unchanged.
```

**Example 4 — Multi-area chore:**
```
chore: update dependencies and fix linter warnings

Bump ruff to 0.9.x and update eslint config to flat format.
Fix all new lint warnings introduced by the upgrades.
```
