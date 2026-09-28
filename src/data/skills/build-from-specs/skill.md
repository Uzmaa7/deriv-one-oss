---
name: build-from-specs
description: >-
  Build software products by driving Claude Code (via ACP) against a specification repository with specs/ and workspace/ directories. Use when the user asks to build, develop, or implement a component or product from specification files. The agent acts as a product owner -- points Claude Code at the right specs, lets it handle all planning and coding, verifies results via builds and endpoints, reports bugs in plain language, and iterates until the product works. Do NOT use for general coding tasks without spec files. Triggers on: "build the grid game", "implement the arcade", "develop this product", "start development", "build from specs", working with spec-based repos, any request to build something that has spec files.
version: 1.0.0
author: platform-team
tags: [claude-code, acp, build, specs]
---

# Build From Specs

Act as the product owner. You read specs to understand WHAT to build, then hand the work to Claude Code via ACP. Claude Code does ALL planning, architecture, and coding. You verify results and report issues.

## Core Principle: ONE Session Per Product

**Always use a single persistent Claude Code session per product.** Claude Code accumulates context about the codebase, architecture, and past fixes across rounds. Starting fresh sessions throws away that intelligence and forces YOU to become the "brain" that re-explains everything each time.

- **One product = one Claude Code session** — spawned once, messaged many times
- **Iteration happens inside the session** — send follow-up bugs to the SAME session
- **Resume if interrupted** — use `resumeSessionId` to reconnect to an existing session
- **Track the session key** — save it so you can send follow-ups across your own session resets

### Session Binding Constraints

`mode: "session"` requires `thread: true`, and thread binding is only available on some surfaces (e.g. Discord). Two options for non-thread surfaces (Slack, CLI):

**Option A (preferred for greenfield builds): direct `acpx` persistent session.**

`sessions_spawn(runtime:"acp", mode:"run")` terminates Claude Code after one burst (~3 files). For greenfield where you need Claude Code to loop continuously, use `acpx` directly — it keeps the same process alive across rounds.

```bash
ACPX=/home/kaveh/.npm-global/lib/node_modules/agent-repo/node_modules/.bin/acpx

# Create the persistent session ONCE
cd {repo_path} && $ACPX claude sessions new --name {product}-dev

# Each round: write prompt to a file, then fire
cd {repo_path} && nohup $ACPX \
  --approve-all --max-turns 200 --timeout 3600 --ttl 0 \
  --format text --suppress-reads \
  claude -s {product}-dev --file /tmp/{product}/round_N.txt \
  > /tmp/{product}/round_N.log 2>&1 &
```

Claude Code resumes the same session each time, so context and codebase knowledge persist. Tail the log to watch progress.

**Observed productivity difference (GridRush, 2026-04-17):** 4 rounds of `sessions_spawn(mode:"run")` produced 15 files and a non-compiling backend. Switching to persistent `acpx` produced 40 files + full working stack in one round (~35 min).

**Option B (fallback): `sessions_spawn(mode:"run")` with journal bridging.**

Use only for small, focused tasks (bug fixes, single-file edits). Each round is a fresh Claude Code context — compensate by:

- Writing thorough `journal.md` entries (Claude Code does this)
- Including a summary of prior work and current bugs in each new run's task
- Keeping the label consistent (e.g. `gridrush-fix-r4`) for your own tracking

### Escalate harness concerns fast

If two consecutive `sessions_spawn(mode:"run")` rounds produce <5 files each and the build still isn't bootable, STOP. Don't do a third. Check memory for prior successes on this repo (persistent `acpx` sessions often exist), then switch harnesses or flag to the user. Silent grinding through a bad harness burns time and attention.

### Process Cleanup

Run `scripts/cleanup-claude.sh` (in this skill's directory) before AND after every Claude Code spawn. Each session spawns ~4 processes (~230MB each). Stale sessions accumulate silently and can OOM the host.

```bash
bash {skill_dir}/scripts/cleanup-claude.sh
```

**Rule: Never have more than 1 Claude Code session running at a time.**

## Your Role vs Claude Code's Role

**You (Product Owner + QA Lead):**

- Find the repo, identify what to build
- Read specs enough to know what "done" looks like
- Invoke Claude Code with clear, spec-focused directives
- QA every round: build, interact, verify outcomes, compare to spec
- Report bugs as symptoms — never diagnose code
- Iterate until the product works correctly

**Claude Code (Developer):**

- Read specs in depth, plan architecture, write all code
- Debug and fix bugs from your symptom reports
- Journal progress for context continuity
- Stage git changes

You are the product owner talking to your developer. Say "build this, it should do X, here's the spec." Don't say "create a file called server.go with a handler on line 42."

## Step 0 — GitHub Repository Setup (if remote repo)

If the user provides a GitHub URL or repo name:

```bash
git clone https://github.com/{owner}/{repo}.git {repo_path}
cd {repo_path} && git pull origin main
```

Verify: repo is clean (`git status`), has `specs/` and `workspace/` directories.

## Step 1 — Discover and Understand the Component

```bash
find {repo}/specs -name "start.md" -o -name "product_spec.md" -o -name "*_spec.md" | sort
```

Read `specs/start.md` for the project overview. Read the target component's `start.md` to understand:

- What specs Claude Code needs to read
- Where output goes (`workspace/{component}/`)
- What "done" looks like — acceptance criteria, required endpoints, UI requirements

**Extract a mental checklist of testable requirements** from the spec. You'll use this in QA.

## Step 2 — Spawn Claude Code

```
sessions_spawn:
  runtime: "acp"
  agentId: "claude"
  mode: "run"  # or "session" with thread:true if surface supports it
  cwd: "{repo_path}"
  label: "{component}-build-r1"
  task: "<directive>"
  runTimeoutSeconds: 600
```

**Initial directive template:**

> Read `specs/products/{component}/start.md` and build the {component}. Follow all specs completely — read the product spec, platform standard, and design system. The product must be accessible at http://localhost:{port}. Make sure `docker compose up --build` works end-to-end. Journal your progress in `workspace/{component}/journal.md`.

**For follow-up rounds** (when using `mode: "run"`), include context:

> Read `workspace/{component}/journal.md` for development history. The project builds and runs. Here are the current bugs to fix:
>
> 1. [symptom description]
> 2. [symptom description]
>
> After fixing, rebuild with `cd workspace/{component} && docker compose up --build -d` and verify the build is clean.

Save the returned `childSessionKey` for tracking.

## Step 3 — QA the Results

**This is where you add the most value. Don't rubber-stamp.** Your job is to be the user who actually tries the product and finds what's broken.

### Before You Start: Run Smoke Test

```bash
bash {skill_dir}/scripts/smoke-test.sh {port} {repo}/workspace/{component}
```

If smoke test fails → send build errors back to Claude Code immediately. Don't proceed.

### QA Methodology: The Four Layers

You must test all four layers. Each catches different bug classes.

#### Layer 1: Infrastructure (does it run?)

- All Docker services up and healthy
- Health/ready endpoints responding
- Backend logs free of panics/fatals
- Frontend loads without console errors
- WebSocket/SSE connections establish

#### Layer 2: Interaction (does it respond to input?)

**Open the product in a real browser via CDP.** This is not optional.

```bash
# Verify Chrome is running
ss -tlnp 2>/dev/null | grep ':9222' || echo "Chrome not running on 9222"
# Navigate
curl -s http://localhost:9222/json/new?url=http://localhost:{port}
```

**Test every user action the product supports.** Make a list. Check them off:

For each action:

1. Trigger it (CDP click/input/hover)
2. Check backend logs — did it receive the message?
3. Check the response — did the backend accept or reject it? Why?
4. Check the frontend — did it update? Screenshot before/after.
5. Check side effects — balance, state, notifications

**Critical: test EVERY input path separately.** If there are 3 ways to do something, test all 3. The most common bug is "path A works, path B was wired differently and is broken."

#### Layer 3: Outcomes (does every end state work?)

This is the layer I historically miss. Products have multiple outcome paths — success/failure, win/lose/refund, valid/invalid, etc.

**For every outcome the product can produce:**

- [ ] Trigger that specific outcome (you may need to wait, or set up conditions)
- [ ] Verify the backend sends the right message to the right recipient
- [ ] Verify the frontend receives it and updates correctly
- [ ] Verify side effects (balance credited/debited, state changed)

**The "targeted delivery" test:** If a message goes to a specific user (not broadcast), verify the lookup key matches how users are registered. Mismatched ID formats between systems cause messages to silently disappear — the sender thinks it sent, the receiver never gets it. This is invisible unless you specifically test for it.

**How to test targeted delivery:**

1. Place a bet / trigger an action
2. Check backend logs for the settlement/response message
3. Check if the frontend actually received it (UI updated, balance changed)
4. If the UI didn't update but the backend logged a send — the lookup key is wrong

#### Layer 4: Time (does it hold up over 30+ seconds?)

For anything with animation, real-time updates, scrolling, or timed state changes:

- Watch for 30+ seconds minimum
- Take screenshots at t=0, t=10s, t=30s
- Do moving elements move smoothly? Any jumps at periodic boundaries?
- Do placed/created items track their container as it moves?
- Do timed transitions happen on schedule?

**Interact-then-wait QA (critical):**

For products with user-placed markers (bets, chips, annotations, positions) or timed lifecycles (settlement, countdown, expiry), a fresh screenshot WILL miss a whole class of bugs. The sequence must be:

1. Interact — place N marks/bets/inputs.
2. Screenshot → save as `t=0`.
3. Wait 60–180 seconds without interacting.
4. Screenshot → save as `t=N`.
5. Diff the two. Look for:
   - Stale markers painted at their original screen position while the underlying content has scrolled (should have moved with it, or disappeared).
   - Frozen relative timestamps ("9s ago" that never advances).
   - Duplicate list entries that survive across time.
   - Settlement/resolution outcomes showing on items that shouldn't be settled yet.
   - Ghost residue or memory-leak-like accumulation.

**Real example (GridRush, 2026-04-17):** Bet chips were drawn at absolute screen coordinates instead of cell coordinates. At t=0, everything looked fine. At t=180s, the same chips were still at the same screen positions — but the underlying grid had scrolled, so they were "floating" on future cells with impossible settlement markers. Single-moment QA missed this entirely for multiple rounds until a 3-minute wait surfaced it.

### After QA: Spec Compliance Check

Re-read the spec's acceptance criteria. Check each one:

- [ ] Present and working
- [ ] Deviates from spec (flag it)
- [ ] Missing entirely (file as bug)

### Full QA Checklist

See `references/qa-checklist.md` in this skill's directory for the complete checklist. Run it every round.

## Step 4 — Report Issues and Iterate

**Send follow-up bugs to Claude Code.** If using persistent session, use `sessions_send`. If using `mode: "run"`, spawn a new run with full context.

**Report bugs as symptoms, not diagnoses:**

✅ Good: "When I win a bet, my balance doesn't change and I don't see a settlement notification. Losing bets work fine — I see 'Lost $1.00' and my balance doesn't change (correct). But winning bets just stay as 'Active' forever."

❌ Bad: "In settlement.go line 85, `so.AccountID.String()` returns a UUID but the hub is keyed by string. Change it to use the original account ID."

Claude Code has the full codebase. Give it accurate symptoms and it'll find the code-level fix. You describing code-level fixes is slower, less accurate, and bypasses Claude Code's advantage.

**Exception:** If Claude Code fails to fix a bug after 2+ attempts with symptom descriptions, THEN read the relevant code and give a more targeted hint.

**After each fix, re-run the FULL QA.** Don't skip layers because the fix was "small."

**Iteration budget:** Interactive/visual products: 3-6 rounds is normal. Backend-only: 1-2 rounds. Don't rush or get frustrated — each round makes the product better.

### Two-source visual verification (do not skip)

Claude Code's self-QA is unreliable. When it claims "I can see X at pixel Y in the screenshot," it is often describing the render it _intended_, not the render that actually exists. Observed failure rate in one 7-round build: ~3 of 7 rounds had at least one self-QA claim that my independent screenshot refuted.

Rule: take your OWN screenshot and analyze it yourself (vision model or direct inspection) before accepting any visual fix as done. When Claude Code's assessment and yours disagree, yours is always authoritative.

When pushing back, be concrete: "You claimed `history cells with borders at x=200..400`. My screenshot shows only a line chart in that region, no cell borders. Bug not fixed — try again." Vague disagreement ("doesn't look right") won't help Claude Code re-diagnose.

### Distinguish "true duplicate" from "same-looking, different data"

When a list shows what appear to be duplicate rows, check the underlying record IDs before deduping. Often the rows are genuinely different entries (e.g., two separate contracts placed on the same grid cell) that appear identical because the UI only shows a subset of fields. Fix by:

- Aggregating with a count suffix: `C4:R1122 · 2.2x (×2) · +$4.38`
- Or adding a disambiguator column (short ID tail, timestamp)

Do NOT silently dedupe by visible-fields key — you'll hide real data and create invariants that break under edge cases.

## Step 5 — Completion

When everything works:

1. Run the full QA checklist one final time
2. Run smoke test: `bash {skill_dir}/scripts/smoke-test.sh {port} {compose_dir}`
3. Ask user for final confirmation if they want to check themselves
4. Tell Claude Code: "Stage all changes: `git add workspace/{component}/`"
5. Report to user: what was built, URL, how to start/stop
6. Clean up: `bash {skill_dir}/scripts/cleanup-claude.sh`

## Step 6 — Demo via ngrok (optional)

```bash
ngrok http {port}
```

Share the public URL with the user.

## Self-Improvement Loop

Log friction to `{repo}/workspace/self_improve.md` during builds. Review AFTER the build is complete (not during). Convert observations into:

- Skill rule updates (edit this file)
- New scripts in `scripts/`
- New reference docs in `references/`

Format:

```
## YYYY-MM-DD | {product} | Phase N
- **Pain:** {what happened}
- **Trace:** {which step/rule, or "new"}
- **Idea:** {what would fix it}
```

## Rules

1. **Never write code.** Delegate everything to Claude Code via ACP.
2. **Never plan architecture.** That's Claude Code's job after reading specs.
3. **Never debug code.** Describe symptoms, let Claude Code fix. (Exception: 2+ failed attempts.)
4. **QA every outcome path.** Not just the happy path. If the product has win/lose/refund, test all three. If it has 3 ways to trigger an action, test all 3.
5. **Test targeted delivery.** If messages go to specific users (not broadcast), verify the lookup actually works. Silent drops are the hardest bugs to find.
6. **QA over time.** Watch the product for 30+ seconds. Single-moment screenshots miss animation, scrolling, and transition bugs.
7. **ONE session per product** when the surface supports it. Otherwise, use journal.md as the context bridge between runs.
8. **Clean up processes.** Run `cleanup-claude.sh` before AND after every spawn.
9. **Smoke test every round.** Run `smoke-test.sh` before deep QA. No point testing features if the build is broken.
10. **Full QA every round.** Never skip layers because the fix was "small." Regressions hide in small changes.
11. **Specs are the source of truth.** Don't pass verbal overrides to Claude Code.
12. **Save session keys and labels.** Write them to memory files for context continuity.
13. **Be patient.** Complex products need 3-6 rounds. This is normal, not failure.
14. **No specs = no work.** Period.
15. **Prefer persistent `acpx` sessions for greenfield.** `sessions_spawn(mode:"run")` is a one-shot harness; Claude Code wraps after ~3 files. For builds that need continuous loops, shell out to `acpx claude -s <name>` directly.
16. **Interact-then-wait for any scrolling/timed UI.** Place inputs, walk away 60-180s, screenshot, diff. Single-moment captures miss stale-marker, frozen-timestamp, and ghost-residue bugs.
17. **Two-source visual verification.** Never accept Claude Code's self-QA of a visual fix without an independent screenshot + analysis. When they disagree, yours wins.


---

## Trigger Tests

### Should trigger
- build the grid game from specs
- implement the arcade product
- develop this product from the spec repo
- start development on the dashboard spec
- build from specs in this GitHub repo

### Should NOT trigger
- write code for a function -- answer directly
- review my pull request -- answer directly
- what does this spec file mean -- answer directly, no Claude Code needed
