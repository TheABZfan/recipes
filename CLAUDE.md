# Recipe Tracker

Single-file web app: [index.html](index.html). No build step, no dependencies.
This file is the permanent home of the app and its recipes; edit it directly.

It is hosted on GitHub Pages so the user can use it on their phone (added to
their home screen). The user's main workflow is asking Claude — from Claude Code
on desktop or a Claude Code cloud session on their phone — to add or tweak
recipes. **After editing, commit and push to `main`**: Pages redeploys
automatically and the phone app picks up the change on next refresh. Prefer
committing directly to `main` over opening a PR — the user wants changes live
immediately, not a review step.

## How recipes are stored

- Recipes live in the `SEED_RECIPES` array inside index.html. That array is
  the source of truth for anything Claude manages.
- The browser also keeps a copy in localStorage (key `recipe-tracker:recipes`) so
  in-app edits persist. On load the app merges the file into storage by `id`.

## Editing recipes (the common request)

- **Add a recipe**: append an object to `SEED_RECIPES` with a new unique `id`
  (e.g. `'seed-shakshuka'`) and `rev: 1`. Fields: `id`, `rev`, `title`,
  `prepTime` (string, minutes), `servings` (number), `tags` (lowercase strings),
  `ingredients` (strings, one per line, quantity first so scaling works),
  `steps` (strings).
- **Tweak a recipe**: edit it in `SEED_RECIPES` and **bump its `rev` by 1** —
  otherwise the browser's stored copy wins and the change never shows up.
- **Recipe deleted in the app**: it stays deleted (id recorded in localStorage).
  To restore it, re-add it under a new id.
- **Recipes the user added in the app's form**: those exist only in their browser's
  localStorage — Claude cannot see them in the file. If the user refers to a recipe
  that isn't in `SEED_RECIPES`, ask them to open the app's **Import & export** screen,
  copy the exported JSON, and paste it into the chat; then merge it into `SEED_RECIPES`
  (keep the same `id`, set `rev` to at least the existing value + 1 if changing it).

## Verifying changes

On desktop, use the preview tools with the `recipe-tracker` config in
`.claude/launch.json`. After editing, reload and check the recipe list renders
and the console is clean. In a cloud session without preview, at minimum check
the file parses (e.g. `node --check` on the extracted script, or careful review)
before pushing — a syntax error takes the whole app down.
