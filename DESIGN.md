---
version: alpha
name: PeerAssist Academic Editorial
description: A quiet, evidence-first academic review workspace for teachers and researchers.
colors:
  primary: "#0F4C4C"
  primary-hover: "#0B3C3C"
  ink: "#172121"
  muted: "#536363"
  paper: "#FFFFFF"
  surface: "#F5F7F7"
  border: "#D8DEDE"
  warning: "#8A4B08"
  danger: "#B42318"
  success: "#146C43"
  info: "#175CD3"
typography:
  h1:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: 2rem
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "0em"
  h2:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: 1.375rem
    fontWeight: 650
    lineHeight: 1.35
    letterSpacing: "0em"
  paper-title:
    fontFamily: "ui-serif, Georgia, Cambria, Times New Roman, serif"
    fontSize: 1.25rem
    fontWeight: 600
    lineHeight: 1.45
    letterSpacing: "0em"
  body-md:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: 1rem
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: "0em"
  body-sm:
    fontFamily: "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: 0.875rem
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0em"
rounded:
  sm: 4px
  md: 6px
  lg: 8px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  minimumTouchTarget: 44px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.paper}"
    typography: "{typography.body-md}"
    rounded: "{rounded.md}"
    height: "{spacing.minimumTouchTarget}"
    padding: 16px
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
    textColor: "{colors.paper}"
    typography: "{typography.body-md}"
    rounded: "{rounded.md}"
    height: "{spacing.minimumTouchTarget}"
    padding: 16px
  button-secondary:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.body-md}"
    rounded: "{rounded.md}"
    height: "{spacing.minimumTouchTarget}"
    padding: 16px
  navigation-entry:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.body-md}"
    rounded: "{rounded.sm}"
    height: "{spacing.minimumTouchTarget}"
    padding: 12px
  evidence-warning:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.warning}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    padding: 12px
  status-danger:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.danger}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    padding: 8px
  status-success:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.success}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    padding: 8px
  status-info:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.info}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    padding: 8px
---

## Overview

PeerAssist is a light academic editorial workspace for evidence-driven AI review. It should feel like a dependable reading and writing tool, not an automation dashboard: compact, calm, precise, and built around the teacher's manuscript.

The four primary teacher entries are **我的审稿**, **论文研读**, **审稿意见**, and **历史记录**. Internal concepts such as agents, queues, traces, and provider infrastructure never compete with these entries.

Linear informs information density, alignment, and hairline separation. Notion informs document hierarchy and uninterrupted reading. Neither product's branding is copied.

## Colors

- **Deep ink** is the default text color and keeps the page editorial rather than administrative.
- **Paper white** and **cool gray** create reading surfaces; use borders and spacing before shadows.
- **Dark teal** is reserved for the active location, links, focus, and the single primary action.
- **Amber** means evidence needs attention, while semantic red, green, and blue mean error, success, and information.
- Color never carries status alone; pair it with a label, icon, or accessible description.

## Typography

Chinese interface text uses the local system sans stack for reliable rendering and fast scanning. Paper titles may use the serif stack to distinguish the manuscript from application chrome. All letter spacing is zero; hierarchy comes from size, weight, line height, and whitespace.

Keep interface headings compact. Large display typography and marketing-style slogans do not belong in the authenticated workspace.

## Layout

Use a dense editorial shell with a 220px navigation rail, a flexible reading area, and an optional 360px evidence panel on wide screens. Hairline borders separate regions. Nested decorative cards are prohibited; repeated review items may use a single bounded row or card.

Spacing follows the 4px base scale. Interactive controls are at least 44px high. At narrower breakpoints, protect the manuscript reading width before secondary navigation or evidence tools.

## Elevation & Depth

The default interface is flat. Use borders, background changes, and document whitespace for hierarchy. Shadows are limited to temporary overlays such as dialogs, drawers, and menus; permanent navigation, panels, and page sections remain unshadowed.

## Shapes

Radii are deliberately restrained: 4px for compact entries and status surfaces, 6px for controls, and 8px for dialogs or repeated item containers. Pills are reserved for short machine-readable statuses, never for ordinary navigation or long text.

## Components

The primary button is unique within its immediate workflow region. Secondary actions use paper surfaces and ink text. Navigation entries maintain a stable 44px row height, and active state uses teal plus a non-color cue.

Evidence warnings explain what must be checked and link back to the source location. Semantic statuses always retain readable text on paper white. Form controls, icon buttons, and row actions inherit the 44px minimum target even when their visible icon is smaller.

## Do's and Don'ts

- Do preserve the paper title, review state, and next action across reading and writing views.
- Do use compact document hierarchy, hairline dividers, explicit empty states, and evidence-linked language.
- Do keep focus indicators visible and all text/background combinations at WCAG AA or better.
- Don't use dark mode, purple-led palettes, pastel marketing surfaces, gradients, decorative blobs, or oversized hero typography.
- Don't expose infrastructure terminology in the teacher's primary workflow.
- Don't hide failures behind spinners, color alone, or optimistic completion states.
