# PeerAssist Design System

This document explains how PeerAssist turns the open `DESIGN.md` format into a
repeatable UI contract. It is a project guide, not a second source of visual
tokens. Exact values live in the repository root [`DESIGN.md`](../DESIGN.md);
the generated primitive export lives in
[`web/peerassist-workspace/design-tokens.json`](../web/peerassist-workspace/design-tokens.json).

## Source Of Truth

PeerAssist follows the public [DESIGN.md specification](https://getdesign.md/)
and the upstream [format documentation](https://github.com/google-labs-code/design.md/blob/main/README.md).
The file has two layers:

- YAML front matter contains machine-readable colors, typography, spacing,
  rounding, and component tokens.
- Markdown sections explain the product context and the reason each token is
  used. This prose is guidance for people and coding agents; it does not
  silently override token values.

The canonical section order is `Overview`, `Colors`, `Typography`, `Layout`,
`Elevation & Depth`, `Shapes`, `Components`, and `Do's and Don'ts`. Variants
such as hover and pressed states are sibling component entries, for example
`button-primary-hover`; they are not nested objects.

## PeerAssist Application Rules

PeerAssist is an academic editorial workspace. The design should help a teacher
read a manuscript, verify evidence, edit a review, and export it. The four
primary destinations are **我的审稿**, **论文研读**, **审稿意见**, and
**历史记录**. Infrastructure terms such as queue, agent, provider, and trace
are secondary diagnostics and must not compete with those destinations.

Use the following rules when implementing a view:

1. Use deep ink for ordinary text, paper or cool-gray surfaces for reading
   areas, dark teal for the active location and the single primary action, and
   amber/red/green/blue only for semantic status. Never communicate status by
   color alone.
2. Keep the interface compact and document-first. Prefer hairline borders,
   spacing, and explicit hierarchy over permanent shadows or decorative cards.
3. Keep controls and navigation rows at least 44px high. Use 4px-based spacing
   and restrained 4/6/8px radii. Do not use gradients, oversized hero text, or
   purple-led marketing surfaces in the authenticated workspace.
4. Every generated conclusion needs an evidence location or an explicit
   “待人工核查” state. Warnings explain what must be checked and link to the
   source location.
5. Loading, empty, denied, conflict, and failure states must state what
   happened and expose the next valid action. A spinner alone is not a state.

## Token Usage

Use token references in `DESIGN.md` components instead of copying hex values:

```yaml
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.paper}"
```

The DTCG JSON export is intended for foundational tokens consumed by tooling.
Component semantics, typography stacks, and usage rationale remain normative in
`DESIGN.md`. If a component needs a new value, update the source file first and
regenerate the export; do not hand-edit the JSON.

## Change Workflow

From the repository root, run:

```bash
npx -y @google/design.md lint --format json DESIGN.md
.venv/bin/python scripts/check_design_md.py
make design
```

When token values change, regenerate the export and validate its JSON:

```bash
npx -y @google/design.md export --format dtcg DESIGN.md \
  > web/peerassist-workspace/design-tokens.json
.venv/bin/python -m json.tool web/peerassist-workspace/design-tokens.json >/dev/null
```

For a visual-system change, compare the proposed file with the current one
before merging:

```bash
npx -y @google/design.md diff DESIGN.md DESIGN.next.md
```

Then run the relevant frontend build and repository documentation check:

```bash
cd web/peerassist-workspace && npm ci && npm run build
cd ../.. && .venv/bin/python scripts/check_docs.py
```

The `design:lint` npm script is a convenience wrapper for the official CLI:

```bash
cd web/peerassist-workspace && npm run design:lint
```

## Review Checklist

- The change starts from a user task in the teacher workflow, not from a new
  internal infrastructure concept.
- New colors, dimensions, and component variants are represented in
  `DESIGN.md` and use valid token references.
- Official lint reports zero errors and no WCAG AA contrast failure.
- The DTCG export exactly matches the source file.
- Empty, loading, denied, conflict, and failure states have readable copy and
  a next action.
- Keyboard focus, visible focus rings, 44px targets, responsive reading width,
  and reduced-motion behavior are preserved.
- Existing login/session work and generated assets are left intact unless the
  change explicitly targets them.

## Migration Boundary

The current React/Vite workspace is a compatibility surface with ongoing login
and session work. Its CSS is not silently rewritten when tokens change. A
future UI migration must consume this contract component by component, include
desktop and mobile screenshots on port `8766`, and remove the compatibility
styles only after behavior parity is demonstrated.
