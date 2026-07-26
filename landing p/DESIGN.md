---
name: Expense Calc
colors:
  surface: '#f5fbf3'
  surface-dim: '#d6dcd4'
  surface-bright: '#f5fbf3'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f0f5ed'
  surface-container: '#eaefe7'
  surface-container-high: '#e4eae2'
  surface-container-highest: '#dee4dc'
  on-surface: '#171d18'
  on-surface-variant: '#3e4a40'
  inverse-surface: '#2c322d'
  inverse-on-surface: '#edf2ea'
  outline: '#6e7a6f'
  outline-variant: '#bdcabd'
  surface-tint: '#006d3b'
  primary: '#006a39'
  on-primary: '#ffffff'
  primary-container: '#0a864b'
  on-primary-container: '#f6fff4'
  inverse-primary: '#73db96'
  secondary: '#605c6a'
  on-secondary: '#ffffff'
  secondary-container: '#e6e0f1'
  on-secondary-container: '#666270'
  tertiary: '#535f59'
  on-tertiary: '#ffffff'
  tertiary-container: '#6b7771'
  on-tertiary-container: '#f5fff8'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#8ff8b1'
  primary-fixed-dim: '#73db96'
  on-primary-fixed: '#00210e'
  on-primary-fixed-variant: '#00522b'
  secondary-fixed: '#e6e0f1'
  secondary-fixed-dim: '#c9c4d4'
  on-secondary-fixed: '#1c1a26'
  on-secondary-fixed-variant: '#484552'
  tertiary-fixed: '#d9e6de'
  tertiary-fixed-dim: '#bdcac2'
  on-tertiary-fixed: '#131e19'
  on-tertiary-fixed-variant: '#3e4944'
  background: '#f5fbf3'
  on-background: '#171d18'
  surface-variant: '#dee4dc'
typography:
  display-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 48px
    fontWeight: '700'
    lineHeight: '1.1'
    letterSpacing: -0.02em
  display-lg-mobile:
    fontFamily: Plus Jakarta Sans
    fontSize: 32px
    fontWeight: '700'
    lineHeight: '1.2'
    letterSpacing: -0.01em
  headline-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 24px
    fontWeight: '600'
    lineHeight: '1.3'
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 18px
    fontWeight: '400'
    lineHeight: '1.6'
  body-md:
    fontFamily: Plus Jakarta Sans
    fontSize: 16px
    fontWeight: '400'
    lineHeight: '1.5'
  label-sm:
    fontFamily: Plus Jakarta Sans
    fontSize: 14px
    fontWeight: '500'
    lineHeight: '1.4'
    letterSpacing: 0.01em
  label-xs:
    fontFamily: Plus Jakarta Sans
    fontSize: 12px
    fontWeight: '600'
    lineHeight: '1.2'
    letterSpacing: 0.03em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  unit: 4px
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  2xl: 48px
  3xl: 64px
  container-max: 1280px
  gutter: 24px
  margin-mobile: 16px
---

## Brand & Style
The design system for this product is rooted in a **Refined Minimalist** aesthetic, blending the precision of high-end SaaS tools like Linear and Stripe with an approachable, airy warmth. It targets financial professionals and business owners who value clarity, speed, and a sense of calm during data-heavy tasks.

The visual language is defined by expansive whitespace, soft geometric shapes, and a "light-touch" interface. By combining a professional Dark Navy foundation with a comforting Cream background, the UI avoids the sterile feel of typical enterprise software, instead evoking a premium, editorial quality. Interactive elements utilize subtle glassmorphism and soft shadows to create a clear but gentle visual hierarchy.

## Colors
The palette is designed to reduce cognitive load while providing distinct visual anchors for action and information.

- **Primary Mint (#2E9B5D):** Used exclusively for primary actions, success states, and key data points. It conveys growth and financial health.
- **Surface & Background:** The main canvas is **Cream (#FFFDF8)**, which provides a softer contrast for the eyes than pure white. **Pure White (#FFFFFF)** is reserved for cards and elevated containers to make them "pop" against the background.
- **Accent & Utility:** **Lavender (#F4EEFF)** serves as a secondary accent for subtle highlights or category tags, adding a modern, sophisticated touch. **Dark Navy (#111827)** provides high-legibility typography.
- **Light Gray (#F7F8FA):** Used for subtle borders and secondary backgrounds to create a layered effect without adding visual noise.

## Typography
We use **Plus Jakarta Sans** for its modern, geometric clarity and friendly curves, which align with the approachable nature of the brand.

- **Headlines:** Use tighter letter spacing and heavier weights (600-700) to create a strong visual anchor.
- **Body:** Set with generous line heights (1.5-1.6) to ensure long-form data reports remain readable.
- **Labels:** Small labels use a slightly increased letter spacing and medium weight to maintain legibility at small scales.
- **Hierarchy:** Maintain a clear vertical rhythm by using the `display` styles for hero sections and `headline` styles for module headers.

## Layout & Spacing
The layout follows a **Fluid Grid** model with fixed maximum widths for desktop to ensure optimal line lengths and data density.

- **Grid:** A 12-column grid is used for desktop (1280px max-width) with 24px gutters. On mobile, this transitions to a single-column layout with 16px side margins.
- **Spacing Rhythm:** All spacing is derived from a 4px base unit. Consistent use of `lg (24px)` for internal card padding and `2xl (48px)` for section vertical spacing creates a balanced, professional rhythm.
- **Negative Space:** Don't be afraid of "empty" space. The design system encourages generous margins around primary data visualizations to drive focus.

## Elevation & Depth
Depth is achieved through a combination of **Tonal Layering** and **Ambient Shadows**, avoiding harsh borders.

- **Level 0 (Background):** The Cream surface (#FFFDF8).
- **Level 1 (Cards):** Pure White (#FFFFFF) with a very soft, diffused shadow: `0 4px 20px rgba(17, 24, 39, 0.04)`.
- **Level 2 (Hover/Active):** Increased shadow depth: `0 12px 32px rgba(17, 24, 39, 0.08)`.
- **Overlays (Glassmorphism):** Modals and flyouts use a semi-transparent White (#FFFFFFBF) with a `20px` backdrop-blur and a subtle `1px` white border at 20% opacity to simulate light hitting the edge of glass.

## Shapes
The shape language is consistently **Rounded**, reinforcing the approachable and modern SaaS feel.

- **Standard Elements:** Buttons, inputs, and small widgets use a `0.5rem (8px)` radius.
- **Main Containers:** Cards and large dashboard modules use a `rounded-lg` (16px) or `rounded-xl` (24px) radius to create a distinct, friendly silhouette.
- **Chips/Badges:** Use a "pill" shape (full radius) to differentiate them from interactive button elements.

## Components
Consistent component behavior is vital for the "Premium SaaS" feel:

- **Buttons:**
    - **Primary:** Solid Mint (#2E9B5D) with white text. Hover state features a subtle scale-up (1.02x) and a shift to a slightly darker gradient.
    - **Secondary:** Light Mint (#EAF7EF) background with Mint text. Transitions to a soft shadow on hover.
    - **Ghost:** No background, Dark Navy text. Background appears as Light Gray (#F7F8FA) on hover.
- **Input Fields:** Use the Light Gray (#F7F8FA) for the background with a 1px border. Focus state should highlight the border in Mint and add a soft Mint outer glow.
- **Cards:** Always White (#FFFFFF) on the Cream background. Include a subtle 1px border (#F1F1F1) to define edges in high-light environments.
- **Chips/Tags:** Used for expense categories. Utilize the Lavender (#F4EEFF) for neutral categories and Light Mint for positive/reconciled entries.
- **Smooth Transitions:** All interactive states (hover, focus, active) must use a `200ms ease-in-out` transition for color and transform properties to ensure a high-end, fluid experience.