# Altavela — agent memory

## Bug reports
- When asked for bugs, problems, or issues: present findings organized by severity (critical/high/medium/low) and ask which to fix. Do NOT auto-fix unless asked.
- Present count, file, line, and one-line description. Let the user decide priority.

## Deploy
- Manual deploy only. No CI/CD deploy job.
- `pnpm build && pnpm deploy` for UI, `git push && ssh pull+restart` for backend.
