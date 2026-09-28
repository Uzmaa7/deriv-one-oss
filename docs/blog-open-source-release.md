# Open-Sourcing Deriv One: The Internal AI Platform We Run in Production

Last month at Deriv, our teams ran **~55,800 agent interactions**. Not in a sandbox. Not in a pilot. In Slack—where payments, compliance, partnerships, and security already work.

**~288 people**—roughly a third of the company—used Deriv One in the last 30 days. They drew on **221 shared skills** published by **54** colleagues, talking to **35** live Slack agents. The Payments team is at **~89%** adoption; Compliance at **~68%**. **99% of agent interactions are Slack @mentions**, not DMs in a side tab. This isn't an engineering side project. It's how the organisation gets answers.

We built **Deriv One** to make that possible: a self-hosted AI platform with a governed skills catalogue, registerable agents, a multi-pane workspace, sandboxed execution, and an app builder—wired together and secure enough for a global, regulated company.

Today we're releasing it as **open source** (MIT). Same architecture we run in production. Your model gateway. Your infrastructure. Your name on the tin.

---

## The gap we kept hitting

Early adoption at Deriv looked familiar: power users moved fast; everyone else stalled somewhere between "install this" and "configure MCP." Even strong adopters ended up with incompatible setups—different tools, different prompts, different secrets—so good ideas didn't compound.

We didn't think the answer was to dumb the product down. Finance, compliance, payments, engineering, and support don't need a simpler AI toy. They need **the full capability**, with guardrails that don't require a security review every time someone tries something new.

That pushed us toward a few convictions that shaped every layer of Deriv One.

---

## What we believed before we built

### 1. Governance and capability are the same product

If security is a separate conversation from "can I use AI for my job," most people will choose neither.

Skills go through review before they're published. Secrets are never stored in the database—only opaque references resolved at execution time. Model-generated code runs in disposable sandboxes, not on someone's laptop with full access. Agent invocations are isolated processes so one compromised prompt can't read another agent's tokens from memory.

None of that is documentation theater. It's how the system is wired.

### 2. Skills are how organizations learn

A skill is a markdown instruction set: what to do, when to use it, when *not* to.

When someone in payments figures out a reliable way to investigate a class of issue, that shouldn't die in a private ChatGPT thread. It should become a versioned, reviewable artifact the next person can install in one click. The catalogue is permissioned—people only see what their role allows—but the floor rises when good work is shared, not re-discovered.

### 3. The harness should disappear

Non-technical users shouldn't need terminals, API keys, or MCP JSON. Admins run a setup wizard once: branding, model gateway, identity, directory, optional Slack. Employees sign in and get a configured environment—chat, skills, agents, automations—without becoming part-time infra engineers.

### 4. You should own the stack

Deriv One is self-hosted. You point it at your OpenAI-compatible model gateway. Blobs, secrets, queues, directory, and sandbox execution are **provider interfaces** with local reference implementations—so it runs on a laptop for development, and on Postgres, object storage, and Kubernetes in production, without the core codebase knowing which cloud you use.

### 5. Agents need the same discipline as production software

Letting every team spin up its own bot with its own API keys doesn't scale—it scales *risk*. Every Slack agent should be **registered**, not improvised: a defined owner, reviewed scopes, attached skills, and credentials stored in the vault—not pasted into a config file.

**Access control** is first-class. Sensitive capabilities go through request → approver → time-bounded grant → expiry, with an audit trail. Agents have roles and collaborators; channels can be open or allowlisted. The question isn't "can this model do it?" but "is this *person* allowed to invoke this *agent* for this *capability* right now?"

**Creation should be standardized**, not bespoke. Guided registration walks owners through a canonical Slack app shape—minimum scopes inferred from what the agent actually does, expanded only against an allowlist. Manual paths exist for power users, but the default is the same template, the same security bar, the same manifest conventions. Admins get a **platform agent** for company-wide coverage; teams get their own agents without reinventing the wiring each time.

**Cost belongs in one place.** Every workspace turn, agent invocation, and skill run flows through your model gateway—and Deriv One records token usage against it centrally. You're not reconciling a dozen shadow ChatGPT subscriptions; you have one bill, one attribution model, and one place to see what's driving spend.

---

## What Deriv One actually is

Think of it as four surfaces that share the same brain.

### Workspace

Most AI products give you a single conversation thread. Real work doesn't look like that. You're drafting in one place, checking a policy doc in another, sanity-checking a CSV export in a third—and you shouldn't have to lose context every time you switch.

The Workspace is built like an editor, not a chatbot. **Split panes** let you run multiple conversations side by side. Open a markdown file, an HTML preview, a spreadsheet, or generated code **inline as tabs** next to the chat that produced it. Drag, tile, and rearrange; when you come back tomorrow, the layout is where you left it.

The composer is built for how people actually invoke capability: type `@` to attach a **skill** directly in the message, the same way you'd mention a colleague. A **context meter** shows how much room you have left in the model window, so long threads don't fail mysteriously mid-task. Behind the scenes, a **complexity router** sends greetings and quick lookups to a fast, cheap model and escalates to a stronger one when the turn needs reasoning—or when a retry suggests the first answer wasn't good enough. You pick a default model in settings; the platform handles the rest.

The Workspace is also where ideas turn into durable artifacts. Mid-conversation, the assistant can **propose** a new skill, automation, or Lab app; you review, edit, dry-run, or discard before anything is created. That's enablement in context—not a separate "AI training" session, but a nudge while you're already doing the work.

### Skills

If agents are *who* does the work, skills are *how*. A skill is a markdown document with frontmatter: what it's for, when to use it, and—just as important—**when not to**. That last part matters. A skill that fires on the wrong request is worse than no skill at all.

Skills live in a **governed catalogue**, not a shared folder of prompts. Before anything is published, it goes through review—including **AI-assisted security review** for risky patterns. Published skills are **versioned** with full diff history, support **co-owners**, and sync across replicas so every node in a deployment sees the same catalogue. Access isn't all-or-nothing: an ACL resolved from your **directory** decides which skills each person can see and install. Compliance sees compliance skills. Partnerships sees partnerships skills. Nobody gets the whole library by default.

When someone asks a question, the platform doesn't naively keyword-match a skill name. It **routes** in two stages: first against the catalogue you're allowed to see, then against the skill's full instructions—because a description that *sounds* right isn't enough to act on. Skills can also ship **artifacts**—small Python helpers they call at runtime. Each artifact declares which hosts it reaches and which secrets it reads; a **static gate** reviews that manifest before approval, and a **fingerprint** of the approved code is stored so the running version can't drift silently after sign-off. Secrets and PII are stripped from generated code before it's persisted.

At Deriv, **221 approved skills from 54 publishers** is what this looks like when the pipeline keeps up with demand: domain experts packaging what they know into something the next person can reuse—without everyone maintaining their own private prompt library.

### Agents

Agents are persistent assistants with an identity, not a one-off chat session. Each has a **persona**, a chosen model, attached skills, optional **memory**, and an optional **knowledge base** of private documents it can search. Register one for your team in the UI—or, when you're ready for Slack, as **its own Slack app** with its own signing secret, bot token, and scope profile. That separation is deliberate: a payments agent and a compliance agent don't share credentials, and a prompt injection against one can't read another's tokens from memory—each invocation runs in an **isolated subprocess** loading only that agent's secrets.

Slack integration is where agents become infrastructure. Scope inference starts from what the agent is supposed to do and requests the **minimum** Slack permissions needed—expanded only against an **allowlist**, default-deny. In channels, agents can respond to `@mentions`, DMs, or—when configured—**ambient** messages where a lightweight gate decides whether an unaddressed thread actually needs a reply (fail closed, because the cost of a wrong "yes" is an agent talking over people). **Per-channel configuration** lets the same agent behave differently in `#support` than in a leadership channel: different soul text, skills, access policy, or join behaviour.

Memory isn't a dump of every past message. It's consolidated over time, governed by retention policy, with an append-only record of *how* the agent came to believe something—so you can audit reasoning, not just outputs. Knowledge bases ingest documents asynchronously; indexing state is visible in the UI so operators know what's searchable and what isn't. Agents can be **scheduled**, shared with **collaborators**, and granted time-bounded **access** to sensitive capabilities through the same governed-access flow as the rest of the platform.

For company-wide Slack coverage, a **platform agent** singleton can be configured by admins with per-channel overrides—one managed entry point, many channel personalities underneath.

### Lab — build internal tools without opening a ticket

Skills answer *how to do a task*. Lab answers *"we need a small tool for this."*

Describe what you want in plain language—a dashboard, a form, a calculator, a team utility—and Lab figures out the rest. It classifies the app, picks a stack (static page, Python, or React based on complexity), and attaches the right **skills from your catalogue** so the generated code can call approved capabilities instead of reinventing integrations from scratch.

You build iteratively in one place: chat on the left, **live preview** on the right, full file tree when you need it. Iterate in conversation ("add a CSV export", "restrict this to my team") and watch the app update. Wire **secrets and env vars** through the same vault model as agents—references in the database, values resolved only at run time. When you're ready, **deploy** to a URL on your own instance with private, shared, or workspace visibility.

Everything runs in the **same sandbox** as skill execution: disposable containers, no credentials baked into generated code, isolation you can explain to security. Lab is how a compliance analyst, partnerships lead, or ops manager ships a working internal app in an afternoon—not how they learn Git.

Underneath: **automations** (cron), **flows** (user-owned skill chains), and **workflows** (agent graphs with human approval). Same platform, different shapes of "run this again later."

---

## How we use it at Deriv

Deriv One is part of how we work, not a side experiment.

Against **905 active employees** in our IAM directory, roughly **one in three** has used the platform in the last 30 days. That number understates the story: the heaviest usage isn't coming from engineering. It's coming from the teams where getting an answer right matters most.

### Adoption

| Window | Distinct people | Notes |
|--------|-----------------|-------|
| Last 30 days | **~288** | Agent invokers + product activity (workspace, Lab, automations) |
| Last 7 days | **~170** | Same union |
| All time | **~442** | Cumulative reach |
| Product UI (14-day avg DAU) | **~30** | Distinct users in the web workspace |
| Agent invokers (weekdays, 30d) | **~50/day** | People talking to Slack agents on a typical workday |

On weekdays, **~43–50 people per day** invoke an agent (median ~47 over the last two weeks; weekends drop to ~20). That's **~2,500 agent interactions per day** recently—about **55,800 in the last 30 days**. **99% of those interactions are Slack @mentions**, not DMs. People aren't hiding in private chats. They're working where the team already is.

The strongest pockets of adoption, measured as 30-day active users against department headcount:

| Department | Active / headcount | |
|------------|-------------------|---|
| Payments | 25 / 28 | **~89%** |
| Compliance | 48 / 71 | **~68%** |
| Security | 24 / 52 | |
| Global Partnerships | 18 / 60 | |

We also see sustained use across Digital Marketing, Customer Experience, HR, Finance/PayOps, and Accounting. Official analytics "rollout" departments (Compliance, Payments, Global Partnerships, Finance/PayOps) account for 191 people on paper—but product use has already spread well beyond that list.

### Shared capability at scale

| Asset | Count |
|-------|-------|
| Approved skills | **221** distinct slugs |
| Skill publishers | **54** people |
| Skills on active agents | **136** |
| Active Slack agents | **35** (31 excluding staging) |
| Active automations | **20** |

**221 skills. 54 publishers.** That ratio is the point: this isn't a platform built by a central AI team and pushed downhill. Dozens of people across the company have packaged what they know into something the next person can reuse.

### Where it shows up in real work

The workspace matters for building skills, reviewing drafts, and running longer tasks. But day to day, **Slack is where the volume is**—routing questions, answering procedure lookups, triaging support threads, and surfacing domain knowledge without pulling a human out of flow every time.

That pattern shows up most clearly in the departments with the highest adoption: Payments and Compliance lead on penetration, with Security and Global Partnerships close behind. The use cases differ—client payment queries, regulatory routing, partner lifecycle questions, investigation workflows—but the shape is the same: a **registered Slack agent** with its own skills, scopes, and guardrails, not a shared API key duct-taped to a channel.

### The compounding effect

When a team figures out a reliable workflow, it doesn't have to stay in one person's head. The skills behind it live in the catalogue. Other agents can attach them. Other departments can adapt them. **221 skills from 54 publishers** is evidence that the flywheel is turning: **one person's breakthrough becomes everyone's baseline**, without everyone having to become an AI engineer to benefit.

---

## Why we're open-sourcing it

### The problem is general

Regulated companies, distributed teams, and anyone who can't ship customer data to a third-party chat product face the same harness gap. We benefited from running this internally; others shouldn't have to rebuild the same security and governance layers from scratch.

### The model layer is commoditizing; the platform layer isn't

Gateways, models, and context windows will keep changing. What endures is identity, secrets, skills, auditability, sandboxing, and how you distribute capability across a company. That's what we're putting in the open.

### We want to learn in public

External adopters will stress different providers, directories, and compliance regimes than we do. Issues, plugins, and skills from outside make the core better for Deriv too.

### What we're not shipping

We're **not** open-sourcing our production cloud plugins, internal integrations, production Terraform/Helm for our exact footprint, the Slack DMZ relay we run at scale, or every internal-only Lab feature we use at Deriv.

The repo ships:

- Provider interfaces and local reference implementations
- Docker images and compose files
- Helm chart (`deploy/helm/platform/`) and Kubernetes starter manifests (`deploy/k8s/`)
- Starter Terraform for AWS/GCP data plane (`deploy/terraform/`)
- Adopter documentation

You bring your model gateway, your identity proxy, and your cloud.

---

## What you get on day one

Clone the repo, run the setup wizard, point at any OpenAI-compatible gateway (including a local LM Studio or Ollama instance), and you have:

- Workspace chat with model routing
- Skills registry with publish/review flow
- Agent registration (manual Slack or guided setup)
- **Lab** — describe, build, preview, and deploy internal apps (Python, React, or static)
- Sandboxed execution for skills and Lab runs
- SQLite locally; Postgres for multi-replica production
- Mock auth for solo dev; OIDC for team mode

**License:** MIT — no vendor account required to try it.

```bash
git clone https://github.com/deriv-com/deriv-one-oss.git && cd deriv-one-oss
npm run setup -- --quick --test-gateway
npm run dev:stack
```

- [Adopter guide](ADOPTER_GUIDE.md) — setup, identity, directory, production checklist
- [Cloud deployment](cloud-deployment.md) — GKE/EKS architecture and managed services
- [Slack DMZ relay spec](slack-dmz-relay.md) — multi-replica Slack events

---

## What we learned building it

**The product teaches faster than the workshop.** The people who stuck weren't the ones who attended the most training. They were the ones who installed a skill, got a useful result in the first session, and then asked "what else can this do?" Enablement embedded in the workflow beats enablement in a slide deck.

**Security constraints accelerated design, not the opposite.** "Secrets never in the DB" and "one subprocess per agent" forced uncomfortable early decisions. They also made it possible to say yes to more teams—not "use ChatGPT on the side," but "use this on approved infrastructure."

**Sharing has to be as easy as hoarding.** If publishing a skill is harder than keeping a private prompt, the catalogue dies. We now have **221 approved skills from 54 publishers**—proof that review can keep pace with demand when the payoff is immediate. Versioning has to be obvious, and discovery has to meet people in context—not expect them to browse hundreds of entries cold.

**Open source is a commitment to boundaries.** We scrubbed internal branding, blocked forbidden patterns in CI, documented what adopters must bring themselves, and separated the portable core from Deriv-specific ops. Releasing software is also releasing responsibility for someone else's production.

---

## What's next

Deriv One will keep evolving inside Deriv. The open-source tree is the same architecture—provider plugins, skills, agents, workspace, Lab—with a neutral default name (**Platform**) so you can make it yours.

If you're building internal AI infrastructure, try it, break it, and tell us what's missing. If the problems we cared about—governance, compounding skills, self-hosting, real Slack agents—match yours, we'd rather compete on who runs it best than on who rebuilds the harness from zero.

| | |
|---|---|
| **Repository** | [github.com/deriv-com/deriv-one-oss](https://github.com/deriv-com/deriv-one-oss) |
| **License** | MIT |
| **Security** | [SECURITY.md](../SECURITY.md) |

---

*Built by Ansh Choudhary, Joel Joseph, Aditya Singhal, Sejal Chopra, and Khirthana Sundaram at Deriv.*
