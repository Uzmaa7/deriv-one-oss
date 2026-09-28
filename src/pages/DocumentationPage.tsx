export default function DocumentationPage() {
  return (
    <div className="flex-1 bg-white">
      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        <h1 className="text-3xl font-bold text-slate-900 mb-8">Documentation</h1>
        <article className="prose prose-slate max-w-none text-slate-600 prose-headings:text-slate-900">
          <h2>What a skill is</h2>
          <p>
            A skill is a markdown file that tells an agent how to do one thing well. It carries
            frontmatter describing itself, instructions in the body, and optionally code the agent
            may call. Skills are versioned, reviewed before publication, and visible only to the
            people your access manifest allows.
          </p>

          <h3>Writing one</h3>
          <p>
            Create the skill from the <strong>Skills</strong> page, or write the file yourself and
            publish it. The frontmatter names the skill and describes when it should be used — that
            description is what the router matches an incoming request against, so it is worth
            writing carefully. Say what the skill is for <em>and</em> when not to reach for it.
          </p>
          <pre className="bg-slate-900 text-slate-200 text-sm"><code>{`---
name: invoice-lookup
description: >
  Look up invoice status, payment dates and vendor balances.
  Use when someone asks where an invoice is or whether it has been paid.
  Do NOT use for creating or approving payments.
version: "1.0.0"
publisher: finance-engineering
---

# Invoice Lookup

## When to use this
Someone asks about the status of an invoice, a payment date, or a vendor balance.

## How to answer
1. Resolve the vendor or invoice number from the question.
2. Query the invoicing API for its current state.
3. Report status, amount and expected payment date. If it is blocked, say why.

## Never
Do not approve, schedule or modify a payment. This skill is read-only.
`}</code></pre>

          <h3>Access</h3>
          <p>
            Each skill has an entry in the access manifest naming the departments that may use it
            without asking, its risk level, and what happens when somebody outside those departments
            requests it. Requests route to an approver, grants are time-bounded, and everything is
            audited. Skills marked <code>critical</code> grant nobody automatic access — every
            request goes to an administrator.
          </p>

          <h3>Attaching code</h3>
          <p>
            A skill can carry Python the agent may call. It declares the hosts it reaches and the
            secrets it reads, is reviewed before approval, and is fingerprinted afterwards so the
            approved source cannot quietly change. It runs in a sandbox with no credentials beyond
            those it declared.
          </p>

          <h3>Attaching a skill to an agent</h3>
          <p>
            Agents are given skills from the agent's page. An agent can only use skills its owner
            can access, and attaching one that reaches a high-risk system raises an alert.
          </p>
        </article>
      </div>
    </div>
  );
}
