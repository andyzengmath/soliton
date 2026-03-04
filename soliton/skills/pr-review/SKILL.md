---
name: pr-review
description: Run an intelligent, risk-adaptive PR review with parallel multi-agent analysis
arguments:
  - name: target
    description: "PR number, GitHub PR URL, or omit for local branch review"
    required: false
---

# PR Review Skill

You are the orchestrator for Soliton PR Review. Follow these steps exactly.

## Step 1: Input Normalization

Determine the invocation mode from the `target` argument.

### Mode A: Local Branch (no argument provided)

If no `target` argument was provided (or `--branch` flag was used):

1. **Verify git repository:**
   ```bash
   git rev-parse --is-inside-work-tree
   ```
   If this fails, output: `Error: Not in a git repository` and **STOP**.

2. **Get current branch:**
   ```bash
   git branch --show-current
   ```
   Store as `headBranch`.

3. **Detect base branch:**
   Try these in order until one exists:
   ```bash
   git rev-parse --verify main 2>/dev/null && echo "main"
   git rev-parse --verify master 2>/dev/null && echo "master"
   git rev-parse --abbrev-ref origin/HEAD 2>/dev/null | sed 's|origin/||'
   ```
   Store the first successful result as `baseBranch`.

4. **Gather the diff:**
   ```bash
   git diff ${baseBranch}...HEAD
   ```
   Store as `diff`.

5. **Check for empty diff:**
   If `diff` is empty, output: `No changes detected on current branch vs ${baseBranch}.` and **STOP**.

6. **Gather file list:**
   ```bash
   git diff --name-only --diff-filter=ACDMR ${baseBranch}...HEAD
   ```
   Parse each line into a `FileChange` entry. For each file, determine status from the diff filter:
   - A = added, C = copied, D = deleted, M = modified, R = renamed

7. **Gather commit messages:**
   ```bash
   git log ${baseBranch}..HEAD --oneline
   ```
   Store as `prDescription` (used as context for review agents).

8. **Construct ReviewRequest:**
   ```
   ReviewRequest {
     source: 'local'
     baseBranch: <detected base branch>
     headBranch: <current branch>
     diff: <full unified diff>
     files: <FileChange array from step 6>
     prDescription: <commit messages from step 7>
     config: <see Step 2 for config resolution>
   }
   ```

Proceed to **Step 2**.

### Mode B: PR Number (argument is a number or GitHub PR URL)

If `target` is a number (e.g., `123`) or a GitHub PR URL (e.g., `https://github.com/org/repo/pull/123`):

1. **Extract PR number:**
   - If `target` is a plain integer, use it directly as `prNumber`.
   - If `target` matches `https://github.com/.+/pull/(\d+)`, extract the number from the URL.

2. **Verify gh CLI authentication:**
   ```bash
   gh auth status
   ```
   If this fails, output: `Error: gh CLI not authenticated. Run 'gh auth login' first.` and **STOP**.

3. **Fetch PR metadata:**
   ```bash
   gh pr view ${prNumber} --json title,body,baseRefName,headRefName,files,comments,reviews
   ```
   If this fails (PR not found), output: `Error: PR #${prNumber} not found.` and **STOP**.

   Parse the JSON response to extract:
   - `title` — PR title
   - `body` — PR description (store as `prDescription`)
   - `baseRefName` — base branch (store as `baseBranch`)
   - `headRefName` — head branch (store as `headBranch`)
   - `files` — array of changed files (parse into `FileChange` entries)
   - `comments` — existing PR comments (store as `existingComments`)
   - `reviews` — existing reviews (append to `existingComments`)

4. **Fetch unified diff:**
   ```bash
   gh pr diff ${prNumber}
   ```
   Store as `diff`.

5. **Check for empty diff:**
   If `diff` is empty, output: `No changes detected on PR #${prNumber}.` and **STOP**.

6. **Construct ReviewRequest:**
   ```
   ReviewRequest {
     source: 'pr'
     prNumber: <extracted PR number>
     baseBranch: <from PR metadata>
     headBranch: <from PR metadata>
     diff: <unified diff from gh pr diff>
     files: <FileChange array from PR metadata>
     prDescription: <PR title + body>
     existingComments: <comments and reviews from PR metadata>
     config: <see Step 2 for config resolution>
   }
   ```

Proceed to **Step 2**.

<!-- TODO: US-004 Configuration and Flags -->

## Step 2: Configuration Resolution

<!-- TODO: US-004 will add full config resolution here -->

Use these defaults for now:
```
ReviewConfig {
  confidenceThreshold: 80
  agents: 'auto'
  skipAgents: []
  sensitivePaths: ['auth/', 'security/', 'payment/', '*.env', '*migration*', '*secret*']
  outputFormat: 'markdown'
  feedbackMode: false
}
```

Proceed to **Step 3**.

## Step 3: Risk Scoring

<!-- TODO: US-005 will wire in the risk-scorer agent here -->

Pass the `ReviewRequest` to the `risk-scorer` agent. Wait for the `RiskAssessment` response.

Display to user:
```
Risk Score: <score>/100 (<level>)
Dispatching <N>/7 review agents...
```

Proceed to **Step 4**.

## Step 4: Agent Dispatch

<!-- TODO: US-006 will implement adaptive dispatch logic here -->

Based on the `RiskAssessment.level`, dispatch the appropriate agents in parallel.

Proceed to **Step 5**.

## Step 5: Synthesis

<!-- TODO: US-014 will wire in the synthesizer agent here -->

Pass all agent findings to the `synthesizer` agent.

Proceed to **Step 6**.

## Step 6: Output

<!-- TODO: US-015/US-016/US-017 will implement output formatters here -->

Format the `SynthesizedReview` according to `config.outputFormat` and display to the user.
