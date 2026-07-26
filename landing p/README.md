# Expense Calc — Landing Page

Premium SaaS landing page for **Expense Calc**, an AI-powered business management product for small businesses.

## Stack

- React 19 + TypeScript
- Vite 8
- Tailwind CSS 4
- Framer Motion
- Lucide icons

## Design system

Matches the Stitch blueprint in `DESIGN.md`:

- Primary mint `#2E9B5D`
- Cream background `#FFFDF8`
- Lavender accent `#F4EEFF`
- Navy text `#111827`
- Plus Jakarta Sans

## Scripts

```bash
npm install
npm run dev
npm run build
npm run preview
```

## Structure

```
src/
  components/
    dashboard/   # React dashboard mock used in hero & showcase
    layout/      # Navbar, Footer
    sections/    # Landing page sections
    ui/          # Shared primitives
  hooks/
  lib/           # Content + animation tokens
```

The original Stitch HTML (`code.html`) and `screen.png` are kept as design references.
