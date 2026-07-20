# PeerAssist Compact Login Design

## Goal

Make login feel like the first step of a teacher's review workflow, not a marketing landing page. The user should recognize PeerAssist, enter credentials, and continue without competing content.

## Direction

Use one centered card with a maximum width of 460px. A compact brand row sits above the form; the login heading, credentials, primary action, registration link, and a short privacy note follow in one reading order. Remove the split layout, oversized slogan, decorative feature list, background grid, and large promotional panel.

## Visual System

- Warm neutral page background, white paper-like surface, dark ink, restrained teal action color.
- One subtle border and shadow; no gradients, glass effects, nested cards, or decorative geometry.
- Serif heading for an editorial cue; highly legible sans-serif form labels and controls.
- 44px minimum interactive targets, visible focus ring, and reduced-motion support.

## Responsive Behavior

- Desktop and tablet: centered 460px card with stable form dimensions.
- Mobile below 560px: 12px page inset, reduced card padding, full-width controls, no horizontal overflow.
- The form remains visible in the first viewport at 390x844 and 1440x900.

## Acceptance Criteria

- No `brand-features` marketing list or split-column layout remains.
- Keycloak login, registration, error, remember-me, password visibility, and localization behavior remain intact.
- Repository theme contract passes.
- Chromium screenshots at 1440x900 and 390x844 show no clipping or overlap.
- Login can be completed with the existing test account and redirects to the workspace.
